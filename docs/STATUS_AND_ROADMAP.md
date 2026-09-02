# CEF Research → Daily Trading Tool: Status and Roadmap

*As of 2026-09-01. Every number in this document is read from committed artifacts
(coverage audit 2026-09-01T03:08Z, nightly table 2026-09-01T03:00Z, UK daily panel
2026-08-31T23:00Z), not estimated.*

This document answers three questions: what has been built, what is in progress
or unproven, and what remains between the current system and a trading tool that
(1) prices every live fund daily, (2) compares each against its own discount
history, (3) alerts when a fund is more discounted than normal, (4) scrapes,
reads and stores every announcement daily, updating the fund's record, and
(5) alerts on newly announced catalysts that may close the discount.

---

## Part 1 — What has been built

### 1.1 The research foundation (`src/uk_cef`, `src/au_lic` backtests)

A survivorship-free UK investment-trust discount backtest over 2007–2026 (303
securities, 41,241 security-months, AIC archive data only, price returns
ex-dividends by explicit policy), plus a parallel ASX LIC study. The findings
that should *shape* the trading tool, not just precede it:

- **Wide discounts predicted returns, monotonically.** The best pre-specified
  strategy (E, the overshoot composite: 50% z-rank + 25% absolute-discount rank
  + 25% 3-month-widening rank) did 28% gross price CAGR, 17% alpha (t=11.9), and
  held up out-of-sample 2022+ (18% alpha, t=6.98).
- **Cheap versus a fund's own history beats cheap in absolute terms** — z-score
  strategies (C/D/E) roughly double the absolute-discount strategies. The AU
  holdout went further: absolute discount level was an *anti-signal*. This is
  why the live system gates on own-history z only (`params.yaml`).
- **The alpha concentrates in the first month after the signal.** Skip-month
  tests collapse everything except the overshoot composite (2.8% alpha, t=2.28).
  Practical reading: speed matters; a monthly screen leaves most of it behind.
- **Catalysts re-rate at the announcement, not after it.** Cheap trusts with an
  announced catalyst earned *less* over the following month than cheap trusts
  without one (1.18% vs 2.08%) — the discount closes on the day. Twice
  confirmed (AIC completed-action proxy and Investegate announcement dates).
  **Catalyst alpha, if harvestable, requires announcement-day execution** —
  which is exactly the alerting capability this roadmap builds toward.
- **Essentially all excess return is discount capture** (decomposition: −1.0%
  NAV component, +14.7% discount component for strategy A), and deep-discount
  portfolios are not defensive (drawdowns as deep as the benchmark, faster
  recovery).
- Strategy F (quality × value) is a documented negative result post bug-fix:
  nothing survives the skip-month test and the effect is ~zero from 2022.

### 1.2 The live layer (`src/cef_live`) — the trading tool already ~60% exists

Running nightly in CI today:

| Capability | State | Evidence (2026-09-01) |
|---|---|---|
| **Universe registry** — every UK trust + ASX LIC ever listed, identity-only, liveness decided from the funds' *own filings* | Production | 1,116 vehicles (944 UK / 172 AU); 399 live |
| **Ticker resolution** — keyfacts ISIN → OpenFIGI → Investegate-verified | Production | 646 verified, 0 unresolved |
| **Live NAV (Tier 0)** — parsed from each fund's own RNS / ASX PDF, aggregator print never outranks a published NAV | Production | 713/1,116 rows carry a NAV (206 published, 223 factor roll-forward, 284 stale carry) |
| **Factor roll-forward (Tier 1)** — per-fund OLS + walk-forward sigma, so a stale NAV carries an error band | Production | `share_with_sigma` = 1.0 |
| **Daily prices** — Yahoo v8, raw close (never adjusted close), unit reconciliation against the fund's own NAV | Production | 1.16M UK daily bars across 282 funds; 100 AU live quotes |
| **UK daily discount panel** — price/NAV joined on *publication* date, per-fund staleness, split-adjusted, 756-day rolling z | Production | 1.12M fund-days 2007→2026-08-31; 161 funds usable today; median discount −6.1% |
| **Catalyst scan** — 9-class weighted headline taxonomy (liquidation 5, scheme/merger 5, tender 4, continuation vote 4, strategic review 3, manager change 3, discount control 3, substantial holder 2, distribution policy 2) | Working | 117 catalysts across 65 funds in last 30 days |
| **Forward IRR screen** — 5y NAV-growth path, discount narrowing to own median, three sensitivities | Working | 546/1,116 rows |
| **Daily universe spreadsheet, emailed** — Universe / Live / Catalysts / 40 most dislocated | Production | `emailed: true`, four dated workbooks committed |
| **Coverage audit** — GREEN/AMBER/RED per fund with a recommended fix on every failing row | Production (dispatch-only, by design) | see 1.4 |
| **Alerting channel** | Working, single channel | Gmail SMTP (`notify.py`); heartbeat + critical priorities |

