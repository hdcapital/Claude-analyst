"""ASX Appendix 4C / 5B — quarterly cash flow report (PDF text).

Both appendices share the numbered-item layout:
  1.1 Receipts from customers            (5B: 1.1 Receipts from... sales)
  1.9 Net cash from / (used in) operating activities   (5B: 1.9 too)
  4.6 Cash and cash equivalents at end of period
  8.x Estimated quarters of funding available
Numbers are in $A'000, current quarter column first. Validation: operating
cash flow AND cash at end must both be readable, and cash-at-end must be
non-negative.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parse_number, parsed, provenance, register, unparsed

# label -> (fact key, list of regexes tried in order)
_ITEMS: dict[str, list[str]] = {
    "receipts_from_customers": [
        r"1\.1\s+Receipts from customers[^\n\d(-]*",
    ],
    "payments_total": [
        r"1\.2\s+Payments for[^\n]*",
    ],
    "operating_cash_flow": [
        r"1\.9\s+Net cash from\s*/?\s*\(?used in\)?\s*operating\s*activities[^\n\d(-]*",
        r"Net cash from\s*/\s*\(used in\)\s*operating\s*activities[^\n\d(-]*",
    ],
    "cash_at_end": [
        r"4\.6\s+Cash and cash equivalents at end of\s*(?:period|quarter)[^\n\d(-]*",
        r"5\.5\s+Cash and cash equivalents at end of\s*(?:period|quarter)[^\n\d(-]*",
    ],
    "estimated_quarters_of_funding": [
        r"8\.5\s+Estimated quarters of funding available[^\n\d(-]*",
        r"Estimated quarters of funding available[^\n\d(-]*",
    ],
}

_NUMBER_AFTER = re.compile(r"\(?-?[\d,]+(?:\.\d+)?\)?")


def _first_number_after(text: str, pattern: str) -> tuple[float | None, int]:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None, -1
    window = text[m.end() : m.end() + 120]
    nm = _NUMBER_AFTER.search(window)
    if not nm:
        return None, m.start()
    return parse_number(nm.group(0)), m.start()


@register("asx_quarterly")
def parse_asx_quarterly(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 200:
        return unparsed("no extractable text (image PDF?)")
    is_5b = bool(re.search(r"appendix\s*5b", text[:4000], re.IGNORECASE)) or bool(
        re.search(r"appendix\s*5b", ann.title, re.IGNORECASE)
    )

    values: dict[str, float | None] = {}
    first_offset = -1
    for key, patterns in _ITEMS.items():
        value: float | None = None
        for pattern in patterns:
            value, offset = _first_number_after(text, pattern)
            if value is not None:
                if first_offset < 0:
                    first_offset = offset
                break
        values[key] = value

    if values["operating_cash_flow"] is None or values["cash_at_end"] is None:
        return unparsed("operating cash flow or cash-at-end not readable")
    if values["cash_at_end"] is not None and values["cash_at_end"] < 0:
        return unparsed("cash at end negative — misparse suspected")

    data = {
        "form": "Appendix 5B" if is_5b else "Appendix 4C",
        "currency_unit": "A$'000",
        **values,
    }
    # over-read bias: short funding runway is a financing-risk needle, and an
    # unreadable runway figure is a needle we could not rule out
    quarters = values.get("estimated_quarters_of_funding")
    escalate = quarters is None or float(quarters) < 2
    return parsed(
        Fact(
            fact_type="quarterly_cash_flow",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {max(first_offset, 0)}+ (items 1.1-8.5)"),
            parser="asx_quarterly",
            confidence="parsed",
        ),
        escalate=bool(escalate),
    )
