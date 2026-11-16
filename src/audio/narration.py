"""Narrate a whole episode with ONE consistent voice and produce a precise timeline.

Output (in the episode folder):
  narration_master.wav  loudness-normalised 44.1 kHz master
  timeline.json         start/end of every segment and sentence (drives video + subtitles)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from ..config import Settings, secret
from ..editorial.textutil import sentences, word_count
from ..gemini_client import Usage
from ..util import StageError, write_json
from . import audio_processor as ap
from .base import TTSError, TTSProvider
from .text_normalize import normalize_for_tts

log = logging.getLogger(__name__)
WORK_SR = 24000


def make_provider(name: str, settings: Settings, usage: Usage | None = None) -> TTSProvider:
    s = settings.get_path
    if name == "gemini":
        from .gemini_tts import GeminiTTSProvider
        prov = GeminiTTSProvider(
            secret("GEMINI_API_KEY") or "", list(s("tts.gemini.models")), s("tts.gemini.voice", "Charon"),
            s("tts.gemini.style", ""), int(s("tts.gemini.max_words_per_chunk", 300)),
            float(s("tts.gemini.min_interval_seconds", 21)), float(s("tts.gemini.timeout_seconds", 300)),
            int(s("tts.gemini.max_calls_per_run", 14)), usage)
        prov.preferred_check_model = s("gemini.preferred_model")
        return prov
    if name == "piper":
        from .piper_tts import PiperTTSProvider
        spk = s("tts.piper.speaker")
        return PiperTTSProvider(s("tts.piper.voice"), int(spk) if spk is not None else None,
                                float(s("tts.piper.length_scale", 1.0)),
                                float(s("tts.piper.sentence_silence", 0.25)))
    raise StageError("narration", f"unknown TTS provider {name!r}")


def plan_chunks(segments: list[dict], max_words: int) -> list[list[int]]:
    """Group consecutive segment indexes so each chunk stays under max_words."""
    if max_words <= 0:
        return [[i] for i in range(len(segments))]
    chunks, cur, n = [], [], 0
    for i, seg in enumerate(segments):
        w = word_count(seg["narration"])
        if cur and n + w > max_words:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += w
    if cur:
        chunks.append(cur)
    return chunks


def _synth_checked(provider: TTSProvider, text: str, words: int, wpm: float) -> tuple[np.ndarray, int]:
    """Synthesize and reject truncated/garbled output (protects against abrupt cuts)."""
    expected = words / wpm * 60
    for attempt in (1, 2):
        samples, sr = provider.synthesize(text)
        samples = ap.trim_silence(samples, sr)
        dur = len(samples) / sr
        if not (expected * 0.5 <= dur <= expected * 2.2 or expected < 4):
            problem = f"audio is {dur:.1f}s for ~{expected:.1f}s of text"
        else:
            problem = provider.verify(samples, sr, text)
            if problem is None:
                return samples, sr
        log.warning("%s: %s (attempt %d)", provider.name, problem, attempt)
    raise TTSError(f"{provider.name} audio rejected twice: {problem}")


def voice_segments(segments: list[dict], provider: TTSProvider, settings: Settings
                   ) -> list[np.ndarray]:
    """Return one WORK_SR audio array per segment, all in the same voice."""
    s = settings.get_path
    wpm = float(s("editorial.words_per_minute", 150))
    lex = s("tts.pronunciations") or {}
    chunks = plan_chunks(segments, provider.max_words_per_call)
    for restart in range(3):
        signature = None
        out: list[np.ndarray] = [np.zeros(0, np.int16)] * len(segments)
        switched = False
        for ci, idxs in enumerate(chunks):
            texts = [normalize_for_tts(segments[i]["narration"], lex) for i in idxs]
            words = sum(word_count(t) for t in texts)
            t0 = time.time()
            audio, sr = _synth_checked(provider, "\n\n".join(texts), words, wpm)
            audio = ap.resample(audio, sr, WORK_SR)
            log.info("chunk %d/%d (%s): %d words -> %.1fs audio in %.0fs", ci + 1, len(chunks),
                     ",".join(segments[i]["id"] for i in idxs), words, len(audio) / WORK_SR,
                     time.time() - t0)
            if signature is None:
                signature = provider.voice_signature
            elif provider.voice_signature != signature:
                log.warning("voice changed mid-episode (%s -> %s): re-voicing from the start",
                            signature, provider.voice_signature)
                switched = True
                break
            if len(idxs) == 1:
                out[idxs[0]] = audio
                continue
            sil = ap.find_silences(audio, WORK_SR, min_len=0.25)
            cuts = ap.align_boundaries([len(t) for t in texts], len(audio) / WORK_SR, sil, window=3.0)
            edges = [0] + [int(c * WORK_SR) for c in cuts] + [len(audio)]
            for k, i in enumerate(idxs):
                out[i] = ap.trim_silence(audio[edges[k]:edges[k + 1]], WORK_SR)
        if not switched:
            return out
    raise TTSError("voice kept changing between chunks; giving up for consistency")


def build_timeline(segments: list[dict], audios: list[np.ndarray], gap: float) -> tuple[np.ndarray, dict]:
    parts, t = [], 0.0
    timeline = {"sample_rate": WORK_SR, "segments": []}
    lead = 0.4
    parts.append(ap.silence(WORK_SR, lead))
    t = lead
    for seg, audio in zip(segments, audios):
        audio = ap.fade_edges(audio, WORK_SR)
        dur = len(audio) / WORK_SR
        sents = sentences(seg["narration"]) or [seg["narration"]]
        sil = ap.find_silences(audio, WORK_SR, min_len=0.12)
        cuts = ap.align_boundaries([max(1, len(x)) for x in sents], dur, sil, window=1.5) if len(sents) > 1 else []
        bounds = [0.0] + cuts + [dur]
        timeline["segments"].append({
            "id": seg["id"], "type": seg["type"], "start": round(t, 3), "end": round(t + dur, 3),
            "sentences": [{"text": x, "start": round(t + bounds[k], 3), "end": round(t + bounds[k + 1], 3)}
                          for k, x in enumerate(sents)],
        })
        parts.append(audio)
        parts.append(ap.silence(WORK_SR, gap))
        t += dur + gap
    parts.append(ap.silence(WORK_SR, 1.0))
    full = np.concatenate(parts)
    timeline["duration"] = round(len(full) / WORK_SR, 3)
    return full, timeline


def narrate(episode: dict, out_dir: Path, settings: Settings, usage: Usage | None = None) -> dict:
    s = settings.get_path
    segments = [x for x in episode["segments"] if x.get("narration")]
    primary = s("tts.provider", "gemini")
    order = [primary] + ([s("tts.fallback")] if s("tts.fallback") not in (None, "", "fail", primary) else [])
    errors = {}
    for name in order:
        try:
            provider = make_provider(name, settings, usage)
            audios = voice_segments(segments, provider, settings)
            break
        except Exception as e:  # noqa: BLE001
            log.error("TTS provider %s failed: %s", name, e)
            errors[name] = str(e)[:400]
    else:
        raise StageError("narration", "all TTS providers failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    rate = float(s("tts.speaking_rate", 1.0))
    if abs(rate - 1.0) >= 0.01:
        audios = [ap.change_tempo(a, WORK_SR, rate) for a in audios]
    full, timeline = build_timeline(segments, audios, float(s("tts.segment_gap_seconds", 0.7)))
    raw = out_dir / "narration_raw.wav"
    ap.write_wav(raw, full, WORK_SR)
    master_wav = out_dir / "narration_master.wav"
    loud = ap.master(raw, master_wav, float(s("tts.loudness_lufs", -16)))
    raw.unlink(missing_ok=True)
    timeline.update({"voice": provider.describe(), "fallback_errors": errors, "loudness": loud,
                     "master": master_wav.name})
    write_json(out_dir / "timeline.json", timeline)
    log.info("narration: %.1f min, voice %s", timeline["duration"] / 60, provider.voice_signature)
    return timeline
