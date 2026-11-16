"""Gemini text-to-speech provider (free tier, podcast project's GEMINI_API_KEY)."""
from __future__ import annotations

import logging

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

    @property
    def voice_signature(self) -> str:
        return f"gemini:{self.client.model}:{self.voice}"

    def prompt(self, text: str) -> str:
        # The transcript ONLY. Instructions in the same request were sometimes read aloud
        # ("please read the following..."), so a style text is deliberately not supported.
        return text

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            pcm, sr = self.client.generate_speech(self.prompt(text), self.voice)
        except GeminiError as e:
            raise TTSError(str(e)) from e
        return pcm_to_array(pcm), sr
