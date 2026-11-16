"""Small text helpers shared by the editorial modules."""
from __future__ import annotations

import re
from datetime import date

GICS_SPOKEN = {
    "XLK": "Information Technology", "XLC": "Communication Services",
    "XLY": "Consumer Discretionary", "XLP": "Consumer Staples", "XLF": "Financials",
    "XLV": "Health Care", "XLI": "Industrials", "XLB": "Materials", "XLE": "Energy",
    "XLU": "Utilities", "XLRE": "Real Estate",
}


def pct(x: float | None, digits: int = 2) -> str:
    """'+1.42%' style for screens."""
    if x is None:
        return "n/a"
    return f"{x:+.{digits}f}%"


def spoken_move(x: float | None, digits: int = 2) -> str:
    """'rose 1.42 percent' / 'fell 0.36 percent' / 'was flat'."""
    if x is None:
        return "had no reported change"
    if abs(x) < 0.05:
        return "was essentially flat"
    verb = "rose" if x > 0 else "fell"
    return f"{verb} {abs(x):.{digits}f} percent"


def long_date(d: date) -> str:
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"


def short_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.,%$-]*", text or ""))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“$])", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def clean_signs(text: str) -> str:
    """'dropped -2.01%' -> 'dropped 2.01%' (the verb already carries the sign)."""
    text = re.sub(r"\b(fell|dropped|slipped|declined|lost|dipped|down|lower by|decreased|slid|sank)\s+-(\d)",
                  r"\1 \2", text, flags=re.I)
    text = re.sub(r"\b(rose|gained|advanced|added|climbed|up|rallied|increased|jumped|secured a)\s+\+(\d)",
                  r"\1 \2", text, flags=re.I)
    text = re.sub(r"(?<![\w])\+(\d)", r"\1", text)               # "+1.42%" -> "1.42%"
    text = re.sub(r"\s\((?:SPY|XL[A-Z]{1,2})\)", "", text)          # "(XLP)" ETF tickers
    return text


def shorten(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"
