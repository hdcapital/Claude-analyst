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
# MAR template variant: "1. Details of the Director/ PDMR ... (a) \n Name \n <value>"
_NAME_SECTIONED = re.compile(
    r"Details of the (?:Director/?\s*)?PDMR[\s\S]{0,80}?\(a\)\s*\n\s*Name\s*:?\s*\n\s*([^\n]{3,80})",
    re.IGNORECASE,
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


# Bank-style multi-PDMR tables:
#   Name of PDMR / Position of PDMR / No. of Shares sold / Sale price / Date
#   Solange Chamberlain \n CEO, Retail \n 20,000 \n £6.9023 \n 2 September 2026
_TABLE_HEADER = re.compile(r"Name of (?:the )?PDMRs?\s*\n", re.IGNORECASE)
_TABLE_ROW = re.compile(
    r"\n([A-Z][a-zA-Z'.-]+(?: [A-Z][a-zA-Z'.-]+){1,3})\s*\n"  # person
    r"([^\n]{2,60})\s*\n"  # position
    r"([\d,]+)\s*\n"  # volume
    r"[£$€]?\s*([\d.,]+p?)\s*\n"  # price
    r"(\d{1,2} \w+ \d{4})"  # date
)


def _parse_table(text: str) -> list[dict[str, object]] | None:
    if not _TABLE_HEADER.search(text):
        return None
    rows = []
    for m in _TABLE_ROW.finditer(text):
        volume = parse_number(m.group(3))
        price = parse_number(m.group(4))
        if volume is None or volume <= 0:
            continue
        rows.append(
            {
                "person": m.group(1).strip(),
                "position": m.group(2).strip(),
                "volume": volume,
                "price": price,
                "date": m.group(5),
            }
        )
    return rows or None


@register("rns_pdmr")
def parse_rns_pdmr(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no text")

    table = _parse_table(text)
    if table:
        header = _TABLE_HEADER.search(text)
        assert header is not None
        total_volume = sum(float(str(r["volume"])) for r in table)
        data = {
            "person": table[0]["person"] if len(table) == 1 else f"{len(table)} PDMRs",
            "transactions": table,
            "volume": total_volume,
            "price": table[0]["price"] if len(table) == 1 else None,
        }
        return parsed(
            Fact(
                fact_type="pdmr_dealing",
                issuer_key=ann.issuer_key,
                data=data,
                provenance=provenance(ann, f"chars {header.start()}+ (PDMR table)"),
                parser="rns_pdmr",
                confidence="parsed",
            )
        )

    nm = _NAME.search(text) or _NAME_SECTIONED.search(text)
    if not nm:
        return unparsed("PDMR name not readable")
    name = next((g for g in nm.groups() if g), "").strip().strip(":").strip()
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
