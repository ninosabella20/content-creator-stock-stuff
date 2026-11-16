"""YouTube publishing through the official YouTube Data API v3.

* OAuth: the long-lived refresh token (secret YOUTUBE_REFRESH_TOKEN) is exchanged for a
  short-lived access token on every run. Nothing is ever written to disk or logs.
* Upload: resumable upload protocol (survives network hiccups), then thumbnail and
  captions (best effort: a failure there never fails the episode).
* Duplicate protection: the episode ID is written into the description, and the
  channel's latest uploads are checked for it before uploading (costs ~2 quota units).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from ..config import Settings, secret
from ..util import HTTP

log = logging.getLogger(__name__)
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
CHUNK = 16 * 1024 * 1024


class YouTubeError(RuntimeError):
    pass


def access_token() -> str:
    cid, csec, rtok = (secret("YOUTUBE_CLIENT_ID"), secret("YOUTUBE_CLIENT_SECRET"),
                       secret("YOUTUBE_REFRESH_TOKEN"))
    missing = [n for n, v in (("YOUTUBE_CLIENT_ID", cid), ("YOUTUBE_CLIENT_SECRET", csec),
                              ("YOUTUBE_REFRESH_TOKEN", rtok)) if not v]
    if missing:
        raise YouTubeError("missing GitHub secret(s): " + ", ".join(missing))
    r = HTTP.post(TOKEN_URL, data={"client_id": cid, "client_secret": csec, "refresh_token": rtok,
                                   "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        err = r.json().get("error", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        hint = (" The refresh token was revoked or expired. If the OAuth consent screen is in "
                "'Testing', tokens expire after 7 days: publish the app ('In production') and "
                "create a new token (README, step 'YouTube refresh token').") if err == "invalid_grant" else ""
        raise YouTubeError(f"token refresh failed: HTTP {r.status_code} {err}.{hint}")
    return r.json()["access_token"]


def _h(tok: str, **extra) -> dict:
    return {"Authorization": f"Bearer {tok}", **extra}


def find_existing(tok: str, episode_id: str, look_back: int = 25) -> str | None:
    """Return the videoId of an upload whose description mentions episode_id."""
    r = HTTP.get(f"{API}/channels", params={"part": "contentDetails", "mine": "true"},
                 headers=_h(tok), timeout=30)
    if r.status_code != 200:
        log.warning("could not list channel (HTTP %s); skipping remote duplicate check", r.status_code)
        return None
    items = r.json().get("items") or []
    if not items:
        raise YouTubeError("this Google account has no YouTube channel")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    r = HTTP.get(f"{API}/playlistItems", params={"part": "snippet", "playlistId": uploads,
                                                 "maxResults": look_back}, headers=_h(tok), timeout=30)
    if r.status_code != 200:
        return None
    for it in r.json().get("items") or []:
        sn = it.get("snippet") or {}
        if episode_id in (sn.get("description") or ""):
            return (sn.get("resourceId") or {}).get("videoId")
    return None


def upload_video(tok: str, video: Path, meta: dict) -> dict:
    size = video.stat().st_size
    r = HTTP.post(f"{UPLOAD}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                  headers=_h(tok, **{"Content-Type": "application/json; charset=UTF-8",
                                     "X-Upload-Content-Type": "video/mp4",
                                     "X-Upload-Content-Length": str(size)}),
                  data=json.dumps(meta), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        raise YouTubeError(f"could not start upload: HTTP {r.status_code} {r.text[:400]}")
    url = r.headers["Location"]
    pos, failures = 0, 0
    with open(video, "rb") as f:
        while True:
            f.seek(pos)
            chunk = f.read(CHUNK)
            end = pos + len(chunk) - 1
            try:
                r = HTTP.put(url, data=chunk, timeout=300, headers=_h(tok, **{
                    "Content-Length": str(len(chunk)), "Content-Range": f"bytes {pos}-{end}/{size}"}))
            except requests.RequestException as e:
                r = None
                log.warning("upload chunk error: %s", e)
            if r is not None and r.status_code in (200, 201):
                return r.json()
            if r is not None and r.status_code == 308:
                rng = r.headers.get("Range")
                pos = int(rng.split("-")[1]) + 1 if rng else 0
                failures = 0
                log.info("uploaded %.0f%%", 100 * pos / size)
                continue
            if r is not None and r.status_code not in (500, 502, 503, 504):
                raise YouTubeError(f"upload failed: HTTP {r.status_code} {r.text[:400]}")
            failures += 1
            if failures > 6:
                raise YouTubeError("upload failed after repeated server/network errors")
            time.sleep(min(60, 2 ** failures))
            q = HTTP.put(url, headers=_h(tok, **{"Content-Range": f"bytes */{size}"}), timeout=60)
            if q.status_code in (200, 201):
                return q.json()
            rng = q.headers.get("Range")
            pos = int(rng.split("-")[1]) + 1 if rng else 0


def set_thumbnail(tok: str, video_id: str, image: Path) -> str:
    r = HTTP.post(f"{UPLOAD}/thumbnails/set", params={"videoId": video_id},
                  headers=_h(tok, **{"Content-Type": "image/jpeg"}), data=image.read_bytes(), timeout=120)
    if r.status_code == 200:
        return "ok"
    if r.status_code == 403:
        return ("not allowed (403): custom thumbnails need a verified channel. Verify at "
                "youtube.com/verify, then re-run.")
    return f"failed: HTTP {r.status_code} {r.text[:200]}"


def upload_captions(tok: str, video_id: str, srt: Path, language: str = "en") -> str:
    boundary = "mbdcaptionboundary"
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": "English",
                                   "isDraft": False}})
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + \
        srt.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    r = HTTP.post(f"{UPLOAD}/captions", params={"uploadType": "multipart", "part": "snippet"},
                  headers=_h(tok, **{"Content-Type": f"multipart/related; boundary={boundary}"}),
                  data=body, timeout=120)
    return "ok" if r.status_code == 200 else f"failed: HTTP {r.status_code} {r.text[:200]}"


def publish(episode: dict, files: dict[str, Path], title: str, description: str, tags: list[str],
            settings: Settings, allow_public: bool) -> dict:
    s = settings.get_path
    privacy = s("publishing.youtube.privacy", "private")
    if privacy == "public" and not allow_public:
        log.warning("privacy 'public' requested but PUBLISH_PUBLICLY is not true: uploading as private")
        privacy = "private"
    tok = access_token()
    existing = find_existing(tok, episode["episode_id"])
    if existing:
        log.info("episode already on YouTube as %s; not uploading again", existing)
        return {"status": "already_uploaded", "video_id": existing,
                "url": f"https://youtu.be/{existing}"}
    meta = {
        "snippet": {"title": title, "description": description, "tags": tags,
                    "categoryId": str(s("publishing.youtube.category_id", "25")),
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy,
                   "selfDeclaredMadeForKids": bool(s("publishing.youtube.made_for_kids", False)),
                   "containsSyntheticMedia": True},   # AI narration disclosure
    }
    res = upload_video(tok, files["video"], meta)
    vid = res.get("id")
    out = {"status": "uploaded", "video_id": vid, "url": f"https://youtu.be/{vid}",
           "privacy_requested": privacy,
           "privacy_reported": (res.get("status") or {}).get("privacyStatus"),
           "upload_status": (res.get("status") or {}).get("uploadStatus")}
    if files.get("thumbnail"):
        out["thumbnail"] = set_thumbnail(tok, vid, files["thumbnail"])
    if files.get("captions") and s("publishing.youtube.upload_captions", True):
        out["captions"] = upload_captions(tok, vid, files["captions"])
    log.info("YouTube upload done: %s", {k: v for k, v in out.items() if k != "url"})
    return out
