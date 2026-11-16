"""Make script text easy to pronounce for any TTS engine.

Subtitles keep the original text; only the text sent to the voice is normalised.
"""
from __future__ import annotations

import re

from ..editorial.textutil import clean_signs

ORDINAL_Q = {"1": "first", "2": "second", "3": "third", "4": "fourth"}
CURRENCY = [  # (pattern prefix, spoken unit)
    (r"C\$", "Canadian dollars"), (r"A\$", "Australian dollars"), (r"US\$", "dollars"),
    (r"\$", "dollars"), (r"€", "euros"), (r"£", "pounds"), (r"¥", "yen"),
]
ABBREV = {
    r"\bS&P\b": "S and P", r"\bM&A\b": "M and A", r"\bR&D\b": "R and D", r"\bAT&T\b": "A T and T",
    r"\bP/E\b": "price to earnings", r"\bYTD\b": "year to date", r"\bYoY\b": "year over year",
    r"\bvs\.?\s": "versus ", r"\bbps\b": "basis points", r"\bEPS\b": "earnings per share",
    r"\bFY\s?(\d{2,4})\b": r"fiscal \1", r"\bH([12])\b": r"half \1", r"\bWall St\.": "Wall Street",
    r"\bInc\.": "Inc", r"\bCorp\.": "Corp", r"\bCo\.": "Co", r"\bLtd\.": "Limited",
    r"\bNYSE\b": "N Y S E", r"\bETF\b": "E T F", r"\bETFs\b": "E T Fs", r"\bREITs?\b": "REIT",
    r"\bLNG\b": "L N G", r"\bAI\b": "A I", r"\bGDP\b": "G D P", r"\bHBM\b": "H B M",
    r"\bNOI\b": "N O I", r"\bGLP-1s?\b": "G L P 1", r"\bCFO\b": "C F O",
}
SCALE = {"T": "trillion", "B": "billion", "BN": "billion", "M": "million", "K": "thousand"}


def normalize_for_tts(text: str, lexicon: dict[str, str] | None = None) -> str:
    t = clean_signs(" ".join((text or "").split()))
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s\((?:SPY|XL[A-Z]{1,2})\)", "", t)          # (XLK) etc.
    for pat, rep in ABBREV.items():
        t = re.sub(pat, rep, t)
    t = re.sub(r"\bQ([1-4])\b", lambda m: f"{ORDINAL_Q[m.group(1)]} quarter", t)
    # money: "$12.5B" / "$1.6 trillion" / "C$102M" -> "12.5 billion dollars"
    for prefix, unit in CURRENCY:
        t = re.sub(prefix + r"\s?(\d[\d,]*(?:\.\d+)?)\s?(trillion|billion|million|thousand|BN|bn|[TBMK])?\b",
                   lambda m, u=unit: _money(m.group(1), m.group(2), u), t)
    t = re.sub(r"(?<![\w])[+](\d)", r"\1", t)                  # +1.4% -> 1.4%
    t = re.sub(r"(?<![\w-])-(\d[\d.]*)\s?%", r"minus \1 percent", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = t.replace("&", " and ").replace("…", "...")
    for word, spoken in (lexicon or {}).items():
        t = re.sub(rf"\b{re.escape(word)}\b", spoken, t)
    return " ".join(t.split())


def _money(num: str, scale: str | None, unit: str) -> str:
    s = (scale or "").strip()
    word = SCALE.get(s.upper(), s.lower()) if s else ""
    return f"{num} {word} {unit}".replace("  ", " ")
