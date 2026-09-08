"""Unpack the committed real-lake fixture archives into _lake_mirror/.

The archives under tests/fixtures/real/lake_archives/ are real lake days
exported through the ingester (plus dry-run harvests of forms the production
lake excludes). This script materialises them as a local lake root the
adapter can read: MARKET_INGESTER_PATH=./_lake_mirror.
"""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "_lake_mirror"
    dest.mkdir(parents=True, exist_ok=True)
    archives = sorted((REPO / "tests/fixtures/real/lake_archives").glob("*.tar.gz"))
    if not archives:
        raise SystemExit("no archives found under tests/fixtures/real/lake_archives/")
    for archive in archives:
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")
        print(f"unpacked {archive.name}")
    docs = sum(1 for _ in dest.rglob("*.json"))
    print(f"lake mirror at {dest}: {docs} JSON objects")


if __name__ == "__main__":
    main()
