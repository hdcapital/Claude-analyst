"""Parser framework.

A parser is a callable ``(Announcement) -> ParseResult`` registered by name.
Contract (hard rules):

* returns ``PARSED`` only when validation passes — every fact carries
  provenance with a locator into the source text;
* on any failure returns ``UNPARSED`` with a reason — the pipeline then
  routes the document to the AI lane instead of dropping it;
* never invents values: everything in ``Fact.data`` is read from the
  document text or its lake metadata.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final

from ..models import Announcement, Fact, ParseOutcome, ParseResult, Provenance

ParserFn = Callable[[Announcement], ParseResult]

_REGISTRY: dict[str, ParserFn] = {}


def register(name: str) -> Callable[[ParserFn], ParserFn]:
    def deco(fn: ParserFn) -> ParserFn:
        if name in _REGISTRY:
            raise ValueError(f"duplicate parser {name}")
        _REGISTRY[name] = fn
        return fn

    return deco


def get_parser(name: str) -> ParserFn | None:
    return _REGISTRY.get(name)


def parser_names() -> list[str]:
    return sorted(_REGISTRY)


def unparsed(reason: str) -> ParseResult:
    return ParseResult(outcome=ParseOutcome.UNPARSED, reason=reason)


def parsed(*facts: Fact, escalate: bool = False) -> ParseResult:
    return ParseResult(outcome=ParseOutcome.PARSED, facts=tuple(facts), escalate=escalate)


def provenance(ann: Announcement, locator: str) -> Provenance:
    return Provenance(doc_id=ann.doc_id, published_date=ann.published_date, locator=locator)


# -- shared text helpers ------------------------------------------------------

_NUM_RE: Final = re.compile(r"-?[\d,]+(?:\.\d+)?")


def parse_number(raw: str) -> float | None:
    """'1,234,567.89' -> 1234567.89; returns None when nothing numeric."""
    if raw is None:
        return None
    text = raw.strip().replace("$", "").replace("A$", "").replace("US$", "")
    negative = text.startswith("(") and text.endswith(")")
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return -value if negative else value


def find_after(
    text: str, label_pattern: str, window: int = 200, flags: int = re.IGNORECASE
) -> tuple[str, int] | None:
    """First window of text after a label regex, plus the match offset."""
    m = re.search(label_pattern, text, flags)
    if not m:
        return None
    start = m.end()
    return text[start : start + window], m.start()


def locator_for(offset: int, length: int = 0) -> str:
    return f"chars {offset}-{offset + length}" if length else f"char {offset}"
