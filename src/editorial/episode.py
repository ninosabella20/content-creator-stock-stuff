"""Episode model: build the editorial context and assemble the final episode JSON.

Writers (Gemini or the template fallback) return only *words*: narration, short
on-screen text and which candidate story they chose. Everything factual that is
displayed or linked (performance numbers, headlines, URLs, publishers, dates) is
attached here from Market Brief's data, so it can't be invented.
"""
from __future__ import annotations

from datetime import date

from ..config import Settings
from ..data.geopolitics import Geopolitics
from ..data.market_brief import SECTOR_NAMES, MarketBrief, PerfRow
from .story_selection import Scored
from .textutil import shorten, word_count

GEO_PRIORITY = ["cn", "eu", "mx", "ca", "jp", "tw", "gb", "in", "sa", "ru"]


def perf_dict(r: PerfRow | None) -> dict | None:
    if r is None:
        return None
    return {k: getattr(r, k) for k in ("ticker", "name", "last", "day", "week", "month", "ytd", "y1", "y5")}


def episode_id(settings: Settings, d: date) -> str:
    return f"{settings.get_path('show.episode_id_prefix', 'market-brief-daily')}-{d.isoformat()}"


def build_context(mb: MarketBrief, geo: Geopolitics | None, ranked: dict[str, list[Scored]],
                  episode_date: date, settings: Settings) -> dict:
    """Everything the writer may use. Also the reference set for number checks."""
    n = int(settings.get_path("editorial.candidates_per_sector", 5))
    leaders, laggards = mb.leaders_laggards(3)
    ctx: dict = {
        "episode_date": episode_date.isoformat(),
        "episode_weekday": episode_date.strftime("%A"),
        "market_session": mb.as_of_label or mb.as_of,
        "market_session_date": mb.as_of,
        "market_session_weekday": mb.market_date.strftime("%A") if mb.market_date else None,
        "intraday": mb.intraday,
        "sp500": perf_dict(mb.index),
        "market_recap": mb.recap,
        "index_explanation": mb.reasons.get("SPY", ""),
        "leaders_today": [r.name for r in leaders],
        "laggards_today": [r.name for r in laggards],
        "sectors": [],
        "geopolitics": [],
    }
    for t in settings.get_path("editorial.sector_order", list(SECTOR_NAMES)):
        cands = ranked.get(t, [])[:n]
        ctx["sectors"].append({
            "sector": t,
            "sector_name": SECTOR_NAMES.get(t, t),
            "performance_pct": {k: v for k, v in (perf_dict(mb.sectors.get(t)) or {}).items()
                                if k in ("day", "week", "month", "ytd", "y1")},
            "why_it_moved": mb.reasons.get(t, ""),
            "candidates": [
                {**c.story.brief(), "pre_rank_score": round(c.score, 1),
                 "pre_rank_notes": c.labels, "fresh": c.fresh}
                for c in cands
            ],
        })
    if geo:
        players = sorted(geo.players, key=lambda p: GEO_PRIORITY.index(p.code)
                         if p.code in GEO_PRIORITY else 99)
        ctx["geopolitics"] = [p.brief() for p in players if p.week_line or p.impacts]
    return ctx


