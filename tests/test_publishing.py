"""Market-close data, RSS feed and release publishing (all offline)."""
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.market_close import CloseDataError, Series, apply_close, latest_session
from src.publishing import podcast_feed
from src.state import Ledger


def _series(sym, last_day=date(2026, 11, 16), closes=None, mt_hour=21):
    days, d = [], last_day
    while len(days) < 400:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    closes = closes or [100 + i * 0.1 for i in range(len(days))]
    mt = datetime.combine(last_day, datetime.min.time(), timezone.utc).replace(hour=mt_hour, minute=5)
    return Series(sym, days, closes, mt)


def test_latest_session_rows_and_periods():
    rows, session = latest_session(date(2026, 11, 16), fetch=lambda s, *a: _series(s))
    assert session == date(2026, 11, 16) and len(rows) == 12
    r = rows["SPY"]
    assert r.day == pytest.approx((r.last / (r.last - 0.1) - 1) * 100, abs=0.01)
    assert r.week is not None and r.ytd is not None and r.as_of == "2026-11-16"


def test_intraday_snapshot_is_rejected():
    with pytest.raises(CloseDataError):     # 15:00 New York = before the close
        latest_session(date(2026, 11, 16), fetch=lambda s, *a: _series(s, mt_hour=20 - 0))


def test_morning_text_from_previous_session_becomes_context_only(mb):
    rows, session = latest_session(date(2026, 11, 16), fetch=lambda s, *a: _series(s))
    recap = mb.recap
    apply_close(mb, rows, session)
    assert mb.recap == "" and mb.reasons == {}
    assert mb.prior_session["recap"] == recap and "13 Nov" in mb.prior_session["label"]
    assert mb.as_of == "2026-11-16" and mb.as_of_label == "Close Mon 16 Nov 2026"


def test_rss_feed_written_from_ledger(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(podcast_feed, "FEED_DIR", tmp_path / "docs" / "podcast")
    monkeypatch.setattr(podcast_feed, "ROOT", tmp_path)
    monkeypatch.setenv("PODCAST_OWNER_EMAIL", "owner@example.com")
    monkeypatch.setattr(podcast_feed, "ensure_release_asset", lambda tag, t, b, mp3: {
        "url": f"https://github.com/o/r/releases/download/{tag}/{mp3.name}", "bytes": 1234, "release": "x"})
    ledger = Ledger(tmp_path / "e.json")
    mp3 = tmp_path / "ep.mp3"
    mp3.write_bytes(b"x")
    ep = {"episode_id": "market-brief-daily-2026-11-16", "date": "2026-11-16"}
    res = podcast_feed.publish(ep, {"mp3": mp3}, "16 Nov 2026: Title & more", "Desc <b>", {"duration": 650.4},
                               ledger, settings)
    assert res["status"] == "in_feed"
    root = ET.parse(tmp_path / "docs" / "podcast" / "feed.xml").getroot()
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    ch = root.find("channel")
    assert ch.find("title").text == "Daily Market Brief"
    assert ch.find("itunes:owner/itunes:email", ns).text == "owner@example.com"
    item = ch.find("item")
    assert item.find("enclosure").get("type") == "audio/mpeg"
    assert item.find("guid").text == "market-brief-daily-2026-11-16"
    assert item.find("itunes:duration", ns).text == "650"
