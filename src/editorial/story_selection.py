"""Rank each sector's Market Brief stories by how consequential they are.

This is a transparent, rule-based pre-ranking. It never invents stories: it only
orders the real, link-verified items that Market Brief already collected. The
script writer (Gemini) then makes the final editorial choice among the top
candidates, and this ranking is the fallback when it can't.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

from ..data.market_brief import SECTOR_ORDER, MarketBrief, Story

# Publisher reliability. Press-release wires are reliable *as company statements*.
SOURCE_TIERS: dict[str, float] = {
    "reuters": 3.0, "bloomberg": 3.0, "the wall street journal": 3.0, "wsj": 3.0,
    "financial times": 3.0, "associated press": 3.0, "ap": 3.0, "barrons.com": 2.6,
    "barron's": 2.6, "cnbc": 2.6, "the new york times": 2.6, "afp": 2.5, "axios": 2.2,
    "business wire": 2.2, "pr newswire": 2.2, "globenewswire": 2.2, "cnw group": 2.0,
    "marketwatch": 2.0, "techcrunch": 2.0, "investing.com": 1.8, "mt newswires": 1.8,
    "fortune": 2.0, "yahoo finance": 1.8, "the motley fool": 1.0, "benzinga": 1.4,
    "zacks": 1.2, "tipranks": 1.2, "sourcing journal": 1.4, "cre daily": 1.4,
    "simply wall st.": 1.0, "marketbeat": 0.9, "moneywise": 0.8, "24/7 wall st.": 0.9,
    "gurufocus.com": 0.9, "tikr": 0.8, "insidermonkey": 0.6,
}
DEFAULT_TIER = 1.2

# (pattern, points, label). Applied to headline + event text, case-insensitive.
EVENT_RULES: list[tuple[str, float, str]] = [
    (r"\b(ceo|chief executive)\b.*\b(exit|resign|step(s|ped)? down|depart|ousted|fired)|"
     r"\b(resign|step(s|ped)? down)\b.*\b(ceo|chief executive)", 4.0, "leadership change"),
    (r"\binterim ceo\b", 2.0, "leadership change"),
    (r"\b(acquir|acquisition|merger|merge|takeover|buyout|to buy|agrees to buy|deal collapse)", 3.0, "M&A"),
    (r"\b(raises?|cuts?|lowers?|lifts?|slashes|reaffirm\w*|withdraws?)\b.*\b(guidance|outlook|forecast)", 3.0, "guidance"),
    (r"\b(guidance|outlook|forecast)s?\b", 1.5, "outlook"),
    (r"\b(earnings|quarter|q[1-4]|results|net income|revenue|sales of)\b", 2.0, "earnings"),
    (r"\b(regulator|antitrust|ftc|doj|sec\b|probe|investigation|fine[sd]?\b|lawsuit|sues|class action|settle)", 2.2, "regulatory/legal"),
    (r"\b(fda|chmp|approval|approved|phase 3|phase iii|trial)\b", 2.5, "drug/regulatory approval"),
    (r"\b(job cuts?|cuts? [\d,]+ jobs|layoffs?|lay off)", 2.2, "restructuring"),
    (r"\b(bankrupt|chapter 11|default)", 4.0, "distress"),
    (r"\b(tariff|sanction|export control|ban)\b", 2.5, "policy/trade"),
    (r"\b(upgrade|downgrade|price target)\b", 1.0, "analyst view"),
    (r"\b(capital plan|capex|investment|invest|expansion|factory|plant|breaks ground|sanctions? .* development)", 1.8, "investment"),
    (r"\b(credit rating|s&p upgrades|moody)", 1.2, "credit"),
    (r"\b(contract|deal|wins|secures|partnership|launch)", 1.0, "business deal"),
    (r"\b(market value|trillion|plunge|surge|soar|jump|slump|tumble)", 1.5, "big market move"),
]
# Low-signal items: routine corporate housekeeping.
MINOR_RULES: list[tuple[str, float, str]] = [
    (r"\b(sells|sold|buys) \$?[\d.,]+ (million|thousand)? ?(in|of|worth)? ?(company )?shares", -3.0, "insider trade"),
    (r"\bdeclares? (quarterly )?(cash )?dividend\b", -2.5, "routine dividend"),
    (r"\b(appoints?|names?)\b.*\b(head|director|vp|vice president)\b.*\bfor\b", -2.5, "minor appointment"),
    (r"\bboard (director|member) .*resign", -1.5, "board change"),
    (r"\b(redeems?|redemption)\b.*\b(notes|debt|bonds)\b", -1.5, "routine financing"),
    (r"\b(adds?|adding)\b.*\b(provider|supplier|vendor) list\b", -2.5, "operational detail"),
]

_MONEY = re.compile(r"(?:(?:US)?\$|C\$|€|£)\s?([\d.,]+)\s*(trillion|billion|million|bn|b|m|t)?\b"
                    r"|([\d.,]+)\s*(trillion|billion|million)\s*(?:dollars|euros|usd)?", re.I)
_SCALE = {"t": 1e12, "trillion": 1e12, "b": 1e9, "bn": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6}


def largest_amount(text: str) -> float:
    best = 0.0
    for m in _MONEY.finditer(text):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        try:
            v = float(num.replace(",", ""))
        except (TypeError, ValueError):
            continue
        v *= _SCALE.get((unit or "").lower(), 1.0)
        best = max(best, v)
    return best


@dataclass
class Scored:
    story: Story
    score: float
    labels: list[str] = field(default_factory=list)
    fresh: bool = True

    def as_dict(self) -> dict:
        return {"id": self.story.id, "score": round(self.score, 2), "labels": self.labels,
                "fresh": self.fresh, "headline": self.story.headline}


def score_story(st: Story, episode_date: date, max_age_days: int,
                recent_urls: set[str] | frozenset = frozenset()) -> Scored:
    text = f"{st.headline}. {st.event}"
    score, labels = 0.0, []
    for pat, pts, label in EVENT_RULES:
        if re.search(pat, text, re.I):
            score += pts
            if label not in labels:
                labels.append(label)
    for pat, pts, label in MINOR_RULES:
        if re.search(pat, text, re.I):
            score += pts
            labels.append(label)
    tier = SOURCE_TIERS.get(st.source.lower(), DEFAULT_TIER)
    score += tier
    amount = largest_amount(text)
    if amount >= 1e7:
        score += min(4.0, math.log10(amount / 1e7))   # $10M -> 0 ... $100B+ -> 4
    if st.market_cap:
        score += min(2.5, max(0.0, math.log10(st.market_cap / 1e10)))  # $10B -> 0, $3T -> 2.5
    if st.week_return is not None and abs(st.week_return) >= 5:
        score += min(2.0, abs(st.week_return) / 5)
        labels.append("big stock move")
    score += max(0.0, 1.0 - 0.25 * st.position)        # Market Brief's own order
    if st.country == "United States" or (st.ticker and re.fullmatch(r"[A-Z.]{1,5}", st.ticker)
                                         and "." not in st.ticker):
        score += 1.0                                    # US-market show: slight preference
    if not st.link_verified:
        score -= 3.0
        labels.append("link unverified")
    fresh = True
    if st.published:
        age = (episode_date - st.published).days
        score -= 0.4 * max(0, age - 1)
        if age > max_age_days:
            fresh = False
            labels.append(f"old ({age} days)")
    if st.url in recent_urls:
        score -= 6.0
        fresh = False            # the same article never leads twice
        labels.append("covered in a recent episode")
    return Scored(st, score, labels, fresh)


def rank_sectors(mb: MarketBrief, episode_date: date, max_age_days: int = 5,
                 recent_urls: set[str] | frozenset = frozenset(),
                 order: list[str] | None = None) -> dict[str, list[Scored]]:
    """Ranked candidates per sector, with a penalty for re-using a company that an
    earlier sector already leads with (avoids the same story twice)."""
    ranked: dict[str, list[Scored]] = {}
    used_tickers: set[str] = set()
    for tick in order or SECTOR_ORDER:
        sec = mb.stories.get(tick)
        items = [score_story(s, episode_date, max_age_days, recent_urls)
                 for s in (sec.stories if sec else [])]
        for sc in items:
            if sc.story.ticker and sc.story.ticker in used_tickers:
                sc.score -= 2.5
                sc.labels.append("company already featured")
        items.sort(key=lambda x: (x.fresh, x.score), reverse=True)
        ranked[tick] = items
        if items and items[0].fresh:
            used_tickers.add(items[0].story.ticker)
    return ranked


def default_pick(candidates: list[Scored]) -> Scored | None:
    """Top fresh candidate, or None ('no standout verified development')."""
    return next((c for c in candidates if c.fresh), None)
