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

Final ledger — **$9.2212 total against the $10 hard cap** (never exceeded;
every run stopped or finished under the gate):

| Run | What | Cost |
|---|---|---|
| First E2E attempt (run 34210635762) | batch submitted, CI job cancelled before collection; batch cancelled server-side but a sliver had processed | $0.0300 (reconciled) |
| Validation E2E (150 real Sep-7 docs, realtime) | proved triage quality, exposed the 1,500-token Stage-2 truncation and the caching miss | $0.3880 |
| Full multi-day E2E (run 34230547892) | 1,725 docs routed, 886 Stage-1 verdicts, hit the daily gate mid-Stage-2; **database lost** because outputs pushed only on success | $5.9444 (reconciled) |
| Recovery E2E (run 34239054850) | full 2026-09-07 day: 494 Stage-1 calls, 51 Stage-2, 19 audit; database kept in `e2e_output/` | $2.8588 |

Remaining headroom: ~$0.78.

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

## Final E2E results (2026-09-07, one full real day)

Outputs live in `e2e_output/` on this branch (`analyst.db`, `briefs/`,
`companies/`); produced by CI run 34239054850 against the real lake mirror.

- **Coverage / nothing dropped**: 1,013 announcements stored, 1,013 routed,
  0 unrouted (480 AI, 491 deterministic, 42 diff). The brief header prints
  the check.
- **Stage 1**: 559 verdicts (Haiku, strict JSON validated).
- **Stage 2**: 47 valid assessments of 51 attempts (**92% validity** at the
  2,400-token cap; 4 responses still wrote to the cap and were discarded as
  truncated JSON — see next steps). Up from 0% validity at the original
  1,500 cap.
- **Situations + brief**: 47 situations; `briefs/2026-09-07.md` ranks them
  with every factual sentence carrying `[doc_id locator]` provenance and a
  link back to the source announcement. Top of book was real and sensible:
  Ingenia's rejected $4.75 Warburg Pincus bid, the Pacific Current
  strategic review, the Forrestania/Zenith takeover dispute.
- **Audit sampler**: 18 valid samples of culled/low-scored docs (2% rate);
  **miss rate 11.1%** — 2 flagged misrouted, both genuine (a Santos Papua
  LNG stake acquisition Stage-1 scored 4; a tungsten PFS presentation
  scored 3). Honest signal that Stage-1 under-scores dense PDFs whose
  substance sits deep in the document.
- **Eval**: runs and reports empty — the labels table is intentionally
  unpopulated until a real price feed exists (never fabricated).
- **Company files**: per-issuer append-only MD+JSON with theses (e.g.
  `companies/asx/INA.md` carries the take-private thesis with provenance).

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

Smaller items observed in the final run: 4/51 Stage-2 responses still hit
the 2,400-token cap (retry once with an explicit "memo ran long — halve
it" instruction, or record the partial), and the audit's two misses point
at giving Stage-1 a slightly larger snippet window for dense PDF decks
(cost trade-off: +~30% Stage-1 input).

## Deferred / not built (by design)

- **Backfill** — see BACKFILL.md (format is ready; job not built).
- **Outcome labelling** — `labels` table + `analyst eval` exist; labels must
  come from a real price feed supplied later. Never fabricated.
- **OTC market** — lake supports it; out of scope per the brief.
- **Price-sensitive flag** — not stored by the ingester; Stage-2 escalation
  uses "not admin-noise AND watchlisted" instead (ASSUMPTIONS.md).
