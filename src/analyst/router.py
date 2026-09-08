"""Route announcements into lanes by metadata only.

Rules live in config/routing.yaml (editable without touching code). A rule
matches on form type or headline pattern — never on document body text, so
culling can never be content-based. First match wins; no match → AI lane.
Every decision is returned with the rule name for the routing log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import Announcement, Lane, RouteDecision

DEFAULT_RULE = "default.unmatched"


@dataclass(frozen=True)
class _Rule:
    name: str
    lane: Lane
    parser: str | None
    diff_group: str | None
    forms: frozenset[str]
    form_prefixes: tuple[str, ...]
    title_re: re.Pattern[str] | None

    def matches(self, ann: Announcement) -> bool:
        matched = False
        if self.forms:
            if (ann.form or "").upper() not in self.forms:
                return False
            matched = True
        if self.form_prefixes:
            form = (ann.form or "").upper()
            if not any(form.startswith(p) for p in self.form_prefixes):
                return False
            matched = True
        if self.title_re is not None:
            if not self.title_re.search(ann.title or ""):
                return False
            matched = True
        return matched


class Router:
    def __init__(self, rules_by_market: dict[str, list[_Rule]]) -> None:
        self.rules_by_market = rules_by_market

    @classmethod
    def load(cls, path: Path) -> Router:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        rules_by_market: dict[str, list[_Rule]] = {}
        for market, rules in raw.items():
            compiled: list[_Rule] = []
            for spec in rules or []:
                match = spec.get("match") or {}
                lane = Lane(spec["lane"])
                parser = spec.get("parser")
                diff_group = spec.get("diff_group")
                if lane is Lane.DETERMINISTIC and not parser:
                    raise ValueError(f"rule {spec['name']}: DETERMINISTIC lane needs a parser")
                if lane is Lane.DIFF and not diff_group:
                    raise ValueError(f"rule {spec['name']}: DIFF lane needs a diff_group")
                compiled.append(
                    _Rule(
                        name=str(spec["name"]),
                        lane=lane,
                        parser=parser,
                        diff_group=diff_group,
                        forms=frozenset(str(x).upper() for x in match.get("form", [])),
                        form_prefixes=tuple(str(x).upper() for x in match.get("form_prefix", [])),
                        title_re=(
                            re.compile(match["title_regex"], re.IGNORECASE)
                            if match.get("title_regex")
                            else None
                        ),
                    )
                )
            rules_by_market[market] = compiled
        return cls(rules_by_market)

    def route(self, ann: Announcement) -> RouteDecision:
        for rule in self.rules_by_market.get(ann.market, []):
            if rule.matches(ann):
                return RouteDecision(
                    lane=rule.lane,
                    rule=rule.name,
                    parser=rule.parser,
                    diff_group=rule.diff_group,
                )
        return RouteDecision(lane=Lane.AI, rule=DEFAULT_RULE)
