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

# Real Investegate TR-1 layout: section 3 is a small table —
#   "3. Details of person subject to the notification obligation
#    Name
#    <holder>"
# Section 4 ("Full name of shareholder(s)") is usually blank unless the
# shareholder differs from the notifier, so try 4 first, then 3.
_HOLDER_LABELS = [
    r"Full name of shareholder\(?s?\)?[^\n]*\n\s*Name\s*\n\s*([^\n]{3,120})",
    r"(?:person|entity) subject to the\s*\n?notification obligation[^\n]*\n\s*Name\s*\n\s*([^\n]{3,120})",
    r"notification obligation[^\n]*\n\s*Name[^\n]*\n\s*([^\n]{3,120})",
    r"Full name of shareholder\(?s?\)?[^\n:]*:?\s*\n?\s*([^\n]{3,120})",
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


_NUM_LINE = re.compile(r"^\d{1,3}(?:[.,]\d+)?$")


def _numeric_block_after(text: str, label: str, max_lines: int = 8) -> list[float]:
    """Consecutive bare-number lines following a label — the TR-1 template
    renders the 8.A/8.B/total percentages as a values block with no % signs."""
    m = re.search(label, text, re.IGNORECASE)
    if not m:
        return []
    numbers: list[float] = []
    seen_any = False
    for line in text[m.end() : m.end() + 400].splitlines():
        line = line.strip()
        if not line:
            if seen_any:
                break
            continue
        if _NUM_LINE.match(line.replace(" ", "")):
            numbers.append(float(line.replace(",", ".")))
            seen_any = True
            if len(numbers) >= max_lines:
                break
        elif seen_any:
            break
        elif len(line) > 60:
            continue  # wrapped label text
    return numbers


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

    # values block: [%8.A, %8.B, total%, total voting rights count]
    resulting = None
    block = _numeric_block_after(
        text, r"Resulting situation on the date on which(?: the)? threshold was crossed"
    )
    pct_block = [v for v in block if 0 < v <= 100]
    if len(pct_block) >= 3:
        resulting = pct_block[2]  # "Total of both in %"
    elif pct_block:
        resulting = pct_block[0]
    if resulting is None:
        m = _RESULTING.search(text)
        if m:
            resulting = _pct(m.group(1))
    if resulting is None:
        return unparsed("resulting voting-rights % not readable")

    previous = None
    prev_block = _numeric_block_after(text, r"Position of previous notification")
    prev_pcts = [v for v in prev_block if 0 < v <= 100]
    if len(prev_pcts) >= 3:
        previous = prev_pcts[2]
    elif prev_pcts:
        previous = prev_pcts[0]
    if previous is None:
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
        ),
        escalate=True,  # over-read bias: holdings crossings always get a model read
    )
