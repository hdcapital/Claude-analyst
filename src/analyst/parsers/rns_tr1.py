"""RNS TR-1 — standard form for notification of major holdings (HTML text).

Investegate serves the TR-1 template as labelled text. We read the holder,
the resulting total voting-rights percentage and, where present, the
previous percentage and the threshold-crossing reason. Validation: holder
plus a resulting percentage in (0, 100].
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

_HOLDER_LABELS = [
    r"Full name of shareholder\(?s?\)?[^\n:]*:?\s*\n?\s*([^\n]{3,120})",
    r"Name of the person\(?s?\)? subject to the\s*notification obligation[^\n:]*:?\s*\n?\s*([^\n]{3,120})",
    r"Full name of person\(s\) subject to the[^\n]*\n\s*([^\n]{3,120})",
]
_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")
_RESULTING = re.compile(
    r"(?:Resulting situation|Total of both in\s*%|otal\s*(?:of both)?\s*\(8\.A|"
    r"otal number of voting rights)[^%]{0,600}?(\d{1,3}(?:[.,]\d+)?)\s*%",
    re.IGNORECASE | re.DOTALL,
)
_PREVIOUS = re.compile(
    r"previous notification[^%]{0,400}?(\d{1,3}(?:[.,]\d+)?)\s*%", re.IGNORECASE | re.DOTALL
)


def _pct(raw: str) -> float | None:
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        return None
    return value if 0 < value <= 100 else None


@register("rns_tr1")
def parse_rns_tr1(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no text")

    holder = None
    holder_offset = -1
    for label in _HOLDER_LABELS:
        m = re.search(label, text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip().strip(":").strip()
            if len(candidate) >= 3 and not candidate.lower().startswith(("name", "full")):
                holder = candidate
                holder_offset = m.start()
                break
    if holder is None:
        return unparsed("holder name not readable")

    resulting = None
    m = _RESULTING.search(text)
    if m:
        resulting = _pct(m.group(1))
    if resulting is None:
        # fall back: last percentage in the "resulting situation" half of the doc
        half = text[len(text) // 3 :]
        pcts = [_pct(p.group(1)) for p in _PCT.finditer(half)]
        pcts = [p for p in pcts if p is not None]
        resulting = pcts[0] if pcts else None
    if resulting is None:
        return unparsed("resulting voting-rights % not readable")

    previous = None
    pm = _PREVIOUS.search(text)
    if pm:
        previous = _pct(pm.group(1))

    data = {
        "holder": holder,
        "resulting_voting_rights_pct": resulting,
        "previous_voting_rights_pct": previous,
    }
    return parsed(
        Fact(
            fact_type="major_holding",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {holder_offset}+"),
            parser="rns_tr1",
            confidence="parsed",
        )
    )
