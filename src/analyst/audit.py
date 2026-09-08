"""Audit sampler: re-check a random slice of culled/low-scored announcements
with the strong model, asking only "was this misrouted or under-scored?".

Sampled population for a day: documents routed to DETERMINISTIC or DIFF
lanes, plus AI-lane documents whose Stage-1 interest score fell below the
Stage-2 threshold. Results are logged to ``audit_samples``; the brief
reports the miss rate.
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from sqlalchemy import select

from .adapter import LakeAdapter
from .db import Store
from .db.schema import AnnouncementRow, AuditSampleRow, TriageResultRow
from .llm import LLMClient, LLMResponse

log = logging.getLogger(__name__)

MAX_TOKENS = 300
MAX_DOC_CHARS = 20000

_SYSTEM = [
    {
        "type": "text",
        "text": (
            "You audit an investment-research triage pipeline. Each document you see was "
            "culled by cheap routing rules or scored low by a first-pass model. Decide "
            "whether that was correct. Respond with STRICT JSON only: "
            '{"verdict": "ok" | "misrouted" | "under_scored", '
            '"explanation": "<one sentence naming the evidence>"} '
            '"ok" means the cull/low score was right. Flag "misrouted" when a deterministic '
            'or diff lane hid a situation needing analyst eyes; "under_scored" when the '
            "content deserves interest >= 6 for a special-situations or quality-compounder "
            "investor. Judge only from the provided text; never invent context."
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


def _candidates(store: Store, day: str, stage2_threshold: int) -> list[AnnouncementRow]:
    with store.session() as s:
        low_scored = {
            doc_id
            for (doc_id,) in s.execute(
                select(TriageResultRow.doc_id).where(
                    TriageResultRow.stage == 1,
                    TriageResultRow.interest_score < stage2_threshold,
                )
            )
        }
        rows = (
            s.execute(
                select(AnnouncementRow).where(AnnouncementRow.published_date == day)
            )
            .scalars()
            .all()
        )
    out = []
    for row in rows:
        if row.lane in ("DETERMINISTIC", "DIFF") or row.doc_id in low_scored:
            out.append(row)
    return out


def _fetch_text(lake: LakeAdapter, row: AnnouncementRow) -> str:
    y, m, d = row.published_date.split("-")
    key = f"documents/{row.market}/{y}/{m}/{d}/{row.native_id}.json"
    raw = lake.store.get_bytes(key)
    if raw is None:
        return ""
    try:
        return str((json.loads(raw.decode("utf-8")).get("content") or {}).get("text") or "")
    except (ValueError, UnicodeDecodeError):
        return ""


def _validate(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("ok", "misrouted", "under_scored"):
        return None
    return data


def run_audit(
    store: Store,
    lake: LakeAdapter,
    llm: LLMClient,
    day: str,
    sample_rate: float,
    stage2_threshold: int,
    rng: random.Random | None = None,
    realtime: bool = False,
) -> dict[str, int]:
    rng = rng or random.Random()
    candidates = _candidates(store, day, stage2_threshold)
    with store.session() as s:
        already = {
            doc_id
            for (doc_id,) in s.execute(
                select(AuditSampleRow.doc_id).where(AuditSampleRow.day == day)
            )
        }
    candidates = [c for c in candidates if c.doc_id not in already]
    k = max(1, round(len(candidates) * sample_rate)) if candidates else 0
    sample = rng.sample(candidates, min(k, len(candidates)))
    log.info("audit day=%s: %d candidates, sampling %d", day, len(candidates), len(sample))

    counts = {"ok": 0, "misrouted": 0, "under_scored": 0, "invalid": 0}
    requests: list[dict[str, Any]] = []
    rows_by_id: dict[str, AnnouncementRow] = {}
    for row in sample:
        text = _fetch_text(lake, row)
        rows_by_id[row.doc_id] = row
        requests.append(
            {
                "custom_id": row.doc_id,
                "params": {
                    "system": _SYSTEM,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Lane: {row.lane} (rule {row.route_rule})\n"
                                f"Market: {row.market} | Ticker: {row.ticker or 'unknown'}\n"
                                f"Title: {row.title}\n---\n{text[:MAX_DOC_CHARS]}"
                            ),
                        }
                    ],
                    "max_tokens": MAX_TOKENS,
                },
            }
        )
    if not requests:
        return counts
    responses: dict[str, LLMResponse]
    if realtime:
        from .llm import BudgetExceeded

        responses = {}
        for req in requests:
            custom_id = str(req["custom_id"])
            params: dict[str, Any] = dict(req["params"])
            try:
                responses[custom_id] = llm.message(
                    model=llm.deep_model,
                    purpose="audit",
                    doc_id=custom_id,
                    **params,
                )
            except BudgetExceeded as exc:
                log.warning("audit budget stop after %d samples: %s", len(responses), exc)
                break
    else:
        responses = llm.run_batch(model=llm.deep_model, requests=requests, purpose="audit")
    for doc_id, row in rows_by_id.items():
        resp = responses.get(doc_id)
        if resp is None:
            continue  # never sampled (budget stop) — not counted as invalid
        data = _validate(resp.text)
        if data is None:
            counts["invalid"] += 1
            continue
        counts[str(data["verdict"])] += 1
        with store.session() as s:
            stage1 = s.execute(
                select(TriageResultRow.interest_score).where(
                    TriageResultRow.doc_id == doc_id, TriageResultRow.stage == 1
                )
            ).scalar_one_or_none()
            s.add(
                AuditSampleRow(
                    day=day,
                    doc_id=doc_id,
                    original_lane=row.lane or "?",
                    original_score=stage1,
                    verdict=str(data["verdict"]),
                    explanation=str(data.get("explanation", "")),
                    model=llm.deep_model,
                )
            )
    log.info("audit day=%s results: %s", day, counts)
    return counts
