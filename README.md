# Claude-analyst

An autonomous investment-research agent over the `market-ingestion` lake.
Every day it reads the new announcement flow from the ASX, LSE and US
exchanges, routes each document down one of three lanes (deterministic
parsers, repeat-filing diffs, or an AI triage cascade), accumulates
per-company fact files with full provenance, and writes a ranked daily brief
of event-driven and compounder situations.

```
lake (market-ingestion) ─► adapter ─► router ─┬─► deterministic parsers ──┐
                                              ├─► diff lane ──────────────┼─► facts DB ─► company files ─► scoring ─► daily brief
                                              └─► AI triage (cheap→strong)┘                 ▲
                                                   audit sampler (2% of culled) ────────────┘
```

Three hard guarantees, enforced in code and tests:

1. **Nothing is silently dropped.** Every announcement gets exactly one lane
   and a routing-log row; parsers that fail validation return `unparsed` and
   the document goes to the AI lane instead.
2. **Every fact carries provenance** — source announcement id, date and a
   locator (character offsets / form item). A fact without provenance cannot
   be constructed.
3. **Hard spend caps.** Every model call is estimated first and refused with
   `BudgetExceeded` if it would breach the total or daily USD cap. There is
   no bypass flag.

## Setup

```bash
git clone <this repo> && cd Claude-analyst
make install          # pip install -e ".[dev,s3]"
cp .env.example .env  # then fill it in
make check            # lint + mypy + tests
```

`.env` (see `.env.example` for the full commented list):

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | model calls fail fast without it; offline commands (brief/company/spend/eval, `run --dry-run`) work without |
| `MARKET_INGESTER_PATH` | no | synced lake root, ingester checkout, or `s3://bucket/market-data/`; unset → discovery (`~/market-data`, `../market-ingestion/_lake`) |
| `ANALYST_DATA_DIR` | yes | SQLite DB, company files, briefs (default `./data`) |
| `ANALYST_BUDGET_USD_TOTAL` / `_DAILY` | yes | hard caps |
| `TRIAGE_MODEL` / `DEEP_MODEL` | no | default: newest Haiku-/Sonnet-class from the live models endpoint, restricted to models priced in `config/pricing.yaml` |
| `ANALYST_TIMEZONE` | no | default `Australia/Sydney` |

Getting the lake locally (the ingester README documents the layout):

```bash
aws s3 sync s3://$AWS_BUCKET_NAME/market-data/ ~/market-data/
echo 'MARKET_INGESTER_PATH=~/market-data' >> .env
# or point straight at S3:
echo 'MARKET_INGESTER_PATH=s3://YOUR-BUCKET/market-data/' >> .env
```

## First run

```bash
analyst run --dry-run --limit 50 --plain-logs   # no model calls: check routing
analyst run --limit 100                         # real run, Batch API, budget-capped
analyst run --realtime                          # same-day mode (per-call API)
analyst brief --date 2026-09-08                 # writes data/briefs/2026-09-08.md
analyst spend                                   # running spend by day/model/purpose
```

`analyst run` picks the latest complete lake day automatically; use
`--since/--until/--limit` for cheap partial runs. Re-running a day is
idempotent (keyed on the lake's `doc_id`).

Other commands:

```bash
analyst company show PME            # print an issuer's file (or asx/PME)
analyst company diff PME --since 2026-09-01
analyst audit --date 2026-09-05    # strong-model audit of culled/low-scored docs
                                    # (--realtime to skip the Batch API queue)
analyst eval                        # precision/recall vs the labels table
analyst rate S-ab12cd34ef --score 4 --note "good catch"
```

## Editing the routing rules

`config/routing.yaml` maps metadata (SEC form types, headline patterns —
never body text) to lanes. First match wins; anything unmatched goes to the
AI lane. Each rule names the parser (DETERMINISTIC) or diff group (DIFF).
The `admin_log` parser is the zero-drop sink for pure paperwork: the
announcement is recorded as a dated fact and skipped by the models (the
audit sampler re-checks a slice daily).

`config/taxonomy.yaml` holds the event/compounder taxonomy embedded in the
triage prompts, the Stage-2 escalation threshold (`stage2_interest_threshold`)
and the audit sample rate. `config/pricing.yaml` prices every model the
client is allowed to call; a model missing from it cannot be called.

## Data layout

```
data/
  analyst.db             SQLite (announcements, facts, triage_results,
                         situations, spend_log, routing_log, audit_samples,
                         ratings, labels, company_snapshots)
  companies/<mkt>/<TICKER>.md    human-readable company file (append-only history)
  companies/<mkt>/<TICKER>.json  structured sidecar (source of truth)
  briefs/YYYY-MM-DD.md   daily brief
```

Migrations: plain versioned schema — `meta.schema_version` plus ordered,
additive SQL steps in `MIGRATIONS` (`src/analyst/db/store.py`). New tables
appear via `create_all`; destructive changes are not used.

## Scheduling (cron example)

```cron
# after the lake's ingest slots (see market-ingestion README; times UTC):
50 2 * * 2-6  cd /path/to/Claude-analyst && .venv/bin/analyst run >> run.log 2>&1
0  3 * * 2-6  cd /path/to/Claude-analyst && .venv/bin/analyst audit && .venv/bin/analyst brief >> run.log 2>&1
```

## Development

```bash
make test / make typecheck / make lint / make check
```

Test fixtures: real announcements pulled through the ingester live under
`tests/fixtures/real/` (each carries its lake doc_id); synthetic fixtures
under `tests/fixtures/synthetic/` use obviously fake identifiers
(`SYNTH-…`) and are never loaded into any non-test store. See
`ASSUMPTIONS.md` for discovered ingester facts and build notes, `BACKFILL.md`
for the planned history backfill, and `PROGRESS.md` for status, measured
parser coverage and spend.