def assemble(writer: dict, mb: MarketBrief, geo: Geopolitics | None,
             ranked: dict[str, list[Scored]], episode_date: date, settings: Settings,
             generator: str, flags: list[str] | None = None) -> dict:
    segs_in = writer.get("segments") or {}
    order = settings.get_path("editorial.sector_order", list(SECTOR_NAMES))
    segments: list[dict] = []

    intro = segs_in.get("intro") or {}
    segments.append({"id": "intro", "type": "intro", "title": "Market Brief Daily",
                     "narration": intro.get("narration", "").strip()})

    market = segs_in.get("market") or {}
    leaders, laggards = mb.leaders_laggards(3)
    segments.append({
        "id": "market", "type": "market_overview", "title": "Market Overview",
        "narration": market.get("narration", "").strip(),
        "on_screen": {"headline": shorten(market.get("screen_headline") or "", 70),
                      "key_points": [shorten(k, 60) for k in (market.get("key_points") or [])][:3]},
        "data": {"sp500": perf_dict(mb.index),
                 "sectors": {t: perf_dict(mb.sectors.get(t)) for t in order},
                 "leaders": [r.ticker for r in leaders], "laggards": [r.ticker for r in laggards],
                 "session_label": mb.as_of_label or mb.as_of},
    })

    by_sector = {s.get("sector"): s for s in (segs_in.get("sectors") or []) if isinstance(s, dict)}
    for t in order:
        w = by_sector.get(t, {})
        cand_by_id = {c.story.id: c for c in ranked.get(t, [])}
        chosen = cand_by_id.get(w.get("story_id") or "")
        story = None
        if chosen:
            st = chosen.story
            story = {"id": st.id, "headline": st.headline, "company": st.company,
                     "ticker": st.ticker, "url": st.url, "source": st.source,
                     "published": st.published.isoformat() if st.published else None,
                     "link_verified": st.link_verified, "week_return": st.week_return,
                     "y1_return": st.y1_return, "consensus": st.consensus or None,
                     "selection_score": round(chosen.score, 2), "selection_notes": chosen.labels}
        segments.append({
            "id": t, "type": "sector", "sector": t, "title": SECTOR_NAMES.get(t, t),
            "narration": (w.get("narration") or "").strip(),
            "no_standout": story is None,
            "story": story,
            "on_screen": {"headline": shorten(w.get("screen_headline") or (story or {}).get("headline", ""), 90),
                          "key_points": [shorten(k, 70) for k in (w.get("key_points") or [])][:3]},
            "data": {"perf": perf_dict(mb.sectors.get(t)),
                     "rank_today": _rank(mb, t)},
        })

    g = segs_in.get("geopolitics") or {}
    codes = [c.lower() for c in (g.get("countries") or []) if isinstance(c, str)]
    sources = []
    countries = []
    if geo:
        for c in codes:
            p = geo.by_code(c)
            if not p:
                continue
            countries.append({"code": p.code, "name": p.name, "relation": p.relation,
                              "lever": p.lever, "week_line": p.week_line,
                              "trade_b": p.numbers.get("trade_b"),
                              "treasuries_b": p.numbers.get("treasuries_b"),
                              "impacts": [{"title": i.title, "direction": i.direction,
                                           "sectors": i.sectors} for i in p.impacts]})
            sources += p.week_links
    segments.append({
        "id": "geopolitics", "type": "geopolitics", "title": "Geopolitical Intelligence",
        "narration": (g.get("narration") or "").strip(),
        "on_screen": {"headline": shorten(g.get("screen_headline") or "", 80),
                      "key_points": [shorten(k, 70) for k in (g.get("key_points") or [])][:3]},
        "countries": countries,
        "sources": sources,
    })

    c = segs_in.get("closing") or {}
    segments.append({
        "id": "closing", "type": "closing", "title": "The Takeaway",
        "narration": (c.get("narration") or "").strip(),
        "on_screen": {"takeaway": shorten(c.get("takeaway") or "", 120),
                      "watch": [shorten(x, 70) for x in (c.get("watch") or [])][:3]},
    })

    words = sum(word_count(s["narration"]) for s in segments)
    wpm = float(settings.get_path("editorial.words_per_minute", 150))
    return {
        "episode_id": episode_id(settings, episode_date),
        "date": episode_date.isoformat(),
        "market_session": mb.as_of_label or mb.as_of,
        "title_hook": shorten(writer.get("title_hook") or "", 60),
        "summary": (writer.get("summary") or "").strip(),
        "generator": generator,
        "flags": list(flags or []),
        "data_generated_at": mb.generated_at.isoformat() if mb.generated_at else None,
        "geo_generated_at": geo.generated_at.isoformat() if geo and geo.generated_at else None,
        "word_count": words,
        "estimated_minutes": round(words / wpm, 1),
        "segments": segments,
    }


def _rank(mb: MarketBrief, t: str) -> int | None:
    rows = sorted([r for r in mb.sectors.values() if r.day is not None], key=lambda r: r.day,
                  reverse=True)
    for i, r in enumerate(rows, 1):
        if r.ticker == t:
            return i
    return None
