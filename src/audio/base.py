"""Provider-independent text-to-speech interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class TTSError(RuntimeError):
    pass


class TTSProvider(ABC):
    #: name used in reports and config ("gemini", "piper")
    name: str = "base"
    #: how many words may go into one synthesis call; 0 = exactly one segment per call
    max_words_per_call: int = 0

    @abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Speak `text` (already normalised). Returns (int16 mono samples, sample_rate)."""

    def verify(self, samples: np.ndarray, sr: int, text: str) -> str | None:
        """Optional quality check of generated audio. Return a problem description, or None."""
        return None

    @property
    def voice_signature(self) -> str:
        """Identifies the exact voice. If it changes mid-episode the episode is re-voiced,
        so one episode always has one consistent narrator."""
        return self.name

    def describe(self) -> dict:
        return {"provider": self.name, "voice": self.voice_signature}
