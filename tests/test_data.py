from datetime import datetime, timezone

import pytest

from src.data.geopolitics import parse_geo
from src.data.market_brief import SECTOR_ORDER, parse_market_brief
from src.data.validation import fetch_validated, validate
from src.util import StageError

from .conftest import EPISODE_DATE, FRESH_NOW


def test_reads_all_11_sectors_and_index(mb):
    assert mb.index is not None and mb.index.ticker == "SPY"
    assert set(SECTOR_ORDER) <= set(mb.sectors)
    assert set(SECTOR_ORDER) <= set(mb.stories)
    assert mb.recap and mb.generated_at is not None


def test_stories_keep_original_urls_and_sources(mb):
    for t in SECTOR_ORDER:
        for st in mb.stories[t].stories:
            assert st.url.startswith("http")
            assert st.source
            assert st.headline


def test_geo_players_parsed(geo):
    assert geo.players and geo.players[0].rank == 1
    p = geo.by_code("cn")
    assert p and p.week_line and p.impacts and all(l["url"].startswith("http") for l in p.week_links)


def test_missing_fields_are_tolerated():
    mb = parse_market_brief({"market": {"rows": [{"ticker": "SPY", "day": "bad"}]},
                             "stories": {"sectors": {"XLK": {"items": [{"headline": "x"}, "junk"]}}}})
    assert mb.index.day is None
    assert mb.stories["XLK"].stories == []          # no URL -> dropped, never invented
    geo = parse_geo({"players": [{"code": "cn", "name": "China", "profile": "oops"}]})
    assert geo.players[0].impacts == []


def test_fresh_data_passes(mb, geo, settings):
    rep = validate(mb, geo, EPISODE_DATE, settings, now=FRESH_NOW)
    assert rep.ok and rep.market_fresh and rep.geo_fresh and not rep.flagged


def test_yesterdays_data_is_stale(mb, geo, settings):
    from datetime import date
    rep = validate(mb, geo, date(2026, 11, 16), settings, now=datetime(2026, 11, 16, 7, 45, tzinfo=timezone.utc))
    assert not rep.market_fresh and rep.flagged
    assert any("not from today's refresh" in w for w in rep.warnings)


def test_missing_index_is_fatal(raw_data, geo, settings):
    raw_data["market"]["rows"] = [r for r in raw_data["market"]["rows"] if r["ticker"] != "SPY"]
    rep = validate(parse_market_brief(raw_data), geo, EPISODE_DATE, settings, now=FRESH_NOW)
    assert not rep.ok


def test_fetch_retries_then_refuses_stale(mb, geo, settings, monkeypatch):
    import src.data.validation as v
    monkeypatch.setattr(v, "now_utc", lambda: datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc))
    settings.set_path("sources.retry_max_minutes", 0.0001)
    calls = []
    with pytest.raises(StageError) as e:
        fetch_validated(settings, __import__("datetime").date(2026, 11, 16), sleep=calls.append,
                        fetch_mb=lambda _: mb, fetch_g=lambda _: geo)
    assert e.value.stage == "validate_data" and "stale" in e.value.message


def test_fetch_waits_for_refresh(raw_data, geo, settings, monkeypatch):
    """First fetch is yesterday's data, second fetch is today's: the run proceeds."""
    import src.data.validation as v
    monkeypatch.setattr(v, "now_utc", lambda: FRESH_NOW)
    old = dict(raw_data, generated_at="2026-11-14T05:00:00Z")
    seq = [parse_market_brief(old), parse_market_brief(raw_data)]
    slept = []
    mb, _, rep = fetch_validated(settings, EPISODE_DATE, sleep=slept.append,
                                 fetch_mb=lambda _: seq.pop(0), fetch_g=lambda _: geo)
    assert rep.market_fresh and len(slept) == 1


def test_allow_stale_produces_flag(mb, geo, settings, monkeypatch):
    import src.data.validation as v
    monkeypatch.setattr(v, "now_utc", lambda: datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc))
    settings.set_path("sources.retry_max_minutes", 0)
    settings.set_path("sources.allow_stale", True)
    _, _, rep = fetch_validated(settings, __import__("datetime").date(2026, 11, 16), sleep=lambda _: None,
                                fetch_mb=lambda _: mb, fetch_g=lambda _: geo)
    assert rep.flagged
