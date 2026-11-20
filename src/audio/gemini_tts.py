"""Gemini text-to-speech provider (free tier, podcast project's GEMINI_API_KEY)."""
from __future__ import annotations

import io
import logging
import re
import wave

import numpy as np

from ..gemini_client import Gemini, GeminiError, Usage
from .audio_processor import pcm_to_array
from .base import TTSError, TTSProvider

log = logging.getLogger(__name__)


class GeminiTTSProvider(TTSProvider):
    name = "gemini"

    def __init__(self, key: str, models: list[str], voice: str = "Charon", style: str = "",
                 max_words_per_call: int = 300, min_interval: float = 21, timeout: float = 300,
                 max_calls: int = 14, usage: Usage | None = None):
        if not key:
            raise TTSError("GEMINI_API_KEY is not set")
        self.voice = voice
        self.style = " ".join(style.split())
        self.max_words_per_call = max_words_per_call
        self.client = Gemini(key, models, min_interval=min_interval, timeout=timeout,
                             max_calls=max_calls, usage=usage)
        self._key = key
        self._checker: Gemini | None = None
        self.check_seconds = 20.0
        self.preferred_check_model: str | None = None

    @property
    def voice_signature(self) -> str:
        return f"gemini:{self.client.model}:{self.voice}"

    def prompt(self, text: str) -> str:
        # The transcript ONLY. Instructions in the same request were sometimes read aloud
        # ("please read the following..."), so a style text is deliberately not supported.
        return text

    def _check_client(self) -> Gemini:
        if self._checker is None:
            from ..gemini_client import list_models, pick_text_models
            self._checker = Gemini(self._key, pick_text_models(list_models(self._key), None,
                                                               self.preferred_check_model),
                                   min_interval=4, timeout=60, max_calls=12)
        return self._checker

    def verify(self, samples: np.ndarray, sr: int, text: str) -> str | None:
        """Listen to the start of the audio (via transcription) and make sure it starts with
        the script, i.e. nothing like "please read the following..." was spoken first."""
        clip = samples[: int(sr * self.check_seconds)]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.asarray(clip, dtype="<i2").tobytes())
        try:
            heard = self._check_client().transcribe(buf.getvalue())
        except Exception as e:  # noqa: BLE001 - the check must never block narration
            log.warning("audio check skipped (transcription unavailable: %s)", str(e)[:150])
            return None
        log.info("audio check heard: %r", " ".join(heard.split()[:14]))
        return leak_problem(heard, text)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            pcm, sr = self.client.generate_speech(self.prompt(text), self.voice)
        except GeminiError as e:
            raise TTSError(str(e)) from e
        return pcm_to_array(pcm), sr


def _words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", t.lower())


# Phrases that only appear when a voice reads instructions instead of the script.
LEAK_RE = re.compile(r"(read (the following|this|it|aloud)|following (transcript|script|text)|"
                     r"\btranscript\b|as a (calm|confident|professional|financial news) presenter|"
                     r"in a way that sounds|exactly as written|without adding anything)", re.I)


def _squash(t: str) -> str:
    """Letters/digits only, so 'Health Care 2.05 percent' ~ 'Healthcare 2.05%'."""
    t = re.sub(r"\bpercent\b", "", t.lower())
    return re.sub(r"[^a-z0-9]", "", t)


def leak_problem(heard: str, script: str) -> str | None:
    """Return a problem only when we're confident extra speech precedes the script:
    the transcript contains instruction-like wording, or the script's opening clearly
    appears later in the transcript. Transcription noise never blocks an episode."""
    if LEAK_RE.search(heard) and not LEAK_RE.search(script[: len(heard) + 80]):
        return f"audio contains instruction-like speech: {' '.join(heard.split()[:16])!r}"
    hs, ss = _squash(heard), _squash(script)
    if len(hs) < 20 or len(ss) < 20:
        return None
    for n in (24, 16):
        pos = hs.find(ss[:n])
        if pos == -1:
            continue
        if pos <= 6:
            return None                                   # starts with the script
        return f"audio starts with extra speech before the script: {heard[:120]!r}"
    return None   # can't tell (e.g. numbers spelled differently): don't block
