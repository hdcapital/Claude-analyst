"""Central configuration.

All secrets and paths come from the environment (a git-ignored ``.env`` is
loaded if present). Required settings fail fast with a message naming the
variable; nothing else in the codebase reads ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in "
            f"(see README 'Setup')."
        )
    return value


def _require_float(name: str) -> float:
    raw = _require(name)
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment, built once per process."""

    anthropic_api_key: str
    ingester_path: str  # local lake root / repo checkout / s3://bucket/prefix ("" = discover)
    data_dir: Path
    budget_usd_total: float
    budget_usd_daily: float
    triage_model: str  # "" = resolve from the live models endpoint at first use
    deep_model: str
    timezone: ZoneInfo
    repo_root: Path = field(default=_REPO_ROOT)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "analyst.db"

    @property
    def companies_dir(self) -> Path:
        return self.data_dir / "companies"

    @property
    def briefs_dir(self) -> Path:
        return self.data_dir / "briefs"

    @property
    def fixtures_real_dir(self) -> Path:
        return self.repo_root / "tests" / "fixtures" / "real"

    @property
    def routing_yaml(self) -> Path:
        return self.repo_root / "config" / "routing.yaml"

    @property
    def taxonomy_yaml(self) -> Path:
        return self.repo_root / "config" / "taxonomy.yaml"

    @property
    def pricing_yaml(self) -> Path:
        return self.repo_root / "config" / "pricing.yaml"


def load_settings(dotenv_path: str | os.PathLike[str] | None = None) -> Settings:
    """Read the environment (plus .env) into a Settings snapshot."""
    load_dotenv(dotenv_path or _REPO_ROOT / ".env", override=False)
    tz_name = os.environ.get("ANALYST_TIMEZONE", "Australia/Sydney").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # zoneinfo raises several types for bad keys
        raise ConfigError(f"ANALYST_TIMEZONE {tz_name!r} is not a valid IANA zone") from exc
    data_dir = Path(os.environ.get("ANALYST_DATA_DIR", "").strip() or "./data").expanduser()
    return Settings(
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        ingester_path=os.environ.get("MARKET_INGESTER_PATH", "").strip(),
        data_dir=data_dir,
        budget_usd_total=_require_float("ANALYST_BUDGET_USD_TOTAL"),
        budget_usd_daily=_require_float("ANALYST_BUDGET_USD_DAILY"),
        triage_model=os.environ.get("TRIAGE_MODEL", "").strip(),
        deep_model=os.environ.get("DEEP_MODEL", "").strip(),
        timezone=tz,
    )


def load_settings_no_llm(dotenv_path: str | os.PathLike[str] | None = None) -> Settings:
    """Settings for commands that never call the API (dry runs, brief, company).

    Identical to :func:`load_settings` but tolerates a missing ANTHROPIC_API_KEY
    so offline commands work on a box with no key configured.
    """
    load_dotenv(dotenv_path or _REPO_ROOT / ".env", override=False)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        os.environ["ANTHROPIC_API_KEY"] = "OFFLINE-NO-KEY"
        try:
            return load_settings(dotenv_path)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
    return load_settings(dotenv_path)
