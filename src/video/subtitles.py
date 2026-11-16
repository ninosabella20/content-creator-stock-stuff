"""Subtitle cues from the narration timeline, plus SRT / WebVTT writers."""
from __future__ import annotations

import re
from pathlib import Path

MAX_CHARS = 84        # two lines of ~42 characters
MIN_CUE = 1.0         # seconds


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split a sentence into readable pieces, preferring punctuation, then word boundaries."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]
    n = -(-len(text) // max_chars)               # pieces needed
    target = len(text) / n
    out, cur = [], ""
    tokens = re.findall(r"\S+\s*", text)
    for tok in tokens:
        if cur and len(cur) + len(tok) > max_chars:
            out.append(cur.strip())
            cur = ""
        cur += tok
        if len(cur) >= target * 0.8 and re.search(r"[,;:]\s*$", tok) and len(out) < n - 1:
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def build_cues(timeline: dict, max_chars: int = MAX_CHARS) -> list[dict]:
    cues: list[dict] = []
    for seg in timeline.get("segments", []):
        for s in seg.get("sentences", []):
            pieces = _split_text(s["text"], max_chars)
            total = sum(len(p) for p in pieces) or 1
            t, dur = s["start"], s["end"] - s["start"]
            for p in pieces:
                d = dur * len(p) / total
                cues.append({"start": round(t, 3), "end": round(t + d, 3), "text": p,
                             "segment": seg["id"]})
                t += d
    # merge too-short cues into their neighbour; never overlap
    merged: list[dict] = []
    for c in cues:
        if merged and (c["end"] - c["start"] < MIN_CUE) and c["segment"] == merged[-1]["segment"] \
                and len(merged[-1]["text"]) + len(c["text"]) <= max_chars + 20:
            merged[-1]["text"] += " " + c["text"]
            merged[-1]["end"] = c["end"]
        else:
            merged.append(dict(c))
    for a, b in zip(merged, merged[1:]):
        a["end"] = min(a["end"], b["start"])
    return merged


def split_lines(text: str, max_line: int = 46) -> list[str]:
    """Break a cue into at most two balanced lines."""
    if len(text) <= max_line:
        return [text]
    words = text.split()
    best, best_diff = [text], 10 ** 9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if diff < best_diff and max(len(a), len(b)) <= max_line + 12:
            best, best_diff = [a, b], diff
    return best


def _ts(t: float, sep: str) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(cues: list[dict], path: Path) -> None:
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}",
                  "\n".join(split_lines(c["text"])), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(cues: list[dict], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for c in cues:
        lines += [f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}",
                  "\n".join(split_lines(c["text"])), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
