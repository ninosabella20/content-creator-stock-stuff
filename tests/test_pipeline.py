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
    monkeypatch.setattr("src.audio.narration.make_provider", lambda name, s, u=None: ToneTTS(wps=3))
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


def test_schedule_window_new_york(settings):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny, utc = ZoneInfo("America/New_York"), ZoneInfo("UTC")
    # winter (EST, UTC-5): 20:30 UTC = 15:30 NY too early; 21:30 UTC = 16:30 NY due
    assert not m.in_window(settings, datetime(2026, 12, 1, 20, 30, tzinfo=utc).astimezone(ny))
    assert m.in_window(settings, datetime(2026, 12, 1, 21, 30, tzinfo=utc).astimezone(ny))
    # summer (EDT, UTC-4): 20:30 UTC = 16:30 NY due
    assert m.in_window(settings, datetime(2026, 7, 1, 20, 30, tzinfo=utc).astimezone(ny))


def test_scheduled_run_skips_weekends(env, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    sat = datetime(2026, 11, 14, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(m, "local_now", lambda tz: sat)
    assert m.main(["run", "--scheduled"]) == 0
    assert not (env / "episodes.json").exists()


def test_scheduled_run_skips_market_holidays(env, monkeypatch):
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    thanksgiving = datetime(2026, 11, 26, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    monkeypatch.setattr(m, "local_now", lambda tz: thanksgiving)
    monkeypatch.setattr("src.data.market_close.latest_session", lambda d: ({}, date(2026, 11, 25)))
    assert m.main(["run", "--scheduled"]) == 0
    assert not (env / "episodes.json").exists()


def test_publish_failure_keeps_episode_produced(env, monkeypatch):
    monkeypatch.setenv("PUBLISH_YOUTUBE", "true")

    def boom(*a, **k):
        from src.publishing.youtube import YouTubeError
        raise YouTubeError("missing GitHub secret(s): YOUTUBE_REFRESH_TOKEN")
    monkeypatch.setattr("src.publishing.youtube.publish", boom)
    # full render needed for publishing; use the cheap fake voice and allow the template flag
    rc = m.main(["run", "--date", "2026-11-15", "--data-dir", str(FIX), "--force", "--allow-flagged"])
    assert rc == 1
    out = env / "out" / "market-brief-daily-2026-11-15"
    rep = json.loads((out / "run_report.json").read_text())
    assert rep["status"] == "produced_publish_failed" and rep["youtube"]["status"] == "failed"
    ledger = json.loads((env / "episodes.json").read_text())
    assert ledger["episodes"]["market-brief-daily-2026-11-15"]["status"] == "produced"


def test_quota_estimate(settings):
    from src.quota import estimate
    e = estimate(settings)
    assert e["per_month_30d"]["videos"] == 30 and 250 <= e["per_month_30d"]["narration_minutes"] <= 330
    assert any(v.startswith("OK") for v in e["verdict"])
