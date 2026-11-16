"""Piper: free, open-source, offline neural TTS (runs on the GitHub runner's CPU).

Piper is run as a separate program (`python -m piper`, GPL-3.0), not linked into this
code base. Voices are downloaded once and cached. Check each voice's MODEL_CARD for
its dataset licence; the default LibriTTS-based voices come from CC BY 4.0 data, which
allows commercial use with attribution (see README "Voice licensing").
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from ..util import HTTP
from .audio_processor import read_wav
from .base import TTSError, TTSProvider

log = logging.getLogger(__name__)
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# Voices also published as GitHub release assets (used when HuggingFace is unreachable).
GITHUB_VOICES = {
    "en-us-libritts-high": "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-libritts-high.tar.gz",
}
CACHE = Path(os.environ.get("PIPER_CACHE", Path.home() / ".cache" / "market-brief-daily" / "piper"))


def ensure_voice(voice: str, cache: Path = CACHE) -> Path:
    """Return the path of `<voice>.onnx`, downloading it (and its .json) if needed."""
    cache.mkdir(parents=True, exist_ok=True)
    onnx = cache / f"{voice}.onnx"
    if onnx.exists() and (cache / f"{voice}.onnx.json").exists():
        return onnx
    if voice in GITHUB_VOICES:
        import tarfile
        tgz = cache / f"{voice}.tar.gz"
        _download(GITHUB_VOICES[voice], tgz)
        with tarfile.open(tgz) as tf:
            for m in tf.getmembers():
                if m.name.endswith((".onnx", ".onnx.json", "MODEL_CARD")) and m.isfile():
                    m.name = Path(m.name).name if not m.name.endswith("MODEL_CARD") else f"{voice}.MODEL_CARD"
                    tf.extract(m, cache, filter="data")
        tgz.unlink(missing_ok=True)
    else:
        m = re.fullmatch(r"([a-z]{2})_([A-Z]{2})-(.+)-(x_low|low|medium|high)", voice)
        if not m:
            raise TTSError(f"unknown Piper voice name {voice!r} (expected e.g. en_US-ryan-high)")
        lang, region, name, quality = m.groups()
        base = f"{HF}/{lang}/{lang}_{region}/{name}/{quality}/{voice}"
        _download(base + ".onnx.json", cache / f"{voice}.onnx.json")
        _download(base + ".onnx", onnx)
        try:
            _download(f"{HF}/{lang}/{lang}_{region}/{name}/{quality}/MODEL_CARD",
                      cache / f"{voice}.MODEL_CARD")
        except TTSError:
            pass
    if not onnx.exists():
        raise TTSError(f"voice {voice} download did not produce {onnx.name}")
    return onnx


def _download(url: str, dest: Path) -> None:
    log.info("downloading %s", url)
    try:
        with HTTP.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            tmp.replace(dest)
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"download failed for {url}: {e}") from e


class PiperTTSProvider(TTSProvider):
    name = "piper"
    max_words_per_call = 0   # one segment per call: exact segment timing

    def __init__(self, voice: str = "en-us-libritts-high", speaker: int | None = None,
                 length_scale: float = 1.0, sentence_silence: float = 0.25):
        self.voice = voice
        self.speaker = speaker
        self.length_scale = length_scale
        self.sentence_silence = sentence_silence
        self.model = ensure_voice(voice)

    @property
    def voice_signature(self) -> str:
        return f"piper:{self.voice}:{self.speaker}"

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.wav"
            cmd = [sys.executable, "-m", "piper", "-m", str(self.model), "-f", str(out),
                   "--length-scale", str(self.length_scale),
                   "--sentence-silence", str(self.sentence_silence)]
            if self.speaker is not None:
                cmd += ["-s", str(self.speaker)]
            p = subprocess.run(cmd, input=text, text=True, capture_output=True)
            if p.returncode != 0 or not out.exists():
                raise TTSError(f"piper failed: {p.stderr[-400:]}")
            return read_wav(out)
