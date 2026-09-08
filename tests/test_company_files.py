from pathlib import Path

from analyst.company import CompanyFiles
from analyst.models import Fact, Provenance
from tests.conftest import make_announcement


def make_fact(doc_id: str = "synth:SYNTH-001", date: str = "2026-01-05") -> Fact:
    return Fact(
        fact_type="synthetic_test",
        issuer_key="asx/SYNTH",
        data={"value": 42},
        provenance=Provenance(doc_id, date, "chars 0-10"),
        parser="test",
        confidence="parsed",
    )


def test_append_only_history(tmp_path: Path) -> None:
    companies = CompanyFiles(tmp_path)
    ann = make_announcement()
    companies.ensure_identity(ann)
    companies.append_fact(make_fact(), "value=42")
    companies.append_change("asx/SYNTH", "2026-01-05", "first event", "synth:SYNTH-001")
    companies.append_change("asx/SYNTH", "2026-01-06", "second event", "synth:SYNTH-002")

    doc = companies.load("asx/SYNTH")
    assert len(doc["changelog"]) == 2
    md = companies.paths("asx/SYNTH").md.read_text()
    assert "first event" in md and "second event" in md
    # newest first in the what-changed section
    assert md.index("second event") < md.index("first event")
    # provenance rendered on the fact line
    assert "[synth:SYNTH-001 @ 2026-01-05, chars 0-10]" in md


def test_fact_append_idempotent(tmp_path: Path) -> None:
    companies = CompanyFiles(tmp_path)
    companies.append_fact(make_fact(), "value=42")
    companies.append_fact(make_fact(), "value=42")
    assert len(companies.load("asx/SYNTH")["facts"]) == 1


def test_thesis_supersede_leaves_log_entry(tmp_path: Path) -> None:
    companies = CompanyFiles(tmp_path)
    companies.update_thesis("asx/SYNTH", "Original synthetic thesis.", "2026-01-05", "synth:SYNTH-001")
    doc = companies.load("asx/SYNTH")
    assert doc["thesis"]["text"] == "Original synthetic thesis."
    assert not any("superseded" in e["entry"] for e in doc["changelog"])

    companies.update_thesis("asx/SYNTH", "Replacement synthetic thesis.", "2026-02-01", "synth:SYNTH-002")
    doc = companies.load("asx/SYNTH")
    assert doc["thesis"]["text"] == "Replacement synthetic thesis."
    superseded = [e for e in doc["changelog"] if "superseded" in e["entry"]]
    assert len(superseded) == 1
    assert "Original synthetic thesis." in superseded[0]["entry"]


def test_backfill_order(tmp_path: Path) -> None:
    """Older-dated entries inserted later still sort into place."""
    companies = CompanyFiles(tmp_path)
    companies.append_fact(make_fact("synth:SYNTH-NEW", "2026-03-01"), "newer")
    companies.append_fact(make_fact("synth:SYNTH-OLD", "2026-01-01"), "older backfilled")
    facts = companies.load("asx/SYNTH")["facts"]
    assert [f["date"] for f in facts] == ["2026-01-01", "2026-03-01"]


def test_diff_since(tmp_path: Path) -> None:
    companies = CompanyFiles(tmp_path)
    companies.append_change("asx/SYNTH", "2026-01-05", "old entry", "synth:SYNTH-001")
    companies.append_change("asx/SYNTH", "2026-03-05", "new entry", "synth:SYNTH-002")
    out = companies.diff_since("asx/SYNTH", "2026-02-01")
    assert "new entry" in out and "old entry" not in out
