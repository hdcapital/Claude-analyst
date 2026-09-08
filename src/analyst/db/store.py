"""Store: one object owning the SQLite engine + sessions.

Single-process by design (the daily run is a cron job). All writes go
through upsert-style helpers so re-running a day never duplicates.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ..models import Announcement, Fact, RouteDecision
from . import schema
from .schema import (
    AnnouncementRow,
    Base,
    MetaRow,
    RoutingLogRow,
    SpendLogRow,
)

log = logging.getLogger(__name__)

# Additive migration steps, applied in order above the stored version.
# Each entry: (version, [SQL statements]). Version 1 is create_all.
MIGRATIONS: list[tuple[int, list[str]]] = []


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}")
        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        with self.session() as s:
            row = s.get(MetaRow, "schema_version")
            current = int(row.value) if row else 0
            for version, statements in MIGRATIONS:
                if version > current:
                    from sqlalchemy import text

                    for stmt in statements:
                        s.execute(text(stmt))
                    current = version
            if row is None:
                s.add(MetaRow(key="schema_version", value=str(max(schema.SCHEMA_VERSION, current))))
            else:
                row.value = str(max(schema.SCHEMA_VERSION, current))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # -- announcements ------------------------------------------------------

    def upsert_announcement(self, ann: Announcement, decision: RouteDecision | None = None) -> bool:
        """Insert or refresh; returns True if the row was new."""
        with self.session() as s:
            row = s.get(AnnouncementRow, ann.doc_id)
            new = row is None
            if row is None:
                row = AnnouncementRow(doc_id=ann.doc_id)
                s.add(row)
            row.market = ann.market
            row.exchange = ann.exchange
            row.native_id = ann.native_id
            row.source = ann.source
            row.url = ann.url
            row.published_at = ann.published_at
            row.published_date = ann.published_date
            row.scraped_at = ann.scraped_at
            row.ticker = ann.ticker
            row.exchange_qualified = ann.exchange_qualified
            row.company_name = ann.company_name
            row.cik = ann.cik
            row.doc_type = ann.doc_type
            row.form = ann.form
            row.title = ann.title
            row.text_chars = len(ann.text)
            row.text_sha256 = ann.text_sha256
            row.extraction = ann.extraction
            row.raw_key = ann.raw_key
            row.truncated = ann.truncated
            row.is_admin_noise = ann.is_admin_noise
            row.noise_rule = ann.noise_rule
            row.issuer_key = ann.issuer_key
            if decision is not None:
                row.lane = decision.lane.value
                row.route_rule = decision.rule
                row.diff_group = decision.diff_group
            return new

    def previous_same_group(
        self, issuer_key: str, diff_group: str, before_date: str, before_doc_id: str
    ) -> AnnouncementRow | None:
        """The issuer's most recent earlier filing in the same diff group."""
        with self.session() as s:
            return s.execute(
                select(AnnouncementRow)
                .where(
                    AnnouncementRow.issuer_key == issuer_key,
                    AnnouncementRow.diff_group == diff_group,
                    AnnouncementRow.published_date <= before_date,
                    AnnouncementRow.doc_id != before_doc_id,
                )
                .order_by(AnnouncementRow.published_date.desc(), AnnouncementRow.scraped_at.desc())
                .limit(1)
            ).scalar_one_or_none()

    def mark_processed(self, doc_id: str) -> None:
        with self.session() as s:
            row = s.get(AnnouncementRow, doc_id)
            if row is not None:
                row.processed_at = schema.utcnow()

    def is_processed(self, doc_id: str) -> bool:
        with self.session() as s:
            row = s.get(AnnouncementRow, doc_id)
            return bool(row is not None and row.processed_at)

    # -- routing log --------------------------------------------------------

    def log_route(
        self,
        run_id: str,
        doc_id: str,
        decision: RouteDecision,
        outcome: str = "routed",
        detail: str = "",
    ) -> None:
        with self.session() as s:
            s.add(
                RoutingLogRow(
                    run_id=run_id,
                    doc_id=doc_id,
                    lane=decision.lane.value,
                    rule=decision.rule,
                    parser=decision.parser,
                    outcome=outcome,
                    detail=detail,
                )
            )

    # -- facts --------------------------------------------------------------

    def add_fact(self, fact: Fact) -> bool:
        """Idempotent insert; returns False when the identical fact identity exists."""
        from .schema import FactRow

        with self.session() as s:
            existing = s.execute(
                select(FactRow).where(
                    FactRow.source_doc_id == fact.provenance.doc_id,
                    FactRow.fact_type == fact.fact_type,
                    FactRow.source_locator == fact.provenance.locator,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return False
            s.add(
                FactRow(
                    fact_type=fact.fact_type,
                    issuer_key=fact.issuer_key,
                    data=fact.data,
                    source_doc_id=fact.provenance.doc_id,
                    source_date=fact.provenance.published_date,
                    source_locator=fact.provenance.locator,
                    parser=fact.parser,
                    confidence=fact.confidence,
                )
            )
            return True

    # -- spend --------------------------------------------------------------

    def record_spend(
        self,
        *,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int,
        cache_read_tokens: int,
        batch: bool,
        cost_usd: float,
        doc_id: str | None = None,
    ) -> None:
        with self.session() as s:
            s.add(
                SpendLogRow(
                    day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    model=model,
                    purpose=purpose,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_write_tokens=cache_write_tokens,
                    cache_read_tokens=cache_read_tokens,
                    batch=batch,
                    cost_usd=cost_usd,
                    doc_id=doc_id,
                )
            )

    def total_spend_usd(self) -> float:
        with self.session() as s:
            value = s.execute(select(func.coalesce(func.sum(SpendLogRow.cost_usd), 0.0))).scalar()
            return float(value or 0.0)

    def spend_usd_on_day(self, day: str) -> float:
        with self.session() as s:
            value = s.execute(
                select(func.coalesce(func.sum(SpendLogRow.cost_usd), 0.0)).where(
                    SpendLogRow.day == day
                )
            ).scalar()
            return float(value or 0.0)

    def spend_breakdown(self) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.execute(
                select(
                    SpendLogRow.day,
                    SpendLogRow.model,
                    SpendLogRow.purpose,
                    func.count(),
                    func.sum(SpendLogRow.cost_usd),
                    func.sum(SpendLogRow.input_tokens),
                    func.sum(SpendLogRow.output_tokens),
                    func.sum(SpendLogRow.cache_read_tokens),
                )
                .group_by(SpendLogRow.day, SpendLogRow.model, SpendLogRow.purpose)
                .order_by(SpendLogRow.day)
            ).all()
        return [
            {
                "day": r[0],
                "model": r[1],
                "purpose": r[2],
                "calls": int(r[3]),
                "cost_usd": round(float(r[4] or 0.0), 6),
                "input_tokens": int(r[5] or 0),
                "output_tokens": int(r[6] or 0),
                "cache_read_tokens": int(r[7] or 0),
            }
            for r in rows
        ]
