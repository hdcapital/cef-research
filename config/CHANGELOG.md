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
