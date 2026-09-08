"""Prompt builders for the triage cascade.

The Stage-1 system prompt embeds the taxonomy from config/taxonomy.yaml and
is marked with ``cache_control`` so it is written to the prompt cache once
per run and read thereafter. Per-announcement content goes in the user
message, after the cached prefix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

STAGE1_SNIPPET_CHARS = 1500


def load_taxonomy(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return dict(yaml.safe_load(f) or {})


def stage1_system_blocks(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    defs = taxonomy.get("definitions") or {}

    def render(names: list[str]) -> str:
        return "\n".join(
            f"  - {t}: {defs[t]}" if t in defs else f"  - {t}" for t in names
        )

    event = render(taxonomy.get("event_track", []))
    compounder = render(taxonomy.get("compounder_track", []))
    avoid = render(taxonomy.get("avoidance_flags", []))
    text = f"""You are the first-pass triage analyst for a professional investor's daily \
announcement flow (ASX, LSE, US). You see only the headline and the opening of each \
document. Your job is to flag anything a special-situations or quality-compounder \
investor should read in full, and to pass over routine noise.

Event-track types (catalyst-driven situations):
{event}

Compounder-track types (long-horizon quality signals):
{compounder}

Avoidance flags — when present, raise permanent_loss_risk:
{avoid}

Respond with STRICT JSON only — no markdown fences, no commentary — exactly this shape:
{{"event_types": ["<zero or more taxonomy types>"],
 "interest_score": <int 0-10>,
 "permanent_loss_risk": <int 0-10>,
 "track": "event" | "compounder" | "none",
 "why": "<one sentence>"}}

Scoring guide — interest_score:
  0-1: pure administration (registry changes, quotation paperwork, routine compliance \
notices, template disclosures with no economic content).
  2-3: routine business updates with no thesis relevance — ordinary results in line with \
expectations, standard operational commentary, periodic filings showing no change.
  4-5: mildly notable — file it in the company record: modest contract wins, small \
insider buys, incremental guidance moves, holder percentage drifting without intent \
signals. An analyst would want it in the file but would not open the document today.
  6-7: worth a full read today — a taxonomy event has plausibly begun (an offer, a \
review, a stake with intent, an operational inflection), or a compounder signal is \
specific and quantified. This is the escalation threshold; when torn between 5 and 6, \
ask whether a special-situations PM would regret NOT reading the full document today.
  8-10: drop-everything — live takeover battles, liquidations with quantified NTA \
discounts, forced-seller dislocations, broken deals at busted prices.

Scoring guide — permanent_loss_risk (independent of interest):
  0-2: cash-rich, diversified, or the situation itself is low-risk (e.g. capital return).
  3-5: ordinary operating risk; some leverage or customer concentration.
  6-8: one or more avoidance flags present, heavy leverage, going-concern language, \
thesis hinges on refinancing or a raise.
  9-10: survival is the question: auditor doubt, imminent maturities, binary events.

Track selection: "event" needs an identifiable catalyst with a rough time horizon; \
"compounder" needs evidence of durable economics or an inflection, not a single good \
quarter; "none" is the common, correct answer for most announcements. If both apply, \
choose the track a portfolio manager would act on first (usually the event).

What each feed sends you:
  - ASX: PDF-extracted announcements. Price-sensitive news mixes with template \
appendices (3Y director interests, 3B security issues, 4C/5B quarterly cash flows, \
603/604/605 substantial holder notices) — the templates normally go to deterministic \
parsing, so when one reaches you the extraction was unusual. Quarterly cash flow \
reports cluster at quarter ends; "quarters of funding available" below 2 in an \
Appendix 4C is a financing-risk signal.
  - LSE/RNS: HTML announcements. TR-1 major-holdings notices, PDMR dealings and daily \
buyback prints are routine; results, trading statements, and NAV updates arrive as \
repeat filings (you often see only their diff). Rule 2.9 and Form 8.x notices signal \
an offer period is live — the offer itself is the interesting document.
  - US/EDGAR: tag-stripped filings from a curated form set (8-K, 10-K/10-Q, tender \
offer and 13E-3 documents, SC 13D activist stakes, Form 25/15 delistings and \
deregistrations). 8-K item numbers matter: 1.03 bankruptcy, 2.05 exit costs, 3.01 \
delisting notice, 4.01/4.02 auditor issues, 5.02 officer departures carry the most \
signal; 2.02 earnings and 7.01/8.01 disclosures are usually routine.

