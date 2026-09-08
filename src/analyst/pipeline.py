"""The daily run: adapter → router → lanes → facts → company files → triage.

Zero-drop invariant: every announcement read from the lake ends the run with
exactly one lane in the routing log. Deterministic parses that fail
validation are escalated to the AI lane, never discarded. Dry runs execute
everything except model calls and report what WOULD go to each stage.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import diff_lane
from .adapter import LakeAdapter
from .company import CompanyFiles
from .config import Settings
from .db import Store
from .db.schema import SituationRow, TriageResultRow
from .llm import BudgetExceeded, LLMClient
from .models import Announcement, Lane, ParseOutcome, RouteDecision, TriageVerdict
from .parsers import base as parser_base
from .router import Router
from .triage import Stage1Triage, Stage2Assessor
from .triage.prompts import load_taxonomy

log = logging.getLogger(__name__)


def situation_id_for(doc_id: str) -> str:
    return "S-" + hashlib.sha1(doc_id.encode()).hexdigest()[:10]


def summarise_fact(fact_type: str, data: dict[str, Any]) -> str:
    """One-line rendering of a fact for the company file's time series."""
    interesting = {
        k: v
        for k, v in data.items()
        if v not in (None, "", []) and k not in ("url", "is_admin_noise", "delta_preview")
    }
    parts = [f"{k}={v}" for k, v in list(interesting.items())[:8]]
    return f"{fact_type}: " + ", ".join(parts) if parts else fact_type


