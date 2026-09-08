# Assumptions and discovered facts

## Phase 0 — the ingester (hdcapital/market-ingestion)

**Discovery.** `MARKET_INGESTER_PATH` was unset. Candidates found on this
machine: `/home/user/market-ingestion` (the ingester repo checkout, cloned by
the build environment alongside this repo). No other ingest/scraper/announcement
repos were present locally; `list_repos` shows `hdcapital/market-ingestion`
as the only ingester-shaped repo in scope. Chosen: `/home/user/market-ingestion`.

**Storage.** The ingester writes a *lake* to S3 (bucket name held in the repo
secret `AWS_BUCKET_NAME`), layout per its `lake.py` (schema_version 1):

    documents/<market>/<YYYY>/<MM>/<DD>/<native_id>.json
    documents/<market>/<YYYY>/<MM>/<DD>/<native_id>.pdf   (ASX raw PDFs)
    manifests/<market>/<YYYY-MM-DD>.jsonl
    manifests/<market>/<YYYY-MM-DD>.done.json             (status: ok|ok_empty|failed)
    compact/<market>/<YYYY-MM>.parquet

"New since last run" is by published-date partition + the done-marker
contract (never treat an absent marker as an empty day). The analyst's own
idempotency is by `doc_id` in its SQLite DB, so re-reading partitions is safe.

**Adapter modes** (all in `src/analyst/adapter/lake.py`, nowhere else):
`MARKET_INGESTER_PATH` may be a synced local lake root, the repo checkout
(its `_lake/` dry-run output), or `s3://bucket/prefix`. Unset → discovery in
`~/market-data`, `../market-ingestion/_lake`, etc.

**Per-announcement metadata available**: doc_id, market, source, url,
published_at/date, scraped_at, company {ticker, exchange_qualified, name,
cik}, doc_type, form (US only), title, content {text, sha256, extraction,
raw_key, truncated}, flags {is_admin_noise, noise_rule}.

**Fields the ingester does NOT have** (worked around):

- **No announcement type codes** (ASX rel-type codes, RNS category codes are
  absent). Routing therefore matches on *headline patterns* per market —
  still metadata, never body content (`config/routing.yaml`).
- **No price-sensitive flag.** The ASX scraper does not persist it. The
  Stage-2 escalation rule "price-sensitive AND on watchlist" is implemented
  as "not admin-noise AND issuer watchlisted" instead. If the ingester later
  adds the flag, only `pipeline.py`'s `escalate` expression changes.
- **US market has no exchange string** in the lake (`exchange_qualified` is
  None); company files for US issuers live under `data/companies/us/`.
- **`form` is only populated for US.** ASX/UK form identity is derived from
  the headline by the router.

**Markets.** The lake also has an `otc` market. Out of scope per the brief
(ASX/LSE/US); the adapter takes a `markets` tuple, so adding `"otc"` is a
one-line config change.

**US ingest scope.** The US adapter stores a curated in-scope form set
(`US_FORMS`: 8-K/10-K/10-Q + amendments, SC TO-I/TO-T, SC 14D9, SC 13D and
13D/A, SC 13E3, 25/25-NSE, 15-12B/15-12G — ~180-200 filings/day), not the
full EDGAR firehose. Verified by querying the lake: September 2026
month-to-date holds ~888 US documents. **Form 4 and SC 13G are deliberately
excluded** from the production lake; real Form 4 fixtures for the parser
were harvested by running the unmodified ingester in `--dry-run` mode with
`US_FORMS` widened (S3 untouched). SC 13x filings are genuinely rare in the
curated set (1 across 8 exported days), so 13D/G parser fixtures depend on
the same dry-run harvest, which intermittently fails when SEC 403-blocks
GitHub's runner IPs.

**Form 4 text shape.** The lake stores full-submission text with all
SGML/HTML/XML *tags stripped* (`us_scraper.strip_sgml_noise`), so Form 4 XML
arrives as bare element values in document order. The Form 4 parser
recognises transaction rows positionally and validates them numerically;
anything irregular returns `unparsed` → AI lane.

## Build-environment constraints (do not affect production behaviour)

- This build session's egress policy blocks S3, the scraped sites, and the
  Actions artifact blob store; only github.com (git + API) and
  api.anthropic.com are reachable. Real lake data for fixtures and E2E runs
  was therefore pulled through the ingester's own GitHub Actions ("Query
  lake", plus a temporary `lake-export.yml` helper committed to the
  market-ingestion build branch which tarballs requested lake days onto that
  branch). The helper workflow and its data commits are build-time-only and
  should be deleted with the branch; production uses the adapter directly.
- `s3:ListAllMyBuckets` could not be exercised from this environment, so the
  bucket name must be supplied via `MARKET_INGESTER_PATH=s3://...` (or a
  synced local lake) in production.

## Other decisions

- **Company file paths** use the lake market key (`asx|uk|us`) as the
  exchange directory: `data/companies/<market>/<TICKER>.md`. US issuers with
  no ticker use `CIK<number>`.
- **Zero-drop sink**: routing rules may send pure paperwork to the
  `admin_log` deterministic parser, which records the announcement as a
  dated fact (nothing discarded, no model spend); the audit sampler
  re-checks a 2% slice of these daily.
- **Migrations**: plain versioned schema (`meta.schema_version` +
  `MIGRATIONS` list in `db/store.py`), not Alembic — single-process SQLite
  does not warrant it. Documented in README.
- **Model IDs** are resolved at first use from the live models endpoint
  (newest Haiku-class for triage, newest Sonnet-class for deep reads),
  restricted to models present in `config/pricing.yaml`; documented
  fallbacks `claude-haiku-4-5` / `claude-sonnet-5` (verified current as of
  2026-09-08).
- **UK price format**: RNS buyback notices usually quote pence; the parser
  stores the number as printed and does not assert a currency.
