"""Core typed records shared across the pipeline.

The :class:`Announcement` dataclass is the adapter's output contract: one
record per lake document, carrying every field downstream code relies on.
Facts carry provenance (source announcement id + date + locator) — a fact
without provenance is a bug, so the constructor enforces it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Lane(str, enum.Enum):
    DETERMINISTIC = "DETERMINISTIC"
    DIFF = "DIFF"
    AI = "AI"


@dataclass(frozen=True)
class Announcement:
    """One announcement/filing as read from the market-ingestion lake."""

    doc_id: str  # "<market>:<native_id>", the lake's global identity
    market: str  # asx | uk | us | otc
    exchange: str  # ASX | LSE | "" (US) | OTC — display grouping
    native_id: str
    source: str
    url: str
    published_at: str  # ISO timestamp or date as stored by the ingester
    published_date: str  # YYYY-MM-DD
    scraped_at: str
    ticker: str | None
    exchange_qualified: str | None  # e.g. "ASX:ABC"
    company_name: str | None
    cik: str | None
    doc_type: str  # announcement | filing | news
    form: str | None  # SEC form type where present
    title: str
    text: str
    text_sha256: str | None
    extraction: str
    raw_key: str | None  # lake key of the raw PDF, if any
    truncated: bool
    is_admin_noise: bool
    noise_rule: str | None

    @property
    def issuer_key(self) -> str:
        """Stable per-issuer key for company files: exchange dir + symbol."""
        if self.market == "us":
            sym = self.ticker or (f"CIK{self.cik}" if self.cik else "UNKNOWN")
            return f"us/{sym}"
        return f"{self.market}/{self.ticker or 'UNKNOWN'}"


@dataclass(frozen=True)
class Provenance:
    """Where a fact came from. Every fact must carry one."""

    doc_id: str
    published_date: str
    locator: str  # human-readable pointer: "section 3", "chars 1200-1450", "item 2"

    def render(self) -> str:
        return f"[{self.doc_id} @ {self.published_date}, {self.locator}]"


@dataclass(frozen=True)
class Fact:
    """A structured extraction with mandatory provenance."""

    fact_type: str  # e.g. "director_interest_change", "quarterly_cash_flow"
    issuer_key: str
    data: dict[str, Any]
    provenance: Provenance
    parser: str  # which parser/lane produced it
    confidence: str  # "parsed" (validated deterministic) | "diff" | "ai"

    def __post_init__(self) -> None:
        if not self.provenance.doc_id or not self.provenance.published_date:
            raise ValueError("Fact without provenance (doc_id/date) is a bug")


class ParseOutcome(str, enum.Enum):
    PARSED = "parsed"
    UNPARSED = "unparsed"  # parser ran but failed validation -> AI lane


@dataclass(frozen=True)
class ParseResult:
    outcome: ParseOutcome
    facts: tuple[Fact, ...] = ()
    reason: str = ""  # why unparsed, for the routing log
    escalate: bool = False  # parser succeeded but wants AI scrutiny too (e.g. 8-K item 1.03)


@dataclass(frozen=True)
class RouteDecision:
    lane: Lane
    rule: str  # the routing rule that fired, e.g. "asx.title:appendix_3y"
    parser: str | None = None  # deterministic parser name, when lane=DETERMINISTIC
    diff_group: str | None = None  # repeat-filing group, when lane=DIFF


@dataclass(frozen=True)
class TriageVerdict:
    """Validated Stage-1 output."""

    doc_id: str
    event_types: tuple[str, ...]
    interest_score: int
    permanent_loss_risk: int
    track: str  # event | compounder | none
    why: str
    model: str
    stage: int = 1
    raw: dict[str, Any] = field(default_factory=dict)
