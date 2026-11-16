"""Read Market Brief's docs/geo.json (geopolitics tab) and normalise it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..util import http_get_json, parse_ts
from .market_brief import _num, _str


@dataclass
class Impact:
    title: str
    detail: str
    direction: str          # positive | negative | mixed
    sectors: list[str]


@dataclass
class Player:
    code: str
    name: str
    rank: int | None
    score: float | None
    relation: str = ""
    lever: str = ""
    self_view: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    impacts: list[Impact] = field(default_factory=list)
    numbers: dict[str, float] = field(default_factory=dict)
    week_line: str = ""
    week_links: list[dict] = field(default_factory=list)
    profile_as_of: datetime | None = None

    @classmethod
    def from_json(cls, p: dict) -> "Player":
        prof = p.get("profile") if isinstance(p.get("profile"), dict) else {}
        week = p.get("week") if isinstance(p.get("week"), dict) else {}
        impacts = []
        for im in prof.get("impacts") or []:
            if isinstance(im, dict) and _str(im.get("title")):
                impacts.append(Impact(_str(im.get("title")), _str(im.get("detail")),
                                      _str(im.get("direction")) or "mixed",
                                      [_str(s).upper() for s in (im.get("sectors") or []) if _str(s)]))
        links = []
        for ln in week.get("links") or []:
            if isinstance(ln, dict) and _str(ln.get("url")):
                links.append({"title": _str(ln.get("title")), "url": _str(ln.get("url")),
                              "publisher": _str(ln.get("publisher"))})
        nums = p.get("numbers") if isinstance(p.get("numbers"), dict) else {}
        rank = p.get("rank")
        return cls(
            code=_str(p.get("code")).lower(),
            name=_str(p.get("name")),
            rank=int(rank) if isinstance(rank, (int, float)) else None,
            score=_num(p.get("score")),
            relation=_str(prof.get("relation")),
            lever=_str(prof.get("lever")),
            self_view=_str(prof.get("self_view")),
            strengths=[_str(s) for s in (prof.get("strengths") or []) if _str(s)],
            weaknesses=[_str(s) for s in (prof.get("weaknesses") or []) if _str(s)],
            impacts=impacts,
            numbers={k: v for k, v in ((k, _num(v)) for k, v in nums.items()) if v is not None},
            week_line=_str(week.get("line")),
            week_links=links,
            profile_as_of=parse_ts(p.get("profile_as_of")),
        )

    def brief(self) -> dict:
        d: dict[str, Any] = {
            "code": self.code, "name": self.name, "rank": self.rank,
            "relation_to_us": self.relation, "main_lever": self.lever,
            "this_week": self.week_line,
            "this_week_sources": [{"title": l["title"], "publisher": l["publisher"]}
                                  for l in self.week_links],
            "us_trade_billion_usd": self.numbers.get("trade_b"),
            "us_trade_balance_billion_usd": self.numbers.get("balance_b"),
            "us_treasuries_held_billion_usd": self.numbers.get("treasuries_b"),
            "gdp_billion_usd": self.numbers.get("gdp_b"),
            "possible_us_impacts": [
                {"title": i.title, "detail": i.detail, "direction": i.direction, "sectors": i.sectors}
                for i in self.impacts],
            "weaknesses": self.weaknesses[:2],
        }
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class Geopolitics:
    generated_at: datetime | None
    week_updated_at: datetime | None
    profiles_updated_at: datetime | None
    players: list[Player]
    data_as_of: dict
    notices: list[str]

    def by_code(self, code: str) -> Player | None:
        return next((p for p in self.players if p.code == code), None)


def parse_geo(g: dict) -> Geopolitics:
    if not isinstance(g, dict):
        raise ValueError("geo.json is not a JSON object")
    players = [Player.from_json(p) for p in (g.get("players") or []) if isinstance(p, dict)]
    players = [p for p in players if p.code and p.name]
    players.sort(key=lambda p: p.rank if p.rank is not None else 999)
    return Geopolitics(
        generated_at=parse_ts(g.get("generated_at")),
        week_updated_at=parse_ts(g.get("week_updated_at")),
        profiles_updated_at=parse_ts(g.get("profiles_updated_at")),
        players=players,
        data_as_of=g.get("data_as_of") if isinstance(g.get("data_as_of"), dict) else {},
        notices=[_str(n) for n in (g.get("notices") or []) if _str(n)],
    )


def fetch_geo(url: str) -> Geopolitics:
    return parse_geo(http_get_json(url))
