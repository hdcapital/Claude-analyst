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
    event = "\n".join(f"  - {t}" for t in taxonomy.get("event_track", []))
    compounder = "\n".join(f"  - {t}" for t in taxonomy.get("compounder_track", []))
    avoid = "\n".join(f"  - {t}" for t in taxonomy.get("avoidance_flags", []))
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

Scoring guide: 0-2 routine noise; 3-5 mildly notable, file it; 6-7 worth a full read \
today; 8-10 drop-everything situation. Score the SITUATION, not the company's size. \
Use only types from the lists above; empty list with track "none" is the common case. \
Never invent facts not present in the excerpt; "why" must reference what you actually saw."""
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
 "memo": "<150-300 word assessment; every factual sentence ends with [source_id locator]>",
 "discount_or_upside_pct": <number or null — only if computable from sourced figures>,
 "months_to_catalyst": <number or null>,
 "catalyst_date": "<YYYY-MM-DD or null>",
 "evidence_strength": <int 0-10 — for compounder track, strength of runway/returns evidence>,
 "interest_score": <int 0-10>,
 "permanent_loss_risk": <int 0-10>,
 "avoidance_flags": ["<flags that apply>"],
 "watchlist": <true|false — should this issuer's future price-sensitive announcements go deep automatically>,
 "thesis": "<one-paragraph refreshed thesis for the company file, or null to keep the existing one>",
 "citations": [{"claim": "<short>", "source": "<doc_id>", "locator": "<where>"}]}

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
    max_doc_chars: int = 60000,
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
