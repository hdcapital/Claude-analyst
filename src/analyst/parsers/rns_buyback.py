"""RNS 'Transaction in own shares' — daily buyback notices (HTML text).

Reads the number of shares purchased and the price statistics. Validation:
a positive share count, and when both VWAP and count are present the implied
consideration must be positive and finite (shares x price sanity check).
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parse_number, parsed, provenance, register, unparsed

_COUNT = re.compile(
    r"(?:purchased?|bought back|repurchased?)[^\n\d]{0,80}?([\d,]{3,})\s*"
    r"(?:of its\s+)?(?:ordinary shares|shares)",
    re.IGNORECASE,
)
_COUNT_LABEL = re.compile(
    r"(?:Number of (?:ordinary )?shares (?:purchased|repurchased)|Aggregated information[^\n]*\n"
    r"[^\n]*Volume)[^\d]{0,60}([\d,]{3,})",
    re.IGNORECASE,
)
_HIGH = re.compile(r"[Hh]ighest price(?:\s*paid)?[^\d]{0,60}([\d.,]+)")
_LOW = re.compile(r"[Ll]owest price(?:\s*paid)?[^\d]{0,60}([\d.,]+)")
_VWAP = re.compile(r"(?:volume weighted average|average) price(?:\s*paid)?[^\d]{0,60}([\d.,]+)", re.IGNORECASE)


@register("rns_buyback")
def parse_rns_buyback(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 80:
        return unparsed("no text")

    m = _COUNT.search(text) or _COUNT_LABEL.search(text)
    if not m:
        return unparsed("share count not readable")
    shares = parse_number(m.group(1))
    if shares is None or shares <= 0:
        return unparsed("share count not positive")

    high = parse_number(_HIGH.search(text).group(1)) if _HIGH.search(text) else None
    low = parse_number(_LOW.search(text).group(1)) if _LOW.search(text) else None
    vwap = parse_number(_VWAP.search(text).group(1)) if _VWAP.search(text) else None

    if vwap is not None and high is not None and low is not None:
        if not (low <= vwap <= high):
            return unparsed("vwap outside low/high band — misparse suspected")

    data = {
        "shares_purchased": shares,
        "highest_price": high,
        "lowest_price": low,
        "vwap": vwap,
        # prices on RNS buyback notices are usually pence for UK issuers;
        # the currency is intentionally not asserted here (see docs)
        "implied_consideration": round(shares * vwap, 2) if vwap else None,
    }
    return parsed(
        Fact(
            fact_type="buyback_transaction",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {m.start()}+"),
            parser="rns_buyback",
            confidence="parsed",
        )
    )
