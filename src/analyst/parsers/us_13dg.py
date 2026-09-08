"""SEC Schedule 13D / 13G (initial and amendments) — activist/major stakes.

The cover pages are labelled ("NAME OF REPORTING PERSON", "PERCENT OF CLASS
REPRESENTED BY AMOUNT IN ROW"), which survives tag-stripping. Validation:
a reporting person plus a percent in (0, 100].
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

_PERSON = re.compile(
    r"NAMES?\s+OF\s+REPORTING\s+PERSONS?[^\n]*\n+\s*([^\n]{3,100})", re.IGNORECASE
)
_PCT = re.compile(
    r"PERCENT\s+OF\s+CLASS\s+REPRESENTED\s+BY\s+AMOUNT\s+IN\s+ROW[^\n%]{0,200}?"
    r"(\d{1,3}(?:\.\d+)?)\s*%",
    re.IGNORECASE | re.DOTALL,
)
_AGGREGATE = re.compile(
    r"AGGREGATE\s+AMOUNT\s+BENEFICIALLY\s+OWNED\s+BY\s+EACH\s+REPORTING\s+PERSON"
    r"[^\n\d]{0,200}?([\d,]{3,})",
    re.IGNORECASE | re.DOTALL,
)
_PURPOSE = re.compile(r"Item\s+4[.:]?\s*Purpose of (?:the )?Transaction", re.IGNORECASE)


def _kind(form: str | None, title: str) -> str:
    joined = f"{form or ''} {title}".upper()
    schedule = "13D" if "13D" in joined else "13G"
    amended = "/A" in joined or "AMENDMENT" in joined
    return f"{schedule}{'/A' if amended else ''}"


@register("us_13dg")
def parse_us_13dg(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text:
        return unparsed("no text")

    person_m = _PERSON.search(text)
    if not person_m:
        return unparsed("reporting person not readable")
    person = person_m.group(1).strip().strip(":").strip()
    # tag-stripping can leave numbering like "1." on its own; skip such lines
    if re.fullmatch(r"[\d\s.()]+", person):
        after = text[person_m.end() : person_m.end() + 200].splitlines()
        candidates = [ln.strip() for ln in after if len(ln.strip()) > 3]
        candidates = [ln for ln in candidates if not re.fullmatch(r"[\d\s.()]+", ln)]
        if not candidates:
            return unparsed("reporting person unreadable")
        person = candidates[0][:100]

    pct_m = _PCT.search(text)
    if not pct_m:
        return unparsed("percent-of-class not readable")
    pct = float(pct_m.group(1))
    if not 0 <= pct <= 100:
        return unparsed(f"percent-of-class out of range: {pct}")

    shares_m = _AGGREGATE.search(text)
    shares = float(shares_m.group(1).replace(",", "")) if shares_m else None
    kind = _kind(ann.form, ann.title)
    data = {
        "schedule": kind,
        "reporting_person": person,
        "percent_of_class": pct,
        "aggregate_shares": shares,
        "has_purpose_section": bool(_PURPOSE.search(text)),
    }
    # a 13D (activist intent) is always worth analyst eyes; 13G is passive
    escalate = kind.startswith("13D")
    return parsed(
        Fact(
            fact_type="schedule_13dg",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {person_m.start()}+ (cover page)"),
            parser="us_13dg",
            confidence="parsed",
        ),
        escalate=escalate,
    )
