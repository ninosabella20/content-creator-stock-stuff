"""Same-day US closing numbers for the after-close episode.

Market Brief refreshes in the European morning, so at 16:30 New York time its
numbers describe the PREVIOUS session. This module fetches today's official
closes (Yahoo Finance chart API, free, no key) for SPY and the 11 sector ETFs,
computes the same periods Market Brief shows, and swaps them in. Market Brief's
morning recap/explanations are kept only as clearly-labelled previous-session
context, so yesterday's reasons are never presented as today's.
"""
from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..util import HTTP
from .market_brief import INDEX_TICKER, SECTOR_NAMES, MarketBrief, PerfRow

log = logging.getLogger(__name__)
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (market-brief-daily)"}


class CloseDataError(RuntimeError):
    pass


def _months_back(d: date, months: int) -> date:
    """Same day N calendar months earlier (clamped to month end), like pandas DateOffset."""
    import calendar
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


@dataclass
class Series:
    symbol: str
    dates: list[date]
    closes: list[float]
    market_time: datetime | None

    def _on_or_before(self, d: date) -> float | None:
        i = bisect_right(self.dates, d) - 1
        return self.closes[i] if i >= 0 else None

    def change(self, ref: float | None) -> float | None:
        if not ref or not self.closes:
            return None
        return round((self.closes[-1] / ref - 1) * 100, 2)

    def row(self, ticker: str, name: str) -> PerfRow:
        last_d = self.dates[-1]
        prev = self.closes[-2] if len(self.closes) > 1 else None
        ytd_ref = self._on_or_before(date(last_d.year - 1, 12, 31))
        return PerfRow(
            ticker=ticker, name=name, last=round(self.closes[-1], 2),
            day=self.change(prev),
            week=self.change(self._on_or_before(last_d - timedelta(days=7))),
            month=self.change(self._on_or_before(_months_back(last_d, 1))),
            ytd=self.change(ytd_ref),
            y1=self.change(self._on_or_before(_months_back(last_d, 12))),
            y5=self.change(self._on_or_before(_months_back(last_d, 60))),
            as_of=last_d.isoformat(),
        )


def fetch_series(symbol: str, rng: str = "10y") -> Series:
    last_err = None
    for host in ("query1", "query2"):
        try:
            r = HTTP.get(CHART.format(sym=symbol).replace("query1", host),
                         params={"range": rng, "interval": "1d", "includePrePost": "false"},
                         headers=UA, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            pts = [(datetime.fromtimestamp(t, timezone.utc).astimezone(NY).date(), c)
                   for t, c in zip(ts, closes) if c is not None]
            if len(pts) < 3:
                raise CloseDataError(f"{symbol}: too little price history")
            mt = res.get("meta", {}).get("regularMarketTime")
            return Series(symbol, [p[0] for p in pts], [float(p[1]) for p in pts],
                          datetime.fromtimestamp(mt, timezone.utc) if mt else None)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise CloseDataError(f"price data for {symbol} unavailable: {last_err}")


def session_label(d: date) -> str:
    return f"Close {d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"


def latest_session(episode_date: date, fetch=fetch_series) -> tuple[dict[str, PerfRow], date]:
    """Rows for SPY + the 11 sector ETFs at the latest completed session."""
    rows: dict[str, PerfRow] = {}
    session = None
    for t in [INDEX_TICKER, *SECTOR_NAMES]:
        s = fetch(t)
        rows[t] = s.row(t, "S&P 500" if t == INDEX_TICKER else SECTOR_NAMES[t])
        session = s.dates[-1] if session is None else max(session, s.dates[-1])
        # today's bar must be the official close, not an intraday snapshot
        if s.dates[-1] == episode_date and s.market_time:
            close_time = datetime.combine(episode_date, datetime.min.time(), NY).replace(hour=16)
            if s.market_time.astimezone(NY) < close_time:
                raise CloseDataError(f"{t}: today's session has not closed yet")
    stale = [t for t, r in rows.items() if r.as_of != session.isoformat()]
    if stale:
        raise CloseDataError(f"inconsistent session dates for {stale}")
    return rows, session


def stock_day_moves(tickers: list[str], fetch=fetch_series) -> dict[str, float]:
    """Today's % move for story companies (US listings only; best effort)."""
    out = {}
    for t in dict.fromkeys(tickers):
        if not t or "." in t or "-" in t or len(t) > 5:
            continue
        try:
            s = fetch(t, "5d")
            out[t] = s.change(s.closes[-2]) if len(s.closes) > 1 else None
        except Exception:  # noqa: BLE001
            continue
    return {k: v for k, v in out.items() if v is not None}


def apply_close(mb: MarketBrief, rows: dict[str, PerfRow], session: date) -> None:
    """Swap in the latest close. Morning text about another session becomes context only."""
    if mb.as_of != session.isoformat():
        mb.prior_session = {"label": mb.as_of_label or mb.as_of, "recap": mb.recap,
                            "explanations": dict(mb.reasons)}
        mb.recap = ""
        mb.reasons = {}
    mb.index = rows[INDEX_TICKER]
    mb.sectors = {t: rows[t] for t in SECTOR_NAMES if t in rows}
    mb.as_of = session.isoformat()
    mb.as_of_label = session_label(session)
    mb.intraday = False
