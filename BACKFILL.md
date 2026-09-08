# Backfill (planned follow-up — not built yet)

Historical backfill of company files from the ingester's archive is a
planned follow-up. It is deliberately not implemented in this build.

The file format already supports it:

- The JSON sidecar (`data/companies/<market>/<TICKER>.json`) is the source
  of truth; `facts` and `changelog` entries each carry their own `date` and
  are re-sorted by date on every save, so entries inserted later with
  *older* dates slot into chronological position without rewriting history
  (covered by `tests/test_company_files.py::test_backfill_order`).
- The Markdown file is re-rendered from the sidecar after every append, so
  backfilled entries appear in order automatically.
- Fact identity is (source doc_id, fact_type, locator), so re-processing an
  old day is idempotent.

Sketch of the eventual job: iterate the lake's historical partitions oldest
first with `analyst run --since <date> --until <date> --dry-run`-style
batches (deterministic + diff lanes only, AI lane disabled to keep spend at
zero), then optionally run triage over the small set of docs the router
sends to the AI lane, month by month, under the daily budget cap.
