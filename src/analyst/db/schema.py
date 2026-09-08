"""SQLite schema (SQLAlchemy 2.0 declarative).

Migration policy (documented in README): the schema is versioned by
``SCHEMA_VERSION`` in a ``meta`` table. ``Store.__init__`` runs
``create_all`` (new tables appear automatically) and applies any additive
migration steps in ``store.MIGRATIONS`` keyed by version. SQLite +
single-process keeps this deliberately simpler than Alembic.

Idempotency: every per-announcement table keys on ``doc_id`` (the lake's
global identity), so re-running a day upserts instead of duplicating.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(DeclarativeBase):
    pass


class MetaRow(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


class AnnouncementRow(Base):
    """Mirror of the adapter output plus the routing decision."""

    __tablename__ = "announcements"

    doc_id: Mapped[str] = mapped_column(String, primary_key=True)
    market: Mapped[str] = mapped_column(String, index=True)
    exchange: Mapped[str] = mapped_column(String)
    native_id: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[str] = mapped_column(String)
    published_date: Mapped[str] = mapped_column(String, index=True)
    scraped_at: Mapped[str] = mapped_column(String)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    exchange_qualified: Mapped[str | None] = mapped_column(String, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    cik: Mapped[str | None] = mapped_column(String, nullable=True)
    doc_type: Mapped[str] = mapped_column(String)
    form: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    text_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction: Mapped[str] = mapped_column(String)
    raw_key: Mapped[str | None] = mapped_column(String, nullable=True)
    truncated: Mapped[bool] = mapped_column(default=False)
    is_admin_noise: Mapped[bool] = mapped_column(default=False)
    noise_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    lane: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    route_rule: Mapped[str | None] = mapped_column(String, nullable=True)
    diff_group: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    processed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer_key: Mapped[str] = mapped_column(String, index=True, default="")


class RoutingLogRow(Base):
    """One row per routing decision per run — nothing is silently dropped."""

    __tablename__ = "routing_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    doc_id: Mapped[str] = mapped_column(String, index=True)
    lane: Mapped[str] = mapped_column(String)
    rule: Mapped[str] = mapped_column(String)
    parser: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, default="routed")  # routed|parsed|unparsed|...
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[str] = mapped_column(String, default=utcnow)

    __table_args__ = (Index("ix_routing_log_doc_run", "doc_id", "run_id"),)


class FactRow(Base):
    """Structured extraction with mandatory provenance."""

    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fact_type: Mapped[str] = mapped_column(String, index=True)
    issuer_key: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_doc_id: Mapped[str] = mapped_column(String, ForeignKey("announcements.doc_id"), index=True)
    source_date: Mapped[str] = mapped_column(String)
    source_locator: Mapped[str] = mapped_column(String)
    parser: Mapped[str] = mapped_column(String)
    confidence: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)

    __table_args__ = (
        # idempotency: one fact of a given type+locator per source document
        Index("ux_fact_identity", "source_doc_id", "fact_type", "source_locator", unique=True),
    )


class TriageResultRow(Base):
    __tablename__ = "triage_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String, ForeignKey("announcements.doc_id"), index=True)
    stage: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    interest_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    permanent_loss_risk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track: Mapped[str | None] = mapped_column(String, nullable=True)
    why: Mapped[str] = mapped_column(Text, default="")
    assessment: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # stage 2
    created_at: Mapped[str] = mapped_column(String, default=utcnow)

    __table_args__ = (Index("ux_triage_doc_stage", "doc_id", "stage", unique=True),)


class SituationRow(Base):
    """A scored situation surfaced by Stage 2 — what the brief ranks."""

    __tablename__ = "situations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    situation_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    issuer_key: Mapped[str] = mapped_column(String, index=True)
    track: Mapped[str] = mapped_column(String)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    headline: Mapped[str] = mapped_column(Text)
    memo: Mapped[str] = mapped_column(Text)
    discount_or_upside_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    months_to_catalyst: Mapped[float | None] = mapped_column(Float, nullable=True)
    catalyst_date: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_strength: Mapped[int | None] = mapped_column(Integer, nullable=True)  # compounder 0-10
    interest_score: Mapped[int] = mapped_column(Integer)
    permanent_loss_risk: Mapped[int] = mapped_column(Integer)
    avoidance_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_doc_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_seen_date: Mapped[str] = mapped_column(String, index=True)
    last_updated: Mapped[str] = mapped_column(String, default=utcnow)
    status: Mapped[str] = mapped_column(String, default="open")  # open|closed|superseded


class CompanySnapshotRow(Base):
    """Latest structured state per issuer (mirrors the JSON sidecar)."""

    __tablename__ = "company_snapshots"

    issuer_key: Mapped[str] = mapped_column(String, primary_key=True)
    exchange: Mapped[str] = mapped_column(String)
    ticker: Mapped[str] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    watchlisted: Mapped[bool] = mapped_column(default=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow)


class SpendLogRow(Base):
    """One row per API call (or batch item) with priced usage."""

    __tablename__ = "spend_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String, default=utcnow, index=True)
    day: Mapped[str] = mapped_column(String, index=True)  # YYYY-MM-DD UTC
    model: Mapped[str] = mapped_column(String)
    purpose: Mapped[str] = mapped_column(String)  # triage1|triage2|audit|smoke|...
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    batch: Mapped[bool] = mapped_column(default=False)
    cost_usd: Mapped[float] = mapped_column(Float)
    doc_id: Mapped[str | None] = mapped_column(String, nullable=True)


class AuditSampleRow(Base):
    __tablename__ = "audit_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String, index=True)
    doc_id: Mapped[str] = mapped_column(String, ForeignKey("announcements.doc_id"))
    original_lane: Mapped[str] = mapped_column(String)
    original_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str] = mapped_column(String)  # ok | misrouted | under_scored
    explanation: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)

    __table_args__ = (Index("ux_audit_doc_day", "doc_id", "day", unique=True),)


class RatingRow(Base):
    """User feedback on situations, kept for future few-shot prompts."""

    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    situation_id: Mapped[str] = mapped_column(String, index=True)
    score: Mapped[int] = mapped_column(Integer)  # 1..5
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class LabelRow(Base):
    """Ground-truth labels for the eval harness (populated later from a real
    price feed supplied by the user — never fabricated)."""

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)  # interesting | not_interesting
    source: Mapped[str] = mapped_column(String)  # who/what produced the label
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String, default=utcnow)

    __table_args__ = (Index("ux_label_doc_source", "doc_id", "source", unique=True),)
