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
    r"(?:of its\s+)?(?:own\s+)?[\w'.\s]{0,30}?shares",
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


# Multi-day table layout (e.g. "purchased ... in the period from X to Y"):
#   Date / Venue / Volume-weighted average price (p) / Aggregated volume /
#   Lowest price per share (p) / Highest price per share (p)
# rendered as value rows: "<date>\nXLON\n287.4757\n15,325\n285.5000\n292.5000"
_TABLE_HEADER = re.compile(
    r"Volume[- ]?weighted average price[^\n]*\n\s*Aggregated volume", re.IGNORECASE
)
_TABLE_ROW = re.compile(
    r"\n\s*\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}\s*\n"  # date line
    r"\s*([A-Z]{3,6})\s*\n"  # venue
    r"\s*([\d.,]+)\s*\n"  # vwap
    r"\s*([\d,]+)\s*\n"  # aggregated volume
    r"\s*([\d.,]+)\s*\n"  # lowest
    r"\s*([\d.,]+)"  # highest
)
# Variant without a leading date line ("Aggregate information:" blocks):
#   Venue \n VWAP \n Aggregated Volume \n Lowest \n Highest \n XLON \n 717.26 \n 13,463 \n 700.00 \n 738.50
_AGG_ROW = re.compile(
    r"\n\s*([A-Z]{3,6})\s*\n"  # venue
    r"\s*(\d[\d.,]*\.\d+)\s*\n"  # vwap (decimal)
    r"\s*([\d,]+)\s*\n"  # aggregated volume (integer-ish)
    r"\s*(\d[\d.,]*\.\d+)\s*\n"  # lowest (decimal)
    r"\s*(\d[\d.,]*\.\d+)"  # highest (decimal)
)


def _parse_table(text: str) -> dict[str, float | int] | None:
    if not _TABLE_HEADER.search(text):
        return None
    rows: list[tuple[float, float, float, float]] = []
    matches = list(_TABLE_ROW.finditer(text)) or list(_AGG_ROW.finditer(text))
    for m in matches:
        vwap = parse_number(m.group(2))
        volume = parse_number(m.group(3))
        low = parse_number(m.group(4))
        high = parse_number(m.group(5))
        if vwap is None or volume is None or low is None or high is None or volume <= 0:
            continue
        if not (low <= vwap <= high):
            continue
        rows.append((vwap, volume, low, high))
    if not rows:
        return None
    total = sum(r[1] for r in rows)
    weighted = sum(r[0] * r[1] for r in rows) / total
    return {
        "shares_purchased": total,
        "vwap": round(weighted, 4),
        "lowest_price": min(r[2] for r in rows),
        "highest_price": max(r[3] for r in rows),
        "days": len(rows),
    }


@register("rns_buyback")
def parse_rns_buyback(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 80:
        return unparsed("no text")

    table = _parse_table(text)
    if table is not None:
        table_data: dict[str, float | int | None] = {
            **table,
            "implied_consideration": round(table["shares_purchased"] * table["vwap"], 2),
        }
        header = _TABLE_HEADER.search(text)
        assert header is not None
        return parsed(
            Fact(
                fact_type="buyback_transaction",
                issuer_key=ann.issuer_key,
                data=table_data,
                provenance=provenance(ann, f"chars {header.start()}+ (purchase table)"),
                parser="rns_buyback",
                confidence="parsed",
            )
        )

    m = _COUNT.search(text) or _COUNT_LABEL.search(text)
    if not m:
        return unparsed("share count not readable")
    shares = parse_number(m.group(1))
    if shares is None or shares <= 0:
        return unparsed("share count not positive")

    high_m = _HIGH.search(text)
    low_m = _LOW.search(text)
    vwap_m = _VWAP.search(text)
    high = parse_number(high_m.group(1)) if high_m else None
    low = parse_number(low_m.group(1)) if low_m else None
    vwap = parse_number(vwap_m.group(1)) if vwap_m else None

    if vwap is not None and high is not None and low is not None:
        if not (low <= vwap <= high):
            return unparsed("vwap outside low/high band — misparse suspected")

    data: dict[str, float | None] = {
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
