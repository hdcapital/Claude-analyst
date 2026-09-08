"""Price API usage from config/pricing.yaml.

The YAML is the single source of truth; a model absent from it cannot be
called (the guard refuses to estimate a price for it), so adding a model is
a config edit, never a code edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class UnpricedModel(RuntimeError):
    """Model not present in pricing.yaml — refuse to call it blind."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens_5m: int = 0
    cache_write_tokens_1h: int = 0
    cache_read_tokens: int = 0

    @property
    def cache_write_tokens(self) -> int:
        return self.cache_write_tokens_5m + self.cache_write_tokens_1h

    @classmethod
    def from_api(cls, usage: Any) -> Usage:
        """Build from an SDK usage object (or any duck-typed equivalent)."""
        creation_5m = 0
        creation_1h = 0
        breakdown = getattr(usage, "cache_creation", None)
        if breakdown is not None:
            creation_5m = int(getattr(breakdown, "ephemeral_5m_input_tokens", 0) or 0)
            creation_1h = int(getattr(breakdown, "ephemeral_1h_input_tokens", 0) or 0)
        total_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if creation_5m + creation_1h == 0 and total_creation:
            creation_5m = total_creation  # no breakdown -> assume default 5m TTL
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_write_tokens_5m=creation_5m,
            cache_write_tokens_1h=creation_1h,
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )


class Pricing:
    def __init__(self, config: dict[str, Any]) -> None:
        self.models: dict[str, dict[str, float]] = {
            name: {k: float(v) for k, v in spec.items()}
            for name, spec in (config.get("models") or {}).items()
        }
        mult = config.get("multipliers") or {}
        self.cache_write_5m = float(mult.get("cache_write_5m", 1.25))
        self.cache_write_1h = float(mult.get("cache_write_1h", 2.0))
        self.cache_read = float(mult.get("cache_read", 0.10))
        self.batch = float(mult.get("batch", 0.5))

    @classmethod
    def load(cls, path: Path) -> Pricing:
        with open(path, encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    def known(self, model: str) -> bool:
        return model in self.models

    def _rates(self, model: str) -> tuple[float, float]:
        try:
            spec = self.models[model]
            return spec["input_per_mtok"], spec["output_per_mtok"]
        except KeyError as exc:
            raise UnpricedModel(
                f"Model {model!r} is not in config/pricing.yaml — add it (with a "
                f"citation) before calling it."
            ) from exc

    def cost_usd(self, model: str, usage: Usage, batch: bool = False) -> float:
        inp, out = self._rates(model)
        cost = (
            usage.input_tokens * inp
            + usage.cache_write_tokens_5m * inp * self.cache_write_5m
            + usage.cache_write_tokens_1h * inp * self.cache_write_1h
            + usage.cache_read_tokens * inp * self.cache_read
            + usage.output_tokens * out
        ) / 1_000_000
        return cost * self.batch if batch else cost

    def estimate_usd(
        self, model: str, *, input_chars: int, max_tokens: int, batch: bool = False
    ) -> float:
        """Conservative pre-call ceiling: chars/3 input tokens (English prose
        runs ~4 chars/token; /3 over-estimates on purpose), full max_tokens
        out, no cache discount."""
        inp, out = self._rates(model)
        est = (input_chars / 3 * inp * self.cache_write_5m + max_tokens * out) / 1_000_000
        return est * self.batch if batch else est
