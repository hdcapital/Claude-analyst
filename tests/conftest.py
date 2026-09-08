"""Shared test helpers.

Synthetic data only (obviously fake identifiers, SYNTH- prefix) — real
announcements live under tests/fixtures/real/ with their lake source ids.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from analyst.config import Settings
from analyst.db import Store
from analyst.models import Announcement

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict = dict(
        anthropic_api_key="SYNTH-TEST-KEY",
        ingester_path=str(tmp_path / "lake"),
        data_dir=tmp_path / "data",
        budget_usd_total=10.0,
        budget_usd_daily=2.0,
        triage_model="claude-haiku-4-5",
        deep_model="claude-sonnet-5",
        timezone=ZoneInfo("Australia/Sydney"),
        repo_root=REPO_ROOT,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_announcement(**overrides: object) -> Announcement:
    defaults: dict = dict(
        doc_id="synth:SYNTH-001",
        market="asx",
        exchange="ASX",
        native_id="SYNTH-001",
        source="synthetic-test",
        url="https://example.invalid/SYNTH-001",
        published_at="2026-01-05T00:00:00Z",
        published_date="2026-01-05",
        scraped_at="2026-01-05T01:00:00Z",
        ticker="SYNTH",
        exchange_qualified="ASX:SYNTH",
        company_name="Example Synthetic Holdings",
        cik=None,
        doc_type="announcement",
        form=None,
        title="Synthetic test announcement",
        text="",
        text_sha256=None,
        extraction="html",
        raw_key=None,
        truncated=False,
        is_admin_noise=False,
        noise_rule=None,
    )
    defaults.update(overrides)
    return Announcement(**defaults)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "test.db")
