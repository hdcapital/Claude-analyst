"""Stage 1: cheap-model triage over headline + opening text.

Validates the strict-JSON contract; one retry on validation failure, then the
announcement escalates to Stage 2 (never dropped). Batch API by default; a
realtime path for same-day runs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..llm import LLMClient, LLMResponse
from ..models import Announcement, TriageVerdict
from . import prompts

log = logging.getLogger(__name__)

MAX_TOKENS = 300


class Stage1Invalid(ValueError):
    """Model output failed the strict-JSON contract."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def validate_verdict(
    doc_id: str, raw_text: str, taxonomy: dict[str, Any], model: str
) -> TriageVerdict:
    try:
        data = json.loads(_strip_fences(raw_text))
    except ValueError as exc:
        raise Stage1Invalid(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Stage1Invalid("top level is not an object")
    allowed = set(taxonomy.get("event_track", [])) | set(taxonomy.get("compounder_track", []))
    event_types = data.get("event_types")
    if not isinstance(event_types, list) or not all(isinstance(t, str) for t in event_types):
        raise Stage1Invalid("event_types must be a list of strings")
    unknown = [t for t in event_types if t not in allowed]
    if unknown:
        raise Stage1Invalid(f"event_types outside taxonomy: {unknown}")
    try:
        interest = int(data["interest_score"])
        risk = int(data["permanent_loss_risk"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage1Invalid("interest_score/permanent_loss_risk must be integers") from exc
    if not (0 <= interest <= 10 and 0 <= risk <= 10):
        raise Stage1Invalid("scores out of 0-10 range")
    track = data.get("track")
    if track not in ("event", "compounder", "none"):
        raise Stage1Invalid(f"bad track {track!r}")
    why = data.get("why")
    if not isinstance(why, str) or not why.strip():
        raise Stage1Invalid("why must be a non-empty string")
    return TriageVerdict(
        doc_id=doc_id,
        event_types=tuple(event_types),
        interest_score=interest,
        permanent_loss_risk=risk,
        track=track,
        why=why.strip(),
        model=model,
        stage=1,
        raw=data,
    )


class Stage1Triage:
    def __init__(self, llm: LLMClient, taxonomy_path: Path) -> None:
        self.llm = llm
        self.taxonomy = prompts.load_taxonomy(taxonomy_path)
        self.system = prompts.stage1_system_blocks(self.taxonomy)

    def _params(self, ann: Announcement) -> dict[str, Any]:
        return {
            "system": self.system,
            "messages": [
                {
                    "role": "user",
                    "content": prompts.stage1_user_message(
                        ann.title, ann.market, ann.ticker, ann.text
                    ),
                }
            ],
            "max_tokens": MAX_TOKENS,
        }

    def _to_verdict(self, ann: Announcement, resp: LLMResponse) -> TriageVerdict | None:
        try:
            return validate_verdict(ann.doc_id, resp.text, self.taxonomy, resp.model)
        except Stage1Invalid as exc:
            log.warning("stage1 invalid for %s: %s", ann.doc_id, exc)
            return None

    def triage_realtime(self, ann: Announcement) -> TriageVerdict | None:
        """One call, one retry on invalid JSON; None = escalate to Stage 2."""
        model = self.llm.triage_model
        for attempt in (1, 2):
            resp = self.llm.message(
                model=model, purpose="triage1", doc_id=ann.doc_id, **self._params(ann)
            )
            verdict = self._to_verdict(ann, resp)
            if verdict is not None:
                return verdict
            log.info("stage1 retry %d for %s", attempt, ann.doc_id)
        return None

    def triage_batch(self, anns: list[Announcement]) -> dict[str, TriageVerdict | None]:
        """Batch the whole set; retry invalid ones once in a second batch.

        Result maps doc_id -> verdict (None = escalate to Stage 2).
        """
        if not anns:
            return {}
        model = self.llm.triage_model
        by_id = {a.doc_id: a for a in anns}
        results: dict[str, TriageVerdict | None] = dict.fromkeys(by_id, None)

        pending = list(anns)
        for round_no in (1, 2):
            if not pending:
                break
            requests = [
                {"custom_id": a.doc_id, "params": self._params(a)} for a in pending
            ]
            responses = self.llm.run_batch(model=model, requests=requests, purpose="triage1")
            next_pending: list[Announcement] = []
            for ann in pending:
                resp = responses.get(ann.doc_id)
                verdict = self._to_verdict(ann, resp) if resp is not None else None
                if verdict is not None:
                    results[ann.doc_id] = verdict
                else:
                    next_pending.append(ann)
            pending = next_pending
            if pending:
                log.info("stage1 batch round %d: %d invalid/missing", round_no, len(pending))
        return results
