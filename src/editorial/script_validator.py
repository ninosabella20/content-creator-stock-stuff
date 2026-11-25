"""Checks a script before any audio/video work starts.

Errors block production (or trigger one repair call); warnings are reported.
The number check compares every figure in the narration against the figures in
the source data given to the writer, to catch invented numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textutil import word_count

NUM_RE = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)")
ADVICE_RE = re.compile(
    r"\b(you should (buy|sell|invest|consider buying)|we recommend|our pick|"
    r"(strong )?buy (now|today)|guaranteed returns?|can't lose|sure thing)\b", re.I)
CLICHE_RE = re.compile(
    r"\b(buckle up|delve|fast-paced world|game[- ]changer|it'?s worth noting|"
    r"navigat(e|ing) the (landscape|waters)|in conclusion|let'?s dive in)\b", re.I)
# Prompt/instruction wording that must never be spoken.
INSTRUCTION_RE = re.compile(
    r"(read (the following|this|aloud)|in a way that (sounds|is)|as a (calm|professional|financial news) "
    r"presenter|\btranscript\b|\bword count\b|\bstory_id\b|\bpre_rank|\bJSON\b|"
    r"\bkey_points\b|\bscreen_headline\b|\bthis script\b|\bspoken words\b|"
    r"(please )?(describe|explain|summari[sz]e) (the )?(activity|this|the following)|"
    r"\bas an ai (language model|assistant)\b|\bi am an ai\b|\bhere is (the|your) (script|json|narration)\b)", re.I)
SEGMENT_WORDS = {"intro": (15, 60), "market_overview": (80, 190), "sector": (55, 130),
                 "geopolitics": (70, 190), "closing": (45, 140)}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"errors": self.errors, "warnings": self.warnings,
                "unverified_numbers": self.unverified_numbers}


def _collect_numbers(obj, out: set[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in NUM_RE.finditer(obj):
            try:
                out.add(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def allowed_numbers(context: dict) -> set[str]:
    """String forms (0-2 decimals, plus scaled for large values) of every source number."""
    raw: set[float] = set()
    _collect_numbers(context, raw)
    forms: set[str] = set()

    def add(x: float) -> None:
        x = abs(x)
        for d in (0, 1, 2):
            forms.add(f"{round(x, d):.{d}f}")
        # truncations too ("2.01" may be said "2.0"); ints as "2"
        forms.add(str(int(x)))

    for v in raw:
        add(v)
        for scale in (1e3, 1e6, 1e9, 1e12):
            if abs(v) >= scale:
                add(v / scale)
    # always-allowed: small counts, sector count, years, days of month
    for i in range(0, 32):
        forms.add(str(i))
    for y in range(2015, 2041):
        forms.add(str(y))
    forms.update({"500", "100", "10"})
    return forms


def _norm(token: str) -> list[str]:
    t = token.replace(",", "").lstrip("$")
    try:
        v = float(t)
    except ValueError:
        return [t]
    out = {t}
    for d in (0, 1, 2):
        out.add(f"{round(v, d):.{d}f}")
    if v == int(v):
        out.add(str(int(v)))
    return list(out)


def check_numbers(text: str, allowed: set[str]) -> list[str]:
    bad = []
    for m in NUM_RE.finditer(text):
        tok = m.group(1)
        t = tok.replace(",", "")
        if "." in t:
            ok = t in allowed or t.rstrip("0").rstrip(".") in allowed
        else:
            ok = t in allowed
        if not ok:
            bad.append(tok)
    return bad


def validate_script(episode: dict, context: dict, settings, raw_writer: dict | None = None
                    ) -> ValidationResult:
    res = ValidationResult()
    s = settings.get_path
    order = s("editorial.sector_order")
    segs = episode.get("segments") or []
    sector_segs = [x for x in segs if x.get("type") == "sector"]

    # 1. all 11 sectors, in order, each with narration and a valid story choice
    got = [x.get("sector") for x in sector_segs]
    if got != order:
        res.errors.append(f"sector list/order is wrong: {got}")
    writer_sectors = {x.get("sector"): x for x in ((raw_writer or {}).get("segments", {})
                                                   .get("sectors") or []) if isinstance(x, dict)}
    seen_story_ids: dict[str, str] = {}
    seen_companies: dict[str, str] = {}
    for x in sector_segs:
        t = x.get("sector")
        if word_count(x.get("narration", "")) < 20:
            res.errors.append(f"{t}: narration missing or too short")
        wanted = (writer_sectors.get(t) or {}).get("story_id")
        if wanted and x.get("no_standout"):
            res.errors.append(f"{t}: story_id {wanted!r} is not one of this sector's candidates")
        st = x.get("story")
        if st:
            if not st.get("url"):
                res.errors.append(f"{t}: chosen story has no source URL")
            if st["id"] in seen_story_ids:
                res.errors.append(f"{t}: same story already used in {seen_story_ids[st['id']]}")
            seen_story_ids[st["id"]] = t
            comp = (st.get("ticker") or st.get("company") or "").upper()
            if comp and comp in seen_companies:
                res.warnings.append(f"{t}: company {comp} also leads {seen_companies[comp]}")
            seen_companies.setdefault(comp, t)

    for x in segs:
        n = word_count(x.get("narration", ""))
        lo, hi = SEGMENT_WORDS.get(x.get("type"), (0, 10_000))
        if x.get("type") != "sector" and n == 0:
            res.errors.append(f"{x['id']}: narration missing")
        elif not lo <= n <= hi:
            res.warnings.append(f"{x['id']}: {n} words (expected {lo}-{hi})")

    # 2. length
    total = episode.get("word_count", 0)
    wmin, wmax = int(s("editorial.target_words_min", 1250)), int(s("editorial.target_words_max", 1450))
    if total < wmin * 0.75 or total > wmax * 1.3:
        res.errors.append(f"script length {total} words is far from target {wmin}-{wmax}")
    elif not wmin * 0.92 <= total <= wmax * 1.08:
        res.warnings.append(f"script length {total} words (target {wmin}-{wmax})")

    # 3. sign-off and no advice
    closing = next((x for x in segs if x.get("type") == "closing"), {})
    sign_off = (s("show.sign_off") or "").strip().rstrip(".")
    if sign_off and sign_off.lower()[:30] not in closing.get("narration", "").lower():
        res.errors.append("closing does not contain the standard sign-off")
    full = "\n".join(x.get("narration", "") for x in segs)
    for m in ADVICE_RE.finditer(full):
        res.errors.append(f"possible investment advice: {m.group(0)!r}")
    for m in INSTRUCTION_RE.finditer(full):
        res.errors.append(f"instruction/prompt wording in narration: {m.group(0)!r}")
    for m in CLICHE_RE.finditer(full):
        res.warnings.append(f"cliché: {m.group(0)!r}")
    if "http" in full or "www." in full:
        res.errors.append("narration contains a URL (should not be read aloud)")

    # 4. invented numbers
    allowed = allowed_numbers(context)
    allowed |= {str(int(x)) for x in re.findall(r"\b(\d{1,2})\b", episode.get("date", ""))}
    for x in segs:
        for tok in check_numbers(x.get("narration", ""), allowed):
            res.unverified_numbers.append(f"{x['id']}: {tok}")
    limit = int(s("editorial.max_unverified_numbers", 2))
    if len(res.unverified_numbers) > limit:
        res.errors.append(f"{len(res.unverified_numbers)} numbers not found in source data: "
                          + ", ".join(res.unverified_numbers[:12]))
    elif res.unverified_numbers:
        res.warnings.append("numbers not found in source data: " + ", ".join(res.unverified_numbers))
    return res