The architecture has an enforced invariant (CI test `test_nav_source_invariants.py`
runs in every live workflow): registry files are identity-only, every NAV comes
from the fund's own announcement, a modelled estimate never occupies a published
figure's field, and the universe cannot silently narrow.

### 1.3 The announcement machinery (already the hard part)

- **UK**: the Investegate RNS index (~750k throttled fetches, S3 state group)
  covers the live universe including the previously-invisible infrastructure/
  renewables/property cohort (the index-gap job added 70 funds / 34,287 NAV
  announcements). 722,382 announcement texts are archived in S3, **queue
  empty** — the crawl is fully caught up and topped up daily (12 shards, 03:00
  UTC). Because the *text* is archived, parser improvements re-read a decade of
  history at zero cost to the publisher (`reparse` mode).
- **ASX**: every announcement PDF is archived to S3 daily (8 shards, 02:00 UTC);
  the market-wide index is topped up daily; a deterministic extractor (77%
  parse rate, 81% on text-bearing docs; 26,274 historical NAV observations in
  the bucket) runs nightly; a full LLM extraction path (router, schema, guards
  against lookahead/computed-signals/misquotes, batch API, cost estimator,
  2% audit sample) is written and gated behind manual dispatch.

### 1.4 Where coverage actually stands (the audit's numbers)

328 monitoring-eligible funds (UK 230 / ASX 98) out of 1,116 registered:

| Market | GREEN (signal-ready) | AMBER | RED |
|---|---|---|---|
| UK | 105 (45.7%) | 39 | 86 |
| ASX | 15 (15.3%) | 71 | 12 |
| **Total** | **120 (36.6%)** | **110** | **98** |

Funnel: fresh price 291/328 (88.7%) → usable NAV 247 (75.3%) → valid discount
230 (70.1%) → current z-score 169 (51.5%). **Prices are essentially solved;
NAV parse coverage and z-history are the binding constraints.** The two markets
fail differently: ASX is AMBER-dominated (46.9% modelled NAVs; 66 of 98 funds
hold NAV announcements we have *not yet run the extractor over*), UK is
RED-dominated (stale NAVs in the infra/property cohort, 11 identity conflicts,
unparsed announcements at PSH/INPP/CVCE/SMIF and others, unit mismatches, 8
funds Yahoo returns nothing for).

---

## Part 2 — What is in progress or unproven

1. **The ideas/opportunity email has never demonstrably fired.** ⚠️ The
   twice-daily pre-open scan (`ideas.yml`, 06:20 and 23:10 UTC) gates
   dislocation (z ≤ −1.5, staleness ≤ 45d, basis ≤ 2) × catalyst-in-30d ×
   IRR-above-hurdle, writes a ledger, and emails at critical priority. It is
   wired and scheduled — but no ledger file, no `ideas_latest.csv`, and no
   `CI: pre-open idea scan` commit exists anywhere in history. An earlier
   version crashed on every scheduled run (missing panel build, since fixed).
   **This is the single most important thing to verify: the alert path from
   signal to inbox is unproven end-to-end.**
2. **ASX NAV extraction quality is the active work front.** Every one of the
   last ~10 PRs is ASX parser reach/quality (headline-pattern duplication,
   biased failure samples, truncated text windows, per-fund label rules).
   Extracted facts currently span 2025-03→2026-08 only (953 observations)
   against a 2016→2026 corpus; label rules cover 32 of 95 funds; the 80% miss
   rate in label discovery is diagnosed as a fixable enumeration problem, not
   a method limit. The last *sharded* deterministic run processed 0 documents
   (index divergence) — currently a no-op that needs fixing.
3. **UK parse backlog**: 190,442 archived announcement rows still unparsed
   (56% cumulative parse rate); 31 funds hold a NAV announcement with no value
   parsed; the reparse button exists and works.
4. **Known holes, documented not fixed**: the ASX market-wide announcement
   index has a hard 2024-01→2026-05 gap (rate-limited endpoint; a 17-hour slow
   crawl is offered as a decision, not started). 45 UK funds have no Yahoo
   price history; 5 currency-mismatch refusals; 11 UK identity conflicts
   awaiting manual review.
5. **Half-wired code**: `eligibility.classify` is not applied when the registry
   is built (the audit applies it; the nightly falls back to a panel column);
   Tier 2 holdings-based NAV is a documented stub; `au_lic/terminal.py`
   (delisting terminal values) is written and tested but unreachable in
   production; half of `au_lic/prices_history.py` (survivorship-free price
   assembly) has tests but no caller; the `pulse:` LLM-budget config block has
   no consuming code.
