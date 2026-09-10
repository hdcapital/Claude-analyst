"""ASX Appendix 3Y — Change of Director's Interest Notice (PDF text).

Real-fixture layout (PyMuPDF text): labels and values sit on separate lines,
sometimes with template "Note:" lines between them, e.g.

    Name of Director
    GEOFFREY WILSON
    ...
    Date of change
    4 September 2026
    Number acquired
    23,183 Ordinary Shares
    Value/Consideration
    Note: If consideration is non-cash, ...
    $30,000.00

So each field is read as "the first plausible value line within a short
window after the label", skipping template noise. Validation: director name
plus date of change plus at least one quantitative field.
"""

from __future__ import annotations

import re

from ..models import Announcement, Fact, ParseResult
from .base import parse_number, parsed, provenance, register, unparsed

_NOISE_LINE = re.compile(
    r"^(note:|\+ see chapter|rule \d|introduced |amended |in the case of|"
    r"for personal use only|appendix 3y|change of director|part \d|contract|"
    r"nature of |direct or indirect|interest acquired|were the|detail of|no\.? of securities$)",
    re.IGNORECASE,
)


def _value_lines_after(text: str, label: str, max_lines: int = 6) -> tuple[list[str], int]:
    m = re.search(label, text, re.IGNORECASE)
    if not m:
        return [], -1
    lines = []
    for line in text[m.end() : m.end() + 400].splitlines():
        line = line.strip()
        if not line:
            continue
        if _NOISE_LINE.search(line):
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines, m.start()


_DATE_PATTERNS = (
    (re.compile(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})"), "dmy"),
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


def _first_number(lines: list[str]) -> float | None:
    for line in lines:
        if re.search(r"^nil\b", line, re.IGNORECASE):
            return 0.0
        if re.search(r"\d", line):
            value = parse_number(line)
            if value is not None:
                return value
    return None


def _first_date(lines: list[str]) -> str | None:
    for line in lines:
        date = normalise_date(line)
        if date:
            return date
    return None


def _name_from(lines: list[str]) -> str | None:
    for line in lines:
        if re.search(r"[A-Za-z]{2}", line) and not re.match(r"^(date|nil|n/?a)\b", line, re.I):
            return line.strip(" :").strip()
    return None


@register("asx_3y")
def parse_asx_3y(ann: Announcement) -> ParseResult:
    text = ann.text
    if not text or len(text.strip()) < 100:
        return unparsed("no extractable text (image PDF?)")

    director_lines, director_offset = _value_lines_after(text, r"Name of Director", 3)
    date_lines, _ = _value_lines_after(text, r"Date of change", 4)
    director = _name_from(director_lines)
    change_date = _first_date(date_lines)
    if not director or not change_date:
        return unparsed("director name or date-of-change not readable")

    acquired = _first_number(_value_lines_after(text, r"Number acquired", 4)[0])
    disposed = _first_number(_value_lines_after(text, r"Number disposed", 4)[0])
    value = _first_number(_value_lines_after(text, r"Value/?\s*Consideration", 5)[0])
    after = _first_number(
        _value_lines_after(text, r"No\.? of securities held after change", 4)[0]
    )
    interest_nature = _name_from(_value_lines_after(text, r"Direct or indirect interest", 2)[0])

    if acquired is None and disposed is None and after is None:
        return unparsed("no quantitative fields readable")

    data = {
        "director": director,
        "date_of_change": change_date,
        "number_acquired": acquired,
        "number_disposed": disposed,
        "value_consideration": value,
        "held_after_change": after,
        "direct_or_indirect": interest_nature,
    }
    return parsed(
        Fact(
            fact_type="director_interest_change",
            issuer_key=ann.issuer_key,
            data=data,
            provenance=provenance(ann, f"chars {director_offset}+ (form fields)"),
            parser="asx_3y",
            confidence="parsed",
        ),
        # over-read bias: every director dealing gets a cheap model read —
        # conviction buys hide among plan vestings, and Stage 1 is ~$0.003
        escalate=True,
    )
