"""Adapter tests over a synthetic local lake (real-lake fixtures are covered
by the parser fixture suite)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from analyst.adapter import LakeAdapter
from analyst.adapter.lake import _LocalStore


def build_synthetic_lake(root: Path) -> None:
    day = "2026-01-05"
    doc_dir = root / "documents/asx/2026/01/05"
    doc_dir.mkdir(parents=True)
    doc = {
        "schema_version": 1,
        "doc_id": "asx:SYNTH-001",
        "market": "asx",
        "source": "synthetic-test",
        "url": "https://example.invalid/1",
        "published_at": "2026-01-05T00:00:00Z",
        "published_date": day,
        "scraped_at": "2026-01-05T01:00:00Z",
        "company": {"ticker": "SYNTH", "exchange_qualified": "ASX:SYNTH",
                    "name": "Example Synthetic Holdings", "cik": None},
        "doc_type": "announcement",
        "form": None,
        "title": "Synthetic announcement",
        "content": {"text": "hello", "text_sha256": "x", "extraction": "html",
                    "raw_key": None, "truncated": False},
        "flags": {"is_admin_noise": False, "noise_rule": None},
        "scraper": {"repo": "test", "version": "0", "run_id": "0"},
    }
    (doc_dir / "SYNTH-001.json").write_text(json.dumps(doc))
    man_dir = root / "manifests/asx"
    man_dir.mkdir(parents=True)
    (man_dir / f"{day}.jsonl").write_text(
        json.dumps({"doc_id": "asx:SYNTH-001",
                    "key": "market-data/documents/asx/2026/01/05/SYNTH-001.json"}) + "\n"
    )
    (man_dir / f"{day}.done.json").write_text(json.dumps({"status": "ok"}))


def test_iter_new_announcements(tmp_path: Path) -> None:
    build_synthetic_lake(tmp_path)
    adapter = LakeAdapter(_LocalStore(tmp_path), markets=("asx",))
    since = datetime(2026, 1, 5, tzinfo=UTC)
    until = datetime(2026, 1, 5, tzinfo=UTC)
    anns = list(adapter.iter_new_announcements(since, until))
    assert len(anns) == 1
    ann = anns[0]
    assert ann.doc_id == "asx:SYNTH-001"
    assert ann.ticker == "SYNTH"
    assert ann.issuer_key == "asx/SYNTH"
    assert adapter.day_statuses[0].status == "ok"


def test_missing_marker_reported(tmp_path: Path) -> None:
    build_synthetic_lake(tmp_path)
    (tmp_path / "manifests/asx/2026-01-05.done.json").unlink()
    adapter = LakeAdapter(_LocalStore(tmp_path), markets=("asx",))
    anns = list(adapter.iter_day("asx", "2026-01-05"))
    assert len(anns) == 1  # still read
    assert adapter.day_statuses[0].status == "missing_marker"  # but loudly reported


def test_directory_fallback_without_manifest(tmp_path: Path) -> None:
    build_synthetic_lake(tmp_path)
    (tmp_path / "manifests/asx/2026-01-05.jsonl").unlink()
    adapter = LakeAdapter(_LocalStore(tmp_path), markets=("asx",))
    anns = list(adapter.iter_day("asx", "2026-01-05"))
    assert len(anns) == 1