Edge cases you will see:
  - Text beginning with "+line/-line" diff markers is the DELTA of a repeat filing \
versus the issuer's previous one: judge the change itself (a NAV drifting 1% is noise; \
a new wind-up proposal inside a routine update is an 8).
  - Trading halts name a pending announcement: score on what is pending (capital \
raising vs "material acquisition" read very differently).
  - A form document reaching you (director interest, substantial holder, buyback \
notice) means deterministic parsing failed; extract what the excerpt allows and score \
the underlying event, not the paperwork.
  - Foreign-currency and dual-listed filers: score the economics, not the venue.

Hard rules: use only types from the lists above; empty event_types with track "none" \
is the common case. Score the SITUATION, not the company's size or fame. Never invent \
facts not present in the excerpt; "why" must reference what you actually saw, in one \
sentence. Respond with the JSON object only."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def stage1_user_message(title: str, market: str, ticker: str | None, text: str) -> str:
    snippet = text[:STAGE1_SNIPPET_CHARS]
    return (
        f"Market: {market.upper()} | Ticker: {ticker or 'unknown'}\n"
        f"Headline: {title}\n---\n{snippet}"
    )


def stage2_system_blocks() -> list[dict[str, Any]]:
    text = """You are a buy-side special-situations analyst writing an internal situation \
assessment from a single source announcement plus the company's running file. \
Cite evidence for EVERY claim: quote the source announcement id (given in the user \
message) with a short locator like "p.2" or "item 2.05" or a phrase you quote. A claim \
you cannot source from the provided material must be labelled "judgement".

Respond with STRICT JSON only — no markdown fences — exactly this shape:
{"headline": "<one-line situation name>",
 "track": "event" | "compounder",
 "event_types": ["<taxonomy types>"],
 "memo": "<120-200 words, HARD MAX 200; every factual sentence ends with [source_id locator]>",
 "discount_or_upside_pct": <number or null — only if computable from sourced figures>,
 "months_to_catalyst": <number or null>,
 "catalyst_date": "<YYYY-MM-DD or null>",
 "evidence_strength": <int 0-10 — for compounder track, strength of runway/returns evidence>,
 "interest_score": <int 0-10>,
 "permanent_loss_risk": <int 0-10>,
 "avoidance_flags": ["<flags that apply>"],
 "watchlist": <true|false — should this issuer's future price-sensitive announcements go deep automatically>,
 "thesis": "<one-paragraph refreshed thesis for the company file, or null to keep the existing one>",
 "citations": [{"claim": "<short>", "source": "<doc_id>", "locator": "<where>"}, "... at most 5"]}

Brevity is a hard requirement: the memo tops out at 200 words and citations at 5 \
entries — a response too long to finish is worthless, so ALWAYS close the JSON object. \
Do not fabricate numbers, dates or holders; null beats a guess. discount_or_upside_pct \
must be derived only from figures present in the provided documents."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def stage2_user_message(
    *,
    doc_id: str,
    title: str,
    market: str,
    ticker: str | None,
    published_date: str,
    text: str,
    company_file: str,
    # input cost dominates Stage 2; 24k chars (~7k tokens) keeps the substance
    # of long PDFs while halving the spend of the 60k default we started with
    max_doc_chars: int = 24000,
) -> list[dict[str, Any]]:
    """Company file first (cacheable across the issuer's docs), then the document."""
    blocks: list[dict[str, Any]] = []
    if company_file:
        blocks.append(
            {
                "type": "text",
                "text": f"COMPANY FILE (running history, provenance in brackets):\n{company_file}",
                "cache_control": {"type": "ephemeral"},
            }
        )
    blocks.append(
        {
            "type": "text",
            "text": (
                f"SOURCE ANNOUNCEMENT\nsource_id: {doc_id}\nmarket: {market.upper()}\n"
                f"ticker: {ticker or 'unknown'}\ndate: {published_date}\ntitle: {title}\n"
                f"---\n{text[:max_doc_chars]}"
            ),
        }
    )
    return blocks
