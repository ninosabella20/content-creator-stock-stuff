"""Minimal Gemini REST client with model fallback, rate spacing and quota accounting.

Uses only the podcast project's key (GEMINI_API_KEY). It never switches to a paid
service: when every free model is exhausted it raises GeminiError and the
pipeline decides what to do (fail safely or use the template script).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

from .util import HTTP

log = logging.getLogger(__name__)
BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    pass


class QuotaExhausted(GeminiError):
    pass


@dataclass
class Usage:
    """Per-run accounting, saved in the run report (quota monitoring)."""
    ok: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    rate_limits: dict[str, str] = field(default_factory=dict)
    prompt_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return {"ok_calls": self.ok, "abandoned_models": self.failed,
                "rate_limit_details": self.rate_limits,
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens}


def list_models(key: str) -> list[str]:
    names, token = [], None
    while True:
        params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
        r = HTTP.get(f"{BASE}/models", headers={"x-goog-api-key": key}, params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        names += [m["name"].split("/", 1)[1] for m in j.get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", [])]
        token = j.get("nextPageToken")
        if not token:
            return names


def pick_text_models(available: list[str], override: str | None = None,
                     prefer: str | None = None) -> list[str]:
    """Newest stable flash first, then flash-latest, previews, then lite models.
    `prefer` (the model that worked last run) is tried first if still available."""
    if override:
        return [override]
    if prefer and prefer in available:
        return [prefer] + [m for m in pick_text_models(available) if m != prefer]
    bad = re.compile(r"image|tts|audio|live|embedding|vision|robotics|computer|omni", re.I)

    def ver(n: str) -> float:
        m = re.match(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    stable = [n for n in available if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash", n)]
    preview = [n for n in available if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash-preview[\w.-]*", n)
               and not bad.search(n)]
    lite = [n for n in available if re.fullmatch(r"gemini-\d+(?:\.\d+)?-flash-lite", n)]
    ordered = sorted(stable, key=ver, reverse=True)
    ordered += [n for n in ("gemini-flash-latest",) if n in available]
    ordered += sorted(preview, key=ver, reverse=True)
    ordered += sorted(lite, key=ver, reverse=True)
    return list(dict.fromkeys(ordered)) or ["gemini-flash-latest", "gemini-2.5-flash"]


class Gemini:
    def __init__(self, key: str, models: list[str], min_interval: float = 12,
                 timeout: float = 180, max_calls: int = 6, usage: Usage | None = None):
        if not key:
            raise GeminiError("GEMINI_API_KEY is not set")
        self.key = key
        self.models = list(models)
        self.model = self.models[0]
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_calls = max_calls          # successful (quota-consuming) calls
        self.max_attempts = max_calls * 4 + 4  # all HTTP attempts incl. timeouts/503s
        self.calls = 0
        self.attempts = 0
        self.usage = usage or Usage()
        self._last = 0.0

    # -- model fallback ---------------------------------------------------------------
    def _drop_model(self, why: str) -> None:
        self.usage.failed[self.model] = why[:300]
        log.warning("Gemini model %s unusable: %s", self.model, why[:300])
        rest = [m for m in self.models if m not in self.usage.failed]
        if not rest:
            raise QuotaExhausted("no usable Gemini model left: " +
                                 "; ".join(f"{k}: {v[:120]}" for k, v in self.usage.failed.items()))
        self.model = rest[0]
        log.info("switching to %s", self.model)

    def _post(self, body: dict) -> dict:
        """POST generateContent with spacing, retries and per-model fallback."""
        attempts = 0
        while True:
            if self.calls >= self.max_calls:
                raise QuotaExhausted(f"run cap of {self.max_calls} successful Gemini calls reached")
            if self.attempts >= self.max_attempts:
                raise GeminiError(f"gave up after {self.attempts} attempts (models busy or timing out)")
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            attempts += 1
            self.attempts += 1
            try:
                r = HTTP.post(f"{BASE}/models/{self.model}:generateContent",
                              headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                              json=body, timeout=self.timeout)
            except requests.Timeout:
                self._drop_model(f"no answer within {self.timeout:.0f}s")
                attempts = 0
                continue
            except requests.RequestException as e:
                if attempts >= 3:
                    raise GeminiError(f"network error: {e}") from e
                time.sleep(10 * attempts)
                continue
            if r.status_code == 200:
                self.calls += 1
                self.usage.ok[self.model] = self.usage.ok.get(self.model, 0) + 1
                j = r.json()
                um = j.get("usageMetadata") or {}
                self.usage.prompt_tokens += int(um.get("promptTokenCount") or 0)
                self.usage.output_tokens += int(um.get("candidatesTokenCount") or 0)
                return j
            text = r.text
            if r.status_code == 429:
                quota = ",".join(sorted(set(re.findall(r'"quotaId":\s*"([^"]+)"', text))))
                limit = ",".join(sorted(set(re.findall(r'"quotaValue":\s*"?(\d+)', text))))
                m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text)
                delay = float(m.group(1)) if m else 30.0
                why = f"429 quota={quota or '?'} limit={limit or '?'} retry={delay:.0f}s"
                self.usage.rate_limits[self.model] = why
                daily = "PerDay" in text or "per day" in text.lower()
                if daily or limit == "0" or delay > 65 or attempts >= 2:
                    self._drop_model(why)
                    attempts = 0
                    continue
                log.info("429 on %s (%s); waiting %.0fs", self.model, why, delay + 2)
                time.sleep(delay + 2)
                continue
            if r.status_code == 404 or (r.status_code in (400, 403) and re.search(
                    r"model|not supported|not available|no longer|modalit", text, re.I)):
                self._drop_model(f"{r.status_code}: {text[:200]}")
                attempts = 0
                continue
            if r.status_code >= 500 and attempts < 2:
                time.sleep(10 * attempts)
                continue
            if r.status_code >= 500:
                self._drop_model(f"{r.status_code}: {text[:200]}")
                attempts = 0
                continue
            raise GeminiError(f"HTTP {r.status_code}: {text[:300]}")

    # -- text ----------------------------------------------------------------------------
    def generate_text(self, prompt: str, temperature: float = 0.4, json_mode: bool = False,
                      system: str | None = None) -> str:
        body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": temperature}}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        j = self._post(body)
        cands = j.get("candidates") or []
        if not cands:
            raise GeminiError(f"no candidates: {json.dumps(j)[:300]}")
        parts = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise GeminiError(f"empty answer (finishReason={cands[0].get('finishReason')})")
        return text

    def transcribe(self, wav_bytes: bytes) -> str:
        """Speech-to-text of a short WAV clip (used to check narration audio)."""
        body = {"contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav_bytes).decode()}},
            {"text": "Transcribe the speech in this audio exactly, word for word. "
                     "Output only the transcript, nothing else."}]}],
                "generationConfig": {"temperature": 0}}
        j = self._post(body)
        parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()

    # -- speech --------------------------------------------------------------------------
    def generate_speech(self, text: str, voice: str) -> tuple[bytes, int]:
        """Return (16-bit little-endian mono PCM, sample_rate)."""
        body = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        while True:
            j = self._post(body)
            for cand in j.get("candidates") or []:
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        mime = inline.get("mimeType") or inline.get("mime_type") or ""
                        m = re.search(r"rate=(\d+)", mime)
                        return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000
            fin = (j.get("candidates") or [{}])[0].get("finishReason") or \
                (j.get("promptFeedback") or {}).get("blockReason")
            # e.g. a false-positive SAFETY block on one model: try the next model
            self._drop_model(f"no audio (finishReason={fin})")


def parse_json_text(text: str):
    """Parse model JSON output, tolerating ```json fences."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
