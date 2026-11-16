"""Podcast distribution (Spotify and other apps).

Spotify has no public API for uploading episodes. Two supported, free routes:

1. "manual" (default): a complete, ready-to-upload package per episode (MP3, optional
   MP4 for Spotify video, cover art, title, description, publish date). You upload it in
   Spotify for Creators (about 2 minutes).
2. "rss": this project generates a standard podcast RSS feed (RSS 2.0 + iTunes tags),
   hosted for free on GitHub Pages with MP3s attached to GitHub Releases. Spotify (and
   Apple, Amazon, ...) pick up new episodes automatically. Moving an existing Spotify for
   Creators show to an external feed uses Spotify's official 301-redirect process. Audio
   only on Spotify via this route (Spotify video needs an upload in Spotify for Creators
   or a Spotify video partner host).

New providers implement `PodcastPublisher`.
"""
from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, time, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from ..config import Settings

UPLOAD_STEPS = """# Upload this episode to Spotify for Creators (about 2 minutes)

1. Open https://creators.spotify.com and sign in. Select the show "{show}".
2. Click **New episode** (top right).
3. Upload **{media}** {media_note}
4. **Title:** copy from `title.txt`.
5. **Description:** copy from `description.txt`.
6. **Episode art:** optional, `cover.jpg` (3000x3000).
7. **Publish date:** now (or schedule). Episode type: Full. Content: not explicit.
8. Click **Publish**.

Checks already done automatically: loudness {lufs} LUFS, {minutes} min, all 11 sectors,
source links in the description.
"""


class PodcastPublisher(ABC):
    @abstractmethod
    def publish(self, package_dir: Path, info: dict) -> dict:
        ...


def build_package(out_dir: Path, episode: dict, files: dict[str, Path], title: str,
                  description: str, settings: Settings, timeline: dict) -> Path:
    """Write output/<episode>/podcast/ with everything needed for a manual upload."""
    s = settings.get_path
    pkg = out_dir / "podcast"
    pkg.mkdir(parents=True, exist_ok=True)
    shutil.copy2(files["mp3"], pkg / "episode.mp3")
    if files.get("cover"):
        shutil.copy2(files["cover"], pkg / "cover.jpg")
    (pkg / "title.txt").write_text(title + "\n", encoding="utf-8")
    (pkg / "description.txt").write_text(description + "\n", encoding="utf-8")
    meta = {
        "show": s("show.name"), "creator": s("show.creator"), "category": s("show.category"),
        "language": s("show.language"), "title": title, "description": description,
        "episode_id": episode["episode_id"], "publish_date": episode["date"],
        "duration_seconds": round(timeline.get("duration", 0)), "explicit": False,
        "episode_type": "full", "audio_file": "episode.mp3",
        "video_file": files["video"].name if files.get("video") else None,
        "flags": episode.get("flags", []),
        "sources": [seg["story"]["url"] for seg in episode["segments"]
                    if seg.get("type") == "sector" and seg.get("story")],
    }
    (pkg / "episode_metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    has_video = bool(files.get("video"))
    (pkg / "UPLOAD_TO_SPOTIFY.md").write_text(UPLOAD_STEPS.format(
        show=s("show.name"),
        media=f"../{files['video'].name}" if has_video else "episode.mp3",
        media_note=("(the MP4 makes it a video podcast on Spotify; or use episode.mp3 for audio only)."
                    if has_video else "."),
        lufs=s("tts.loudness_lufs"), minutes=round(timeline.get("duration", 0) / 60, 1)), encoding="utf-8")
    return pkg


class ManualPublisher(PodcastPublisher):
    def publish(self, package_dir: Path, info: dict) -> dict:
        return {"status": "manual_upload_required", "package": str(package_dir),
                "instructions": str(package_dir / "UPLOAD_TO_SPOTIFY.md")}


# ------------------------------------------------------------------------------ RSS feed
def rss_feed(settings: Settings, items: list[dict], feed_url: str, cover_url: str) -> str:
    """items: [{title, description, guid, date (YYYY-MM-DD), mp3_url, mp3_bytes, duration}]"""
    s = settings.get_path
    show_desc = f"{s('show.description')}\n\n{s('show.disclaimer')}"
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
           'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:podcast="https://podcastindex.org/namespace/1.0">',
           "<channel>",
           f"<title>{escape(s('show.name'))}</title>",
           f"<link>{escape(s('show.website'))}</link>",
           f'<atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>',
           f"<language>{escape(s('show.language'))}</language>",
           f"<description>{escape(show_desc)}</description>",
           f"<itunes:summary>{escape(show_desc)}</itunes:summary>",
           f"<itunes:author>{escape(s('show.creator'))}</itunes:author>",
           f'<itunes:image href="{escape(cover_url)}"/>',
           f'<itunes:category text="Business"><itunes:category text="{escape(s("show.category"))}"/></itunes:category>',
           "<itunes:explicit>false</itunes:explicit>", "<itunes:type>episodic</itunes:type>"]
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        pub = datetime.combine(datetime.fromisoformat(it["date"]).date(), time(7, 30), timezone.utc)
        out += ["<item>",
                f"<title>{escape(it['title'])}</title>",
                f"<description>{escape(it['description'])}</description>",
                f'<guid isPermaLink="false">{escape(it["guid"])}</guid>',
                f"<pubDate>{format_datetime(pub)}</pubDate>",
                f'<enclosure url="{escape(it["mp3_url"])}" length="{int(it["mp3_bytes"])}" type="audio/mpeg"/>',
                f"<itunes:duration>{int(it['duration'])}</itunes:duration>",
                "<itunes:episodeType>full</itunes:episodeType>", "<itunes:explicit>false</itunes:explicit>",
                "</item>"]
    out += ["</channel>", "</rss>"]
    return "\n".join(out)
