import copy
import json

import pytest

from src.data.market_brief import SECTOR_ORDER
from src.editorial.episode import assemble, build_context
from src.editorial.pipeline import produce_script
from src.editorial.script_validator import allowed_numbers, check_numbers, validate_script
from src.editorial.story_selection import default_pick, largest_amount, rank_sectors
from src.editorial.template_writer import write_template
from src.gemini_client import Gemini, QuotaExhausted
from src.util import StageError

from .conftest import EPISODE_DATE


def test_exactly_one_story_per_sector(template_episode):
    secs = [s for s in template_episode["segments"] if s["type"] == "sector"]
    assert [s["sector"] for s in secs] == SECTOR_ORDER
    for s in secs:
        assert s["story"] is None or s["story"]["url"].startswith("http")
    ids = [s["story"]["id"] for s in secs if s["story"]]
    assert len(ids) == len(set(ids))


def test_ranking_prefers_consequential_news(mb):
    ranked = rank_sectors(mb, EPISODE_DATE)
    top = default_pick(ranked["XLK"]).story
    assert "Nvidia" in top.headline                     # CEO exit beats routine items
    xlp = [c.story.headline for c in ranked["XLP"]]
    assert "Sells $1.47 Million" not in xlp[0]         # insider sale is never the lead


def test_recent_story_is_penalised(mb):
    first = default_pick(rank_sectors(mb, EPISODE_DATE)["XLK"]).story
    again = default_pick(rank_sectors(mb, EPISODE_DATE, recent_urls={first.url})["XLK"]).story
    assert again.url != first.url


def test_old_stories_mean_no_standout(mb, settings, geo, data_report):
    from datetime import date
    ranked = rank_sectors(mb, date(2026, 12, 31), max_age_days=5)
    assert all(default_pick(c) is None for c in ranked.values())
    w = write_template(mb, geo, ranked, date(2026, 12, 31), settings)
    assert all(s["story_id"] is None and "no standout" in s["narration"] for s in w["segments"]["sectors"])


def test_money_parser():
    assert largest_amount("lost $1.6 trillion, fee of C$102M and €805 million") == pytest.approx(1.6e12)


def test_template_script_is_valid_and_close_to_10_minutes(template_episode):
    assert template_episode["generator"] == "template"
    assert any("TEMPLATE" in f for f in template_episode["flags"])
    assert 7 <= template_episode["estimated_minutes"] <= 13
    assert template_episode["validation"]["unverified_numbers"] == []


def _context(mb, geo, settings):
    return build_context(mb, geo, rank_sectors(mb, EPISODE_DATE), EPISODE_DATE, settings)


def test_validator_catches_invented_numbers(template_episode, mb, geo, settings):
    ep = copy.deepcopy(template_episode)
    ep["segments"][3]["narration"] += " Revenue jumped 87.3 percent to $912 billion."
    res = validate_script(ep, _context(mb, geo, settings), settings)
    assert any("87.3" in u for u in res.unverified_numbers)
    assert any("912" in u for u in res.unverified_numbers)


def test_number_checker_accepts_rounding():
    allowed = allowed_numbers({"x": 2.0134, "cap": 3932339044352})
    assert check_numbers("down 2.01 percent, about 2 percent, 2.0", allowed) == []
    assert check_numbers("worth $3.9 trillion", allowed) == []
    assert check_numbers("up 7.77 percent", allowed) == ["7.77"]


def test_validator_catches_advice_missing_signoff_and_sector(template_episode, mb, geo, settings):
    ep = copy.deepcopy(template_episode)
    ep["segments"][-1]["narration"] = "You should buy Nvidia now."
    ep["segments"] = [s for s in ep["segments"] if s.get("sector") != "XLE"]
    res = validate_script(ep, _context(mb, geo, settings), settings)
    errs = " ".join(res.errors)
    assert "sign-off" in errs and "investment advice" in errs and "sector list" in errs