6. **Two z-scores coexist**: the daily 756-day rolling z (UK panel) and the
   monthly 36-month z (nightly table). The alert gates consume the *monthly*
   one; the richer daily z is computed and then unused for alerting.

---

## Part 3 — Gap analysis against the trading tool

| Wanted | Have | Gap |
|---|---|---|
| **Price every live fund daily** | UK: daily bars for 282 funds; AU: live quotes nightly for ~100. | ASX has no *daily discount panel* (only monthly-anchored); 45 UK funds priceless on Yahoo — needs a second price source; 11 identity conflicts block pricing. |
| **Compare vs own discount history** | 1.12M fund-day UK history with rolling z; monthly z both markets; 36m windows pre-specified from research. | Only 169/328 funds have a current z. History exists but NAV parse coverage starves it. Daily z not fed to gates. |
| **Alert when more discounted than normal** | Full gate logic + Gmail channel + ledger design, scheduled twice daily. | **Unproven end-to-end** (see Part 2.1). Also: current design only emails 3-gate OPPORTUNITIES; there is no simpler "fund X just crossed z ≤ −2 vs its own history" dislocation alert independent of catalyst/IRR gates. |
| **Scrape announcements daily, read, store, update fund file** | UK: fully archived + daily top-up + reparse; ASX: PDFs archived daily, index topped up daily. Storage: S3 per-announcement, per-fund history parquet. | "Reading" is currently NAV extraction + headline-only catalyst regex. Bodies are not read for content beyond NAV. The LLM extraction path (built, guarded, costed) has never run in production. No single consolidated per-fund file that an announcement updates. |
| **Alert on discount-closing catalysts just announced** | 9-class weighted headline scan, nightly, in email + spreadsheet. | No "new since yesterday" detection (the 30-day window re-reports the same events); no intraday latency (research says the re-rating happens announcement-day); 78% of current hits are low-weight discount-control/holder noise; body-level reading (e.g. tender *size and price*, wind-down *timeline*) doesn't exist. |

---

## Part 4 — Roadmap

### Phase 0 — Prove the alert path (days)

The highest-value, lowest-effort item in the repo.

- Dispatch `ideas.yml` manually; confirm the OPPORTUNITY/WATCH email arrives and
  the ledger commits. Add a CI assertion that a scheduled ideas run always
  commits `reports/build/ideas.json` (even for "no ideas"), so a silent failure
  can never again run for weeks unnoticed.
- Fix the sharded deterministic ASX run (currently 0-document no-op) and wire
  `eligibility.classify` into `cli.build_universe` so the registry carries
  `research_eligible`.

### Phase 1 — The daily dislocation report (1–2 weeks)

Turn the panels you already have into the daily product:

- **One email/artifact per day, every fund**: price, NAV (with basis and
  staleness), discount, own-history z (daily z for UK, monthly for ASX),
  percentile vs own history, and a Δz-since-yesterday column. This is mostly a
  join of `uk_discount_latest.csv` + `nta_live_latest.csv` that already exist.
- **Threshold-crossing alerts**: emit an alert the first day a fund crosses a
  pre-specified z threshold (e.g. −1.5 entry / −2.0 strong), with state kept in
  the ledger so a fund alerts on *crossing*, not every day it stays cheap.
  This is the "more discounted than normal" alert decoupled from the
  catalyst/IRR gates.
- **ASX daily discounts**: reuse the UK panel machinery (as-of join on
  publication date, per-fund staleness) over `close_raw` + Tier 0/extracted
  NTAs — the components all exist.
- Feed the daily z into the opportunity gates (replace the monthly z where
  daily history exists).

### Phase 2 — Coverage push: 36.6% → 70%+ signal-ready (2–4 weeks, mostly runs not code)

Everything here has a named fix in `coverage_failures.csv`:

- Run the ASX deterministic backfill over the full 2016→2026 archived corpus
  (clears the 66-fund "announcement held, not extracted" bucket and extends z
  history); promote label-discovery rules beyond 32 funds by fixing candidate
  enumeration.
- Run UK `reparse` after each parser improvement (190k-row backlog); work the
  31 unparsed-announcement funds and the 20 unit-mismatch flags.
- Resolve the 11 UK identity conflicts (manual review queue already exists).
- Add a second price source for the 45 Yahoo-less UK funds (LSE-licensed data
  is paid; Stooq was probe-rejected — this may be the first place a paid feed
  earns its cost).
- Decide on the 17-hour slow crawl to close the ASX 2024–25 index hole.

### Phase 3 — Announcement intelligence: read everything, keep a fund file (3–6 weeks)

