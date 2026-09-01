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

### After changing the UK NAV parser

The crawl skips any announcement already in the S3 manifest, which is right
for fetching and wrong for parsing: an announcement recorded
`no_nav_parsed` would never be revisited, so a better rule list could not
reach the funds it was written for. The archived payload keeps the
announcement TEXT, so re-parsing costs nothing:

```
Actions -> uk-nav-archive -> Run workflow -> mode: reparse
```

It reads the objects back from S3, re-runs `harvest_nav.parse_uk_nav_text`,
and writes `data/uk_nav_history_reparse_s*.parquet`, which
`_own_nav_history` picks up alongside the crawl shards (rows are
deduplicated on `ann_id`, with the parsed outcome winning).

Measured on the committed corpus (`data/uk_nav_corpus.json.gz`, 175 real
announcements), the rule list reads 174 - up from 136. The one it refuses
is a euro-denominated fund, which is the correct answer: a cents figure in
a pence column is the unit bug again.

### Two failure shapes worth recognising

Both were found by taking a single fund from the coverage audit's RED list
and asking why, and both turned out to be one line of pattern or one
constant - not missing data.

**A fund whose NAV we never even asked for.** Achilles (AIC) published a NAV
on 7 August; the harvest ran on the 30th with a 7-day window and reported
`no_recent_nav`. A week-long window can only see a fund that publishes at
least weekly, and monthly/quarterly publishers are most of the offshore,
property and infrastructure cohort. Achilles also listed in 2025, after the
crawl that built the listing cache, so it had no listing index either - and
the archive job's queue IS that cache. Both are now closed: the Tier 0
window is 45 days, and every listing page the harvest fetches is written
back to the cache it was missing from.

**A fund whose NAV we hold and cannot see.** Underwood Capital (UWC)
publishes its monthly NTA as "UWC Investment Portfolio Performance July
2026". The announcement was indexed on 12 August with a working PDF link
and was never fetched, because the headline contains none of NTA, net
tangible, net asset, NAV or fund update. Australian Leaders (ALF) publishes
under the same shape.

The general lesson: when a fund is RED, check the reach before the data.
`reports/build/uk_tier0_debug.json` and `reports/build/asx_tier0_debug.json`
carry the funnel - candidates, codes with a candidate, attempted, parsed -
so "we never asked" is distinguishable from "we asked and it failed".

### Where the extracted ASX NAV facts live

`python -m au_lic.extract.runner deterministic` writes
`data/asx_extract/facts_det_*.parquet` AND uploads each shard to
`s3://$S3_BUCKET/asx/extract/`. **S3 is the system of record**; the local
directory is a cache a fresh runner does not have.

`au_lic.extract.facts.load()` restores from S3 when the cache is empty, and
both `cef_live.cli._own_nav_history("AU")` and the extraction runner's own
validation go through it. Before that, the nightly restored every other
state group but not this one, so the "our own extracted NAV history" tier
was always empty for Australia and every ASX fund fell back to the
aggregator's monthly print - with 26,274 extracted NAV observations across
147 tickers sitting in the bucket.

The extracted facts currently stop at **2023-08**, because the announcement
index had a hole from 2023-11 until the daily forward top-up repaired it
and `asx-extract` is dispatch-only. To bring the extracted NAV history up
to date, re-run `asx-extract` in `deterministic` mode over the repaired
index; the PDFs are already archived daily to S3 by `asx-s3-archive`.

### Canonical units

`src/cef_live/units.py` states them once: UK NAV and price are both **GBX
(pence)**; AU NAV and price are both **AUD (dollars)**. Conversions are
explicit and only applied when the source unit is stated. A UK NAV read as
pounds and divided into a pence share price is the bug that produced
premiums of 80x-5,000x; the fix is at the reader, never at the output.

## Schedules

