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
| us_13dg (13D/13G) | deferred | — | parser + escalation logic written and unit-covered on synthetic shapes; a real-fixture corpus could not be harvested this session (SEC 403-blocked GitHub Actions runners on 7+ attempts, and SC 13x is rare in the production lake — ~1 filing per 8 days). Measure when filings accumulate or a non-Actions harvest path exists |
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

Ledger so far (final totals filled in after the recovery E2E):

| Run | What | Cost |
|---|---|---|
| First E2E attempt (run 34210635762) | batch submitted, CI job cancelled before collection; batch cancelled server-side but a sliver had processed | ~$0.03 (reconciled) |
| Validation E2E (150 real Sep-7 docs, realtime) | database kept in `e2e_output/` | $0.388 |
| Full multi-day E2E (run 34230547892) | 1,725 docs routed, 886 Stage-1 verdicts, hit the daily gate mid-Stage-2; **database lost** because outputs pushed only on success | $5.944 (reconciled) |

The two lost-spend rows are seeded into the spend log by
`scripts/reconcile_spend.py` (idempotent; runs in CI before the E2E), so
the $10 total gate counts true cumulative spend, not just what the
surviving database saw.

### Incidents (what went wrong and what changed)

1. **Batch outlived CI** — the first E2E's Message Batch sat in the queue
   past the job timeout; nothing was recorded. Fix: batches leave a
   zero-cost `:submitted` marker keyed by batch id, are cancelled on
   timeout, and CI validation runs use `--realtime`.
2. **Lost full run** — the multi-day E2E spent $5.94 and lost its database:
   realtime loops discarded partial results when `BudgetExceeded` fired,
   `analyst audit` let the exception escape as exit 1, and CI pushed
   outputs only on success. All three fixed (incremental recording, audit
   exits 3 cleanly, `if: always()` push), plus spend reconciliation above.

### Caching economics (production note)

Prompt caching never engaged on Stage 1 in live runs: Haiku 4.5's minimum
cacheable prefix is 4,096 tokens and the Stage-1 system prompt is ~2.2k
tokens. Inflating the prompt past 4k just to cache it would cost more than
it saves at daily volumes. The production cost lever is the Batch API
(50% off, the default; CI uses `--realtime` only because runners can't
wait on queue latency). Stage 2 (Sonnet, minimum 1,024) does cache its
system prompt and company-file blocks.

## Next steps (in order)

1. **Backfill** — the highest-value missing piece. The lake holds months of
   history; company files and the diff lane get sharper with every day
   loaded. `BACKFILL.md` specifies the job: replay partitions oldest-first
   through the same pipeline (idempotent by doc_id), deterministic +
   diff lanes only by default (near-zero API cost), with an optional
   capped AI pass over the highest-routed docs. Backfill before trusting
   week-one triage: Stage 2 reads the company file, and an empty file
   means every situation looks new.
2. **Outcome labels from a real price feed** — wire `labels` to actual
   forward returns (the schema and `analyst eval` are ready); only then do
   precision/recall numbers mean anything. Never fabricate labels.
3. **13D/G real-fixture measurement** — the parser is written; collect real
   SC 13D/G texts as the lake accumulates them (rare — ~1 per 8 days) or
   via a non-Actions harvest path, then hold it to the same ≥90% bar.

## Deferred / not built (by design)

- **Backfill** — see BACKFILL.md (format is ready; job not built).
- **Outcome labelling** — `labels` table + `analyst eval` exist; labels must
  come from a real price feed supplied later. Never fabricated.
- **OTC market** — lake supports it; out of scope per the brief.
- **Price-sensitive flag** — not stored by the ingester; Stage-2 escalation
  uses "not admin-noise AND watchlisted" instead (ASSUMPTIONS.md).
