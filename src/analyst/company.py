"""Per-company files: one Markdown file per issuer plus a JSON sidecar.

The sidecar (``<ticker>.json``) is the structured source of truth and is
strictly append-only for history: facts, situations and changelog entries are
only ever appended (each carries its source announcement id + date), and the
one-paragraph thesis is replaced only together with an explicit "superseded"
changelog entry. The Markdown file is re-rendered from the sidecar after
every append — regeneration never loses history because the sidecar never
does. Entries are kept sorted by (date, insertion order), so a future
backfill can insert older-dated entries without breaking the format (see
BACKFILL.md).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Announcement, Fact

log = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 24000  # company file context handed to Stage 2


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class CompanyPaths:
    md: Path
    json: Path


class CompanyFiles:
    def __init__(self, companies_dir: Path) -> None:
        self.root = companies_dir

    def paths(self, issuer_key: str) -> CompanyPaths:
        market, _, symbol = issuer_key.partition("/")
        safe = "".join(c for c in symbol if c.isalnum() or c in "._-") or "UNKNOWN"
        base = self.root / market
        return CompanyPaths(md=base / f"{safe}.md", json=base / f"{safe}.json")

    # -- sidecar ------------------------------------------------------------

    def load(self, issuer_key: str) -> dict[str, Any]:
        p = self.paths(issuer_key)
        if p.json.exists():
            with open(p.json, encoding="utf-8") as f:
                return dict(json.load(f))
        return {
            "issuer_key": issuer_key,
            "identity": {},
            "facts": [],  # {date, type, summary, data, source, locator, recorded_at}
            "situations": [],  # {situation_id, date, headline, status, source}
            "changelog": [],  # {date, entry, source, recorded_at} — append-only
            "thesis": {"text": "", "updated": None},
        }

    def _save(self, issuer_key: str, doc: dict[str, Any]) -> None:
        p = self.paths(issuer_key)
        p.json.parent.mkdir(parents=True, exist_ok=True)
        # keep history ordered by date (stable for equal dates) so backfilled
        # older entries slot in cleanly
        doc["facts"].sort(key=lambda e: str(e.get("date", "")))
        doc["changelog"].sort(key=lambda e: str(e.get("date", "")))
        with open(p.json, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        p.md.write_text(self.render_markdown(doc), encoding="utf-8")

    # -- updates (append-only) ----------------------------------------------

    def ensure_identity(self, ann: Announcement) -> None:
        doc = self.load(ann.issuer_key)
        identity = doc["identity"]
        changed = False
        for key, value in (
            ("ticker", ann.ticker),
            ("exchange", ann.exchange),
            ("name", ann.company_name),
            ("cik", ann.cik),
        ):
            if value and not identity.get(key):
                identity[key] = value
                changed = True
        if changed:
            self._save(ann.issuer_key, doc)

    def append_fact(self, fact: Fact, summary: str) -> None:
        doc = self.load(fact.issuer_key)
        entry = {
            "date": fact.provenance.published_date,
            "type": fact.fact_type,
            "summary": summary,
            "data": fact.data,
            "source": fact.provenance.doc_id,
            "locator": fact.provenance.locator,
            "recorded_at": _now(),
        }
        if any(
            e["source"] == entry["source"]
            and e["type"] == entry["type"]
            and e["locator"] == entry["locator"]
            for e in doc["facts"]
        ):
            return  # idempotent re-run
        doc["facts"].append(entry)
        self._save(fact.issuer_key, doc)

    def append_change(
        self, issuer_key: str, date: str, entry: str, source: str
    ) -> None:
        doc = self.load(issuer_key)
        record = {"date": date, "entry": entry, "source": source, "recorded_at": _now()}
        if any(
            e["date"] == date and e["entry"] == entry and e["source"] == source
            for e in doc["changelog"]
        ):
            return
        doc["changelog"].append(record)
        self._save(issuer_key, doc)

    def record_situation(
        self, issuer_key: str, situation_id: str, date: str, headline: str, source: str,
        status: str = "open",
    ) -> None:
        doc = self.load(issuer_key)
        for s in doc["situations"]:
            if s["situation_id"] == situation_id:
                s["status"] = status
                self._save(issuer_key, doc)
                return
        doc["situations"].append(
            {
                "situation_id": situation_id,
                "date": date,
                "headline": headline,
                "status": status,
                "source": source,
            }
        )
        self._save(issuer_key, doc)

    def update_thesis(self, issuer_key: str, new_thesis: str, date: str, source: str) -> None:
        """Thesis replacement always leaves an explicit superseded entry."""
        doc = self.load(issuer_key)
        old = doc["thesis"]["text"]
        if old.strip() == new_thesis.strip():
            return
        if old.strip():
            doc["changelog"].append(
                {
                    "date": date,
                    "entry": f'thesis superseded (was: "{old[:300]}")',
                    "source": source,
                    "recorded_at": _now(),
                }
            )
        doc["thesis"] = {"text": new_thesis.strip(), "updated": date}
        self._save(issuer_key, doc)

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def render_markdown(doc: dict[str, Any]) -> str:
        identity = doc["identity"]
        lines: list[str] = []
        title = identity.get("name") or identity.get("ticker") or doc["issuer_key"]
        lines.append(f"# {title}")
        lines.append("")
        lines.append("## Identity")
        for key in ("ticker", "exchange", "name", "cik"):
            if identity.get(key):
                lines.append(f"- {key}: {identity[key]}")
        lines.append("")
        if doc["thesis"]["text"]:
            lines.append("## Thesis")
            lines.append(f"{doc['thesis']['text']}")
            lines.append(f"*(updated {doc['thesis']['updated']})*")
            lines.append("")
        open_situations = [s for s in doc["situations"] if s.get("status") == "open"]
        if open_situations:
            lines.append("## Open situations")
            for s in open_situations:
                lines.append(
                    f"- {s['date']} **{s['headline']}** ({s['situation_id']}) [{s['source']}]"
                )
            lines.append("")
        lines.append("## Fact time series")
        for e in doc["facts"]:
            lines.append(
                f"- {e['date']} [{e['type']}] {e['summary']} "
                f"[{e['source']} @ {e['date']}, {e['locator']}]"
            )
        lines.append("")
        lines.append("## What changed (newest first)")
        for e in reversed(doc["changelog"]):
            lines.append(f"- {e['date']}: {e['entry']} [{e['source']}]")
        lines.append("")
        return "\n".join(lines)

    def render_for_prompt(self, issuer_key: str) -> str:
        p = self.paths(issuer_key)
        if not p.md.exists():
            return ""
        text = p.md.read_text(encoding="utf-8")
        if len(text) > MAX_PROMPT_CHARS:
            # keep the head (identity/thesis/situations) and the newest tail
            head = text[: MAX_PROMPT_CHARS // 2]
            tail = text[-MAX_PROMPT_CHARS // 2 :]
            text = head + "\n[... truncated ...]\n" + tail
        return text

    def diff_since(self, issuer_key: str, since: str) -> str:
        doc = self.load(issuer_key)
        lines = [f"# {issuer_key} — changes since {since}", ""]
        facts = [e for e in doc["facts"] if e["date"] >= since]
        changes = [e for e in doc["changelog"] if e["date"] >= since]
        if facts:
            lines.append("## New facts")
            for e in facts:
                lines.append(f"- {e['date']} [{e['type']}] {e['summary']} [{e['source']}]")
            lines.append("")
        if changes:
            lines.append("## Log entries")
            for e in reversed(changes):
                lines.append(f"- {e['date']}: {e['entry']} [{e['source']}]")
        if not facts and not changes:
            lines.append("(no changes)")
        return "\n".join(lines)