@dataclass
class RunReport:
    run_id: str
    dry_run: bool
    read: int = 0
    skipped_processed: int = 0
    lanes: dict[str, int] = field(default_factory=dict)
    parsed: int = 0
    unparsed_to_ai: int = 0
    diff_trivial: int = 0
    diff_forwarded: int = 0
    stage1_done: int = 0
    stage1_escalated: int = 0
    stage2_done: int = 0
    situations: int = 0
    budget_stopped: bool = False
    spend_usd: float = 0.0

    def summary(self) -> str:
        lanes = ", ".join(f"{k}={v}" for k, v in sorted(self.lanes.items()))
        return (
            f"run {self.run_id} ({'dry' if self.dry_run else 'live'}): read={self.read} "
            f"skipped={self.skipped_processed} lanes[{lanes}] parsed={self.parsed} "
            f"unparsed→AI={self.unparsed_to_ai} diff(trivial={self.diff_trivial}, "
            f"forwarded={self.diff_forwarded}) stage1={self.stage1_done} "
            f"stage2={self.stage2_done} situations={self.situations} "
            f"{'BUDGET-STOPPED ' if self.budget_stopped else ''}spend=${self.spend_usd:.4f}"
        )


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        lake: LakeAdapter,
        router: Router,
        companies: CompanyFiles,
        llm: LLMClient | None,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.store = store
        self.lake = lake
        self.router = router
        self.companies = companies
        self.llm = llm
        self.dry_run = dry_run
        taxonomy = load_taxonomy(settings.taxonomy_yaml)
        self.stage2_threshold = int(taxonomy.get("stage2_interest_threshold", 6))

    # -- lane handlers -------------------------------------------------------

    def _handle_deterministic(
        self, ann: Announcement, decision: RouteDecision, run_id: str, report: RunReport
    ) -> bool:
        """Returns True when the doc still needs AI triage."""
        assert decision.parser is not None
        parser = parser_base.get_parser(decision.parser)
        if parser is None:
            self.store.log_route(
                run_id, ann.doc_id, decision, outcome="unparsed",
                detail=f"parser {decision.parser} not implemented",
            )
            report.unparsed_to_ai += 1
            return True
        result = parser(ann)
        if result.outcome is ParseOutcome.UNPARSED:
            self.store.log_route(
                run_id, ann.doc_id, decision, outcome="unparsed", detail=result.reason
            )
            report.unparsed_to_ai += 1
            return True
        self.companies.ensure_identity(ann)
        for fact in result.facts:
            self.store.add_fact(fact)
            self.companies.append_fact(fact, summarise_fact(fact.fact_type, fact.data))
        self.store.log_route(
            run_id, ann.doc_id, decision, outcome="parsed",
            detail=f"{len(result.facts)} facts" + (" +escalate" if result.escalate else ""),
        )
        report.parsed += 1
        if result.escalate:
            report.unparsed_to_ai += 1
            return True
        return False

    def _handle_diff(
        self, ann: Announcement, decision: RouteDecision, run_id: str, report: RunReport
    ) -> tuple[bool, str | None]:
        """Returns (needs_ai, triage_text_override)."""
        assert decision.diff_group is not None
        outcome = diff_lane.run_diff(ann, decision.diff_group, self.store, self.lake)
        self.companies.ensure_identity(ann)
        fact = diff_lane.diff_fact(ann, decision.diff_group, outcome)
        self.store.add_fact(fact)
        self.companies.append_fact(
            fact,
            f"{decision.diff_group} filing: {outcome.reason} "
            f"({outcome.changed_lines}/{outcome.total_lines} lines changed)",
        )
        detail = f"{outcome.reason} changed={outcome.changed_lines}/{outcome.total_lines}"
        if outcome.trivial:
            self.store.log_route(run_id, ann.doc_id, decision, outcome="diff_trivial", detail=detail)
            report.diff_trivial += 1
            return False, None
        self.store.log_route(run_id, ann.doc_id, decision, outcome="diff_forwarded", detail=detail)
        report.diff_forwarded += 1
        override = outcome.delta_text if outcome.delta_text else None
        return True, override

    # -- triage --------------------------------------------------------------

    def _record_stage1(self, verdict: TriageVerdict) -> None:
        with self.store.session() as s:
            existing = (
                s.query(TriageResultRow)
                .filter_by(doc_id=verdict.doc_id, stage=1)
                .one_or_none()
            )
            row = existing or TriageResultRow(doc_id=verdict.doc_id, stage=1)
            row.model = verdict.model
            row.event_types = list(verdict.event_types)
            row.interest_score = verdict.interest_score
            row.permanent_loss_risk = verdict.permanent_loss_risk
            row.track = verdict.track
            row.why = verdict.why
            if existing is None:
                s.add(row)

    def _record_stage2(self, ann: Announcement, assessment: dict[str, Any], model: str) -> None:
        with self.store.session() as s:
            existing = (
                s.query(TriageResultRow).filter_by(doc_id=ann.doc_id, stage=2).one_or_none()
            )
            row = existing or TriageResultRow(doc_id=ann.doc_id, stage=2)
            row.model = model
            row.event_types = list(assessment.get("event_types") or [])
            row.interest_score = int(assessment["interest_score"])
            row.permanent_loss_risk = int(assessment["permanent_loss_risk"])
            row.track = assessment["track"]
            row.why = str(assessment.get("headline", ""))
            row.assessment = assessment
            if existing is None:
                s.add(row)

        sid = situation_id_for(ann.doc_id)
        with self.store.session() as s:
            existing_sit = s.query(SituationRow).filter_by(situation_id=sid).one_or_none()
            sit = existing_sit or SituationRow(
                situation_id=sid, first_seen_date=ann.published_date
            )
            sit.issuer_key = ann.issuer_key
            sit.track = assessment["track"]
            sit.event_types = list(assessment.get("event_types") or [])
            sit.headline = str(assessment.get("headline", ""))
            sit.memo = str(assessment.get("memo", ""))
            sit.discount_or_upside_pct = _num_or_none(assessment.get("discount_or_upside_pct"))
            sit.months_to_catalyst = _num_or_none(assessment.get("months_to_catalyst"))
            sit.catalyst_date = assessment.get("catalyst_date") or None
            sit.evidence_strength = _int_or_none(assessment.get("evidence_strength"))
            sit.interest_score = int(assessment["interest_score"])
            sit.permanent_loss_risk = int(assessment["permanent_loss_risk"])
            sit.avoidance_flags = list(assessment.get("avoidance_flags") or [])
            sit.source_doc_ids = sorted(set((sit.source_doc_ids or []) + [ann.doc_id]))
            if existing_sit is None:
                s.add(sit)

        self.companies.record_situation(
            ann.issuer_key, sid, ann.published_date, str(assessment.get("headline", "")), ann.doc_id
        )
        self.companies.append_change(
            ann.issuer_key,
            ann.published_date,
            f"stage-2 assessment: {assessment.get('headline', '')} "
            f"(interest {assessment['interest_score']}, risk {assessment['permanent_loss_risk']})",
            ann.doc_id,
        )
        thesis = assessment.get("thesis")
        if isinstance(thesis, str) and thesis.strip():
            self.companies.update_thesis(
                ann.issuer_key, thesis, ann.published_date, ann.doc_id
            )
        with self.store.session() as s:
            from .db.schema import CompanySnapshotRow

            snap = s.get(CompanySnapshotRow, ann.issuer_key) or CompanySnapshotRow(
                issuer_key=ann.issuer_key
            )
            snap.exchange = ann.exchange
            snap.ticker = ann.ticker or ""
            snap.name = ann.company_name
            if assessment.get("watchlist") is True:
                snap.watchlisted = True
            snap.snapshot = {"last_assessment": assessment, "last_doc": ann.doc_id}
            s.merge(snap)

    def _is_watchlisted(self, issuer_key: str) -> bool:
        from .db.schema import CompanySnapshotRow

        with self.store.session() as s:
            snap = s.get(CompanySnapshotRow, issuer_key)
            return bool(snap and snap.watchlisted)

    # -- main ----------------------------------------------------------------

    def run(
        self,
        since: datetime,
        until: datetime | None = None,
        limit: int | None = None,
        realtime: bool = False,
    ) -> RunReport:
        run_id = uuid.uuid4().hex[:12]
        report = RunReport(run_id=run_id, dry_run=self.dry_run)
        ai_queue: list[tuple[Announcement, str | None]] = []  # (ann, triage text override)

        for ann in self.lake.iter_new_announcements(since, until):
            if limit is not None and report.read >= limit:
                break
            report.read += 1
            if self.store.is_processed(ann.doc_id):
                report.skipped_processed += 1
                continue
            decision = self.router.route(ann)
            self.store.upsert_announcement(ann, decision)
            report.lanes[decision.lane.value] = report.lanes.get(decision.lane.value, 0) + 1

            if decision.lane is Lane.DETERMINISTIC:
                needs_ai = self._handle_deterministic(ann, decision, run_id, report)
                if needs_ai:
                    ai_queue.append((ann, None))
                else:
                    self.store.mark_processed(ann.doc_id)
            elif decision.lane is Lane.DIFF:
                needs_ai, override = self._handle_diff(ann, decision, run_id, report)
                if needs_ai:
                    ai_queue.append((ann, override))
                else:
                    self.store.mark_processed(ann.doc_id)
            else:
                self.store.log_route(run_id, ann.doc_id, decision)
                self.companies.ensure_identity(ann)
                ai_queue.append((ann, None))

        if self.dry_run or self.llm is None:
            for ann, _ in ai_queue:
                # dry runs leave the doc unprocessed so a later live run picks it up
                log.info("dry-run: %s would go to AI triage", ann.doc_id)
            report.spend_usd = self.store.total_spend_usd()
            log.info(report.summary())
            return report

        try:
            self._run_triage(ai_queue, report, realtime)
        except BudgetExceeded as exc:
            log.error("budget cap hit — stopping model calls cleanly: %s", exc)
            report.budget_stopped = True
        report.spend_usd = self.store.total_spend_usd()
        log.info(report.summary())
        return report

    def _run_triage(
        self,
        ai_queue: list[tuple[Announcement, str | None]],
        report: RunReport,
        realtime: bool,
    ) -> None:
        if not ai_queue:
            return
        assert self.llm is not None
        stage1 = Stage1Triage(self.llm, self.settings.taxonomy_yaml)
        stage2 = Stage2Assessor(self.llm)

        # Stage-1 input: headline + snippet (diff docs use their delta instead)
        prepared: list[Announcement] = []
        for ann, override in ai_queue:
            prepared.append(
                ann if override is None else _with_text(ann, override)
            )

        if realtime:
            verdicts = {a.doc_id: stage1.triage_realtime(a) for a in prepared}
        else:
            verdicts = stage1.triage_batch(prepared)

        by_id = {a.doc_id: a for a, _ in ai_queue}
        stage2_queue: list[tuple[Announcement, str]] = []
        for doc_id, verdict in verdicts.items():
            ann = by_id[doc_id]
            if verdict is None:
                # invalid twice → escalate per spec
                stage2_queue.append((ann, self.companies.render_for_prompt(ann.issuer_key)))
                report.stage1_escalated += 1
                continue
            self._record_stage1(verdict)
            report.stage1_done += 1
            escalate = verdict.interest_score >= self.stage2_threshold or (
                not ann.is_admin_noise and self._is_watchlisted(ann.issuer_key)
            )
            if escalate:
                stage2_queue.append((ann, self.companies.render_for_prompt(ann.issuer_key)))
                report.stage1_escalated += 1
            else:
                self.store.mark_processed(ann.doc_id)

        if not stage2_queue:
            return
        if realtime:
            assessments = {
                ann.doc_id: stage2.assess_realtime(ann, cf) for ann, cf in stage2_queue
            }
        else:
            assessments = stage2.assess_batch(stage2_queue)
        for ann, _cf in stage2_queue:
            assessment = assessments.get(ann.doc_id)
            if assessment is not None:
                self._record_stage2(ann, assessment, self.llm.deep_model)
                report.stage2_done += 1
                report.situations += 1
            else:
                log.warning("stage2 failed for %s — left unprocessed for re-run", ann.doc_id)
                continue
            self.store.mark_processed(ann.doc_id)


def _with_text(ann: Announcement, text: str) -> Announcement:
    from dataclasses import replace

    return replace(ann, text=text)


def _num_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
