"""SEC 8-K — extract the item codes as facts.

Item codes are the 8-K's own metadata ("Item 1.03 Bankruptcy or
Receivership"). The parser records every item present. Certain items are
always escalated to the AI lane for a full read (escalation adds scrutiny;
it never culls). Validation: at least one item code found, else unparsed.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

_ITEM = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)

# Items that warrant analyst eyes regardless of what else the filing says.
ESCALATE_ITEMS = {
    "1.01",  # material definitive agreement
    "1.02",  # termination of material agreement
    "1.03",  # bankruptcy/receivership
    "2.01",  # completion of acquisition or disposition
    "2.05",  # exit/disposal costs
    "2.06",  # material impairments
    "3.01",  # delisting notice
    "4.01",  # auditor change
    "4.02",  # non-reliance on prior financials
    "5.01",  # change in control
    "5.02",  # officer/director departure or appointment
}

_ITEM_NAMES = {
    "1.01": "material definitive agreement",
    "1.02": "termination of material agreement",
    "1.03": "bankruptcy or receivership",
    "2.01": "completion of acquisition/disposition",
    "2.02": "results of operations",
    "2.03": "creation of financial obligation",
    "2.05": "exit or disposal costs",
    "2.06": "material impairments",
    "3.01": "delisting / listing-standard notice",
    "3.02": "unregistered equity sales",
    "4.01": "auditor change",
    "4.02": "non-reliance on prior financials",
    "5.01": "change in control",
    "5.02": "officer/director departure or appointment",
    "5.03": "charter/bylaw amendment",
    "5.07": "shareholder vote results",
    "7.01": "Reg FD disclosure",
    "8.01": "other events",
    "9.01": "exhibits",
}


@register("us_8k")
def parse_us_8k(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text:
        return unparsed("no text")
    head = text[:40000]
    items = sorted(set(_ITEM.findall(head)))
    if not items:
        return unparsed("no 8-K item codes found")
    first = _ITEM.search(head)
    assert first is not None
    escalate = any(item in ESCALATE_ITEMS for item in items)
    data = {
        "items": items,
        "item_names": {i: _ITEM_NAMES.get(i, "?") for i in items},
        "escalated": escalate,
    }
    return parsed(
        Fact(
            fact_type="8k_items",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {first.start()}+ (item headers)"),
            parser="us_8k",
            confidence="parsed",
        ),
        escalate=escalate,
    )
