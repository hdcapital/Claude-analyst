"""Scoring and the daily brief.

Event track ranks by discount_or_upside / months_to_catalyst (catalyst_date
first-class); compounder track by evidence strength. Both always display
permanent_loss_risk — an item without both scores is never surfaced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from .db import Store
from .db.schema import AnnouncementRow, AuditSampleRow, RoutingLogRow, SituationRow

log = logging.getLogger(__name__)

TOP_N = 10


@dataclass(frozen=True)
class ScoredSituation:
    situation: SituationRow
    score: float
    rationale: str


def score_event(sit: SituationRow) -> ScoredSituation:
    upside = sit.discount_or_upside_pct
    months = sit.months_to_catalyst
    if upside is not None and months is not None and months > 0:
        score = upside / months
        rationale = f"{upside:.0f}% upside / {months:.1f} months"
    else:
        # interest carries the ranking when the model couldn't source numbers
        score = float(sit.interest_score)
        rationale = f"interest {sit.interest_score}/10 (upside or timing not sourced)"
    return ScoredSituation(sit, score, rationale)


def score_compounder(sit: SituationRow) -> ScoredSituation:
    evidence = sit.evidence_strength if sit.evidence_strength is not None else sit.interest_score
    return ScoredSituation(sit, float(evidence), f"evidence strength {evidence}/10")


def _fmt_situation(scored: ScoredSituation, store: Store) -> list[str]:
    sit = scored.situation
    lines = [
        f"### {sit.headline}",
        f"- **Issuer**: {sit.issuer_key} | **Situation** `{sit.situation_id}` | "
        f"**Track**: {sit.track} | **Types**: {', '.join(sit.event_types) or '—'}",
        f"- **Rank basis**: {scored.rationale} | **Interest**: {sit.interest_score}/10 | "
        f"**Permanent-loss risk**: {sit.permanent_loss_risk}/10"
        + (f" | **Catalyst**: {sit.catalyst_date}" if sit.catalyst_date else ""),
    ]
    if sit.avoidance_flags:
        lines.append(f"- **Avoidance flags**: {', '.join(sit.avoidance_flags)}")
    lines.append("")
    lines.append(sit.memo.strip())
    lines.append("")
    lines.append("Triggering announcement(s):")
    with store.session() as s:
        for doc_id in sit.source_doc_ids or []:
            ann = s.get(AnnouncementRow, doc_id)
            if ann is not None:
                lines.append(f"- `{doc_id}` {ann.published_date} — {ann.title} ({ann.url})")
            else:
                lines.append(f"- `{doc_id}`")
    lines.append("")
    return lines


def build_brief(store: Store, day: str, briefs_dir: Path, budget_total: float) -> Path:
    with store.session() as s:
        situations = (
            s.execute(
                select(SituationRow).where(
                    SituationRow.status == "open",
                    SituationRow.last_updated >= day,  # touched on/after the brief day
                )
            )
            .scalars()
            .all()
        )
        day_docs = s.execute(
            select(AnnouncementRow).where(AnnouncementRow.published_date == day)
        ).scalars().all()
        routed_ids = {
            r
            for (r,) in s.execute(
                select(RoutingLogRow.doc_id).join(
                    AnnouncementRow, AnnouncementRow.doc_id == RoutingLogRow.doc_id
                ).where(AnnouncementRow.published_date == day)
            )
        }
        audits = s.execute(
            select(AuditSampleRow).where(AuditSampleRow.day == day)
        ).scalars().all()

    event = sorted(
        (score_event(x) for x in situations if x.track == "event"),
        key=lambda sc: sc.score,
        reverse=True,
    )[:TOP_N]
    compounder = sorted(
        (score_compounder(x) for x in situations if x.track == "compounder"),
        key=lambda sc: sc.score,
        reverse=True,
    )[:TOP_N]

    lines: list[str] = [f"# Daily brief — {day}", ""]
    total = len(day_docs)
    unrouted = sum(1 for d in day_docs if d.doc_id not in routed_ids and d.lane is None)
    lines.append(
        f"Coverage: {total} announcements stored for {day}; "
        f"{total - unrouted} routed ({unrouted} unrouted — must be 0)."
    )
    lines.append("")

    lines.append("## Event track")
    lines.append("")
    if event:
        for scored in event:
            lines.extend(_fmt_situation(scored, store))
    else:
        lines.append("No event-track situations surfaced.")
        lines.append("")

    lines.append("## Compounder track")
    lines.append("")
    if compounder:
        for scored in compounder:
            lines.extend(_fmt_situation(scored, store))
    else:
        lines.append("No compounder-track situations surfaced.")
        lines.append("")

    lines.append("## Audit sampler")
    if audits:
        misses = [a for a in audits if a.verdict != "ok"]
        rate = 100.0 * len(misses) / len(audits)
        lines.append(
            f"Sampled {len(audits)} culled/low-scored announcements; "
            f"{len(misses)} flagged as misrouted/under-scored — miss rate {rate:.1f}%."
        )
        for a in misses:
            lines.append(f"- `{a.doc_id}` ({a.original_lane}): {a.verdict} — {a.explanation[:200]}")
    else:
        lines.append("No audit sample recorded for this day.")
    lines.append("")

    total_spend = store.total_spend_usd()
    day_spend = store.spend_usd_on_day(day)
    lines.append(
        f"*API spend: ${day_spend:.4f} today, ${total_spend:.4f} cumulative "
        f"(cap ${budget_total:.2f}).*"
    )
    lines.append("")

    briefs_dir.mkdir(parents=True, exist_ok=True)
    path = briefs_dir / f"{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("brief written to %s", path)
    return path
