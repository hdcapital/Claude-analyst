"""Cost-guarded client over the Anthropic SDK.

Every call path (realtime and batch) goes through the same gate:

  1. estimate a conservative ceiling for the call;
  2. refuse with :class:`BudgetExceeded` if cumulative-or-daily spend plus the
     estimate would breach either cap;
  3. make the call, price the *actual* usage from pricing.yaml, persist it to
     ``spend_log``, and log model/tokens/cache/cost.

There is deliberately no bypass flag.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic

from ..config import Settings
from ..db import Store
from .pricing import Pricing, Usage

log = logging.getLogger(__name__)

# Documented fallbacks if the live models endpoint is unreachable; the current
# cheapest Haiku-class and Sonnet-class IDs per Anthropic's model table
# (cached 2026-06-24, see config/pricing.yaml header for sources).
FALLBACK_TRIAGE_MODEL = "claude-haiku-4-5"
FALLBACK_DEEP_MODEL = "claude-sonnet-5"

BATCH_POLL_SECONDS = 30.0


class BudgetExceeded(RuntimeError):
    """The call would take spend over the total or daily cap."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: Usage
    cost_usd: float
    stop_reason: str | None
    raw: Any = field(repr=False, default=None)


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        pricing: Pricing | None = None,
        sdk: anthropic.Anthropic | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.pricing = pricing or Pricing.load(settings.pricing_yaml)
        self.sdk = sdk or anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._triage_model: str | None = settings.triage_model or None
        self._deep_model: str | None = settings.deep_model or None

    # -- model resolution ---------------------------------------------------

    def _resolve_models(self) -> None:
        """Pick current cheapest-Haiku / Sonnet from the live models endpoint.

        Only models that pricing.yaml can price are eligible. Falls back to
        the documented defaults when the endpoint can't be reached.
        """
        try:
            listed = [m.id for m in self.sdk.models.list()]
        except Exception as exc:
            log.warning("models endpoint unavailable (%s); using documented fallbacks", exc)
            self._triage_model = self._triage_model or FALLBACK_TRIAGE_MODEL
            self._deep_model = self._deep_model or FALLBACK_DEEP_MODEL
            return

        def newest(family: str) -> str | None:
            # listed is newest-first per the API; keep only priceable IDs
            for mid in listed:
                if mid.startswith(f"claude-{family}") and self.pricing.known(mid):
                    return mid
            return None

        if self._triage_model is None:
            self._triage_model = newest("haiku") or FALLBACK_TRIAGE_MODEL
        if self._deep_model is None:
            self._deep_model = newest("sonnet") or FALLBACK_DEEP_MODEL
        log.info("models resolved: triage=%s deep=%s", self._triage_model, self._deep_model)

    @property
    def triage_model(self) -> str:
        if self._triage_model is None:
            self._resolve_models()
        assert self._triage_model is not None
        return self._triage_model

    @property
    def deep_model(self) -> str:
        if self._deep_model is None:
            self._resolve_models()
        assert self._deep_model is not None
        return self._deep_model

    # -- budget gate --------------------------------------------------------

    def spent_total(self) -> float:
        return self.store.total_spend_usd()

    def spent_today(self) -> float:
        return self.store.spend_usd_on_day(_utc_day())

    def _gate(self, estimate_usd: float) -> None:
        total = self.spent_total()
        today = self.spent_today()
        if total + estimate_usd > self.settings.budget_usd_total:
            raise BudgetExceeded(
                f"call estimate ${estimate_usd:.4f} would take total spend "
                f"${total:.4f} past the ${self.settings.budget_usd_total:.2f} cap"
            )
        if today + estimate_usd > self.settings.budget_usd_daily:
            raise BudgetExceeded(
                f"call estimate ${estimate_usd:.4f} would take today's spend "
                f"${today:.4f} past the ${self.settings.budget_usd_daily:.2f} daily cap"
            )

    def _settle(
        self,
        *,
        model: str,
        purpose: str,
        usage: Usage,
        batch: bool,
        doc_id: str | None,
    ) -> float:
        cost = self.pricing.cost_usd(model, usage, batch=batch)
        self.store.record_spend(
            model=model,
            purpose=purpose,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            batch=batch,
            cost_usd=cost,
            doc_id=doc_id,
        )
        log.info(
            "llm_call model=%s purpose=%s in=%d out=%d cache_write=%d cache_read=%d "
            "batch=%s cost_usd=%.6f total_usd=%.4f",
            model,
            purpose,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_write_tokens,
            usage.cache_read_tokens,
            batch,
            cost,
            self.spent_total(),
        )
        return cost

    @staticmethod
    def _sum_chars(system: list[dict[str, Any]] | str | None, messages: list[dict[str, Any]]) -> int:
        def block_chars(content: Any) -> int:
            if isinstance(content, str):
                return len(content)
            if isinstance(content, list):
                return sum(len(str(b.get("text", ""))) for b in content if isinstance(b, dict))
            return len(str(content))

        total = block_chars(system) if system else 0
        for m in messages:
            total += block_chars(m.get("content"))
        return total

    # -- realtime call ------------------------------------------------------

    def message(
        self,
        *,
        model: str,
        system: list[dict[str, Any]] | str | None,
        messages: list[dict[str, Any]],
        max_tokens: int,
        purpose: str,
        doc_id: str | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        estimate = self.pricing.estimate_usd(
            model,
            input_chars=self._sum_chars(system, messages),
            max_tokens=max_tokens,
        )
        self._gate(estimate)
        kwargs: dict[str, Any] = {}
        if system is not None:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self.sdk.messages.create(
            model=model, max_tokens=max_tokens, messages=messages, **kwargs  # type: ignore[arg-type]
        )
        usage = Usage.from_api(response.usage)
        cost = self._settle(model=model, purpose=purpose, usage=usage, batch=False, doc_id=doc_id)
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            model=model,
            usage=usage,
            cost_usd=cost,
            stop_reason=response.stop_reason,
            raw=response,
        )

    # -- batch call ---------------------------------------------------------

    def run_batch(
        self,
        *,
        model: str,
        requests: list[dict[str, Any]],
        purpose: str,
        poll_seconds: float = BATCH_POLL_SECONDS,
        timeout_seconds: float = 24 * 3600.0,
    ) -> dict[str, LLMResponse]:
        """Submit a Message Batch, wait for it, price every result.

        ``requests``: [{"custom_id": ..., "params": {system?, messages, max_tokens, ...}}].
        Returns {custom_id: LLMResponse} for succeeded items; errored items are
        logged and omitted (callers treat missing ids as retryable).
        """
        if not requests:
            return {}
        total_chars = sum(
            self._sum_chars(r["params"].get("system"), r["params"]["messages"]) for r in requests
        )
        max_out = sum(int(r["params"].get("max_tokens", 1024)) for r in requests)
        estimate = self.pricing.estimate_usd(
            model, input_chars=total_chars, max_tokens=max_out, batch=True
        )
        self._gate(estimate)

        api_requests = [
            {"custom_id": r["custom_id"], "params": {"model": model, **r["params"]}}
            for r in requests
        ]
        batch = self.sdk.messages.batches.create(requests=api_requests)  # type: ignore[arg-type]
        log.info("batch %s submitted: %d requests (est ceiling $%.4f)", batch.id, len(requests), estimate)

        deadline = time.monotonic() + timeout_seconds
        while batch.processing_status != "ended":
            if time.monotonic() > deadline:
                raise TimeoutError(f"batch {batch.id} still {batch.processing_status} at timeout")
            time.sleep(poll_seconds)
            batch = self.sdk.messages.batches.retrieve(batch.id)

        out: dict[str, LLMResponse] = {}
        for item in self.sdk.messages.batches.results(batch.id):
            if item.result.type != "succeeded":
                log.warning("batch item %s: %s", item.custom_id, item.result.type)
                continue
            message = item.result.message
            usage = Usage.from_api(message.usage)
            cost = self._settle(
                model=model, purpose=purpose, usage=usage, batch=True, doc_id=item.custom_id
            )
            text = "".join(block.text for block in message.content if block.type == "text")
            out[item.custom_id] = LLMResponse(
                text=text,
                model=model,
                usage=usage,
                cost_usd=cost,
                stop_reason=message.stop_reason,
                raw=message,
            )
        return out
