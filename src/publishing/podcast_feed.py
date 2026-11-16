"""Automatic, audio-only podcast distribution through a self-hosted RSS feed.

* The MP3 is attached to a GitHub Release (tag = episode ID) in this public repo.
* docs/podcast/feed.xml (RSS 2.0 + iTunes tags) is rebuilt from the ledger and served by
  GitHub Pages at settings show.podcast_feed_url.
* Spotify for Creators reads that feed once the show is redirected to it
  (Settings > Redirect your podcast). Apple Podcasts etc. can use the same feed.

Uses only the workflow's built-in GITHUB_TOKEN; no extra credentials.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..config import ROOT, Settings
from ..util import HTTP
from .spotify import rss_feed

log = logging.getLogger(__name__)
API = "https://api.github.com"
FEED_DIR = ROOT / "docs" / "podcast"


class FeedError(RuntimeError):
    pass


def _gh():
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not tok:
        raise FeedError("GITHUB_REPOSITORY/GITHUB_TOKEN not set (podcast publishing runs in GitHub Actions)")
    return repo, {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}


def ensure_release_asset(tag: str, title: str, body: str, mp3: Path) -> dict:
    """Create (or reuse) the release `tag` with `mp3` attached. Idempotent."""
    repo, h = _gh()
    r = HTTP.get(f"{API}/repos/{repo}/releases/tags/{tag}", headers=h, timeout=30)
    if r.status_code == 404:
        r = HTTP.post(f"{API}/repos/{repo}/releases", headers=h, timeout=30, json={
            "tag_name": tag, "target_commitish": os.environ.get("GITHUB_SHA") or None,
            "name": title[:200], "body": body[:120000], "make_latest": "false"})
        if r.status_code not in (200, 201):
            raise FeedError(f"could not create release {tag}: HTTP {r.status_code} {r.text[:300]}")
    elif r.status_code != 200:
        raise FeedError(f"release lookup failed: HTTP {r.status_code} {r.text[:200]}")
    rel = r.json()
    name = mp3.name
    for a in rel.get("assets") or []:
        if a["name"] == name and a.get("state") == "uploaded":
            return {"url": a["browser_download_url"], "bytes": a["size"], "release": rel["html_url"]}
    up = rel["upload_url"].split("{")[0]
    with open(mp3, "rb") as f:
        r = HTTP.post(up, params={"name": name}, data=f, timeout=600,
                      headers={**h, "Content-Type": "audio/mpeg"})
    if r.status_code not in (200, 201):
        raise FeedError(f"MP3 upload failed: HTTP {r.status_code} {r.text[:300]}")
    a = r.json()
    return {"url": a["browser_download_url"], "bytes": a["size"], "release": rel["html_url"]}


def _owner_email(settings: Settings) -> str:
    return (os.environ.get("PODCAST_OWNER_EMAIL") or settings.get_path("show.owner_email") or "").strip()


def write_feed(ledger, settings: Settings, cover: Path | None) -> Path:
    s = settings.get_path
    FEED_DIR.mkdir(parents=True, exist_ok=True)
    feed_url = s("show.podcast_feed_url")
    base = feed_url.rsplit("/", 1)[0]
    cover_dst = FEED_DIR / "cover.jpg"
    if cover and cover.exists() and not cover_dst.exists():
        shutil.copy2(cover, cover_dst)
    items = [rec["podcast"] for rec in ledger.data["episodes"].values()
             if (rec.get("podcast") or {}).get("mp3_url")]
    items = sorted(items, key=lambda x: x["date"], reverse=True)[: int(s("publishing.spotify.feed_max_episodes", 300))]
    xml = rss_feed(settings, items, feed_url, f"{base}/cover.jpg",
                   owner_email=_owner_email(settings))
    out = FEED_DIR / "feed.xml"
    out.write_text(xml, encoding="utf-8")
    (FEED_DIR / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><title>{s("show.name")}</title>'
        f'<p>{s("show.name")} podcast feed: <a href="feed.xml">feed.xml</a></p>', encoding="utf-8")
    return out


def publish(episode: dict, files: dict[str, Path], title: str, description: str, timeline: dict,
            ledger, settings: Settings) -> dict:
    eid = episode["episode_id"]
    asset = ensure_release_asset(eid, title, description, files["mp3"])
    ledger.update(eid, podcast={"title": title, "description": description, "guid": eid,
                                "date": episode["date"], "mp3_url": asset["url"],
                                "mp3_bytes": asset["bytes"], "duration": round(timeline["duration"])})
    feed = write_feed(ledger, settings, files.get("cover"))
    if not _owner_email(settings):
        log.warning("PODCAST_OWNER_EMAIL is not set: Spotify needs an owner email in the feed to "
                    "verify the show (see README, Spotify section)")
    return {"status": "in_feed", "mp3_url": asset["url"], "release": asset["release"],
            "feed": settings.get_path("show.podcast_feed_url"), "feed_file": str(feed.relative_to(ROOT))}
