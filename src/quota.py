"""Free-tier quota estimator: will a daily episode fit the free limits?"""
from __future__ import annotations

import math

from .config import Settings


def estimate(settings: Settings, words: int | None = None) -> dict:
    s = settings.get_path
    words = words or int((s("editorial.target_words_min", 1250) + s("editorial.target_words_max", 1450)) / 2)
    wpm = float(s("editorial.words_per_minute", 150))
    minutes = words / wpm
    chunk = int(s("tts.gemini.max_words_per_chunk", 300)) or 1
    tts_calls = math.ceil(words / chunk) + 2           # chunks (segment boundaries add ~2)
    text_calls = 2                                      # script + possible repair
    yt_units = (s("quotas.youtube_upload_units", 1600) + s("quotas.youtube_thumbnail_units", 50)
                + s("quotas.youtube_caption_units", 400) + 3)
    rpd_text = s("quotas.gemini_text_rpd_per_model", 20)
    rpd_tts = s("quotas.gemini_tts_rpd_per_model", 15)
    n_tts_models = len(s("tts.gemini.models") or [])
    out = {
        "episode": {"words": words, "minutes": round(minutes, 1)},
        "per_day": {"gemini_text_requests": text_calls, "gemini_tts_requests": tts_calls,
                    "youtube_units": yt_units},
        "per_month_30d": {"narration_minutes": round(minutes * 30), "spoken_words": words * 30,
                          "videos": 30, "gemini_text_requests": text_calls * 30,
                          "gemini_tts_requests": tts_calls * 30},
        "free_limits_assumed": {"gemini_text_rpd_per_model": rpd_text,
                                "gemini_tts_rpd_per_model": rpd_tts,
                                "gemini_tts_models_configured": n_tts_models,
                                "youtube_units_per_day": s("quotas.youtube_daily_units", 10000)},
        "verdict": [],
    }
    v = out["verdict"]
    v.append(("OK" if text_calls <= rpd_text else "OVER") +
             f": script needs {text_calls} text requests/day vs {rpd_text}/day per model")
    v.append(("OK" if tts_calls <= rpd_tts else "TIGHT" if tts_calls <= rpd_tts * max(1, n_tts_models) else "OVER") +
             f": narration needs ~{tts_calls} TTS requests/day vs {rpd_tts}/day per TTS model "
             f"({n_tts_models} models configured; Piper fallback is unlimited)")
    v.append(("OK" if yt_units <= s("quotas.youtube_daily_units", 10000) else "OVER") +
             f": YouTube needs ~{yt_units} units/day vs {s('quotas.youtube_daily_units', 10000)}")
    v.append("OK: GitHub Actions minutes are free and unlimited for public repositories "
             "(about 15-25 minutes per episode).")
    v.append("NOTE: these are assumptions. Your real Gemini limits are shown in Google AI Studio "
             "(Dashboard > Rate limit) for the podcast project; each run also records any "
             "429 quota details in run_report.json.")
    return out
