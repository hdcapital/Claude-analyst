"""Collect real parser fixtures from a lake mirror.

Routes every document in the mirror with the production router and copies up
to CAP documents per deterministic parser into tests/fixtures/real/<parser>/,
preserving the full lake document (so fixtures carry their source ids).

Usage: python scripts/collect_fixtures.py [mirror_root] [cap]
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from analyst.adapter import LakeAdapter  # noqa: E402
from analyst.adapter.lake import _LocalStore  # noqa: E402
from analyst.models import Lane  # noqa: E402
from analyst.router import Router  # noqa: E402


def main() -> None:
    mirror = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "_lake_mirror"
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    out_root = REPO / "tests" / "fixtures" / "real"
    router = Router.load(REPO / "config" / "routing.yaml")
    adapter = LakeAdapter(_LocalStore(mirror))

    per_parser: Counter[str] = Counter()
    lanes: Counter[str] = Counter()
    since = datetime(2026, 7, 1, tzinfo=UTC)
    until = datetime(2026, 12, 31, tzinfo=UTC)
    for ann in adapter.iter_new_announcements(since, until):
        decision = router.route(ann)
        lanes[f"{ann.market}:{decision.lane.value}"] += 1
        if decision.lane is not Lane.DETERMINISTIC or decision.parser in (None, "admin_log"):
            continue
        parser = decision.parser
        assert parser is not None
        if per_parser[parser] >= cap:
            continue
        y, m, d = ann.published_date.split("-")
        src = mirror / f"documents/{ann.market}/{y}/{m}/{d}/{ann.native_id}.json"
        dest_dir = out_root / parser
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{ann.market}-{ann.native_id}.json"
        if not dest.exists():
            shutil.copyfile(src, dest)
        per_parser[parser] += 1

    print("lane distribution:")
    for key, count in sorted(lanes.items()):
        print(f"  {key}: {count}")
    print("fixtures collected:")
    for parser, count in sorted(per_parser.items()):
        print(f"  {parser}: {count}")
    index = {
        "collected_at": datetime.now(UTC).isoformat(),
        "mirror": str(mirror),
        "counts": dict(per_parser),
    }
    (out_root / "INDEX.json").write_text(json.dumps(index, indent=2))


if __name__ == "__main__":
    main()
