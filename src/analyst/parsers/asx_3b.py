"""ASX Appendix 3B / 2A — proposed issue / application for quotation of
securities (PDF text).

These are template forms announcing new securities. We extract the number of
securities and, where stated, the issue price and purpose. Validation: a
positive securities count somewhere near its label.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parse_number, parsed, provenance, register, unparsed

_NUMBER_LABELS = [
    r"Number of\s+\+?securities\s+(?:proposed\s+)?to be issued[^\n\d]*",
    r"Total number of\s+\+?securities\s+(?:proposed\s+)?to be (?:issued|quoted)[^\n\d]*",
    r"Number of\s+\+?securities to be quoted[^\n\d]*",
    r"The\s+\+?securities to be quoted[^\n\d]*",
]
_PRICE_LABELS = [
    r"(?:issue|offer) price[^\n\d$]*",
    r"price at which the\s+\+?securities will be issued[^\n\d$]*",
]
_NUM = re.compile(r"[\d,]{3,}(?:\.\d+)?")
_PRICE = re.compile(r"(?:A?\$|AUD\s*)?(\d+(?:\.\d+)?)")


@register("asx_3b")
def parse_asx_3b(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no extractable text (image PDF?)")

    count: float | None = None
    offset = -1
    for label in _NUMBER_LABELS:
        m = re.search(label, text, re.IGNORECASE)
        if not m:
            continue
        nm = _NUM.search(text[m.end() : m.end() + 200])
        if nm:
            count = parse_number(nm.group(0))
            offset = m.start()
            break
    if count is None or count <= 0:
        return unparsed("securities count not readable")

    price: float | None = None
    for label in _PRICE_LABELS:
        m = re.search(label, text, re.IGNORECASE)
        if not m:
            continue
        pm = _PRICE.search(text[m.end() : m.end() + 120])
        if pm:
            price = float(pm.group(1))
            break

    is_2a = bool(re.search(r"appendix\s*2a", f"{ann.title}\n{text[:2000]}", re.IGNORECASE))
    data = {
        "form": "Appendix 2A" if is_2a else "Appendix 3B",
        "securities_count": count,
        "issue_price": price,
        "implied_raise": round(count * price, 2) if price else None,
    }
    return parsed(
        Fact(
            fact_type="securities_issue",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {offset}+"),
            parser="asx_3b",
            confidence="parsed",
        )
    )
