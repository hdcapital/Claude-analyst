"""Structured logging: JSON lines to stderr (and optionally a file).

Kept tiny on purpose — one formatter, one setup function, no dependencies.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(verbose: bool = False, json_logs: bool = True, log_file: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    handler: logging.Handler = logging.StreamHandler(sys.stderr)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    # quieten noisy third-party loggers; our own spend logs stay at INFO
    for noisy in ("httpx", "urllib3", "botocore", "boto3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
