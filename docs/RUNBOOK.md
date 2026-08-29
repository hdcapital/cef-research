# CEF-LIVE Runbook

## Where NAV comes from (architectural invariant)

The AIC and ASX registry files are used for **identity only**: which funds
exist, in what sector, from when to when, under which ISIN. Every NAV value
comes from the fund's own announcement.

This is not a preference; it follows from measurement. The July 2026 AIC MIR
lists 282 UK funds and publishes price/NAV for 137. Guernsey and Jersey
registered trusts are 50-of-56 name-only, and 90 GB-registered rows are blank
too - which silently excluded the entire infrastructure, renewables, property
and private-equity cohort from a pipeline that required the aggregator to
value it.

**Rules that must hold:**

1. Every live fund with a resolved ticker is polled for its own NAV. A fund
   is absent from NAV output only because it published nothing parseable,
   never because a target filter excluded it.
2. A published NAV always outranks a registry print.
3. A modelled estimate never occupies the field reserved for a published
   figure. Estimates carry basis, anchor date, staleness and error band.
4. When the registry gains codes, the announcement index is re-swept for
   them. A fund that lists after the last sweep must not stay unindexed.

`tests/test_nav_source_invariants.py` enforces all four and runs in every
live workflow. If a change narrows the target set or inverts the source
priority, CI fails rather than the universe quietly shrinking - which is
exactly how UWC, AIX, MRE, PCX and WHI lost their NAV route the first time.

## Ticker resolution

Primary source is the AIC **keyfacts/companies** file, which carries a
populated `ticker` column even for the funds the MIR leaves name-only. It is
joined by the same entity resolution the registry uses, so the match is exact.

Yahoo search is a fallback and only ever behind name verification: unaided it
returns British American Tobacco for "British & American", and Bluefield
Solar's Frankfurt line ahead of London. A wrong ticker staples another
company's share price onto this fund's NAV, so an unverifiable candidate is
recorded `unresolved` rather than accepted.

## Schedules

| Job | When | Purpose |
|---|---|---|
| `cef-ideas` | 07:20 London, 09:10 Sydney | Pre-open scan: announcements -> catalysts -> gates -> email |
| `cef-live-nightly` | 21:00 UTC weekdays | Registry, tickers, NAV, IRR, universe spreadsheet |
| `asx-s3-archive` / `uk-nav-archive` | daily, 6 shards | Raw document archive to S3 |

## Failure modes

- **Runner queue**: dispatches can sit pending when many jobs are launched at
  once. Stage dispatches rather than firing shards and pipelines together.
- **Ticker unresolved**: fund gets no NAV and no price. Check
  `reports/build/ticker_resolution.json`; seed `config/investegate_tickers.csv`
  by hand for names that matter.
- **Stale NAV**: `basis 3` and `staleness_days > 45` exclude a fund from
  ideas by design. A stale NAV must never evidence a dislocation.