- **Per-fund file**: one canonical record per fund (identity, liveness, NAV
  history + quality, discount stats, corporate events, catalyst log, data
  flags) materialized from the existing stores, updated by every nightly and
  every announcement — the "fund file" the tool updates when an announcement
  changes something. Most content already exists across
  registry/nta_live/uk_daily/catalysts; this is consolidation, not new data.
- **Read announcement bodies, not just headlines**: turn on the built LLM
  extraction path with the existing budget config (300 cheap docs/day, 20
  escalations, $10/day cap in `params.yaml`) for the `llm` route (corporate
  actions, meetings, narrative reports) — extracting the *terms* of catalysts:
  tender size/price/dates, continuation-vote dates, wind-down timelines,
  strategic-review scope. The guards (verbatim-quote provenance, no computed
  signals, lookahead) are already written and tested.
- Route those structured events into the fund file and the catalyst log with
  event *dates* (vote date, tender close), enabling a forward calendar.

### Phase 3b — The learning layer: mine the dead funds (after Phase 3)

The corpus is the moat: ~750k UK announcements plus the ASX archive,
*including every gone fund*, aligned with point-in-time prices, NAVs and
known terminal outcomes. Almost nobody holds a survivorship-free text
corpus where the endings are known — "what did the eventual wind-ups and
tenders sound like 6–18 months before they resolved?" is answerable here,
and announcement-*anticipation* is exactly where the backtest says the
remaining edge lives (the discount closes at the announcement itself).

Built as a ladder, never as an end-to-end black box — a few hundred
independent resolution episodes cannot support one, and a deep model
trained text→return will memorise fund templates and eras (survivorship
bias wearing a neural-net costume; strategy F and the negative catalyst
study are the house warning labels):

1. **LLM as feature extractor, not learner**: read every announcement
   (dead funds first) and emit a SMALL set of pre-specified structured
   judgments — board tone on the discount, buyback follow-through vs
   promise, activist presence, continuation-vote commitments, wind-down
   credibility, fee changes, NAV-methodology conservatism. The extraction
   guards (verbatim-quote provenance, no computed signals, anti-lookahead)
   already exist; Phase 3's per-fund event files are the substrate.
2. **Test those features in the existing honest framework**: decile
   tests, Fama-MacBeth, the 2022+ holdout, skip-month controls. Twenty
   features over twenty years is learnable and auditable.
3. **Small models on top** (logistic / gradient-boosted) predicting
   "resolution within N months" or forward-return bucket — time-based
   splits only, never fund-based; the label-discovery module is the
   in-house proof of the pattern (learn against known answers, 30%
   holdout, anti-overfit vocabulary clause).
4. **Deep end-to-end text models only as research** behind those
   controls; nothing reaches the live gates without surviving the holdout
   and a dated CHANGELOG entry.

Cost is tractable: batch-triage the corpus with the cheap model and
escalate the interesting minority — the architecture the extraction
runner already implements, under the existing per-day budget knobs.

### Phase 4 — Catalyst-day alerting (2–3 weeks, after Phase 3)

The research's clearest instruction: the discount closes at the announcement.

- **New-event detection**: alert only on catalysts first seen since the last
  run (persisted seen-set), at critical priority for weight ≥ 4 classes
  (liquidation, scheme/merger, tender, continuation vote) on funds currently
  trading below their own norm.
- **Latency**: move from one nightly scan to a morning + midday + evening
  cadence for the announcement index/listing pages (UK RNS is heaviest 07:00
  London; the pre-open runs already exist at the right times) — free-source
  polling budgets permitting. True intraday needs a paid RNS feed; defer until
  the daily loop is proven.
- Combine: "fund at z −2.1 vs own history announced a tender this morning" is
  the exact alert the backtest says pays.

### Phase 5 — Productization (ongoing)

- A dashboard (published artifact or static page rebuilt by the nightly)
  replacing/augmenting the XLSX email: sortable universe, per-fund page from
  the fund file, discount chart vs history, event timeline.
- Ledger → positions: entry/exit tracking against the pre-specified rules
  (entry next open, exit z ≥ −0.5, max 12 months, 10% positions) so alerts
  become an auditable paper (then live) track record.
- Second notification channel (the `notify()` adapter is designed for it) and
  operational monitoring: a daily "system green/amber/red" line in the
  heartbeat derived from the coverage audit's counters.

### Deliberately not on the roadmap

- Tuning thresholds against live outcomes (forbidden by `config/CHANGELOG.md`
  policy — changes require a dated, argued entry).
- Synthetic fills for missing data, rescaling suspicious numbers, or working
  around source rate limits — the project's standing rules, and the reason its
  numbers can be trusted.
