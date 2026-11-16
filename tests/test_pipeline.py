"""End-to-end pipeline runs with fixtures, a fake voice and no network."""
import json

import pytest

from src import main as m

from .conftest import FIX, FRESH_NOW, ToneTTS


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "episodes.json"))
    for k in ("PUBLISH_YOUTUBE", "GEMINI_API_KEY", "YOUTUBE_REFRESH_TOKEN", "TTS_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("src.data.validation.now_utc", lambda: FRESH_NOW)
    monkeypatch.setattr("src.audio.narration.make_provider", lambda name, s, u=None: ToneTTS(wps=4.5))
    return tmp_path


def run(*extra):
    return m.main(["run", "--date", "2026-11-15", "--data-dir", str(FIX), "--video-seconds", "2", *extra])


def test_full_run_then_no_duplicate(env):
    assert run() == 0
    out = env / "out" / "market-brief-daily-2026-11-15"
    for f in ("episode.json", "script.md", "timeline.json", "narration_master.wav",
              "market-brief-daily-2026-11-15.mp3", "market-brief-daily-2026-11-15.mp4",
              "market-brief-daily-2026-11-15.srt", "thumbnail.jpg", "cover.jpg",
              "youtube_metadata.json", "podcast/episode.mp3", "podcast/UPLOAD_TO_SPOTIFY.md", "run_report.json"):
        assert (out / f).exists(), f
    rep = json.loads((out / "run_report.json").read_text())
    assert rep["status"] == "ok" and rep["youtube"]["status"].startswith("disabled")
    ledger = json.loads((env / "episodes.json").read_text())
    assert ledger["episodes"]["market-brief-daily-2026-11-15"]["status"] == "produced"
    # second run the same day: nothing is rebuilt or published
    (out / "run_report.json").unlink()
    assert run() == 0
    assert not (out / "run_report.json").exists()


def test_flagged_or_partial_episode_is_never_uploaded(env, monkeypatch):
    monkeypatch.setenv("PUBLISH_YOUTUBE", "true")
    called = []
    monkeypatch.setattr("src.publishing.youtube.publish", lambda *a, **k: called.append(1))
    assert run("--force") == 0          # template script => flagged; and only 2 s rendered
    rep = json.loads((env / "out" / "market-brief-daily-2026-11-15" / "run_report.json").read_text())
    assert rep["youtube"]["status"] == "skipped" and not called


def test_failure_reports_the_stage(env, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr("src.audio.narration.voice_segments", broken)
    assert run("--force") == 1
    rep = json.loads((env / "out" / "market-brief-daily-2026-11-15" / "run_report.json").read_text())
    assert rep["status"] == "failed" and rep["failed_stage"] == "narration"
    ledger = json.loads((env / "episodes.json").read_text())
    assert ledger["episodes"]["market-brief-daily-2026-11-15"]["status"] == "failed"


def test_schedule_window(settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Madrid")
    # 06:30 UTC in winter = 07:30 Madrid: too early. 07:30 UTC in winter = 08:30: due.
    assert not m.in_window(settings, datetime(2026, 12, 1, 6, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz))
    assert m.in_window(settings, datetime(2026, 12, 1, 7, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz))
    # summer: 06:30 UTC = 08:30 Madrid: due
    assert m.in_window(settings, datetime(2026, 7, 1, 6, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz))


def test_quota_estimate(settings):
    from src.quota import estimate
    e = estimate(settings)
    assert e["per_month_30d"]["videos"] == 30 and 250 <= e["per_month_30d"]["narration_minutes"] <= 330
    assert any(v.startswith("OK") for v in e["verdict"])
