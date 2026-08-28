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
