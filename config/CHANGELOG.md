# Parameter changelog

Every change to `config/params.yaml` gets a dated entry here, committed in
the same commit as the change, with a rationale. Parameters are never
tuned against live outcomes.

## 2026-08-28 — Initial pre-specification
All parameters set per the CEF-LIVE build brief before any live signal has
been generated: z threshold −1.5 (matches the validated research spec's
dislocation band), staleness cap 45 days, error-sanity k=1, IRR hurdle =
universe TR + 8pp, NAV-growth shrinkage 50% to sector with [−5%, +15%]
caps, ledger sizing 10%/10-position cap, budgets 300 cheap docs / 20
escalations / $10 per day. Rationale: inherited from the completed
UK/AU research phase and the brief; no live data existed at the time of
setting, so no tuning was possible.

## 2026-08-29 — Delisting review policy
Added `universe.delist_after_missing_months: 2` and
`review_grace_months: 3`. Rationale: registry sources publish monthly, so
two consecutive absences is the first point at which disappearance is
distinguishable from a late file. Candidates are excluded from alerting
immediately (a fund that may not exist must not generate an idea) but
surfaced for human review rather than dropped, and never deleted from the
registry. Manually added funds are exempt by construction.

## 2026-08-31 — Coverage-audit thresholds
Added the `coverage_audit` block: `price.fresh_days: 5`,
`price.stale_days: 30`, `nav.fresh_days: 35`, `nav.amber_days: 120`,
`zscore.min_months: 24`. Rationale: these are DIAGNOSTIC thresholds for the
on-demand coverage report (`python -m cef_live.coverage_audit`) and nothing
trades off them — no signal, gate or position sizing reads this block. They
are stated in config rather than in code so that "fresh" means one thing
across the whole report and can be argued with. Values are derived, not
chosen against outcomes: 5 calendar days keeps a Friday close fresh on a
Monday after a bank holiday; 30 days is the point past which a quote cannot
support a live discount; 35 days keeps a monthly NAV publisher fresh through
its normal publication lag; 120 days mirrors the existing
`universe.liveness.nav_days`; 24 months mirrors
`live_nta.z_adjustment.min_months`.

## 2026-09-01 — Opportunity gates and the two-email day (owner instruction)
Changed on the owner's explicit instruction, not against live outcomes:

- Gate 3 is now a fixed absolute hurdle, `opportunity.min_irr_central:
  0.15`, replacing `irr_hurdle_excess_pp: 8.0` over the trailing universe
  return. Rationale: the owner's required return is an absolute 15%, and a
  hurdle that floats with the universe's trailing performance answers a
  different question ("is this fund better than the rest lately") than the
  one asked ("does this clear my bar"). The trailing universe return is
  kept as reported context only.
- Replaced `watch_gates_required: 2` with an explicit ladder: OPPORTUNITY
  still requires all three gates on fully sound data; WATCH is now two
  gates OR any single standalone-strength trigger — a dislocation
  (z ≤ −1.5 on the fund's own history), a passing IRR, or a catalyst of
  weight ≥ `standalone_catalyst_weight: 4` (tender, scheme/merger,
  continuation vote, wind-down). Rationale: the owner asked for every
  fund actionable that day on z-score, IRR or catalyst to be surfaced;
  requiring two gates hid a z −2.5 fund that had no catalyst and no IRR
  coverage. The weight-4 floor for catalyst-only ideas exists because the
  current 30-day tape carries 91 routine buyback-programme headlines
  against 7 genuinely discount-closing events; the routine ones remain
  visible in the workbook's Catalysts sheet and still count toward
  two-gate verdicts.
- Email policy: exactly two emails per day, both from the pre-open ideas
  scan (06:20 UTC pre-LSE, 23:10 UTC pre-ASX), each carrying the full
  universe workbook as an attachment plus the actionable ideas. The
  nightly heartbeat, the standalone universe-spreadsheet email and the
  delist-review email no longer send; their content is committed (and the
  delist count is included in the ideas email) instead.

## 2026-09-01 — Pricing the whole universe (owner instruction: 90% coverage target)
Goal set by the owner: 90%+ of currently trading UK and AU funds carrying a
live or well-estimated NAV, an own-history discount z, and a NAV growth
input for the forward IRR. Baseline measured 2026-09-01: 47.0% (154/328
monitoring-eligible). Changes, all measurement-driven:

- **z within the error band is kept, flagged, and never voided.** A
  dislocation smaller than the NAV estimate's own error band must not
  alert — but voiding the z to NaN made 76 fully priced funds
  indistinguishable from funds with no history. `z_adj` now always
  computes; `z_within_error` travels with it; `alert_eligible` and the
  dislocation gate refuse a within-error z exactly as before.
- **The daily panel becomes a z-history fallback** (`z_source:
  own_daily_panel`). The aggregator's monthly panel never priced the
  announcements-only cohort; the UK daily discount panel holds those
  funds' own series and is resampled to the identical monthly 36m spec
  (strict `discount_fresh` reading only). The panel stays primary.
- **Freshness is judged against each fund's own cadence**:
  `staleness_limit_days = clip(3 × own median publication gap,
  max_days_for_alerting, max_days_cadence_cap=200)`. Rationale: a
  45-day flat cap wrote off the quarterly-publishing infrastructure/
  property cohort as stale on data that is current by those funds' own
  cadence. The flat cap remains the floor and the alert gates use the
  per-fund limit.
- **Growth input fallback chain for the forward IRR**, recorded in
  `g_source`: panel TR CAGR → the fund's own NAV-per-share history
  (median monthly log-change annualised, |monthly| < 18% excluded so a
  split cannot poison it) → sector median → market median. Every
  fallback passes the same 50% shrink and [−5%, +15%] caps. Terminal
  discount falls back from the panel's 5y median to the daily panel's.
- **The objective function is measured every run**:
  `reports/build/signal_coverage.json` + per-fund
  `outputs/live/signal_coverage.csv` (the work list), denominated over
  live research-eligible funds.