| Job | When | Purpose |
|---|---|---|
| `cef-ideas` | 07:20 London, 09:10 Sydney | Pre-open brief: announcements -> catalysts -> gates -> **the only two emails of the day**, each with the universe workbook attached |
| `cef-live-nightly` | 21:00 UTC weekdays | Registry, tickers, NAV, IRR, universe spreadsheet (built + committed, not emailed) |
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
| `uk_discount_today.csv` | fund, today, restricted to rows an analyst can act on |
| `uk_nav_archive_readiness.csv` | fund: why a short history is short |
| `uk_nav_quality.csv` | fund: how much its NAV series can be trusted |
| `uk_index_gap_s*of6.csv` | fund: what the gap crawl indexed, or why it could not |

### Four traps this panel is built around

Every one of these produces a plausible percentage rather than an error,
which is why each is named in code and asserted by test. Three were found by
running the thing, not by reading it.

**The pence/pounds trap.** Yahoo's metadata lies. The round-2 probe recorded
`BSIF.L` at `1.0227` labelled `GBp` for a trust that trades near 102p.
Believing the label divides 1.02p by a 110p NAV: a -99% discount on a
healthy fund. So units are reconciled against each fund's own NAV and a x100
correction is applied only where the ratio clusters tightly at 1/100.

**The publication-date trap.** A 30 June NAV announced on 15 July was not
knowable on 1 July. The as-of join is backward on the publication date; the
valuation date is carried only to measure how old the NAV was.

**The split trap.** Yahoo's `close` is retro-adjusted for splits; a
published NAV is not. A trust that subdivided 10-for-1 in 2021 has its whole
pre-2021 price history divided by ten, while its 2015 RNS states pence per
share on the 2015 share count. The first full run measured this on 17 live
trusts - Bankers, Caledonia, Temple Bar, Polar Capital Technology, Lowland,
Murray International, Alliance Witan - whose price/NAV came back near 0.10,
or near 170 for consolidations. Untreated it produces a fund that sits at a
91% discount for years and re-rates to 10% overnight on the day of the
split: an artefact shaped exactly like a real re-rating. Splits are read
from the same chart response that serves the bars and MERGED with the held
set, because a tail-mode fetch only sees splits inside its own window.

**The fitted-to-noise trap**, which hid inside the first one. The unit
reconciliation wanted to rescale twelve funds by 100. Every one of them -
NCYF, CHI, CMPG, CMPI, CYN, GCL, GPM, MTE, PNL, SST, STS, MAJE - has
`cum_assumed_share` 1.0 and a median change between CONSECUTIVE publications
of 26-33%, against 0.54% for the panel. The parser's unlabelled fallback was
matching whatever number sat nearest the words "net asset value", and it was
a different number each time. A scale fitted to that makes a bad NAV look
coherent: CQS New City High Yield was priced at 5060p against an 8879p
"NAV", for a trust that trades near 50p.

Reliability is therefore measured from the series itself
(`unreliable_nav_series`: median change > 15% over at least 10
observations), and the verdict is used twice - no scale is fitted to an
unreliable series, and the panel withdraws its discount. The rows and the
published numbers stay, because deleting them would hide the problem rather
than state it.

### How much the extracted NAV can be trusted

A parser that picks the wrong number off a page does not fail loudly - it
returns a number, and a series of plausible numbers is exactly what a
discount panel cannot detect. But a mis-parse has a signature: a large move
between consecutive publications that immediately REVERSES, because the next
announcement goes back to reading the right line. A real NAV move does not
come back the next day.

Measured over the panel as committed (367,008 consecutive-publication pairs,
2026-08-31):

| | |
|---|---|
| median change between publications | 0.54% |
| 90th percentile | 1.8% |
| moves > 25% | 0.85% |
| jump-and-reverse (likely mis-parse) | 0.065% |

