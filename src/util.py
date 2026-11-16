"""Shared helpers: logging (with secret redaction), HTTP with retries, time helpers."""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import redact


class StageError(RuntimeError):
    """A pipeline stage failed. `stage` names where, for the run report."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - never break logging
            pass
        return True


def setup_logging(level: int = logging.INFO, logfile: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    for h in handlers:
        h.addFilter(_RedactingFilter())
    logging.basicConfig(level=level, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")


HTTP = requests.Session()
HTTP.headers["User-Agent"] = "market-brief-daily/1.0 (+https://github.com)"


def http_get_json(url: str, retries: int = 4, timeout: float = 30) -> Any:
    """GET a JSON document with retries and exponential backoff."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            # cache-buster: raw.githubusercontent.com caches for ~5 minutes
            r = HTTP.get(url, params={"t": int(time.time())}, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries:
                time.sleep(min(60, 2 ** attempt * 2))
    raise RuntimeError(f"could not fetch {url}: {last}")


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp ("2026-11-15T05:01:11Z") into an aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default
