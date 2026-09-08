"""Zero-drop sink for pure administrative paperwork.

Routing rules send known boilerplate here (proxy forms, registry changes,
total-voting-rights notices, Forms 3/5, S-8s...). The parser records the
announcement itself as a dated fact — title, form, url — so the company
file's history stays complete, and nothing is ever discarded. The audit
sampler re-checks a slice of these documents daily.
"""

from __future__ import annotations

from ..models import Announcement, Fact, ParseResult
from .base import parsed, provenance, register


@register("admin_log")
def parse_admin_log(ann: Announcement) -> ParseResult:
    return parsed(
        Fact(
            fact_type="admin_announcement",
            issuer_key=ann.issuer_key,
            data={
                "title": ann.title,
                "form": ann.form,
                "url": ann.url,
                "is_admin_noise": ann.is_admin_noise,
            },
            provenance=provenance(ann, "metadata"),
            parser="admin_log",
            confidence="parsed",
        )
    )
