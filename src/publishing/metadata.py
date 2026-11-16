"""Titles, descriptions, chapters and show notes for YouTube and the podcast."""
from __future__ import annotations

from datetime import date

from ..config import Settings

YT_TITLE_MAX = 100
YT_DESC_MAX_BYTES = 4900       # YouTube limit is 5000 bytes
PODCAST_DESC_MAX = 3900        # Spotify for Creators allows 4000 characters


def _clean(text: str) -> str:
    # YouTube rejects "<" and ">" in titles/descriptions
    return (text or "").replace("<", "‹").replace(">", "›").strip()


def _ts(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _date_label(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def chapters(episode: dict, timeline: dict) -> list[tuple[str, str]]:
    by_id = {s["id"]: s for s in episode["segments"]}
    out = []
    for i, seg in enumerate(timeline.get("segments", [])):
        s = by_id.get(seg["id"], {})
        t = 0.0 if i == 0 else seg["start"]
        if s.get("type") == "sector":
            st = s.get("story") or {}
            label = s["title"] + (f": {st.get('company')}" if st.get("company") else "")
        elif s.get("type") == "intro":
            label = "Intro"
        else:
            label = s.get("title", seg["id"])
        out.append((_ts(t), label))
    return out


def source_lines(episode: dict) -> list[str]:
    lines = []
    for s in episode["segments"]:
        st = s.get("story") if s.get("type") == "sector" else None
        if st and st.get("url"):
            who = st.get("company") + (f" ({st['ticker']})" if st.get("ticker") else "")
            meta = ", ".join(x for x in (st.get("source"), st.get("published")) if x)
            lines.append(f"• {s['title']}: {who}: {st['headline']} [{meta}] {st['url']}")
        elif s.get("type") == "sector":
            lines.append(f"• {s['title']}: no standout verified development")
    geo = next((s for s in episode["segments"] if s["type"] == "geopolitics"), {})
    for src in geo.get("sources", [])[:6]:
        lines.append(f"• Geopolitics: {src.get('title')} [{src.get('publisher')}] {src.get('url')}")
    return lines


def youtube_title(episode: dict) -> str:
    d = date.fromisoformat(episode["date"])
    t = f"Market Brief Daily | {_date_label(d)} | 11 Sectors + Market & Geopolitical News"
    return _clean(t)[:YT_TITLE_MAX]


def youtube_description(episode: dict, timeline: dict, settings: Settings) -> str:
    s = settings.get_path
    parts = [episode.get("summary") or "", ""]
    if episode.get("flags"):
        parts += ["⚠ " + f for f in episode["flags"]] + [""]
    parts.append("Chapters")
    parts += [f"{ts} {label}" for ts, label in chapters(episode, timeline)]
    parts += ["", "Sources (original reporting)"]
    srcs = source_lines(episode)
    tail = ["", f"Market data and analysis: Market Brief, {s('show.website')}",
            f"Market data as of: {episode.get('market_session') or 'latest close'}.", "",
            s("show.disclaimer") or "", "", f"Episode ID: {episode['episode_id']}"]
    text = "\n".join(parts + srcs + tail)
    while len(text.encode("utf-8")) > YT_DESC_MAX_BYTES and srcs:
        srcs = srcs[:-1]       # drop geopolitics/extra links first (last in the list)
        text = "\n".join(parts + srcs + ["• More sources: " + (s("show.website") or "")] + tail)
    return _clean(text)


def youtube_tags(episode: dict, settings: Settings) -> list[str]:
    tags = list(settings.get_path("publishing.youtube.tags") or [])
    for seg in episode["segments"]:
        st = seg.get("story") or {}
        if st.get("company"):
            tags.append(st["company"].replace(",", ""))
    out, total = [], 0
    for t in dict.fromkeys(tags):
        if total + len(t) + 3 > 480:
            break
        out.append(t)
        total += len(t) + 3
    return out


def podcast_title(episode: dict) -> str:
    d = date.fromisoformat(episode["date"])
    hook = episode.get("title_hook")
    return f"{_date_label(d)}: {hook}" if hook else f"Daily Market Brief, {_date_label(d)}"


def podcast_description(episode: dict, timeline: dict, settings: Settings) -> str:
    s = settings.get_path
    lines = [episode.get("summary") or "", "",
             "In this episode: the market overview, the top story in each of the 11 sectors, "
             "and the week's key geopolitical developments.", "", "Chapters"]
    lines += [f"{ts} {label}" for ts, label in chapters(episode, timeline)]
    lines += ["", "Sources"]
    srcs = source_lines(episode)
    tail = ["", f"More: {s('show.website')}", "", s("show.disclaimer") or ""]
    text = "\n".join(lines + srcs + tail)
    while len(text) > PODCAST_DESC_MAX and srcs:
        srcs = srcs[:-1]
        text = "\n".join(lines + srcs + tail)
    return text.strip()
