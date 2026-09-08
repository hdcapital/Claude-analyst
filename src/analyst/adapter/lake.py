"""Read announcements from the market-ingestion lake.

Lake layout (documented in the ingester's README and lake.py, schema_version 1):

    documents/<market>/<YYYY>/<MM>/<DD>/<native_id>.json   canonical document
    documents/<market>/<YYYY>/<MM>/<DD>/<native_id>.pdf    raw PDF (ASX)
    manifests/<market>/<YYYY-MM-DD>.jsonl                  one line per doc written that day
    manifests/<market>/<YYYY-MM-DD>.done.json              run marker (the timing contract)

Consumer contract (ingester README): before reading a day, check the done
marker. ``status`` is ``ok`` | ``ok_empty`` | ``failed``; an absent marker
must never be read as an empty day, so days without a marker are reported to
the caller as "unconfirmed" rather than silently skipped.

Backends:
    * a local lake root (``aws s3 sync``ed copy, or the ingester's ``_lake/``
      dry-run output) — the default;
    * S3 directly (``MARKET_INGESTER_PATH=s3://bucket/market-data/``).

Both serve the same byte-level layout, so one reader class handles both via a
tiny two-method store protocol.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..config import Settings
from ..models import Announcement

log = logging.getLogger(__name__)

DEFAULT_MARKETS: tuple[str, ...] = ("asx", "uk", "us")  # otc exists but is out of scope
_EXCHANGE_BY_MARKET = {"asx": "ASX", "uk": "LSE", "us": "US", "otc": "OTC"}

# Directories probed (relative to $HOME and to this repo's parent) when
# MARKET_INGESTER_PATH is unset.
_DISCOVERY_CANDIDATES = (
    "market-data",
    "market-ingestion/_lake",
    "market-ingestion",
    "code/market-ingestion",
    "projects/market-ingestion",
    "repos/market-ingestion",
)


class LakeNotFound(RuntimeError):
    """No readable lake at (or discoverable from) MARKET_INGESTER_PATH."""


class _Store(Protocol):
    def get_bytes(self, key: str) -> bytes | None: ...

    def list_dir(self, prefix: str) -> list[str]:
        """Immediate child names under prefix (files and directories)."""
        ...

    def describe(self) -> str: ...


class _LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return (self.root / key).read_bytes()
        except OSError:
            return None

    def list_dir(self, prefix: str) -> list[str]:
        base = self.root / prefix
        try:
            return sorted(p.name for p in base.iterdir())
        except OSError:
            return []

    def describe(self) -> str:
        return f"local:{self.root}"


class _S3Store:
    def __init__(self, bucket: str, prefix: str) -> None:
        import boto3  # optional dependency; only needed for s3:// paths

        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.client = boto3.client("s3")

    def get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)
            return bytes(resp["Body"].read())
        except Exception:
            return None

    def list_dir(self, prefix: str) -> list[str]:
        full = self.prefix + prefix.rstrip("/") + "/"
        names: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                names.append(cp["Prefix"][len(full) :].rstrip("/"))
            for obj in page.get("Contents", []):
                name = obj["Key"][len(full) :]
                if name:
                    names.append(name)
        return sorted(names)

    def describe(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


@dataclass(frozen=True)
class DayStatus:
    """What the done-marker said for one (market, date) partition."""

    market: str
    day: str  # YYYY-MM-DD
    status: str  # ok | ok_empty | failed | missing_marker
    doc_count: int


def _doc_to_announcement(doc: dict[str, Any]) -> Announcement:
    company = doc.get("company") or {}
    content = doc.get("content") or {}
    flags = doc.get("flags") or {}
    market = str(doc.get("market", ""))
    return Announcement(
        doc_id=str(doc["doc_id"]),
        market=market,
        exchange=_EXCHANGE_BY_MARKET.get(market, market.upper()),
        native_id=str(doc["doc_id"]).split(":", 1)[1] if ":" in str(doc["doc_id"]) else "",
        source=str(doc.get("source", "")),
        url=str(doc.get("url", "")),
        published_at=str(doc.get("published_at", "")),
        published_date=str(doc.get("published_date", "")),
        scraped_at=str(doc.get("scraped_at", "")),
        ticker=company.get("ticker") or None,
        exchange_qualified=company.get("exchange_qualified") or None,
        company_name=company.get("name") or None,
        cik=str(company["cik"]) if company.get("cik") else None,
        doc_type=str(doc.get("doc_type", "announcement")),
        form=doc.get("form") or None,
        title=str(doc.get("title", "")),
        text=str(content.get("text") or ""),
        text_sha256=content.get("text_sha256") or None,
        extraction=str(content.get("extraction", "")),
        raw_key=content.get("raw_key") or None,
        truncated=bool(content.get("truncated", False)),
        is_admin_noise=bool(flags.get("is_admin_noise", False)),
        noise_rule=flags.get("noise_rule") or None,
    )


def _resolve_local_root(raw: str) -> Path | None:
    """Map a filesystem path to a lake root, accepting a repo checkout too."""
    p = Path(raw).expanduser()
    for candidate in (p, p / "_lake"):
        if (candidate / "documents").is_dir() or (candidate / "manifests").is_dir():
            return candidate
    return None


def _discover_root() -> Path | None:
    homes = [Path.home(), Path(__file__).resolve().parents[4]]
    for home in homes:
        for rel in _DISCOVERY_CANDIDATES:
            root = _resolve_local_root(str(home / rel))
            if root is not None:
                return root
    return None


class LakeAdapter:
    """Iterates lake documents by published-date partition."""

    def __init__(self, store: _Store, markets: tuple[str, ...] = DEFAULT_MARKETS) -> None:
        self.store = store
        self.markets = markets
        self.day_statuses: list[DayStatus] = []  # filled during iteration

    @classmethod
    def from_settings(
        cls, settings: Settings, markets: tuple[str, ...] = DEFAULT_MARKETS
    ) -> LakeAdapter:
        raw = settings.ingester_path
        if raw.startswith("s3://"):
            rest = raw[len("s3://") :]
            bucket, _, prefix = rest.partition("/")
            if not bucket:
                raise LakeNotFound(f"Bad S3 lake path {raw!r}")
            return cls(_S3Store(bucket, prefix or "market-data/"), markets)
        if raw:
            root = _resolve_local_root(raw)
            if root is None:
                raise LakeNotFound(
                    f"MARKET_INGESTER_PATH={raw!r} has no documents/ or manifests/ "
                    f"directory (and no _lake/ inside it)"
                )
            return cls(_LocalStore(root), markets)
        root = _discover_root()
        if root is None:
            raise LakeNotFound(
                "MARKET_INGESTER_PATH is unset and no lake was found in the usual "
                "places (~/market-data, ../market-ingestion/_lake). Sync the lake or "
                "set MARKET_INGESTER_PATH in .env."
            )
        log.info("lake discovered at %s", root)
        return cls(_LocalStore(root), markets)

    # -- reading ------------------------------------------------------------

    def _read_done_marker(self, market: str, day: str) -> str:
        raw = self.store.get_bytes(f"manifests/{market}/{day}.done.json")
        if raw is None:
            return "missing_marker"
        try:
            return str(json.loads(raw.decode("utf-8")).get("status", "missing_marker"))
        except (ValueError, UnicodeDecodeError):
            return "missing_marker"

    def _manifest_keys(self, market: str, day: str) -> list[str]:
        """Document keys for a day: manifest first, directory listing fallback."""
        raw = self.store.get_bytes(f"manifests/{market}/{day}.jsonl")
        if raw is not None:
            keys = []
            for line in raw.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                key = entry.get("key")
                if key:
                    # manifest keys carry the lake prefix ("market-data/...");
                    # our store roots at the prefix, so strip it if present.
                    keys.append(key.split("market-data/", 1)[-1])
            return keys
        y, m, d = day.split("-")
        prefix = f"documents/{market}/{y}/{m}/{d}"
        return [
            f"{prefix}/{name}" for name in self.store.list_dir(prefix) if name.endswith(".json")
        ]

    def iter_day(self, market: str, day: str) -> Iterator[Announcement]:
        status = self._read_done_marker(market, day)
        keys = self._manifest_keys(market, day)
        self.day_statuses.append(DayStatus(market, day, status, len(keys)))
        if status == "failed":
            log.warning("lake day %s/%s marked failed by ingester — reading what exists", market, day)
        if status == "missing_marker" and keys:
            log.warning("lake day %s/%s has documents but no done marker (run may be partial)", market, day)
        for key in keys:
            raw = self.store.get_bytes(key)
            if raw is None:
                log.error("manifest lists %s but the document is unreadable — skipped", key)
                continue
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                log.error("document %s is not valid JSON — skipped", key)
                continue
            yield _doc_to_announcement(doc)

    def iter_new_announcements(
        self, since: datetime, until: datetime | None = None
    ) -> Iterator[Announcement]:
        """All documents in published-date partitions from ``since`` to ``until``.

        Idempotency across runs is the caller's job (the analyst DB keys on
        doc_id); this simply enumerates the partitions.
        """
        start = since.date()
        end = (until or datetime.now(tz=since.tzinfo)).date()
        day = start
        while day <= end:
            iso = day.isoformat()
            for market in self.markets:
                yield from self.iter_day(market, iso)
            day += timedelta(days=1)

    def latest_complete_day(self, lookback_days: int = 14) -> str | None:
        """Most recent date with an ok done-marker in any market."""
        today = date.today()
        for delta in range(lookback_days + 1):
            day = (today - timedelta(days=delta)).isoformat()
            for market in self.markets:
                if self._read_done_marker(market, day) in ("ok", "ok_empty"):
                    return day
        return None

    def get_raw(self, raw_key: str) -> bytes | None:
        """Fetch a raw artifact (ASX PDF) by its lake key."""
        return self.store.get_bytes(raw_key.split("market-data/", 1)[-1])