def test_invalid_story_id_from_model_is_an_error(mb, geo, settings, data_report):
    ranked = rank_sectors(mb, EPISODE_DATE)
    w = write_template(mb, geo, ranked, EPISODE_DATE, settings)
    w["segments"]["sectors"][0]["story_id"] = "made-up-id"
    ep = assemble(w, mb, geo, ranked, EPISODE_DATE, settings, "gemini:test")
    res = validate_script(ep, build_context(mb, geo, ranked, EPISODE_DATE, settings), settings, w)
    assert any("not one of this sector's candidates" in e for e in res.errors)


def test_urls_come_from_data_not_the_model(mb, geo, settings):
    ranked = rank_sectors(mb, EPISODE_DATE)
    w = write_template(mb, geo, ranked, EPISODE_DATE, settings)
    w["segments"]["sectors"][0]["url"] = "https://evil.example/fake"   # ignored
    ep = assemble(w, mb, geo, ranked, EPISODE_DATE, settings, "x")
    real = {s.url for s in mb.stories["XLK"].stories}
    assert ep["segments"][2]["story"]["url"] in real


def test_gemini_path_with_mocked_model(mb, geo, data_report, settings, monkeypatch):
    """A well-formed model answer becomes a validated Gemini episode (no network)."""
    ranked = rank_sectors(mb, EPISODE_DATE)
    answer = write_template(mb, geo, ranked, EPISODE_DATE, settings)
    monkeypatch.setattr("src.editorial.pipeline.list_models", lambda key: ["gemini-9.9-flash"])
    monkeypatch.setattr(Gemini, "generate_text", lambda self, *a, **k: json.dumps(answer))
    monkeypatch.setattr(Gemini, "__init__", lambda self, *a, **k: setattr(self, "model", "gemini-9.9-flash"))
    ep, rep = produce_script(mb, geo, data_report, EPISODE_DATE, settings, "dummy-key")
    assert ep["generator"] == "gemini:gemini-9.9-flash"
    assert not any("TEMPLATE" in f for f in ep["flags"])


def test_quota_exhausted_falls_back_safely(mb, geo, data_report, settings, monkeypatch):
    monkeypatch.setattr("src.editorial.pipeline.list_models", lambda key: ["gemini-9.9-flash"])

    def boom(self, *a, **k):
        raise QuotaExhausted("429 quota=GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    monkeypatch.setattr(Gemini, "generate_text", boom)
    ep, rep = produce_script(mb, geo, data_report, EPISODE_DATE, settings, "dummy-key")
    assert ep["generator"] == "template" and "429" in rep["gemini_error"]
    settings.set_path("editorial.on_gemini_failure", "fail")
    with pytest.raises(StageError):
        produce_script(mb, geo, data_report, EPISODE_DATE, settings, "dummy-key")


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text, self.headers = code, body, json.dumps(body), {}

    def json(self):
        return self._body


def test_gemini_client_drops_models_on_daily_quota(monkeypatch):
    import src.gemini_client as gc
    body = {"error": {"code": 429, "message": "quota", "details": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]}}
    monkeypatch.setattr(gc.HTTP, "post", lambda *a, **k: _Resp(429, body))
    g = gc.Gemini("k", ["m1", "m2"], min_interval=0, max_calls=10)
    with pytest.raises(QuotaExhausted):
        g.generate_text("hi")
    assert set(g.usage.failed) == {"m1", "m2"} and "PerDay" in g.usage.rate_limits["m1"]


def test_gemini_client_call_cap(monkeypatch):
    import src.gemini_client as gc
    ok = {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    monkeypatch.setattr(gc.HTTP, "post", lambda *a, **k: _Resp(200, ok))
    g = gc.Gemini("k", ["m1"], min_interval=0, max_calls=1)
    g.generate_text("a")
    with pytest.raises(QuotaExhausted):
        g.generate_text("b")
