"""Editorial stage: rank stories, write the script, validate, repair once, fall back safely."""
from __future__ import annotations

import logging
from datetime import date

from ..config import Settings
from ..data.geopolitics import Geopolitics
from ..data.market_brief import MarketBrief
from ..data.validation import DataReport
from ..gemini_client import Gemini, GeminiError, Usage, list_models, pick_text_models
from ..util import StageError
from .episode import assemble, build_context
from .script_generator import repair_with_gemini, write_with_gemini
from .script_validator import validate_script
from .story_selection import rank_sectors
from .template_writer import write_template

log = logging.getLogger(__name__)


def produce_script(mb: MarketBrief, geo: Geopolitics | None, data_report: DataReport,
                   episode_date: date, settings: Settings, gemini_key: str | None,
                   recent_urls: set[str] | None = None, usage: Usage | None = None,
                   force_template: bool = False) -> tuple[dict, dict]:
    """Return (episode, report). Raises StageError if no acceptable script can be made."""
    s = settings.get_path
    usage = usage or Usage()
    ranked = rank_sectors(mb, episode_date, int(s("editorial.max_story_age_days", 5)),
                          recent_urls or set(), s("editorial.sector_order"))
    context = build_context(mb, geo, ranked, episode_date, settings)
    flags = []
    if data_report.flagged:
        flags.append("STALE DATA: market data was not refreshed today")
    report: dict = {"ranking": {t: [c.as_dict() for c in cs] for t, cs in ranked.items()},
                    "attempts": []}

    episode = None
    if not force_template:
        try:
            if not gemini_key:
                raise GeminiError("GEMINI_API_KEY is not set")
            models = pick_text_models(list_models(gemini_key), s("gemini.model") or None)
            log.info("script model candidates: %s", ", ".join(models[:6]))
            gem = Gemini(gemini_key, models, float(s("gemini.min_interval_seconds", 12)),
                         float(s("gemini.timeout_seconds", 180)),
                         int(s("gemini.max_calls_per_run", 6)), usage)
            writer = write_with_gemini(gem, context, settings)
            episode = assemble(writer, mb, geo, ranked, episode_date, settings,
                               f"gemini:{gem.model}", flags)
            val = validate_script(episode, context, settings, writer)
            report["attempts"].append({"generator": episode["generator"], **val.as_dict()})
            if not val.ok:
                log.warning("script problems, asking for one repair: %s", val.errors)
                writer = repair_with_gemini(gem, context, writer, val.errors + val.warnings[:6], settings)
                episode = assemble(writer, mb, geo, ranked, episode_date, settings,
                                   f"gemini:{gem.model}", flags)
                val = validate_script(episode, context, settings, writer)
                report["attempts"].append({"generator": episode["generator"] + " (repair)",
                                           **val.as_dict()})
            if not val.ok:
                raise GeminiError("script still invalid after repair: " + "; ".join(val.errors))
            episode["validation"] = val.as_dict()
        except Exception as e:  # noqa: BLE001 - any AI failure goes through the same policy
            log.error("Gemini script generation failed: %s", e)
            report["gemini_error"] = str(e)[:500]
            episode = None
            if s("editorial.on_gemini_failure", "template") != "template":
                raise StageError("generate_script", f"Gemini unavailable and fallback disabled: {e}") from e

    if episode is None:
        writer = write_template(mb, geo, ranked, episode_date, settings)
        flags = flags + ["TEMPLATE SCRIPT: written without AI (Gemini unavailable); review before publishing"]
        episode = assemble(writer, mb, geo, ranked, episode_date, settings, "template", flags)
        val = validate_script(episode, context, settings, writer)
        # The template only re-uses source sentences; length rules don't apply to it.
        val.errors = [e for e in val.errors if "length" not in e and "numbers not found" not in e]
        report["attempts"].append({"generator": "template", **val.as_dict()})
        if not val.ok:
            raise StageError("validate_script", "; ".join(val.errors))
        episode["validation"] = val.as_dict()

    episode["data_report"] = data_report.as_dict()
    report["usage"] = usage.as_dict()
    return episode, report
