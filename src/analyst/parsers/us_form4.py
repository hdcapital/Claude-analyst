"""SEC Form 4 — insider transactions.

The lake stores the full-submission text with tags stripped, so the Form 4
XML arrives as its values in document order. Transaction rows keep a stable
shape after stripping:

    <securityTitle> <YYYY-MM-DD> 4 <code> [0|1] <shares> [<price>] <A|D> <sharesAfter> <D|I>

We extract every such row plus the reporting owner's name (the line after
the owner CIK). Validation: at least one transaction row whose numbers are
sane (shares > 0; where a price is present, shares x price must be finite
and positive). Anything else returns unparsed and goes to the AI lane.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

# transaction row in tag-stripped XML value order (price may be absent for
# awards/gifts; an optional footnote id digit can trail the code)
_TXN = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+4\s+([A-Z])\s+(?:[01]\s+)?"
    r"([\d,]+(?:\.\d+)?)\s+(?:([\d,]+(?:\.\d+)?)\s+)?([AD])\s+"
    r"([\d,]+(?:\.\d+)?)\s+([DI])\b"
)
# owner name: the non-numeric line following a 10-digit CIK inside the
# reporting-owner block (issuer CIK comes first in the doc; skip lines that
# look like the issuer by taking the LAST cik+name pair before the first txn)
_CIK_NAME = re.compile(r"\b(\d{10})\s*\n\s*([A-Za-z][^\n]{2,80})")
_OFFICER_TITLE = re.compile(
    r"\b(?:1|true)\s+(?:1|true|0|false)\s+(?:0|false)\s*\n\s*([A-Za-z][^\n]{2,60})"
)

_CODES = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant/award",
    "D": "disposition to issuer",
    "F": "tax withholding",
    "M": "option exercise",
    "G": "gift",
    "C": "conversion",
    "X": "option exercise (in-the-money)",
}


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


@register("us_form4")
def parse_us_form4(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text:
        return unparsed("no text")

    rows = list(_TXN.finditer(text))
    if not rows:
        return unparsed("no transaction rows recognised in stripped XML")

    first_offset = rows[0].start()
    owner = None
    pairs = [(m.group(1), m.group(2).strip()) for m in _CIK_NAME.finditer(text[:first_offset])]
    # skip pairs whose "name" is actually numeric noise
    pairs = [(cik, name) for cik, name in pairs if not re.fullmatch(r"[\d\s.,/-]+", name)]
    if pairs:
        # issuer block comes first; the reporting owner is the last CIK before rows
        owner = pairs[-1][1][:80].strip()

    transactions = []
    for m in rows:
        shares = _num(m.group(3))
        price = _num(m.group(4))
        after = _num(m.group(6))
        if shares is None or shares <= 0:
            continue
        if price is not None and (price < 0 or shares * price <= 0 or shares * price > 1e12):
            continue
        transactions.append(
            {
                "date": m.group(1),
                "code": m.group(2),
                "code_meaning": _CODES.get(m.group(2), "other"),
                "shares": shares,
                "price": price,
                "acquired_disposed": m.group(5),
                "shares_after": after,
                "direct_indirect": m.group(7),
                "value": round(shares * price, 2) if price else None,
            }
        )
    if not transactions:
        return unparsed("transaction rows failed validation")

    open_market_buy = any(t["code"] == "P" for t in transactions)
    data = {
        "owner": owner,
        "transactions": transactions,
        "open_market_purchase": open_market_buy,
    }
    return parsed(
        Fact(
            fact_type="insider_transaction",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {first_offset}+ (transaction table)"),
            parser="us_form4",
            confidence="parsed",
        ),
        # open-market buys are the classic insider signal — worth AI eyes
        escalate=open_market_buy,
    )
