"""ASX Forms 603/604/605 — substantial holder notices (PDF text).

Real-fixture layout: the ASIC form puts the holder under a section header,

    1.  Details of substantial holder (1)
    Name
    <holder name>

Voting power appears in the "Previous and present voting power" table (604)
or the interest table (603); 605 (ceasing) has no present voting power — its
key fields are the holder and the cessation date. Validation: a holder name
plus, for 603/604, at least one voting-power percentage in (0, 100].
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .asx_3y import normalise_date
from .base import parsed, provenance, register, unparsed

_HOLDER_SECTION = re.compile(
    r"Details of substantial holder[^\n]*\n(?:\s*\n)*\s*Name\s*\n\s*([^\n]{3,120})",
    re.IGNORECASE,
)
_HOLDER_FALLBACK = re.compile(
    r"Name of (?:substantial )?holder[^\n]*\n\s*([^\n]{3,120})", re.IGNORECASE
)
# Lawyer cover letters name the holder even when the form's table extraction
# is jumbled: "...ASIC Form 603 (Notice of initial substantial holder) issued
# by Crimson Australia in relation to..."
_COVER_LETTER = re.compile(
    r"Form 60[345][^\n]{0,120}?issued by\s+([A-Z][^\n(,]{2,80})", re.IGNORECASE
)
# The "Addresses" section table survives extraction as "Name\nAddress\n<holder>"
_ADDRESSES = re.compile(
    r"Addresses[^\n]{0,60}\n(?:[^\n]{0,120}\n){0,3}?\s*Name\s*\n\s*Address\s*\n\s*([^\n]{3,100})",
    re.IGNORECASE,
)
# One common filing package renders page-1 values away from their labels;
# the Name value then lands directly after the "Section 671B" furniture line.
_SECTION_671B = re.compile(
    r"Section 671B\s*\n\s*(?!Notice|Form|To\b|Corporations)([A-Z][^\n]{3,100})"
)
_BECAME = re.compile(
    r"became a substantial holder on\s*:?\s*\n?\s*([^\n]{4,30})", re.IGNORECASE
)
_LABELY = re.compile(
    r"^(acn|arsn|abn|the holder|name of|address|company name|\(if applicable\)|"
    r"details of|class of|nil|n/?a)\b",
    re.IGNORECASE,
)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_VOTING_POWER = re.compile(r"[Vv]oting power[^\n]*")
_CEASED = re.compile(
    r"ceased to be a substantial holder on\s*\n?\s*([^\n]{4,30})", re.IGNORECASE
)


def _form_kind(title: str, text_head: str) -> str:
    joined = f"{title}\n{text_head}".lower()
    if (
        "form 603" in joined
        or "notice of initial substantial holder" in joined
        or "becoming a substantial" in joined
    ):
        return "603_becoming"
    if "form 605" in joined or "ceasing to be a substantial" in joined:
        return "605_ceasing"
    return "604_change"


def _percents_near_voting_power(text: str) -> list[float]:
    out: list[float] = []
    for anchor in _VOTING_POWER.finditer(text):
        chunk = text[anchor.end() : anchor.end() + 300]
        for m in _PCT.finditer(chunk):
            value = float(m.group(1))
            if 0 < value <= 100:
                out.append(value)
        if len(out) >= 2:
            break
    return out


_NZ_HOLDER = re.compile(r"Full Name\(?s?\)?:\s*([^\n]{3,100})")
_NZ_PRESENT = re.compile(
    r"For this disclosure[\s\S]{0,500}?Total percentage held in class:\s*([\d.]+)\s*%",
    re.IGNORECASE,
)
_NZ_PREVIOUS = re.compile(
    r"For last disclosure[\s\S]{0,600}?Total percentage held in class:\s*([\d.]+)\s*%",
    re.IGNORECASE,
)


def _parse_nzx(ann: Announcement, text: str) -> ParseResult | None:
    if "substantial product holder" not in text[:2500].lower():
        return None
    holder_m = _NZ_HOLDER.search(text)
    present_m = _NZ_PRESENT.search(text)
    if not holder_m or not present_m:
        return unparsed("NZX disclosure: holder or percentage not readable")
    previous_m = _NZ_PREVIOUS.search(text)
    data = {
        "form": "nzx_substantial_product_holder",
        "holder": holder_m.group(1).strip(),
        "previous_voting_power_pct": float(previous_m.group(1)) if previous_m else None,
        "present_voting_power_pct": float(present_m.group(1)),
    }
    return parsed(
        Fact(
            fact_type="substantial_holder",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {holder_m.start()}+ (NZX disclosure)"),
            parser="asx_substantial",
            confidence="parsed",
        ),
        escalate=True,  # over-read bias: stake events always get a model read
    )


@register("asx_substantial")
def parse_asx_substantial(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no extractable text (image PDF?)")

    # NZX "substantial product holder" disclosures (dual-listed issuers) use
    # their own clean template — handle it first.
    nz = _parse_nzx(ann, text)
    if nz is not None:
        return nz

    holder = None
    holder_offset = -1
    for pattern in (_HOLDER_SECTION, _HOLDER_FALLBACK, _COVER_LETTER, _ADDRESSES, _SECTION_671B):
        m = pattern.search(text)
        if not m:
            continue
        candidate = m.group(1).strip().strip(":").strip()
        if len(candidate) >= 3 and not _LABELY.match(candidate):
            holder = candidate
            holder_offset = m.start()
            break
    if holder is None:
        return unparsed("holder name not readable")

    kind = _form_kind(ann.title, text[:3000])

    if kind == "605_ceasing":
        ceased_m = _CEASED.search(text)
        ceased = normalise_date(ceased_m.group(1)) if ceased_m else None
        data = {
            "form": kind,
            "holder": holder,
            "ceased_on": ceased,
            "previous_voting_power_pct": None,
            "present_voting_power_pct": None,
        }
        return parsed(
            Fact(
                fact_type="substantial_holder",
                issuer_key=ann.issuer_key,
                data=data,
                provenance=provenance(ann, f"chars {holder_offset}+ (form 605)"),
                parser="asx_substantial",
                confidence="parsed",
            ),
            escalate=True,  # over-read bias: stake events always get a model read
        )

    pcts = _percents_near_voting_power(text)
    became_m = _BECAME.search(text)
    became = normalise_date(became_m.group(1)) if became_m else None
    pcts_seen: list[float] = []
    if not pcts:
        # a Form 603's existence already means >= 5%: holder + date is a
        # complete "became substantial holder" fact. For 604s from filing
        # packages that detach the table values from their labels, record the
        # percentage tokens present in the notice WITHOUT asserting which is
        # previous vs present — honest, and never a wrong assignment.
        pcts_seen = sorted(
            {
                float(m.group(1))
                for m in _PCT.finditer(text)
                if 0 < float(m.group(1)) < 100
            }
        )
        if (kind != "603_becoming" or became is None) and not pcts_seen:
            return unparsed("no voting-power percentage readable")
    # 604 tables list previous then present; 603 has a single figure
    previous_pct = pcts[0] if kind == "604_change" and len(pcts) >= 2 else None
    present_pct = (
        (pcts[1] if kind == "604_change" and len(pcts) >= 2 else pcts[0]) if pcts else None
    )

    data = {
        "form": kind,
        "holder": holder,
        "became_on": became,
        "previous_voting_power_pct": previous_pct,
        "present_voting_power_pct": present_pct,
    }
    if pcts_seen:
        data["voting_power_pcts_seen_unordered"] = pcts_seen
    return parsed(
        Fact(
            fact_type="substantial_holder",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {holder_offset}+ (voting power table)"),
            parser="asx_substantial",
            confidence="parsed",
        ),
        escalate=True,  # over-read bias: stake events always get a model read
    )
