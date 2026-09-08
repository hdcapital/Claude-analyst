"""ASX Forms 603/604/605 — substantial holder notices (PDF text).

Reads the holder's name and present/previous voting power percentages.
603 (becoming) has no previous %; 605 (ceasing) may lack a present %.
Validation: holder name plus at least one voting-power percentage in (0, 100].
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register, unparsed

_HOLDER = re.compile(
    r"Name of (?:substantial holder|holder)[^\n]*\s*:?\s*\n?([^\n]{3,120})", re.IGNORECASE
)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_VOTING_POWER = re.compile(r"[Vv]oting power")
_PREVIOUS = re.compile(r"[Pp]revious notice")
_PRESENT = re.compile(r"[Pp]resent notice")


def _form_kind(title: str, text_head: str) -> str:
    joined = f"{title}\n{text_head}".lower()
    if "603" in joined or "becoming a substantial" in joined:
        return "603_becoming"
    if "605" in joined or "ceasing to be a substantial" in joined:
        return "605_ceasing"
    return "604_change"


def _percent_near(text: str, anchor: re.Match[str], window: int = 400) -> float | None:
    chunk = text[anchor.end() : anchor.end() + window]
    for m in _PCT.finditer(chunk):
        value = float(m.group(1))
        if 0 < value <= 100:
            return value
    return None


@register("asx_substantial")
def parse_asx_substantial(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no extractable text (image PDF?)")

    holder_m = _HOLDER.search(text)
    if not holder_m:
        return unparsed("holder name label not found")
    holder = holder_m.group(1).strip().strip(":").strip()
    if len(holder) < 3:
        return unparsed("holder name unreadable")

    kind = _form_kind(ann.title, text[:2000])

    previous_pct: float | None = None
    present_pct: float | None = None
    prev_anchor = _PREVIOUS.search(text)
    pres_anchor = _PRESENT.search(text)
    if prev_anchor:
        previous_pct = _percent_near(text, prev_anchor)
    if pres_anchor:
        present_pct = _percent_near(text, pres_anchor)
    if present_pct is None and previous_pct is None:
        vp = _VOTING_POWER.search(text)
        if vp:
            present_pct = _percent_near(text, vp)
    if present_pct is None and previous_pct is None:
        return unparsed("no voting-power percentage readable")

    data = {
        "form": kind,
        "holder": holder,
        "previous_voting_power_pct": previous_pct,
        "present_voting_power_pct": present_pct,
    }
    return parsed(
        Fact(
            fact_type="substantial_holder",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {holder_m.start()}+"),
            parser="asx_substantial",
            confidence="parsed",
        )
    )
