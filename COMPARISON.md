# Head-to-head: Claude-analyst vs global-analyst — 2026-09-09

One real day, both systems, same documents. global-analyst's verdicts are
its **production output** (it screened the day on its own schedule; its
per-document analyses were exported from `global-screener/` in S3), so
this compares what each system actually produced, not a re-run.

## Method

- Join key: lake `native_id` — global-analyst stores one analysis per
  document under the same id the lake uses. **829 of 829** ASX+UK
  documents joined (514 ASX, 315 UK); coverage on both sides was total.
- "Flagged" means: global-analyst `score >= 60` or an active decision
  (`DEEPEN_RESEARCH` / `ADD_TO_FUNDAMENTAL_WATCHLIST` /
  `UPDATE_COMPANY_MEMORY`); Claude-analyst a Stage-2 situation
  (interest ≥ 6) in the 2026-09-09 brief.
- **US caveat**: global-analyst reads US filings a day behind, so its
  Sep-9 US analyses did not exist at export time. The 234 US documents
  were processed by Claude-analyst only and are excluded from the join.
- One day, no forward returns: this measures *what each system surfaces*,
  not which one makes money. The `labels`/`analyst eval` path is the
  right tool for that once a price feed exists.

## Headline numbers

| | global-analyst | Claude-analyst |
|---|---|---|
| Documents analysed (ASX+UK) | 829 | 829 (565 deterministic, 82 diff, 416 AI incl. US) |
| Flagged for a human | 8 (2 above its 75-point email gate) | 20 situations in the brief |
| Both systems' top event of the day | Goodwin (GDWN) £1.1bn division sale — 82/100 | Goodwin (GDWN) — 8/10 Stage-1, 7/10 Stage-2 |
| Rank agreement (482 co-scored docs) | — | Spearman ≈ 0.31 (moderate; mandates differ) |

Both systems put **Goodwin PLC's £1.1bn Mechanical Engineering sale**
(≈ its entire market cap) at the top of the day, from both the press-
speculation response and the definitive-agreement announcement, and both
flagged **Austal's US$1.25–1.35bn NBIO from Wildcat**. On the day's two
clearest events the systems agree completely.

## Where they diverge — and why

The divergence is mandate, not accuracy. global-analyst is a **long-only
BUY screen**: it asks "is this a quality business to buy today?" and
archives everything else — including live M&A. Claude-analyst is a
**special-situations/event system**: it tracks catalysts whether or not
the target is a quality compounder.

### Claude-analyst catches global-analyst archived (event-mandate uniques)

| Situation | Claude-analyst | global-analyst |
|---|---|---|
| **Harworth (HWG): rejected 172.5p hostile bid**, 19.7% NDV discount cited | 8/10, top of brief | 22/100, ARCHIVE |
| SThree (STEM): enters offer period, Circle8 approach | 7/10 | 8–15/100, ARCHIVE |
| Future Metals (FEG): Xingye requisitions EGM to remove entire board | 7/10 | 5/100, OUT_OF_SCOPE |
| Celsius (CLA): 40% JV stake foreclosed at auction | 7/10 | 3/100, OUT_OF_SCOPE |
| DCC Energy: ECP/KKR consortium takeover (offer-period cluster) | 6/10 | 2/100, ARCHIVE |
| South32: $3.1bn divestment to Alcoa | 6/10 | 8/100, OUT_OF_SCOPE |
| VMC: $35.15m special dividend post royalty sale | 6/10 | 8/100, OUT_OF_SCOPE |
| Hammer Metals: merger-arb waivers granted | 6/10 | 5/100, OUT_OF_SCOPE |

A live hostile bid at a stated NDV discount (Harworth) archived at 22/100
is the single clearest illustration: for a long-only quality screen it is
correctly out of scope; for a special-situations book it is the day's
second-best idea. The two systems are answering different questions.

### global-analyst flags Claude-analyst passed on (quality-mandate uniques)

| Item | global-analyst | Claude-analyst |
|---|---|---|
| Anpario (ANP): interim results, "market overreacted to softer H2" | 78/100, DEEPEN_RESEARCH | diff lane, Stage-1 5/10 — not escalated |
| Frontier Developments (FDEV): record FY, cash-backed recovery | 68/100, watchlist | Stage-1 3/10 |
| Telecom Plus (TGP): legacy defect settled within provisions | 62/100, watchlist | Stage-1 4/10 |

These are value/quality judgements ("good business, price overreacting")
rather than events. Claude-analyst's compounder track exists but its
Stage-1 rubric scores routine results conservatively (2–3 band) unless an
inflection is quantified in the excerpt — FDEV at 3/10 is the one that
most looks like a genuine under-score rather than a mandate difference,
and is a good candidate for a compounder-rubric tweak.

### Agreement on noise

Of 482 co-scored documents, both systems assign near-zero to the same
administrative mass (82 docs at exactly s1 ≤ 1 and 0/100), and the bulk
of both distributions sits in the bottom bands. Claude-analyst
additionally handled 565 ASX/UK docs deterministically (forms parsed to
facts at zero AI cost) — documents global-analyst spent model calls
scoring.

## Cost and run notes

- The day cost Claude-analyst **$4.72** (610 Stage-1 calls, 96 Stage-2,
  14 audit; realtime pricing — batch would have been ~half). Recorded
  cumulative spend: **$13.95 against the user-approved $14 cap**.
- The run itself completed; the CI job then **failed in the audit step
  because the Anthropic account's credit balance ran out** (API 400), not
  because of the internal budget gate. The 14 audit responses were lost
  (the realtime audit loop catches `BudgetExceeded` but let the billing
  error escape before recording) — a known hardening item now: treat
  unexpected API errors like budget stops (record partials, exit 3). The
  2026-09-09 brief was generated offline from the preserved database.
- Stage-2 JSON validity: 89 of 96 (93%) at the 2,400-token cap.
- global-analyst's OpenAI cost for the day is not visible from here.

## Read of the result

1. **On overlapping mandate, they agree**: the day's two unambiguous
   events (Goodwin, Austal) top both systems' output.
2. **Claude-analyst's event coverage is materially wider**: 8+ real
   situations (hostile bid, offer periods, board spill, foreclosure,
   special dividend, divestment) that the long-only screen structurally
   discards. If the goal is a special-situations book, this is the gap
   the new system was built to close.
3. **global-analyst's quality lens is sharper on "good business, odd
   price"**: ANP and FDEV are defensible ideas Claude-analyst left in
   the 3–5 band. Worth a compounder-rubric iteration (score quantified
   recoveries/records higher when accompanied by capital returns), and
   worth keeping both systems running side-by-side until labels exist.
4. **Judge with returns, not vibes**: populate `labels` from a price feed
   and let `analyst eval` referee both systems on the same days.
