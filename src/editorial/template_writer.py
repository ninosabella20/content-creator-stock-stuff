"""Deterministic, no-AI script writer.

Used when Gemini is unavailable (quota exhausted / outage) and for offline tests.
It only rearranges sentences Market Brief already published, plus fixed connective
phrases, so it cannot introduce new facts. It reads less naturally than the Gemini
script, so episodes it produces are flagged and never auto-published.
"""
from __future__ import annotations

import re
from datetime import date

from ..config import Settings
from ..data.geopolitics import Geopolitics
from ..data.market_brief import SECTOR_NAMES, MarketBrief
from .episode import GEO_PRIORITY
from .story_selection import Scored, default_pick
from .textutil import clean_signs, long_date, sentences, shorten, spoken_move

TRANSITIONS = {
    "XLK": "We start with Information Technology.",
    "XLC": "Next, Communication Services.",
    "XLY": "Turning to Consumer Discretionary.",
    "XLP": "In Consumer Staples,",
    "XLF": "Over to Financials.",
    "XLV": "In Health Care,",
    "XLI": "Industrials next.",
    "XLB": "Turning to Materials.",
    "XLE": "In Energy,",
    "XLU": "Utilities now.",
    "XLRE": "And finally, Real Estate.",
}


QUIET = re.compile(r"quiet week|no (major|significant|notable)", re.I)


def best_impact(p):
    """The profile impact that overlaps most with this week's development."""
    words = set(re.findall(r"[a-z]{4,}", p.week_line.lower()))
    return max(p.impacts, key=lambda i: len(words & set(re.findall(r"[a-z]{4,}",
                                                                   (i.title + " " + i.detail).lower()))))


def _first(text: str, n: int = 1) -> str:
    return " ".join(sentences(text)[:n])


def write_template(mb: MarketBrief, geo: Geopolitics | None, ranked: dict[str, list[Scored]],
                   episode_date: date, settings: Settings) -> dict:
    show = settings.get_path("show.name", "Daily Market Brief")
    session = mb.as_of_label or mb.as_of
    idx = mb.index
    intro = (f"Good morning, and welcome to the {show} for {long_date(episode_date)}. "
             f"Here is what moved markets, sector by sector, and why it matters.")
    leaders, laggards = mb.leaders_laggards(2)
    market = clean_signs(mb.recap) if mb.recap else (
        f"The S&P 500 {spoken_move(idx.day if idx else None)} in the latest session.")
    if leaders and laggards:
        market += (f" Leading the day: {leaders[0].name}. Lagging: {laggards[0].name}.")
    market_points = []
    if idx and idx.day is not None:
        market_points.append(f"S&P 500 {idx.day:+.2f}% on the day")
    if idx and idx.week is not None:
        market_points.append(f"{idx.week:+.2f}% for the week")
    if leaders:
        market_points.append(f"Leader: {leaders[0].name}")

    sectors = []
    order = settings.get_path("editorial.sector_order", list(SECTOR_NAMES))
    for t in order:
        name = SECTOR_NAMES.get(t, t)
        row = mb.sectors.get(t)
        perf_line = clean_signs(_first(mb.reasons.get(t, ""), 1)) or \
            f"{name} {spoken_move(row.day if row else None)}."
        pick = default_pick(ranked.get(t, []))
        parts = [TRANSITIONS.get(t, f"{name}."), perf_line]
        points: list[str] = []
        if pick:
            st = pick.story
            parts.append(f"The main story: {st.headline}.")
            if st.event:
                parts.append(st.event)
            if st.why:
                parts.append(st.why)
            if st.why_it_matters:
                parts.append(f"Why it matters: {st.why_it_matters}")
            if st.source:
                parts.append(f"That's according to {st.source.rstrip('.')}.")
            points = [shorten(st.event or st.headline, 70),
                      shorten(st.why_it_matters, 70)]
            sectors.append({"sector": t, "story_id": st.id, "narration": " ".join(parts),
                            "screen_headline": st.headline, "key_points": [p for p in points if p]})
        else:
            parts.append("There was no standout, verified company development in this sector "
                         "in the latest news cycle.")
            sectors.append({"sector": t, "story_id": None, "narration": " ".join(parts),
                            "screen_headline": f"No standout {name} story", "key_points": []})

    geo_seg = {"narration": "", "countries": [], "key_points": []}
    if geo:
        players = sorted([p for p in geo.players if p.week_line and not QUIET.search(p.week_line)],
                         key=lambda p: GEO_PRIORITY.index(p.code) if p.code in GEO_PRIORITY else 99)[:3]
        lines = ["On the geopolitical front."]
        for p in players:
            lines.append(f"{p.name}: {p.week_line}")
            if p.impacts:
                lines.append(f"Why it matters for the US: {best_impact(p).detail}")
        geo_seg = {"narration": " ".join(lines), "countries": [p.code for p in players],
                   "screen_headline": "Geopolitics this week",
                   "key_points": [shorten(f"{p.name}: {p.week_line}", 70) for p in players]}
    else:
        geo_seg["narration"] = ("Geopolitical intelligence was not available for today's "
                                "edition, so we'll pick up that thread tomorrow.")

    best = max([r for r in mb.sectors.values() if r.day is not None], key=lambda r: abs(r.day),
               default=None)
    take = (f"The biggest sector move came from {best.name}, which {spoken_move(best.day)}."
            if best else "")
    closing = (f"So, the takeaway. {take} Keep an eye on how the stories we covered develop, "
               f"and check the original sources linked in the show notes. "
               f"{settings.get_path('show.sign_off', '')} This is news and commentary, "
               f"not investment advice.")
    first_story = default_pick(ranked.get(order[0], []))
    return {
        "title_hook": shorten(first_story.story.headline if first_story else session, 60),
        "summary": clean_signs(_first(mb.recap, 2)),
        "segments": {
            "intro": {"narration": intro},
            "market": {"narration": market, "screen_headline": "Market Overview",
                       "key_points": market_points},
            "sectors": sectors,
            "geopolitics": geo_seg,
            "closing": {"narration": closing, "takeaway": take,
                        "watch": [s["screen_headline"] for s in sectors[:2]]},
        },
    }
