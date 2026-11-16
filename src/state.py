"""Episode ledger (state/episodes.json), committed back to the repo by the workflow.

It is the memory between daily runs: which episodes exist, which were published
(and where), which stories were used recently. It makes runs idempotent and
prevents duplicate publishing even if a run is retried.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .util import now_utc, read_json, write_json


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = read_json(self.path, {}) or {}
        self.data.setdefault("episodes", {})

    def get(self, episode_id: str) -> dict:
        return self.data["episodes"].get(episode_id, {})

    def update(self, episode_id: str, **fields) -> dict:
        rec = self.data["episodes"].setdefault(episode_id, {})
        rec.update(fields)
        rec["updated_at"] = now_utc().isoformat(timespec="seconds")
        return rec

    def is_produced(self, episode_id: str) -> bool:
        """Done for the day. A flagged, unpublished episode (e.g. Gemini was down and the
        template fallback was used) is NOT done: the next scheduled trigger retries it."""
        rec = self.get(episode_id)
        if rec.get("status") == "published":
            return True
        return rec.get("status") == "produced" and not rec.get("flags")

    @property
    def meta(self) -> dict:
        return self.data.setdefault("meta", {})

    def youtube_done(self, episode_id: str) -> bool:
        yt = self.get(episode_id).get("youtube") or {}
        return yt.get("status") in ("uploaded", "already_uploaded")

    def recent_story_urls(self, before: date, days: int) -> set[str]:
        urls: set[str] = set()
        for rec in self.data["episodes"].values():
            try:
                d = date.fromisoformat(rec.get("date", ""))
            except ValueError:
                continue
            if before - timedelta(days=days) <= d < before:
                urls.update(rec.get("story_urls") or [])
        return urls

    def save(self) -> None:
        # keep the file small: only the last 120 episodes
        eps = self.data["episodes"]
        if len(eps) > 120:
            for k in sorted(eps)[:-120]:
                del eps[k]
        write_json(self.path, self.data)
