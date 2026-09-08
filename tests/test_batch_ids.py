"""Batch custom_id sanitization: lake doc_ids ("market:native") must be
mapped to the API's ^[a-zA-Z0-9_-]{1,64}$ alphabet and back."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from analyst.db import Store
from analyst.llm import LLMClient
from tests.conftest import make_settings


class FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation = None


def _batch_result(custom_id: str) -> Any:
    message = SimpleNamespace(
        usage=FakeUsage(),
        content=[SimpleNamespace(type="text", text="{}")],
        stop_reason="end_turn",
    )
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


def test_batch_ids_round_trip(tmp_path: Any, store: Store) -> None:
    settings = make_settings(tmp_path)
    sdk = MagicMock()
    captured: dict[str, Any] = {}

    def create(requests: list[dict[str, Any]]) -> Any:
        captured["requests"] = requests
        return SimpleNamespace(id="batch_1", processing_status="ended")

    sdk.messages.batches.create.side_effect = create
    sdk.messages.batches.results.side_effect = lambda _id: [
        _batch_result(r["custom_id"]) for r in captured["requests"]
    ]
    client = LLMClient(settings, store, sdk=sdk)
    out = client.run_batch(
        model="claude-haiku-4-5",
        requests=[
            {
                "custom_id": "asx:0ee282389225a52f3d7b4907c6d25c26",
                "params": {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 10},
            }
        ],
        purpose="test",
    )
    sent_id = captured["requests"][0]["custom_id"]
    assert ":" not in sent_id and len(sent_id) <= 64
    # results come back keyed by the ORIGINAL doc_id
    assert "asx:0ee282389225a52f3d7b4907c6d25c26" in out
