"""Read Market Brief's docs/data.json and normalise it.

Every field is optional in practice (the website keeps the previous good copy of a
part when a refresh fails), so all accessors are defensive: missing values become
None / empty, never exceptions.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..util import http_get_json, parse_ts

SECTOR_NAMES = {
    "XLK": "Information Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}
SECTOR_ORDER = list(SECTOR_NAMES)
INDEX_TICKER = "SPY"


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if f == f else None  # NaN -> None
    except (TypeError, ValueError):
        return None


def _str(v: Any) -> str:
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and str(v).strip() else ""


def _date(v: Any) -> date | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


@dataclass
class PerfRow:
    ticker: str
    name: str
    last: float | None
    day: float | None
    week: float | None
    month: float | None
    ytd: float | None
    y1: float | None
    y5: float | None
    as_of: str = ""

    @classmethod
    def from_json(cls, r: dict) -> "PerfRow":
        t = _str(r.get("ticker")).upper()
        return cls(
            ticker=t,
            name=_str(r.get("name")) or SECTOR_NAMES.get(t, t),
            last=_num(r.get("last")), day=_num(r.get("day")), week=_num(r.get("week")),
            month=_num(r.get("month")), ytd=_num(r.get("ytd")), y1=_num(r.get("y1")),
            y5=_num(r.get("y5")), as_of=_str(r.get("as_of")),
        )


@dataclass
class Story:
    sector: str
    headline: str
    company: str
    ticker: str
    url: str
    source: str
    published: date | None
    link_verified: bool
    country: str = ""
    profile: str = ""
    why_it_matters: str = ""
    strategy: str = ""
    event: str = ""
    why: str = ""
    related: list[str] = field(default_factory=list)
    week_return: float | None = None
    y1_return: float | None = None
    price: float | None = None
    currency: str = ""
    market_cap: float | None = None
    industry: str = ""
    consensus: dict = field(default_factory=dict)
    ratings: list[dict] = field(default_factory=list)
    position: int = 0  # order in Market Brief's list (its own editorial ranking)

    @property
    def id(self) -> str:
        return hashlib.sha1((self.url or self.headline).encode()).hexdigest()[:12]

    @classmethod
    def from_json(cls, sector: str, it: dict, position: int) -> "Story":
        wh = it.get("what_happened") if isinstance(it.get("what_happened"), dict) else {}
        growth = it.get("growth") if isinstance(it.get("growth"), dict) else {}
        facts = it.get("facts") if isinstance(it.get("facts"), dict) else {}
        cons = facts.get("consensus") if isinstance(facts.get("consensus"), dict) else {}
        ratings = [r for r in (facts.get("ratings") or []) if isinstance(r, dict)]
        return cls(
            sector=sector,
            headline=_str(it.get("headline")),
            company=_str(it.get("company")) or _str(facts.get("name")),
            ticker=_str(it.get("ticker") or it.get("symbol")).upper(),
            url=_str(it.get("url")),
            source=_str(it.get("source")),
            published=_date(it.get("published")),
            link_verified=bool(it.get("link_verified")),
            country=_str(it.get("country")),
            profile=_str(it.get("profile")),
            why_it_matters=_str(it.get("why_it_matters")),
            strategy=_str(wh.get("strategy")),
            event=_str(wh.get("event")),
            why=_str(wh.get("why")),
            related=[_str(x).upper() for x in (it.get("related") or []) if _str(x)],
            week_return=_num(growth.get("w1")),
            y1_return=_num(growth.get("y1")),
            price=_num(facts.get("price")),
            currency=_str(facts.get("currency")),
            market_cap=_num(facts.get("market_cap")),
            industry=_str(facts.get("industry")),
            consensus={k: cons.get(k) for k in ("rating", "analysts", "target_mean", "upside")
                       if cons.get(k) is not None},
            ratings=[{k: r.get(k) for k in ("firm", "grade", "action", "date", "target")}
                     for r in ratings[:3]],
            position=position,
        )

    def brief(self) -> dict:
        """Compact dict for prompts / episode JSON."""
        d = {
            "id": self.id, "headline": self.headline, "company": self.company,
            "ticker": self.ticker, "country": self.country,
            "published": self.published.isoformat() if self.published else None,
            "source": self.source, "url": self.url, "link_verified": self.link_verified,
            "profile": self.profile, "what_happened": self.event,
            "background": self.strategy, "cause": self.why,
            "why_it_matters": self.why_it_matters,
            "stock_1w_return_pct": self.week_return, "stock_1y_return_pct": self.y1_return,
            "analyst_consensus": self.consensus or None,
            "recent_rating_changes": self.ratings or None,
        }
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class SectorStories:
    ticker: str
    name: str
    updated_at: datetime | None
    stories: list[Story]


@dataclass
class MarketBrief:
    generated_at: datetime | None
    as_of: str
    as_of_label: str
    intraday: bool
    index: PerfRow | None
    sectors: dict[str, PerfRow]
    recap: str
    recap_updated_at: datetime | None
    reasons: dict[str, str]
    stories: dict[str, SectorStories]
    notices: list[str]
    missing: list[str]
    model: str
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def market_date(self) -> date | None:
        return _date(self.as_of)

    def leaders_laggards(self, n: int = 3) -> tuple[list[PerfRow], list[PerfRow]]:
        rows = [r for r in self.sectors.values() if r.day is not None]
        rows.sort(key=lambda r: r.day, reverse=True)
        return rows[:n], list(reversed(rows[-n:]))


def parse_market_brief(d: dict) -> MarketBrief:
    if not isinstance(d, dict):
        raise ValueError("data.json is not a JSON object")
    market = d.get("market") if isinstance(d.get("market"), dict) else {}
    rows = [PerfRow.from_json(r) for r in (market.get("rows") or []) if isinstance(r, dict)]
    index = next((r for r in rows if r.ticker == INDEX_TICKER), None)
    sectors = {r.ticker: r for r in rows if r.ticker in SECTOR_NAMES}

    recap = d.get("recap") if isinstance(d.get("recap"), dict) else {}
    reasons_obj = d.get("reasons") if isinstance(d.get("reasons"), dict) else {}
    reasons = {k.upper(): _str(v) for k, v in (reasons_obj.get("items") or {}).items() if _str(v)}

    stories: dict[str, SectorStories] = {}
    st = d.get("stories") if isinstance(d.get("stories"), dict) else {}
    for tick, sec in (st.get("sectors") or {}).items():
        if not isinstance(sec, dict):
            continue
        tick = tick.upper()
        items = [Story.from_json(tick, it, i) for i, it in enumerate(sec.get("items") or [])
                 if isinstance(it, dict)]
        items = [s for s in items if s.headline and s.url]
        stories[tick] = SectorStories(tick, _str(sec.get("name")) or SECTOR_NAMES.get(tick, tick),
                                      parse_ts(sec.get("updated_at")), items)

    notices = [_str(n) if not isinstance(n, dict) else _str(n.get("text") or n.get("message") or n)
               for n in (d.get("notices") or [])]
    return MarketBrief(
        generated_at=parse_ts(d.get("generated_at")),
        as_of=_str(market.get("as_of")),
        as_of_label=_str(market.get("as_of_label")),
        intraday=bool(market.get("intraday")),
        index=index,
        sectors=sectors,
        recap=_str(recap.get("text")),
        recap_updated_at=parse_ts(recap.get("updated_at")),
        reasons=reasons,
        stories=stories,
        notices=[n for n in notices if n],
        missing=[_str(m) for m in (market.get("missing") or []) if _str(m)],
        model=_str(d.get("model")),
        raw=d,
    )


def fetch_market_brief(url: str) -> MarketBrief:
    return parse_market_brief(http_get_json(url))
