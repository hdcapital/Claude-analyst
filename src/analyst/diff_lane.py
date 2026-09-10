"""Diff lane: repeat filings are compared against the issuer's previous
same-type filing; only a non-trivial delta is forwarded to the AI lane.

The full document always remains available on demand (it is in the lake and
its identity is stored in the announcements table); the diff summary itself
is stored as a fact with provenance to BOTH documents.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .adapter import LakeAdapter
from .db import Store
from .models import Announcement, Fact, Provenance

# Delta is "non-trivial" when at least this many lines changed AND the changed
# share of the document exceeds this fraction.
MIN_CHANGED_LINES = 1  # over-read bias: any real delta is forwarded
MIN_CHANGED_FRACTION = 0.0  # over-read bias: no fraction floor
MAX_DELTA_LINES = 120  # cap what we forward to the AI lane

_WS = re.compile(r"[ \t]+")


def normalise(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = _WS.sub(" ", line.strip())
        if line:
            lines.append(line)
    return lines


@dataclass(frozen=True)
class DiffOutcome:
    trivial: bool
    changed_lines: int
    total_lines: int
    delta_text: str  # unified-diff style block of added/removed lines
    previous_doc_id: str | None
    reason: str  # first_of_type | trivial | non_trivial | previous_unreadable


def compute_diff(current: str, previous: str, previous_doc_id: str) -> DiffOutcome:
    cur = normalise(current)
    prev = normalise(previous)
    total = max(len(cur), 1)
    delta_lines: list[str] = []
    changed = 0
    for line in difflib.unified_diff(prev, cur, lineterm="", n=1):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
            if len(delta_lines) < MAX_DELTA_LINES:
                delta_lines.append(line)
    trivial = changed < MIN_CHANGED_LINES or (changed / total) < MIN_CHANGED_FRACTION
    return DiffOutcome(
        trivial=trivial,
        changed_lines=changed,
        total_lines=total,
        delta_text="\n".join(delta_lines),
        previous_doc_id=previous_doc_id,
        reason="trivial" if trivial else "non_trivial",
    )


def run_diff(ann: Announcement, diff_group: str, store: Store, lake: LakeAdapter) -> DiffOutcome:
    prev_row = store.previous_same_group(
        ann.issuer_key, diff_group, ann.published_date, ann.doc_id
    )
    if prev_row is None:
        # first filing of this type we have seen for the issuer — no baseline,
        # so the whole document goes to the AI lane (never dropped)
        return DiffOutcome(
            trivial=False,
            changed_lines=0,
            total_lines=len(normalise(ann.text)),
            delta_text="",
            previous_doc_id=None,
            reason="first_of_type",
        )
    y, m, d = prev_row.published_date.split("-")
    prev_key = f"documents/{prev_row.market}/{y}/{m}/{d}/{prev_row.native_id}.json"
    prev_bytes = lake.store.get_bytes(prev_key)
    if prev_bytes is None:
        return DiffOutcome(
            trivial=False,
            changed_lines=0,
            total_lines=len(normalise(ann.text)),
            delta_text="",
            previous_doc_id=prev_row.doc_id,
            reason="previous_unreadable",
        )
    import json

    try:
        prev_text = str((json.loads(prev_bytes.decode("utf-8")).get("content") or {}).get("text") or "")
    except (ValueError, UnicodeDecodeError):
        prev_text = ""
    if not prev_text:
        return DiffOutcome(
            trivial=False,
            changed_lines=0,
            total_lines=len(normalise(ann.text)),
            delta_text="",
            previous_doc_id=prev_row.doc_id,
            reason="previous_unreadable",
        )
    return compute_diff(ann.text, prev_text, prev_row.doc_id)


def diff_fact(ann: Announcement, diff_group: str, outcome: DiffOutcome) -> Fact:
    return Fact(
        fact_type="repeat_filing_diff",
        issuer_key=ann.issuer_key,
        data={
            "diff_group": diff_group,
            "previous_doc_id": outcome.previous_doc_id,
            "changed_lines": outcome.changed_lines,
            "total_lines": outcome.total_lines,
            "trivial": outcome.trivial,
            "reason": outcome.reason,
            "delta_preview": outcome.delta_text[:2000],
        },
        provenance=Provenance(
            doc_id=ann.doc_id,
            published_date=ann.published_date,
            locator=f"diff vs {outcome.previous_doc_id or 'none'}",
        ),
        parser="diff_lane",
        confidence="diff",
    )
