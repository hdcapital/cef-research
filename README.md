# UK Investment Trust Discount Backtest (free data only)

> **Historical backtests are research tools, not investment advice. No missing
> financial data has been synthetically filled.**

A fully reproducible research pipeline testing whether a disciplined investor
could historically have generated excess returns by systematically buying
UK-listed closed-end investment companies at wide discounts to NAV, exploiting
discount mean reversion, and tilting toward observable corporate-action
activity — conceptually inspired by closed-end-fund specialists such as
Almitas Capital, but built only on free public data and pre-specified rules.

## Objective

Answer, with real point-in-time data from 2007 to the latest month:

1. Do wide discounts predict subsequent returns in UK investment trusts?
2. Is a trust cheap *versus its own history* (discount z-score) a better
   signal than absolute discount?
3. Does abnormal recent discount widening predict mean reversion?
4. How much survives realistic transaction costs, and did it persist
   out-of-sample (2022+)?
5. How much of the return is NAV beta vs discount capture, and is ~15–20%
   gross per year plausible from long-only discount investing?

The generated answers live in **[`outputs/report.md`](outputs/report.md)**.

## Data sources

**Primary and only return source: the AIC Data Archive**
(<https://www.theaic.co.uk/research-tools/data-archive>), which publishes
historical monthly files back to January 2007:

| Publication | Format | Used for |
|---|---|---|
| Monthly Information Release (MIR) | CSV (+ errata CSVs) | Point-in-time universe, month-end mid price, NAV per share (all published bases), shares in issue, sector, SEDOL/ISIN, trailing dividend yield, gearing |
| Keyfacts / Industry Overview companies file | XLS/XLSX | Market cap, domicile, member flag, listing venue, ISIN/TIDM (later years) |
| Corporate Activity | XLSX (annual) | Tenders, liquidations, mergers, reconstructions, redemptions, splits, name changes — for entity resolution, outcome classification and the catalyst proxy |

The archive is enumerated from the AIC's own public JSON manifest
(`/sites/default/files/data-archive/data-archive.json`). Downloads are
throttled (~1 request / 1.2 s), cached, hashed and recorded in
`data/manifest.csv`; robots.txt is respected and no access controls are
bypassed.

**Dividend & announcement layer (Investegate)**: `python -m uk_cef.cli
dividends` runs a resumable, identity-verified crawl of the free Investegate
RNS archive (~1 request/1.3s) recovering per-share dividend declarations
with real ex-dates and corporate-action announcements with real announcement
dates, for every universe security with a knowable TIDM (delisted trusts
included where their final ticker still resolves). Parsed dividends are
cross-validated per security-year against the AIC's independently published
trailing yield; failing years are excluded from total-return eligibility,
never silently understated. Total-return results are labelled `_TR`
throughout and sit alongside (never replace) the price-only primary series.

**Optional cross-check only**: `validate --external` compares a sample of MIR
prices against Stooq monthly closes for live tickers. External sources never
contribute a return observation.

## Return definition — the honest headline caveat

**All backtest returns are month-end share *price* returns excluding
dividends**, computed from consecutive MIR month-end prices with
split/consolidation adjustment (detected from shares-in-issue jumps and
cross-checked against Corporate Activity "Capital Change" events).

Why: no free source provides point-in-time dividend histories for the dead,
merged and liquidated trusts that make the universe survivorship-free — and
this project's hard rule is that missing data stays missing. Absolute CAGRs
are therefore not total returns and must not be quoted as such. The
*relative* bias is measured rather than assumed: each portfolio's average
published trailing yield vs the universe's is reported in
`outputs/yield_differentials.csv`, and that gap (~1pp for the tested
portfolios) bounds how much a total-return comparison would move the alphas.
Price returns also treat capital distributions by wind-down vehicles as
losses, which penalises exactly the trusts discount strategies hold.

## Survivorship & look-ahead safeguards

- The universe each month is exactly the set of companies in that month's MIR
  file — dead, merged, renamed and liquidated trusts included through their
  final published month. Nothing starts from today's survivors.
- Entities are keyed by SEDOL (UK ISINs embed the SEDOL, chaining the
  2007-era SEDOL codes to the later ISIN codes without guessing); renames are
  linked via Corporate Activity name-change pairs; manual verified links go
  in `config/entity_overrides.csv`. Nothing merges on fuzzy similarity.
- Month-end *t* information first affects the portfolio held during month
  *t+1* (the AIC publishes ~6 working days after month-end).
- MIR errata in the same monthly bundle supersede the main file; rows first
  published in a *later* bundle (late reporters) are flagged and are not
  signal-eligible before their publication month.
- A disappearing price series is never booked as −100% (or 0%): terminal
  months are classified against Corporate Activity outcomes
  (liquidated/merged/reconstructed/…) and unresolved payoffs stay missing,
  with the affected portfolio weight disclosed in `monthly_returns.csv`.
- Corporate actions are recorded by *effective month*, not announcement date,
  so the catalyst analysis uses a **trailing completed-action proxy** and the
  report explicitly does not claim announcement-day catalyst alpha.

## Strategies (pre-specified, unlevered, long-only)

| | Signal | Portfolio |
|---|---|---|
| A | Absolute discount | Cheapest 10%, equal weight, monthly |
| B | Absolute discount | Cheapest 20% |
| C | 36m discount z-score (min 24m history) | Lowest 10% |
| D | Sector-neutral z-score | Cheapest within each AIC sector |
| E | Overshoot composite: 50% z-rank + 25% discount rank + 25% 3m-widening rank | Lowest 10% |

Benchmarks: equal-weight and cap-weight portfolios of the full eligible
point-in-time universe. (FTSE All-Share *total return* has no legitimate free
historical source, so per the rules it is omitted rather than proxied by a
price index.)

Excluded from the default universe: VCTs, split-capital sectors, ZDPs and
preference/C shares, non-GBX quoted lines (all configurable in
`config/default.yaml`). Costs: scenarios at 0/25/50/100 bps one-way plus 0.5%
UK stamp duty on dutiable buys (offshore-domiciled trusts exempt, unknown
domiciles conservatively treated as dutiable).

## Install & run

```bash
pip install -r requirements.txt
pip install -e .
python -m pytest tests/            # unit + parser regression tests

python -m uk_cef.cli discover      # enumerate the AIC archive
python -m uk_cef.cli download      # fetch & cache all needed files (~600, throttled)
python -m uk_cef.cli build-entities
python -m uk_cef.cli build-panel
python -m uk_cef.cli validate      # quality checks + look-ahead assertions
python -m uk_cef.cli backtest
python -m uk_cef.cli report        # charts + outputs/report.md
# or everything:
python -m uk_cef.cli run-all
```

## Live layer: the UK daily discount panel

Separate from the month-end backtest above, `python -m cef_live.cli uk-daily`
maintains a **daily** panel for the UK funds that are still listed:

* **NAV/NTA at each fund's own frequency**, re-parsed out of the S3 archive of
  its own "Net Asset Value(s)" RNS announcements (and the nightly live
  snapshots), back to 2007 — daily for most conventional trusts, monthly or
  quarterly for much of the property and infrastructure cohort. Frequency is
  measured per fund, not assumed.
* **Daily closing price**, raw close only. Yahoo's adjusted close is
  retro-adjusted for dividends paid *after* the date, so a discount built on
  it changes whenever the fund next pays out.
* **Daily discount**, `close / last published NAV − 1`, joined as-of on the
  **publication** date rather than the valuation date, with a staleness flag
  scaled to each fund's own publication cadence.

Three idempotent, incremental stages (`--stages nav,prices,discount`) so the
`uk-daily-discount` workflow can re-run it every weekday evening for a few
hundred requests. Panels live in S3 (`data/uk/**`, state group `uk_daily`);
the small per-fund deliverables are committed under `outputs/live/`.
See **docs/RUNBOOK.md** for the pence/pounds and publication-date traps this
is built around, and for the measured coverage boundary.

## GitHub Actions

`.github/workflows/backtest.yml` runs the whole pipeline (tests → discover →
download (cached between runs) → panel → validate → backtest → report),
uploads `outputs/` as an artifact, and runs monthly (12th, 06:00 UTC) after
the AIC's publication window. Trigger manually with *workflow_dispatch*. No
paid infrastructure or API keys are required.

## Repository layout

```
config/            default.yaml (all knobs), entity_overrides.csv
src/uk_cef/        cli, config, data_sources/ (aic, prices), parsers/
                   (mir, companies, corporate_activity), entities, panel,
                   signals, portfolio, deciles, costs, performance,
                   runner, validation, reporting
tests/             unit tests + parser regressions on real sample files
data/raw|cache/    downloaded archive files (gitignored; recreated by CLI)
data/probe/        archive reconnaissance + genuine sample files (fixtures)
outputs/           data_inventory, coverage reports, holdings, trades,
                   monthly/annual returns, performance summary, decile
                   tables, robustness grid, regressions, decomposition,
                   data quality report, charts/, report.md
```

## Auditability

Every backtest number is traceable: `outputs/holdings.csv` lists every
position each month with its discount, z-score, widening, catalyst flag,
weight and next-month return status; `data/manifest.csv` records the SHA256
and URL of every source file; `outputs/data_inventory.csv` maps every archive
file to its parse status.

## Known limitations

- Returns exclude dividends (see above) — the single most important caveat.
- Universe = AIC member companies (plus all-company files from 2013); a few
  non-members (e.g. 3i Group) are absent.
- MIR month-end mid prices can differ slightly from exchange closes.
- Terminal payoffs of delistings are unresolved rather than estimated.
- Corporate-action timing is month-granular and completion-based.
- VCT-heavy early files mean the *eligible* (conventional) universe is
  ~170–200 names per month, not the full 300+ rows per file.
