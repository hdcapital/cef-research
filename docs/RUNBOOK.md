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
| `uk-daily-discount` | 18:40 UTC weekdays | UK NAV (from S3) + daily close -> daily discount panel |

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
| `uk_daily` | UK NAV / price / discount panels | ~500k archive reads + ~300 price histories |

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

## The UK daily discount panel

`python -m cef_live.cli uk-daily` builds, for every UK fund still listed:
its published NAV history at whatever frequency that fund supplies it, its
daily closing price, and the daily discount of one to the other. The
`uk-daily-discount` workflow runs it at 18:40 UTC on weekdays — after the
London close has settled and after the evening RNS window in which most
trusts publish the day's NAV.

Three idempotent stages, each runnable alone with `--stages`:

| stage | source | incremental because |
|---|---|---|
| `nav` | S3 `uk/nav_announcements/**` + `nta_live/*.parquet` | the archive key carries the ann_id, so what is still to read is a set subtraction against the panel — no cursor to corrupt |
| `prices` | Yahoo chart API, one request per fund | a held fund is topped up over a 30-day tail; only a new fund costs a full history |
| `discount` | the two above | pure computation |

An ordinary evening is ~300 requests and a couple of MB of new parquet. A
run on an empty checkout with bucket credentials rebuilds from 2007.

**NAV values are never re-read from Investegate here.** The archived
announcement TEXT is re-parsed out of S3, so parser improvements propagate
to a decade of history at zero cost to the publisher — and a row stored as
`no_nav_parsed` under an older rule list becomes a real observation under
today's.

### Where the panels live

`data/uk/{nav,prices,discount}/{YYYY}.parquet`, gitignored, synced as the
S3 state group `uk_daily`. A daily job rewriting a 25MB parquet in git
would add ~9GB of history a year, so the repo keeps only the small per-fund
deliverables under `outputs/live/`:

| file | one row per |
|---|---|
| `uk_discount_latest.csv` | fund, today: price, NAV, age, discount, z |
| `uk_discount_coverage.csv` | fund: span, days with a discount, unit status, which store its NAV came from |
| `uk_nav_frequency.csv` | fund: measured publication cadence |
| `uk_price_unit_reconciliation.csv` | fund: the scale applied and the evidence for it |
| `uk_nav_archive_readiness.csv` | fund: why a short history is short |

### Two traps this panel is built around

**The pence/pounds trap.** Yahoo's metadata lies. The round-2 probe
recorded `BSIF.L` at `1.0227` labelled `GBp` for a trust that trades near
102p. Believing the label divides 1.02p by a 110p NAV: a −99% discount on a
healthy fund, indistinguishable in the panel from terminal collapse. So
units are reconciled against each fund's own NAV and a ×100 correction is
applied only where the ratio clusters tightly at 1/100 — real funds do not
trade at 1% of NAV for years. Anything ambiguous gets no discount at all.

**The publication-date trap.** A 30 June NAV announced on 15 July was not
knowable on 1 July. The as-of join is backward on the publication date; the
valuation date is carried only to measure how old the NAV was. Joining on
the valuation date would hand every row in the panel a hindsight window as
wide as that fund's own reporting lag.

### Staleness is per fund, not per panel

A daily publisher whose NAV is a fortnight old has stopped publishing. A
quarterly publisher whose NAV is a fortnight old is completely normal. One
threshold cannot express both — 45 days blanks most of the infrastructure
and property cohort, 200 days lets a suspended daily publisher look
tradable. So the limit is 3× that fund's own median publication gap, floored
at 14 days, and `discount` (the market's own convention: price against the
last published NAV, however old) sits alongside `discount_fresh` (the
stricter reading) with `nav_age_days` and `nav_stale` between them.

### Known boundary: 150 live funds have no archived NAV history

Measured 2026-08-31, from the committed seed alone: 138 of 288 live funds
have NAV history. The other 150 (105 non-VCT, 83 of them `announcements_only`
— HICL, Tritax Big Box, 3i Infrastructure, BH Macro, the renewables cohort)
are missing for a reason that is about our crawl, not about them: the
listings crawl was seeded from the AIC panel's *eligible* universe, which
excludes exactly the funds the aggregator declines to price.

`uk_nav_archive_readiness.csv` separates the causes per fund, and the fix
differs by cause:

- `indexed_not_archived` → run `uk-nav-archive`; the text is fetchable.
- `no_listing_index` → extend the listings crawl (`uk-listings-refresh`) to
  the live registry's tickers. Nothing in S3 can supply what was never
  indexed.
- `archived` → the history is genuinely the fund's own.

Meanwhile these funds do accrue NAV forward from the nightly `nta_live`
snapshots, which poll every addressable fund. So the honest reading of the
panel today is **full history back to 2007 for the archived cohort, and
forward-only history for the rest** — and `nav_history_source` in the
coverage report says which a fund is, per fund, rather than leaving a short
series to be mistaken for a short life.
