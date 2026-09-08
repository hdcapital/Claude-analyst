"""Stage 2: strong-model situation assessment over the full document plus the
issuer's company file (cached context)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..llm import LLMClient
from ..models import Announcement
from . import prompts

log = logging.getLogger(__name__)

MAX_TOKENS = 1500

REQUIRED_KEYS = (
    "headline",
    "track",
    "memo",
    "interest_score",
    "permanent_loss_risk",
    "citations",
)


class Stage2Invalid(ValueError):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def validate_assessment(raw_text: str) -> dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw_text))
    except ValueError as exc:
        raise Stage2Invalid(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Stage2Invalid("top level is not an object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise Stage2Invalid(f"missing keys: {missing}")
    if data["track"] not in ("event", "compounder"):
        raise Stage2Invalid(f"bad track {data['track']!r}")
    for key in ("interest_score", "permanent_loss_risk"):
        try:
            value = int(data[key])
        except (TypeError, ValueError) as exc:
            raise Stage2Invalid(f"{key} not an int") from exc
        if not 0 <= value <= 10:
            raise Stage2Invalid(f"{key} out of range")
        data[key] = value
    if not isinstance(data["citations"], list):
        raise Stage2Invalid("citations must be a list")
    return data


class Stage2Assessor:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.system = prompts.stage2_system_blocks()

    def _params(self, ann: Announcement, company_file: str) -> dict[str, Any]:
        return {
            "system": self.system,
            "messages": [
                {
                    "role": "user",
                    "content": prompts.stage2_user_message(
                        doc_id=ann.doc_id,
                        title=ann.title,
                        market=ann.market,
                        ticker=ann.ticker,
                        published_date=ann.published_date,
                        text=ann.text,
                        company_file=company_file,
                    ),
                }
            ],
            "max_tokens": MAX_TOKENS,
        }

    def assess_realtime(self, ann: Announcement, company_file: str) -> dict[str, Any] | None:
        resp = self.llm.message(
            model=self.llm.deep_model,
            purpose="triage2",
            doc_id=ann.doc_id,
            **self._params(ann, company_file),
        )
        try:
            return validate_assessment(resp.text)
        except Stage2Invalid as exc:
            log.warning("stage2 invalid for %s: %s", ann.doc_id, exc)
            return None

    def assess_batch(
        self, items: list[tuple[Announcement, str]]
    ) -> dict[str, dict[str, Any] | None]:
        if not items:
            return {}
        requests = [
            {"custom_id": ann.doc_id, "params": self._params(ann, company_file)}
            for ann, company_file in items
        ]
        responses = self.llm.run_batch(
            model=self.llm.deep_model, requests=requests, purpose="triage2"
        )
        out: dict[str, dict[str, Any] | None] = {}
        for ann, _ in items:
            resp = responses.get(ann.doc_id)
            if resp is None:
                out[ann.doc_id] = None
                continue
            try:
                out[ann.doc_id] = validate_assessment(resp.text)
            except Stage2Invalid as exc:
                log.warning("stage2 invalid for %s: %s", ann.doc_id, exc)
                out[ann.doc_id] = None
        return out
