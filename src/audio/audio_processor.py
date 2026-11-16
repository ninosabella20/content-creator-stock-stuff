"""Audio helpers: WAV I/O, silence detection, timing alignment, mastering, MP3 export.

All heavy lifting is FFmpeg (loudness normalisation, resampling, MP3); timing work
is plain numpy so it is easy to test.
"""
from __future__ import annotations

import json
import logging
import subprocess
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
FRAME_S = 0.02  # 20 ms analysis frames


# ---------------------------------------------------------------------------- WAV I/O
def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"{path}: expected 16-bit PCM")
    a = np.frombuffer(data, dtype="<i2").astype(np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return a, sr


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def pcm_to_array(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.int16)


def resample(samples: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """High-quality resample through FFmpeg (soxr if available)."""
    if sr_from == sr_to:
        return samples
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(sr_from),
         "-ac", "1", "-i", "pipe:0", "-af", "aresample=resampler=soxr", "-ar", str(sr_to),
         "-f", "s16le", "-ac", "1", "pipe:1"],
        input=np.asarray(samples, dtype="<i2").tobytes(), capture_output=True, check=True)
    return pcm_to_array(p.stdout)


def change_tempo(samples: np.ndarray, sr: int, rate: float) -> np.ndarray:
    """Pitch-preserving tempo change (atempo). rate 1.0 = unchanged."""
    if abs(rate - 1.0) < 0.01:
        return samples
    rate = min(2.0, max(0.5, rate))
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1",
         "-i", "pipe:0", "-af", f"atempo={rate:.3f}", "-f", "s16le", "-ac", "1", "pipe:1"],
        input=np.asarray(samples, dtype="<i2").tobytes(), capture_output=True, check=True)
    return pcm_to_array(p.stdout)


# --------------------------------------------------------------------- silence & timing
def frame_db(samples: np.ndarray, sr: int) -> np.ndarray:
    n = max(1, int(sr * FRAME_S))
    usable = len(samples) // n * n
    if usable == 0:
        return np.array([-120.0])
    f = samples[:usable].astype(np.float32).reshape(-1, n) / 32768.0
    rms = np.sqrt((f ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def find_silences(samples: np.ndarray, sr: int, min_len: float = 0.12,
                  rel_db: float = -32.0) -> list[tuple[float, float]]:
    """Pauses as (start_s, end_s): frames quieter than (speech level + rel_db)."""
    db = frame_db(samples, sr)
    speech = np.percentile(db, 90) if len(db) else -20
    quiet = db < max(speech + rel_db, -60)
    out, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * FRAME_S >= min_len:
                out.append((start * FRAME_S, i * FRAME_S))
            start = None
    return out


def trim_silence(samples: np.ndarray, sr: int, keep: float = 0.08) -> np.ndarray:
    db = frame_db(samples, sr)
    if not len(db):
        return samples
    voiced = np.where(db > max(np.percentile(db, 90) - 35, -55))[0]
    if not len(voiced):
        return samples
    n = int(sr * FRAME_S)
    a = max(0, voiced[0] * n - int(keep * sr))
    b = min(len(samples), (voiced[-1] + 1) * n + int(keep * sr))
    return samples[a:b]


def align_boundaries(weights: list[float], duration: float, silences: list[tuple[float, float]],
                     window: float = 2.5) -> list[float]:
    """Split `duration` into len(weights) parts proportional to weights (e.g. characters),
    snapping each internal boundary to the middle of the longest nearby pause.
    Returns the internal boundary times (len(weights) - 1 values, increasing)."""
    total = sum(weights) or 1.0
    est, acc = [], 0.0
    for w in weights[:-1]:
        acc += w
        est.append(duration * acc / total)
    out: list[float] = []
    prev = 0.0
    for i, t in enumerate(est):
        nxt_limit = est[i + 1] if i + 1 < len(est) else duration
        cands = [(e - s, (s + e) / 2) for s, e in silences
                 if abs((s + e) / 2 - t) <= window and prev < (s + e) / 2 < nxt_limit]
        if cands:
            # longest pause, tie-broken by closeness to the estimate
            best = max(cands, key=lambda c: (round(c[0], 2), -abs(c[1] - t)))[1]
        else:
            best = t
        best = max(best, prev + 0.05)
        out.append(best)
        prev = best
    return out


def fade_edges(samples: np.ndarray, sr: int, ms: float = 12) -> np.ndarray:
    n = min(len(samples) // 2, int(sr * ms / 1000))
    if n <= 0:
        return samples
    a = samples.astype(np.float32)
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    a[:n] *= ramp
    a[-n:] *= ramp[::-1]
    return a.astype(np.int16)


def silence(sr: int, seconds: float) -> np.ndarray:
    return np.zeros(int(sr * seconds), dtype=np.int16)


# ------------------------------------------------------------------------- mastering
def ffprobe_duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(p.stdout)["format"]["duration"])


def master(in_wav: Path, out_wav: Path, lufs: float = -16.0, sr: int = 44100) -> dict:
    """Two-pass EBU R128 loudness normalisation + gentle high-pass, 16-bit WAV master."""
    base = f"highpass=f=60,loudnorm=I={lufs}:TP=-1.5:LRA=11"
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(in_wav), "-af",
                        base + ":print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    stats = {}
    try:
        blob = p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1]
        stats = json.loads(blob)
    except ValueError:
        log.warning("loudnorm analysis failed; using one-pass normalisation")
    if stats:
        af = (f"{base}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
              f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
              f"offset={stats['target_offset']}:linear=true")
    else:
        af = base
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(in_wav),
                    "-af", af, "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
                   check=True)
    return {"input_lufs": stats.get("input_i"), "target_lufs": lufs}


def export_mp3(wav: Path, mp3: Path, meta: dict, cover: Path | None = None,
               bitrate: str = "128k") -> None:
    """Podcast MP3: 44.1 kHz, CBR, ID3v2.3 tags, optional embedded cover art."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav)]
    if cover and cover.exists():
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg",
                "-disposition:v", "attached_pic", "-metadata:s:v", "title=Cover",
                "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-c:a", "libmp3lame", "-b:a", bitrate, "-ar", "44100", "-ac", "1",
            "-id3v2_version", "3"]
    for k, v in meta.items():
        if v:
            cmd += ["-metadata", f"{k}={v}"]
    cmd.append(str(mp3))
    subprocess.run(cmd, check=True)
