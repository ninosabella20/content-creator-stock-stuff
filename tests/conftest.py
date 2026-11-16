import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.audio.base import TTSProvider  # noqa: E402
from src.config import load_settings  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
EPISODE_DATE = date(2026, 11, 15)
# a moment shortly after the fixture data was generated (05:01Z = 06:01 Madrid)
FRESH_NOW = datetime(2026, 11, 15, 7, 45, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch):
    for k in ("TTS_PROVIDER", "PUBLISH_YOUTUBE", "PUBLISH_PUBLICLY", "GEMINI_API_KEY", "ALLOW_STALE",
              "YOUTUBE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    return load_settings()


@pytest.fixture
def raw_data():
    return json.loads((FIX / "data.json").read_text())


@pytest.fixture
def raw_geo():
    return json.loads((FIX / "geo.json").read_text())


@pytest.fixture
def mb(raw_data):
    from src.data.market_brief import parse_market_brief
    return parse_market_brief(raw_data)


@pytest.fixture
def geo(raw_geo):
    from src.data.geopolitics import parse_geo
    return parse_geo(raw_geo)


@pytest.fixture
def data_report(mb, geo, settings):
    from src.data.validation import validate
    return validate(mb, geo, EPISODE_DATE, settings, now=FRESH_NOW)


@pytest.fixture
def template_episode(mb, geo, data_report, settings):
    from src.editorial.pipeline import produce_script
    ep, _ = produce_script(mb, geo, data_report, EPISODE_DATE, settings, None, force_template=True)
    return ep


class ToneTTS(TTSProvider):
    """Deterministic fake voice: a 220 Hz tone per word, 0.25 s pauses at sentence ends."""
    name = "tone"
    max_words_per_call = 0

    def __init__(self, sr=24000, wps=2.2, signature="tone:1", max_words=0):
        self.sr, self.wps, self.sig, self.calls = sr, wps, signature, 0
        self.max_words_per_call = max_words

    @property
    def voice_signature(self):
        return self.sig

    def synthesize(self, text):
        self.calls += 1
        parts = []
        for sent in [s for s in text.replace("\n\n", " ").split(". ") if s.strip()]:
            n = max(1, len(sent.split()))
            t = np.arange(int(self.sr * n / self.wps)) / self.sr
            parts.append((np.sin(2 * np.pi * 220 * t) * 9000).astype(np.int16))
            parts.append(np.zeros(int(self.sr * 0.25), np.int16))
        return np.concatenate(parts), self.sr


@pytest.fixture
def tone_tts():
    return ToneTTS()
