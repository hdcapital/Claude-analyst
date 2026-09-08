# Progress

_Build session status. Updated as phases land; final handover at the bottom._

## What works (implemented + tested)

- **Adapter** (`src/analyst/adapter/`): reads the market-ingestion lake from a
  local root, a repo checkout's `_lake/`, or `s3://` directly; honours the
  done-marker contract; partition listing is authoritative (manifests are
  keyed by run date, not published date — verified on the real lake).
- **Cost guard / LLM client**: pre-call estimate gate on total AND daily caps
  (`BudgetExceeded`), pricing from `config/pricing.yaml` (cited), prompt
  caching, Batch API, per-call spend logging. Guard behaviour unit-tested
  with the SDK mocked (refusal happens BEFORE any API call).
- **Storage**: SQLite/SQLAlchemy, idempotent by lake doc_id; versioned schema.
- **Router**: metadata-only rules in `config/routing.yaml`; every decision
  logged with the rule that fired; unmatched → AI lane.
- **Deterministic parsers** (real-fixture coverage below).
- **Diff lane**: previous same-type filing lookup, normalised diff, trivial
  deltas recorded as facts, non-trivial deltas forwarded to AI triage.
- **AI triage cascade**: Stage 1 (cheap model, strict JSON, cached taxonomy
  system prompt, one retry then escalate), Stage 2 (strong model, full doc +
  company file as cached context, cited assessment). Batch by default,
  `--realtime` available.
- **Company files**: append-only Markdown + JSON sidecar per issuer with
  provenance on every line; thesis replacement forces a "superseded" log
  entry; backfill-safe ordering (tests cover it).
- **Brief, audit sampler, eval scaffold, CLI, Makefile, README.**

## Parser coverage on real fixtures (pulled through the ingester)

| Parser | Fixtures | Parse rate | Notes |
|---|---|---|---|
| asx_3y (App 3Y) | 36 | 92% | misses: image-only/degenerate PDFs → AI lane |
| asx_3b (App 3B/2A) | 39 | 100% | |
| asx_substantial (603/604/605) | 33 | 82% | misses: institutional filing packages whose PDF text detaches values from labels (State Street/Barclays style); deterministically unreadable without guessing → AI lane |
| asx_quarterly (App 4C/5B) | 45 form-bearing | 96% | prose-only "Quarterly Activities" reports (no appendix) correctly return unparsed → AI |
| rns_tr1 (TR-1) | 37 | 97% | |
| rns_pdmr | 30 | 93% | |
| rns_buyback | 50 | 86% | long tail of issuer-specific wordings → AI lane |
| us_form4 (Form 4) | 30 | 97% | real Form 4s harvested via the ingester in dry-run mode (production lake excludes Form 4 by design — see ASSUMPTIONS.md) |
| us_13dg (13D/13G) | pending | — | harvest in progress (SEC intermittently 403s Actions runners) |
| us_8k (item codes) | 25 | 100% | |

The 90% target is met for 3Y, 3B, 4C/5B, TR-1, PDMR, Form 4 and 8-K.
`asx_substantial` (82%) and `rns_buyback` (86%) fall short; every miss
returns `unparsed` and is routed to the AI lane (nothing dropped), and the
failing families are documented in `tests/test_parsers_real_fixtures.py`.

## Spend

Tracked in the analyst DB (`analyst spend`); the E2E runs execute in the
Claude-analyst repo's CI (the only place an ANTHROPIC_API_KEY is available
to this build) and push their `data/` back to the branch as `e2e_output/`.
Numbers recorded here after each run.

## Deferred / not built (by design)

- **Backfill** — see BACKFILL.md (format is ready; job not built).
- **Outcome labelling** — `labels` table + `analyst eval` exist; labels must
  come from a real price feed supplied later. Never fabricated.
- **OTC market** — lake supports it; out of scope per the brief.
- **Price-sensitive flag** — not stored by the ingester; Stage-2 escalation
  uses "not admin-noise AND watchlisted" instead (ASSUMPTIONS.md).
