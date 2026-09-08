"""ASX Appendix 3Y — Change of Director's Interest Notice (PDF text).

The form is a fixed template; PyMuPDF extraction preserves the label lines.
We read director, date of change, securities acquired/disposed, consideration
and the closing balance. Validation: a director name plus a date of change
plus at least one quantitative field, else ``unparsed``.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import find_after, parse_number, parsed, provenance, register, unparsed

_DIRECTOR = r"Name of Director\s*:?\s*\n?([^\n]{3,80})"
_DATE_OF_CHANGE = r"Date of change\s*:?\s*\n?([^\n]{3,40})"
_ACQUIRED = r"Number acquired\s*:?\s*\n?([^\n]{0,60})"
_DISPOSED = r"Number disposed\s*:?\s*\n?([^\n]{0,60})"
_VALUE = r"Value/Consideration[^\n]*\s*:?\s*\n?([^\n]{0,80})"
_AFTER = r"No\.? of securities held after change\s*:?\s*\n?([^\n]{0,60})"
_NATURE = r"Nature of change\s*:?\s*\n?([^\n]{0,160})"

_DATE_PATTERNS = (
    (re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})"), "dmy"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
    (
        re.compile(
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})",
            re.IGNORECASE,
        ),
        "dMy",
    ),
)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ]
    )
}


def normalise_date(raw: str) -> str | None:
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(raw)
        if not m:
            continue
        if kind == "iso":
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if kind == "dmy":
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            day, month, year = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


@register("asx_3y")
def parse_asx_3y(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no extractable text (image PDF?)")

    director = find_after(text, _DIRECTOR)
    date_of_change = find_after(text, _DATE_OF_CHANGE)
    if director is None or date_of_change is None:
        return unparsed("missing director name or date-of-change label")
    director_name = director[0].strip().strip(":").strip()
    change_date = normalise_date(date_of_change[0])
    if not director_name or change_date is None:
        return unparsed("director name or change date unreadable")

    acquired = find_after(text, _ACQUIRED)
    disposed = find_after(text, _DISPOSED)
    value = find_after(text, _VALUE)
    after = find_after(text, _AFTER)
    nature = find_after(text, _NATURE)

    n_acquired = parse_number(acquired[0]) if acquired else None
    n_disposed = parse_number(disposed[0]) if disposed else None
    n_value = parse_number(value[0]) if value else None
    n_after = parse_number(after[0]) if after else None

    if n_acquired is None and n_disposed is None and n_after is None:
        return unparsed("no quantitative fields readable")

    data = {
        "director": director_name,
        "date_of_change": change_date,
        "number_acquired": n_acquired,
        "number_disposed": n_disposed,
        "value_consideration": n_value,
        "held_after_change": n_after,
        "nature_of_change": nature[0].strip()[:160] if nature else None,
    }
    return parsed(
        Fact(
            fact_type="director_interest_change",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {director[1]}-{(after or director)[1]}"),
            parser="asx_3y",
            confidence="parsed",
        )
    )
