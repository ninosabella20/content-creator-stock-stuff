"""Settings: config/settings.yaml + environment-variable overrides.

Secrets (API keys, OAuth tokens) are read from the environment only, through
`secret()`, and are never written to logs or files.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = ROOT / "config" / "settings.yaml"

# env var -> (dotted settings path, type)
ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "GEMINI_MODEL": ("gemini.model", str),
    "MAX_AI_CALLS": ("gemini.max_calls_per_run", int),
    "TTS_PROVIDER": ("tts.provider", str),
    "TTS_FALLBACK": ("tts.fallback", str),
    "NARRATOR_VOICE": ("tts.gemini.voice", str),
    "PIPER_VOICE": ("tts.piper.voice", str),
    "SPEAKING_RATE": ("tts.speaking_rate", float),
    "VIDEO_WIDTH": ("video.width", int),
    "VIDEO_HEIGHT": ("video.height", int),
    "VIDEO_FPS": ("video.fps", int),
    "PUBLISH_YOUTUBE": ("publishing.youtube.enabled", bool),
    "YOUTUBE_PRIVACY": ("publishing.youtube.privacy", str),
    "PUBLISH_SPOTIFY": ("publishing.spotify.enabled", bool),
    "SPOTIFY_MODE": ("publishing.spotify.mode", str),
    "PUBLISH_PUBLICLY": ("publishing.public", bool),
    "OUTPUT_DIR": ("storage.output_dir", str),
    "LEDGER_PATH": ("storage.ledger_path", str),
    "ALLOW_STALE": ("sources.allow_stale", bool),
    "ON_GEMINI_FAILURE": ("editorial.on_gemini_failure", str),
    "DATA_URL": ("sources.data_url", str),
    "GEO_URL": ("sources.geo_url", str),
}

SECRET_NAMES = (
    "GEMINI_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
)


def _to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


class Settings(dict):
    """A dict with dotted-path access: settings.get_path("tts.gemini.voice")."""

    def get_path(self, path: str, default: Any = None) -> Any:
        node: Any = self
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_path(self, path: str, value: Any) -> None:
        node: Any = self
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


def load_settings(path: Path | str | None = None, env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    with open(path or DEFAULT_SETTINGS, encoding="utf-8") as f:
        settings = Settings(copy.deepcopy(yaml.safe_load(f) or {}))
    for name, (dotted, typ) in ENV_OVERRIDES.items():
        raw = env.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        value = _to_bool(raw) if typ is bool else typ(raw)
        settings.set_path(dotted, value)
    return settings


def secret(name: str, env: dict | None = None) -> str | None:
    """Read a secret from the environment. Returns None when it is not set."""
    env = os.environ if env is None else env
    value = env.get(name)
    return value.strip() if value and value.strip() else None


def redact(text: str, env: dict | None = None) -> str:
    """Remove any secret values from a string before it is logged or saved."""
    env = os.environ if env is None else env
    for name in SECRET_NAMES:
        value = env.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, f"<{name}>")
    return text
