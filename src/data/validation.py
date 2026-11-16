"""Freshness and completeness checks for the two Market Brief feeds."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import Settings
from ..util import StageError, now_utc
from .geopolitics import Geopolitics, fetch_geo
from .market_brief import SECTOR_ORDER, MarketBrief, fetch_market_brief

log = logging.getLogger(__name__)


@dataclass
class DataReport:
    episode_date: date
    market_fresh: bool = False
    geo_fresh: bool = False
    fatal: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale_sectors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fatal

    @property
    def flagged(self) -> bool:
        """True when the episode must carry a data-quality flag (never auto-publish)."""
        return not self.market_fresh

    def as_dict(self) -> dict:
        return {"episode_date": self.episode_date.isoformat(), "market_fresh": self.market_fresh,
                "geo_fresh": self.geo_fresh, "fatal": self.fatal, "warnings": self.warnings,
                "stale_sectors": self.stale_sectors}


def is_fresh(ts: datetime | None, episode_date: date, tz: str, min_local_hour: int,
             max_age_hours: float, now: datetime | None = None) -> bool:
    """Fresh = built on the episode's local date (after min_local_hour) and not too old."""
    if ts is None:
        return False
    now = now or now_utc()
    local = ts.astimezone(ZoneInfo(tz))
    if local.date() != episode_date or local.hour < min_local_hour:
        return False
    return (now - ts) <= timedelta(hours=max_age_hours)


def validate(mb: MarketBrief, geo: Geopolitics | None, episode_date: date, settings: Settings,
             now: datetime | None = None) -> DataReport:
    s = settings.get_path
    tz = s("show.timezone", "Europe/Madrid")
    hour = int(s("sources.min_local_hour", 5))
    rep = DataReport(episode_date)

    # ---- market data -------------------------------------------------------------
    rep.market_fresh = is_fresh(mb.generated_at, episode_date, tz, hour,
                                float(s("sources.max_data_age_hours", 20)), now)
    if not rep.market_fresh:
        when = mb.generated_at.isoformat() if mb.generated_at else "unknown"
        rep.warnings.append(f"data.json is not from today's refresh (generated_at={when})")
    if mb.index is None or mb.index.day is None:
        rep.fatal.append("S&P 500 (SPY) performance row missing from data.json")
    missing_rows = [t for t in SECTOR_ORDER if t not in mb.sectors or mb.sectors[t].day is None]
    if len(missing_rows) > 2:
        rep.fatal.append(f"sector performance missing for {', '.join(missing_rows)}")
    elif missing_rows:
        rep.warnings.append(f"sector performance missing for {', '.join(missing_rows)}")
    if not mb.recap:
        rep.warnings.append("market recap text missing")
    if mb.notices:
        rep.warnings.extend(f"Market Brief notice: {n}" for n in mb.notices)

    # ---- stories -----------------------------------------------------------------
    no_stories = [t for t in SECTOR_ORDER if t not in mb.stories or not mb.stories[t].stories]
    if len(no_stories) > 3:
        rep.fatal.append(f"no stories for {len(no_stories)} sectors: {', '.join(no_stories)}")
    elif no_stories:
        rep.warnings.append(f"no stories for {', '.join(no_stories)}")
    for t in SECTOR_ORDER:
        sec = mb.stories.get(t)
        if sec and sec.stories and not is_fresh(sec.updated_at, episode_date, tz, hour,
                                                float(s("sources.max_data_age_hours", 20)) + 24, now):
            rep.stale_sectors.append(t)
    if rep.stale_sectors:
        rep.warnings.append("stories not refreshed today for " + ", ".join(rep.stale_sectors))

    # ---- geopolitics (secondary: warnings only) --------------------------------------
    if geo is None:
        rep.warnings.append("geo.json unavailable: geopolitics segment will be brief")
    else:
        rep.geo_fresh = is_fresh(geo.week_updated_at or geo.generated_at, episode_date, tz, hour,
                                 float(s("sources.max_geo_age_hours", 30)), now)
        if not rep.geo_fresh:
            rep.warnings.append("geo.json weekly lines were not refreshed today")
        if not any(p.week_line for p in geo.players):
            rep.warnings.append("geo.json has no 'this week' developments")
    return rep


def fetch_validated(settings: Settings, episode_date: date, sleep=time.sleep,
                    fetch_mb=fetch_market_brief, fetch_g=fetch_geo
                    ) -> tuple[MarketBrief, Geopolitics | None, DataReport]:
    """Fetch both feeds; while market data is stale, retry at intervals.

    Raises StageError when data is unusable, or stale and allow_stale is false.
    """
    s = settings.get_path
    interval = float(s("sources.retry_interval_minutes", 5)) * 60
    deadline = time.time() + float(s("sources.retry_max_minutes", 60)) * 60
    attempt = 0
    while True:
        attempt += 1
        try:
            mb = fetch_mb(s("sources.data_url"))
        except Exception as e:  # noqa: BLE001
            raise StageError("fetch_data", f"data.json could not be fetched/parsed: {e}") from e
        try:
            geo = fetch_g(s("sources.geo_url"))
        except Exception as e:  # noqa: BLE001
            log.warning("geo.json could not be fetched: %s", e)
            geo = None
        rep = validate(mb, geo, episode_date, settings)
        log.info("data check #%d: market_fresh=%s geo_fresh=%s generated_at=%s",
                 attempt, rep.market_fresh, rep.geo_fresh, mb.generated_at)
        if rep.market_fresh or time.time() + interval > deadline:
            break
        log.info("market data not refreshed yet; retrying in %.0f min", interval / 60)
        sleep(interval)

    if not rep.ok:
        raise StageError("validate_data", "; ".join(rep.fatal))
    if not rep.market_fresh and not s("sources.allow_stale", False):
        raise StageError("validate_data",
                         "data.json was not refreshed today (website build late or failed). "
                         "Not producing an episode from stale data. Re-run later, or set "
                         "ALLOW_STALE=true to produce a clearly flagged, unpublished episode.")
    for w in rep.warnings:
        log.warning("data: %s", w)
    return mb, geo, rep
