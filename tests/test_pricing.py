from pathlib import Path

import pytest

from analyst.llm.pricing import Pricing, UnpricedModel, Usage

REPO_PRICING = Path(__file__).resolve().parents[1] / "config" / "pricing.yaml"


def test_repo_pricing_loads() -> None:
    pricing = Pricing.load(REPO_PRICING)
    assert pricing.known("claude-haiku-4-5")
    assert pricing.known("claude-sonnet-5")


def test_cost_plain() -> None:
    pricing = Pricing.load(REPO_PRICING)
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # haiku 4.5: $1 in + $5 out
    assert pricing.cost_usd("claude-haiku-4-5", usage) == pytest.approx(6.0)


def test_cost_cache_and_batch() -> None:
    pricing = Pricing.load(REPO_PRICING)
    usage = Usage(
        input_tokens=0,
        output_tokens=0,
        cache_write_tokens_5m=1_000_000,
        cache_read_tokens=1_000_000,
    )
    # write 1.25x + read 0.10x on $1/MTok input
    assert pricing.cost_usd("claude-haiku-4-5", usage) == pytest.approx(1.35)
    assert pricing.cost_usd("claude-haiku-4-5", usage, batch=True) == pytest.approx(0.675)


def test_unpriced_model_refused() -> None:
    pricing = Pricing.load(REPO_PRICING)
    with pytest.raises(UnpricedModel):
        pricing.cost_usd("claude-nonexistent-model", Usage(input_tokens=1))
    with pytest.raises(UnpricedModel):
        pricing.estimate_usd("claude-nonexistent-model", input_chars=10, max_tokens=10)


def test_estimate_is_conservative() -> None:
    pricing = Pricing.load(REPO_PRICING)
    # 3000 chars -> ~1000 tokens estimated at /3; realistic is ~750
    est = pricing.estimate_usd("claude-haiku-4-5", input_chars=3000, max_tokens=100)
    real = pricing.cost_usd("claude-haiku-4-5", Usage(input_tokens=750, output_tokens=100))
    assert est > real


def test_usage_from_api_defaults_to_5m_ttl() -> None:
    class FakeUsage:
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = 100
        cache_read_input_tokens = 7
        cache_creation = None

    usage = Usage.from_api(FakeUsage())
    assert usage.cache_write_tokens_5m == 100
    assert usage.cache_read_tokens == 7
