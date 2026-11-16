"""AI editorial engine: turn Market Brief data into one coherent ~10-minute script.

One Gemini call writes the whole episode (and at most one repair call fixes
problems found by the validator), which keeps daily usage far below free limits.
"""
from __future__ import annotations

import json
import logging

from ..gemini_client import Gemini, GeminiError, parse_json_text

log = logging.getLogger(__name__)

SYSTEM = """You are the editor and scriptwriter of "Daily Market Brief", a daily ~10-minute
financial news briefing (video + podcast) by Active Thinking. You write for the ear: a calm,
confident, analytical presenter in the tradition of the best public-radio and financial
newspaper briefings. Objective, clear, never sensational, never promotional."""

RULES = """
EDITORIAL RULES (follow strictly)

Accuracy
- Use ONLY facts, figures, names and dates that appear in INPUT. Never invent announcements,
  numbers, stock moves, analyst actions, quotes, dates, events or sources.
- Every number you write must come from INPUT (you may round it, e.g. 2.01 -> "about 2 percent").
  Do not compute new figures (no sums, differences or ratios of your own).
- Attribute: separate confirmed facts ("X reported..."), company statements ("the company says..."),
  analyst opinions ("analysts at Y rate it..."), market interpretation ("investors appear to...")
  and your own explanation ("the logic is simple: ..."). Never present speculation as fact.
- If INPUT gives a reason for a move, you may use it. If it doesn't, don't guess one.
- No investment advice, no price predictions, no "buy/sell/hold" recommendations of your own.
  Analyst ratings from INPUT may be reported as analysts' views.

Story choice (one per sector)
- For each sector choose exactly ONE story from that sector's "candidates" by its "id".
  Choose the most consequential development: economic importance, impact on the company and
  the wider sector, market relevance, genuinely new, reliable source. "pre_rank_score" is a
  helpful hint, not an order. Prefer "fresh": true. Avoid repeating a company already used
  in another sector unless the cross-sector angle is genuinely important.
- If no candidate is a meaningful verified development, set "story_id": null and say plainly
  that there was no standout verified development, then give useful sector context from
  "why_it_moved". Never invent a story.

Structure and length (total {wmin}-{wmax} spoken words, about 10 minutes)
- intro (25-40 words): greeting, show name, the date ({spoken_date}). One-line preview.
- market (110-150 words): S&P 500 move for the session ({session}) and the week, the main
  market-wide driver, leading and lagging sectors, and rates/Treasury yields ONLY if INPUT
  mentions them. Don't list all 11 sectors here.
- sectors: 11 entries, in INPUT order, each {sec_min}-{sec_max} words (35-45 seconds):
  1) how the sector performed (one short clause, varied phrasing),
  2) the development, 3) what happened, 4) why it matters, 5) implications / what to watch.
  Vary the openings: don't start every sector the same way.
- geopolitics (110-150 words): the most consequential developments for the US economy from
  "geopolitics" ("this_week"), prioritising US-China, trade and tariffs, energy security,
  critical minerals, supply chains, Treasury demand, strategic relationships. Use the profiles
  ("main_lever", "possible_us_impacts") to explain WHY it matters. Don't read out rankings.
  List the country codes you discuss in "countries" (2-4 codes).
- closing (70-110 words): the single most important takeaway, what to watch next (only things
  supported by INPUT; no invented calendar events), then end EXACTLY with: "{sign_off}"
  followed by one short sentence that this is news and commentary, not investment advice.

Spoken style
- Natural spoken English, short-to-medium sentences, smooth transitions between sectors.
- Explain finance simply. E.g. instead of "rising yields pressured tech valuations", explain
  that higher yields raise the discount rate, which lowers today's value of profits expected
  far in the future, and growth stocks rely most on those distant profits.
- Say company names, not tickers. Don't read URLs, ETF tickers or source lists aloud
  (a brief "according to Reuters" is fine).
- Write percentages as "2.01 percent", money as "$12.5 billion", and no "+" or "-" signs.
- Avoid clichés and AI-isms: no "buckle up", "delve", "in today's fast-paced world",
  "game-changer", "it's worth noting", "navigate", "landscape", "unprecedented" (unless quoted),
  no rhetorical hype, no exclamation marks.

On-screen text (for the video; short, no full sentences needed)
- "screen_headline": <= 70 characters, factual.
- "key_points": 2-3 items, each <= 8 words: what happened / why it matters / what to watch.
"""

OUTPUT = """
Return ONLY a JSON object with exactly this shape:
{
  "title_hook": "<= 55 characters: the day's most important story, factual, for the episode title",
  "summary": "2-3 sentence episode summary for show notes",
  "segments": {
    "intro": {"narration": "..."},
    "market": {"narration": "...", "screen_headline": "...", "key_points": ["...", "..."]},
    "sectors": [
      {"sector": "XLK", "story_id": "<candidate id or null>", "narration": "...",
       "screen_headline": "...", "key_points": ["...", "...", "..."]}
      /* ... all 11 sectors in INPUT order ... */
    ],
    "geopolitics": {"narration": "...", "countries": ["cn", "eu"], "screen_headline": "...",
                    "key_points": ["...", "..."]},
    "closing": {"narration": "...", "takeaway": "<= 110 characters", "watch": ["...", "..."]}
  }
}
"""


def build_prompt(context: dict, settings) -> str:
    from datetime import date

    from .textutil import long_date
    s = settings.get_path
    wmin, wmax = int(s("editorial.target_words_min", 1250)), int(s("editorial.target_words_max", 1450))
    rules = RULES.format(
        wmin=wmin, wmax=wmax, sec_min=80, sec_max=100,
        spoken_date=long_date(date.fromisoformat(context["episode_date"])),
        session=context.get("market_session") or "latest session",
        sign_off=s("show.sign_off"),
    )
    return (rules + OUTPUT + "\nINPUT:\n" + json.dumps(context, ensure_ascii=False, indent=1))


def build_repair_prompt(context: dict, draft: dict, problems: list[str], settings) -> str:
    return (build_prompt(context, settings) +
            "\n\nYOUR PREVIOUS DRAFT:\n" + json.dumps(draft, ensure_ascii=False) +
            "\n\nThe draft has these problems. Return the full corrected JSON (same shape), "
            "changing only what is needed:\n- " + "\n- ".join(problems))


def write_with_gemini(gem: Gemini, context: dict, settings) -> dict:
    temp = float(settings.get_path("gemini.temperature", 0.4))
    text = gem.generate_text(build_prompt(context, settings), temperature=temp, json_mode=True,
                             system=SYSTEM)
    try:
        return parse_json_text(text)
    except ValueError as e:
        raise GeminiError(f"script was not valid JSON: {e}") from e


def repair_with_gemini(gem: Gemini, context: dict, draft: dict, problems: list[str],
                       settings) -> dict:
    text = gem.generate_text(build_repair_prompt(context, draft, problems, settings),
                             temperature=0.2, json_mode=True, system=SYSTEM)
    return parse_json_text(text)
