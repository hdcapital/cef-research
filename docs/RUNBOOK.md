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

## Live coverage audit (on demand)

```bash
python -m cef_live.coverage_audit            # audit stored data
python -m cef_live.coverage_audit --refresh  # refresh first, then audit
```

Manual only - no cron, no nightly hook, no push trigger. The workflow
`.github/workflows/live-coverage-audit.yml` is `workflow_dispatch`-only and
a test fails if a schedule is ever added to it.

Outputs: `outputs/live_coverage/` (CSV, XLSX, Markdown, JSON).

Read it top-down:

1. **Denominators** - `registry_total` -> `registry_labelled_live` ->
   `liveness_adjusted_live` -> `research_eligible` -> `monitoring_eligible`.
   Every percentage below is over `monitoring_eligible`.
2. **Per-market coverage** - price freshness, NAV provenance, and the
   conjunction that matters: `signal_ready`.
3. **Why coverage is missing** - the ranked failure table, and
   `coverage_failures.csv` for the fund-level work list with a recommended
   fix on every row.

Verdicts: GREEN = signal-ready; AMBER = monitorable with a stated
qualification; RED = no reliable live signal; EXCLUDED = outside the
monitoring universe, with the reason kept.

What it will not do: rescale a suspicious number, fill a missing
observation, call a historical panel price current, or report an unparsed
NAV announcement as "no NAV published". Those distinctions are the report.

### Liveness is persisted

`python -m cef_live.cli universe` now writes the evidence-based status back
to `data/universe/registry.parquet`. It previously computed it, printed a
summary, and discarded it - so every downstream reader used the
aggregator's status instead. Both are kept: `status` (evidence) and
`aggregator_status` (the file's view), plus `live_status_source`.

`live_stale_nav` is ALIVE. It means "trading, but no NAV fresh enough to
carry a discount" - a data-coverage statement, not a listing one. Any
filter written as `status == "live"` drops those funds from the priced
universe; use `liveness.LIVE_STATUSES` / `TRACKED_STATUSES`.

### Canonical units

`src/cef_live/units.py` states them once: UK NAV and price are both **GBX
(pence)**; AU NAV and price are both **AUD (dollars)**. Conversions are
explicit and only applied when the source unit is stated. A UK NAV read as
pounds and divided into a pence share price is the bug that produced
premiums of 80x-5,000x; the fix is at the reader, never at the output.

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

## Where state lives

S3 is the system of record for anything expensive to rebuild. The GitHub
Actions cache is a speed-up that nothing depends on - it is evicted after 7
days unused or when the repo exceeds 10GB, which is fine for pip wheels and
unacceptable for a 750k-fetch announcement index.

| Group | Contents | Cost to rebuild |
|---|---|---|
| `uk_announcements` | Investegate listings + details | ~750k throttled fetches |
| `asx_index` | market-wide announcement index + sweep state | ~700 calls |
| `asx_pdf_extract` | parsed PDF text | ~700 downloads + parsing |
| `raw_aic` / `raw_asx` | source archive files | a decade of downloads |
| `tickers` | resolved ticker map | verification passes |

Each group is one tarball at `s3://$S3_BUCKET/state/<group>.tar.gz`, versioned
by content hash so an unchanged group is not re-uploaded. Every workflow pulls
what it needs at start and pushes at end with `if: always()`, so state
survives a failed or cancelled run.

Raw documents (announcement PDFs, NAV announcement text) and daily snapshots
are separate, append-only, under `asx/`, `uk/` and `nta_live/`.

The repo keeps what benefits from version history: code, config, params, the
registry, and small analytical outputs. It is not the durability mechanism.

## Known data boundary: ASX announcements stop at 2023-11 (measured 2026-08-30)

The market-wide announcement index covers **2016-10-31 to 2023-11-07** and
then, after a 2.5-year hole, a handful of rows from 2026-06 onward. 2024 and
2025 are absent entirely. This is a hard boundary of the free sources, not a
bug in the pipeline, and it is recorded here so nobody spends another day
rediscovering it.

### How the hole opened

The top-up sweep stopped once it overlapped the index's GLOBAL maximum date.
After one budget-limited pass wrote a block of recent announcements, that
maximum jumped forward to the recent frontier, so the next pass overlapped it
on its FIRST call and stopped immediately. The more recent data it fetched,
the sooner it quit — self-sealing, which is why it survived every run.

Fixed (the sweep now targets the top of the *contiguous* block, the inner
60-call cap is gone, a cursor persists mid-gap, and the index and
sweep_state live in S3 rather than an evictable Actions cache). The logic is
sound; the sources are what stop it.

### Why it cannot currently be refilled

Measured, not assumed:

| endpoint | result |
|---|---|
| `asx.com.au/asx/1/announcement/list` bare | 200 in 0.2s |
| same, with any `end_date` | **first call 200 (2000 items, ONE day); every later call ReadTimeout at 25s** |
| `asx.com.au` home | 200 in 0.2s |
| Markit `companies/{CODE}/announcements` | 200 in 0.6s, but returns only the **latest 5** announcements |

The site is healthy; the index endpoint answers once and then stops. A
timeout that appears only after the first request is a **rate limit expressed
as a timeout**, and this project does not work around rate limits.

The arithmetic that makes it worse: 2000 items covered **one day**. The gap is
~1,023 days, so a market-wide refill needs ~1,000 heavy calls against an
endpoint that stops answering after one.

The per-company Markit endpoint ignores `page` (pages 0-3 return identical
items), ignores `itemsPerPage` (200 returns 5), and carries no pagination or
total-count metadata. It serves the latest few announcements only. Probing
stopped there deliberately: guessing parameter names against a third-party API
is the behaviour that got the other endpoint throttling us.

### What still works for 2024-2026

- **ASX monthly investment-products reports** cover 2024+ and are already
  archived — that is where the panel's NTA rows for the recent period live.
- **Yahoo daily prices** cover the whole period.

So the recent period supports a **monthly-resolution** discount study, not the
daily/weekly one the 2016-2023 announcement data allows. Any result spanning
the boundary must be read as two different resolutions, and a finding that
appears only after 2023-11 should be suspected of being an artefact of that
change before it is believed.

### The option not taken

A deliberately slow market-wide crawl — one call per minute, resumable across
runs, roughly 17 hours of wall clock — would respect the rate limit and close
the gap. It is a large commitment of runner time against an endpoint that may
simply refuse, so it is offered as a decision rather than started.
