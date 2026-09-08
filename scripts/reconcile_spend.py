"""Seed the spend log with API cost whose per-call records were lost.

CI run 34230547892 (the first full multi-day live E2E) spent $5.9444 across
~890 Haiku Stage-1 calls and ~40 Sonnet Stage-2 calls — the amounts are in
that run's logs — but its job failed in `analyst audit` before the workflow
pushed the database back, so the per-call rows were lost with the runner.
An earlier batch (validation attempt 1) was submitted and orphaned when its
job hit the CI timeout: ~$0.03.

The $10 total budget is a hard rule, so this cost must count. Running this
script inserts one reconciliation row per lost amount (idempotent by the
doc_id marker) so the budget gate and `analyst spend` see true cumulative
spend. Nothing here is an estimate of model output — it is bookkeeping for
real, logged API usage.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sqlalchemy import select  # noqa: E402

from analyst.config import load_settings_no_llm  # noqa: E402
from analyst.db import Store  # noqa: E402
from analyst.db.schema import SpendLogRow  # noqa: E402

# (marker, usd, note) — markers make re-runs no-ops
LOST_SPEND = [
    ("recon-ci-run-34230547892", 5.9444, "full E2E run; DB lost when the job failed in audit"),
    ("recon-orphan-batch-run-34210635762", 0.03, "batch orphaned at CI timeout, cancelled late"),
]


def main() -> None:
    settings = load_settings_no_llm()
    store = Store(settings.db_path)
    for marker, usd, note in LOST_SPEND:
        with store.session() as s:
            exists = s.execute(
                select(SpendLogRow).where(SpendLogRow.doc_id == marker)
            ).scalar_one_or_none()
            if exists is not None:
                print(f"already reconciled: {marker}")
                continue
            s.add(
                SpendLogRow(
                    day="2026-09-08",
                    model="(reconciliation)",
                    purpose=f"reconciliation: {note}",
                    input_tokens=0,
                    output_tokens=0,
                    cache_write_tokens=0,
                    cache_read_tokens=0,
                    batch=False,
                    cost_usd=usd,
                    doc_id=marker,
                )
            )
            print(f"reconciled ${usd:.4f}: {marker}")
    print(f"total spend now: ${store.total_spend_usd():.4f}")


if __name__ == "__main__":
    main()