That is what fund NAVs look like. **The finding that matters when using the
panel**: rows flagged `cum_assumed` - the parser's plain fallback, used where
an announcement states no income basis - move > 25% at **3.55%** against
**0.083%** for rows matched by a labelled rule. A 43x higher rate. They are
23% of the panel. They are kept, because they are real observations, and
they are flagged on every row so an analysis that cannot tolerate them can
drop them with one filter. Twelve funds sourced ENTIRELY from that fallback
are excluded from carrying a discount at all - see the fitted-to-noise trap
above; `uk_nav_quality.csv` names them and shows the measurement.

An external check is thinner: only three points in the committed data are
independently comparable against the AIC's own published NAV (the rest of
the aggregator-anchored funds are outside our extracted history). HGEN
matched exactly; EOT and ATY differed by +4.1% and -3.4% on the same date,
which is the size of a NAV *basis* difference - cum vs ex income, debt at
fair value vs par - rather than a parse error. A fuller comparison needs the
AIC panel rebuilt, which the backtest workflow does.

### Coverage, as measured (2026-08-31, run 8)

| | |
|---|---|
| live UK funds addressed | 271 |
| funds with a daily price series | 282 (Yahoo serves 4 nothing) |
| daily price bars | 1,156,446 |
| NAV panel | 472,899 observations across 207 funds |
| daily discount panel | 1,116,334 fund-days, 2007-01-02 to 2026-08-27 |
| fund-days carrying a discount | 651,629 |
| ...against a NAV fresh by that fund's cadence | 547,941 |
| funds with a discount series | 169 |
| ...with history back to 2008 or earlier | 87 |
| ...with NAV into 2026 | 146 |
| funds usable today | 143 |
| median discount today | -6.1% |

Publication cadence, measured per fund over its own last two years: 138
daily, 15 quarterly, 13 monthly, 9 semiannual, 6 weekly, 7 ad hoc, 19 with a
single observation. The daily publishers supply 475,022 of the 547,941 fresh
fund-days; the quarterly and monthly cohort supplies 49,352, which is the
whole point of making the staleness rule relative to each fund's own gap
rather than to one number.

Unit outcomes across 282 priced funds: 169 reconcile directly, 66 are taken
from the quote currency where no NAV overlaps, 5 are refused as a currency
mismatch, 5 more as an unresolvable scale, 7 as too dispersed, and 1 for an
incoherent NAV series. 43 funds needed split adjustment.

`nav_readiness` is now 195 archived, 40 indexed and awaiting the archiver,
31 that publish no NAV RNS at all, and 5 with no listing index - against 135
unindexed before the gap was closed.

### The gap, and what closing it found

The listings crawl was seeded from the AIC panel's *eligible* universe, and
eligibility means the aggregator publishes a price and a NAV - exactly the
test the infrastructure, renewables, property and private-equity trusts
fail. So those funds were never indexed, never archived, and had no NAV.

`uk-index-gap` indexed them. Six shards, under 12 minutes, 99 funds
attempted:

- **70 funds newly indexed, 34,287 NAV announcements** - JPMorgan EMEA
  (5,180), Canadian General (3,179), River UK Micro Cap (2,940), India
  Capital Growth (2,785), Vietnam Enterprise (2,476), VinaCapital (2,027),
  Ruffer (1,275).
- **13 have no Investegate company page** under their ticker - BioPharma
  Credit, Tetragon, Volta Finance, Tufton, US Solar Fund among them, several
  foreign-currency lines. Recorded `not_found`, not guessed around.
- **16 were indexed fully and publish no "Net Asset Value(s)" RNS at all** -
  Tritax Big Box (2,769 announcements), Life Science REIT (1,486), Molten
  Ventures (1,297), Home REIT (1,290), Real Estate Credit (1,136). UK REITs
  and several PE vehicles state EPRA NTA inside interim and annual results
  instead. That is a different source, not a failed crawl, and the run now
  records the headlines those funds DO publish so the distinction stays a
  measurement rather than an inference.

`uk_nav_archive_readiness.csv` separates the causes per fund, and says
`unknown_listing_index_not_pulled` rather than guessing when the index was
not pulled.
