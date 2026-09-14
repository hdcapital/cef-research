# Market-wide discount history

Monthly average discount to NAV/NTA for listed closed-end funds, as far back
as each market's data goes, at three levels: market-wide, per market, and
per sub-segment within each market.

| Market | Source of truth | Starts | Discount base |
|---|---|---|---|
| UK investment trusts | AIC Monthly Information Release | 2007-01 | published NAV per share |
| ASX LICs / LITs | ASX investment-products monthly report | 2017-01 | pre-tax NTA |

## Running it

The aggregation is a pure layer over the two existing panels, so build those
first:

```bash
python -m uk_cef.cli build-entities && python -m uk_cef.cli build-panel
python -m au_lic.cli build-panel
python -m discount_history.cli build
```

The raw archives behind those panels live in S3, not in the repo, so a run
outside CI needs bucket credentials (`S3_BUCKET`, `AWS_*`) and then
`python scripts/sync_state.py pull --groups raw_aic,raw_asx`. The
`discount-history` workflow does exactly this end to end and commits the
results; it is the supported way to run the whole thing.

## What it writes (`outputs/discount_history/`)

| File | Contents |
|---|---|
| `cef_discount_history.xlsx` | every series plus the underlying panel, one sheet each |
| `monthly_market_wide.csv` | both markets pooled, one row per month |
| `monthly_by_market.csv` | one row per month per market |
| `monthly_by_segment.csv` | month x market x sub-segment |
| `monthly_by_sector.csv` | month x market x the market's own sector |
| `segment_summary.csv` | whole-sample stats per market x sub-segment |
| `coverage_by_market_year.csv` | rows held vs rows used, per market-year |
| `security_month_panel.csv` | the fund-month rows behind every number |
| `summary.json` | headline figures |
| `charts/*.png` | the chart set |

## Reading the numbers

**Sign.** Discounts are decimals on the convention `price / NAV - 1`.
Negative means trading *below* asset value: `-0.15` is a 15% discount. A
**falling line is a widening discount**. Columns ending `_pct` are the same
number in percentage points.

**Which average.** `mean_discount` gives every fund one vote (the average
*fund*'s discount); `cap_weighted_discount` weights by market cap (the
average *dollar*'s discount). They separate when large and small trusts are
priced differently, which is a finding rather than an error. On the
market-wide sheet, `mean_discount_balanced` gives each *market* one vote,
because pooling every fund lets the UK's larger fund count dominate.

**The composition break.** The ASX panel starts ten years after the UK one.
`markets_included` records which markets are in each pooled month; a pooled
series read across 2017 is comparing different universes. Use the per-market
sheet, or restrict to 2017+, for like-for-like.

**Thin groups.** Distribution stats are blanked when a group is thinner than
`--min-funds` (default 3) and the row is marked `sufficient = False`.
`n_funds` is always kept, so thinness is visible instead of implied.

**Stale sub-segments.** `segment_summary` resolves "latest" per market *and*
per segment, never against one global last month - the two markets end on
different months. A segment that stopped reporting keeps its own honest
final reading and is marked `is_current = False`.

## Sub-segment taxonomy

The AIC has renamed its sectors repeatedly since 2007 ("Sector Specialist:
Debt" to "Debt - Loans & Bonds", "China / Greater China" to "China - Greater
China", and so on). Grouping on the raw label snaps a 19-year series in half
at every rename, so `discount_history/segments.py` derives two levels from
the label text on every run:

* `sector_canon` - the market's own sector, normalised for punctuation and
  known renames. A sector the AIC introduces later keeps its own name rather
  than being merged into an existing one.
* `segment` - a coarse cross-market asset-class bucket (UK Equity, Private
  Equity, Property, Debt & Fixed Income, Infrastructure & Renewables, ...),
  so a UK sub-segment can be read against its ASX counterpart.

Rules are ordered and first-match-wins, so narrow patterns must precede the
broad ones that contain them. Anything unmatched lands in `Unclassified`
rather than being dropped.

## Scope

Rows are the upstream panels' eligible universe: VCTs, split-capital share
classes and ZDPs excluded, and residual data errors outside -85%/+100%
dropped. Nothing is interpolated and no missing observation is filled.
`market_cap_local` is GBP millions for the UK and AUD millions for the ASX;
it is only ever a within-market weight, and the two are never summed.
