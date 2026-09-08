"""The cost guard must demonstrably stop calls at the caps."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from analyst.db import Store
from analyst.llm import BudgetExceeded, LLMClient
from tests.conftest import make_settings


class FakeUsage:
    input_tokens = 1000
    output_tokens = 200
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation = None


class FakeBlock:
    type = "text"
    text = '{"ok": true}'


class FakeResponse:
    usage = FakeUsage()
    content = [FakeBlock()]
    stop_reason = "end_turn"


def make_client(tmp_path: Any, store: Store, **overrides: Any) -> tuple[LLMClient, MagicMock]:
    settings = make_settings(tmp_path, **overrides)
    sdk = MagicMock()
    sdk.messages.create.return_value = FakeResponse()
    client = LLMClient(settings, store, sdk=sdk)
    return client, sdk


def test_call_records_spend(tmp_path: Any, store: Store) -> None:
    client, sdk = make_client(tmp_path, store)
    resp = client.message(
        model="claude-haiku-4-5",
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        purpose="test",
    )
    assert sdk.messages.create.called
    # 1000 in * $1 + 200 out * $5 per MTok
    assert resp.cost_usd == pytest.approx(0.002)
    assert store.total_spend_usd() == pytest.approx(0.002)


def test_total_cap_blocks_call(tmp_path: Any, store: Store) -> None:
    client, sdk = make_client(tmp_path, store, budget_usd_total=0.001, budget_usd_daily=2.0)
    store.record_spend(
        model="claude-haiku-4-5", purpose="prior", input_tokens=0, output_tokens=0,
        cache_write_tokens=0, cache_read_tokens=0, batch=False, cost_usd=0.001,
    )
    with pytest.raises(BudgetExceeded):
        client.message(
            model="claude-haiku-4-5",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            purpose="test",
        )
    assert not sdk.messages.create.called  # refused BEFORE the API call


def test_daily_cap_blocks_call(tmp_path: Any, store: Store) -> None:
    client, sdk = make_client(tmp_path, store, budget_usd_total=10.0, budget_usd_daily=0.0005)
    store.record_spend(
        model="claude-haiku-4-5", purpose="prior", input_tokens=0, output_tokens=0,
        cache_write_tokens=0, cache_read_tokens=0, batch=False, cost_usd=0.0005,
    )
    with pytest.raises(BudgetExceeded):
        client.message(
            model="claude-haiku-4-5",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            purpose="test",
        )
    assert not sdk.messages.create.called


def test_batch_estimate_gated_up_front(tmp_path: Any, store: Store) -> None:
    client, sdk = make_client(tmp_path, store, budget_usd_total=0.0001)
    with pytest.raises(BudgetExceeded):
        client.run_batch(
            model="claude-haiku-4-5",
            requests=[
                {
                    "custom_id": "SYNTH-001",
                    "params": {
                        "messages": [{"role": "user", "content": "x" * 100000}],
                        "max_tokens": 1000,
                    },
                }
            ],
            purpose="test",
        )
    assert not sdk.messages.batches.create.called


def test_unpriced_model_never_called(tmp_path: Any, store: Store) -> None:
    from analyst.llm.pricing import UnpricedModel

    client, sdk = make_client(tmp_path, store)
    with pytest.raises(UnpricedModel):
        client.message(
            model="claude-mystery-9",
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
            purpose="test",
        )
    assert not sdk.messages.create.called
