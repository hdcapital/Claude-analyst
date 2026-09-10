"""SEC 8-K — extract the item codes as facts.

Item codes are the 8-K's own metadata ("Item 1.03 Bankruptcy or
Receivership"). The parser records every item present, and every filing is
escalated to the AI lane for a full read unless its only item is on the
short routine list (escalation adds scrutiny; it never culls).
Validation: at least one item code found, else unparsed.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

_ITEM = re.compile(r"\bItem\s+(\d{1,2}\.\d{2})\b", re.IGNORECASE)

# The SGML header lists items by NAME ("ITEM INFORMATION: Departure of
# Directors..."), and on real fixtures the stripped body often carries only
# XBRL noise — the header lines are the reliable source. Names below are the
# SEC's own item titles (matched on distinctive prefixes).
_ITEM_INFO = re.compile(r"ITEM INFORMATION:\s*([^\n]+)")
_NAME_TO_CODE = [
    ("entry into a material definitive agreement", "1.01"),
    ("termination of a material definitive agreement", "1.02"),
    ("bankruptcy or receivership", "1.03"),
    ("mine safety", "1.04"),
    ("material cybersecurity incidents", "1.05"),
    ("completion of acquisition or disposition", "2.01"),
    ("results of operations and financial condition", "2.02"),
    ("creation of a direct financial obligation", "2.03"),
    ("triggering events that accelerate", "2.04"),
    ("costs associated with exit or disposal", "2.05"),
    ("material impairments", "2.06"),
    ("notice of delisting", "3.01"),
    ("unregistered sales of equity securities", "3.02"),
    ("material modification to rights", "3.03"),
    ("changes in registrant's certifying accountant", "4.01"),
    ("changes in registrant", "4.01"),
    ("non-reliance on previously issued financial statements", "4.02"),
    ("changes in control of registrant", "5.01"),
    ("departure of directors or certain officers", "5.02"),
    ("election of directors", "5.02"),
    ("amendments to articles of incorporation", "5.03"),
    ("temporary suspension of trading", "5.04"),
    ("amendment to registrant's code of ethics", "5.05"),
    ("change in shell company status", "5.06"),
    ("submission of matters to a vote", "5.07"),
    ("shareholder director nominations", "5.08"),
    ("regulation fd disclosure", "7.01"),
    ("other events", "8.01"),
    ("financial statements and exhibits", "9.01"),
]


def _codes_from_header(text_head: str) -> list[str]:
    codes = []
    for m in _ITEM_INFO.finditer(text_head):
        name = m.group(1).strip().lower()
        for prefix, code in _NAME_TO_CODE:
            if name.startswith(prefix):
                codes.append(code)
                break
    return codes

# Over-read bias (2026-09-10, user-directed): every 8-K gets a model read
# UNLESS its only item is on this routine list. 8-Ks are a news form, and
# needles hide in "8.01 Other Events" and 7.01 Reg-FD disclosures; the item
# codes are still recorded as facts either way.
ROUTINE_ITEMS = {
    "9.01",  # financial statements and exhibits (a bare exhibit index)
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
    header_items = _codes_from_header(head)
    body_items = _ITEM.findall(head)
    items = sorted(set(header_items) | set(body_items))
    if not items:
        return unparsed("no 8-K item codes found")
    first = _ITEM_INFO.search(head) or _ITEM.search(head)
    assert first is not None
    escalate = any(item not in ROUTINE_ITEMS for item in items)
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
