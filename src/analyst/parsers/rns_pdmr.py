"""RNS PDMR dealing notification (EU MAR article 19 template, HTML text).

Reads the PDMR's name, position, transaction nature, price and volume.
Validation: a name plus at least one of price/volume.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parse_number, parsed, provenance, register, unparsed

_NAME = re.compile(
    r"(?:Details of the person discharging managerial responsibilities[^\n]*\n"
    r"(?:[^\n]*\n){0,3}?\s*(?:a\)|Name)\s*:?\s*\n?\s*([^\n]{3,80})"
    r"|^\s*a\)\s*Name\s*:?\s*\n?\s*([^\n]{3,80}))",
    re.IGNORECASE | re.MULTILINE,
)
_POSITION = re.compile(r"Position/status\s*:?\s*\n?\s*([^\n]{2,80})", re.IGNORECASE)
_NATURE = re.compile(
    r"Nature of the transaction\s*:?\s*\n?\s*([^\n]{3,160})", re.IGNORECASE
)
_PRICE = re.compile(
    r"Price\(?s?\)?(?:\s*and\s*volume\(?s?\)?)?\s*:?\s*\n?[^\d]{0,40}([\d.,]+)", re.IGNORECASE
)
_VOLUME = re.compile(
    r"(?:Aggregated\s+)?[Vv]olume\(?s?\)?\s*:?\s*\n?[^\d]{0,40}([\d.,]+)"
)


@register("rns_pdmr")
def parse_rns_pdmr(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no text")

    nm = _NAME.search(text)
    if not nm:
        return unparsed("PDMR name not readable")
    name = (nm.group(1) or nm.group(2) or "").strip().strip(":").strip()
    if len(name) < 3:
        return unparsed("PDMR name unreadable")

    position_m = _POSITION.search(text)
    nature_m = _NATURE.search(text)
    price_m = _PRICE.search(text)
    volume_m = _VOLUME.search(text)
    price = parse_number(price_m.group(1)) if price_m else None
    volume = parse_number(volume_m.group(1)) if volume_m else None
    if price is None and volume is None:
        return unparsed("neither price nor volume readable")

    data = {
        "person": name,
        "position": position_m.group(1).strip() if position_m else None,
        "nature": nature_m.group(1).strip() if nature_m else None,
        "price": price,
        "volume": volume,
        "value": round(price * volume, 2) if price and volume else None,
    }
    return parsed(
        Fact(
            fact_type="pdmr_dealing",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {nm.start()}+"),
            parser="rns_pdmr",
            confidence="parsed",
        )
    )
