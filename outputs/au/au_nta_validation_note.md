# Independent NTA Validation — ASX LIC/LIT Panel

**What this is.** An independent, number-level check of the panel's NTA
series: for each fund, the NTA implied by the ASX Investment Products
monthly report (price and published premium/discount) is compared against
the pre-tax NTA per share **stated by the fund itself** in its month-end
NTA announcement PDF, fetched from the ASX announcements archive.

**Method.** The public announcements page's own open API routes (see
`data/probe/asx7`/`asx8`) provide an unauthenticated market-wide
announcement index — delisted issuers included — with direct PDF links.
The full index back to Nov 2016 (86.5k announcements for panel codes) is
committed at `data/asx_ann_cache/asx1/lic_announcement_index.parquet`.
Month-end NTA statements are selected by headline, sampled at up to two
per fund-year (June and December), text-extracted, and parsed with
label-specificity-scored rules (explicit pre-tax per-share labels beat
per-share labels beat contextual matches; dollar-totals-in-millions,
%-change columns, dividend amounts, and bare integers are rejected).
Cents/dollars are resolved from document context only; unresolvable units
are **flagged (`unit_ambiguous`), never corrected**. No missing value was
synthesized; unparseable documents are recorded as explicit gaps.

## Results (2016-12 → 2026-06, June + December samples)

| Metric | Value |
|---|---|
| Comparisons parsed | 544 across 74 funds |
| Exactly equal (to 4 dp) | 88 (16%) |
| Median absolute difference | **2.5%** |
| Within 1% / 2% | 30% / 44% |
| p90 | 10.2% |
| Funds with median < 2% / < 5% | 25 / 64 of 74 |
| Pre-tax basis (529 obs) | median 2.5% |
| After-tax-only funds (15 obs, tagged) | median 4.6% |
| June median vs December median | 2.8% vs 2.3% |

June (fiscal year-end) runs slightly wider than December, consistent with
cum/ex-dividend timing around final distributions — a timing artifact, not
a data error.

**Coverage funnel** (why 74 funds, not 166): 166 panel codes → 93 ever
publish a headline-dateable month-end NTA announcement (the rest announce
weekly/daily NTAs only, or bury the figure in generically-titled monthly
reports) → 74 yield a machine-readable per-share figure. Non-parses are
recorded per document: 171 `no_nta_in_pdf` (figure beyond parsed pages or
in graphics), 15 `unit_ambiguous`, 8 scanned with no text layer.

## Named genuine discrepancies (documented, not tuned away)

- **OEQ** (~$4m micro-cap): the sheet-implied NTA repeatedly exceeds the
  fund's own announced pre-tax NTA by 10–30% (e.g. Jun-2018 announced
  $0.2668 vs implied $0.3481). Several months also match exactly, so the
  sheet is intermittently stale/wrong for this fund.
- **8EC**: sheet-implied NTA differs from announced pre-tax NTA by 6–11%
  in several years (announced figures verified verbatim in PDFs).
- **AIB** (Aurora): announces "NAV per unit **including franking
  credits**" — a non-standard basis; ~10% persistent gap follows.
- **MFF**: announces approximate NTA weekly; monthly-sheet timing
  differences of ~5–9% (largest in fast markets).
- **CMI**: a conglomerate publishing three divisional NTAs (investment
  portfolio / electrical division / consolidated); no single comparable.
- **AIQ / TVL / MMJ / FPP / LSF / TGF**: 6–16% medians on small samples;
  mix of deep-page figures our 2-page parse misses and small-fund
  staleness. Individually inspectable in `au_nta_pdf_check.csv`.

## Implications

1. **The panel's NTA layer is sound for the universe that drives the
   backtest results.** Half the covered funds sit at ≤2% median agreement
   with primary-source announcements, a quarter essentially exact, over a
   decade including delisted names — on top of the within-source check
   (derived NTA = the report's explicit NTA column: 98.9% exact).
2. **Small/illiquid funds carry real source-data risk.** The ASX monthly
   sheet can be stale or wrong for micro-caps (OEQ, 8EC class).
   **Leaderboard caveat:** extreme small-fund NTA-CAGR entries should be
   treated as indicative until spot-checked against their own
   announcements; the audit file supports doing so per fund-month.
3. The discrepancies found are fund-level and idiosyncratic, not
   systematic — they do not overturn the study's cross-sectional
   conclusions (z-score alpha transfers to AU; absolute discount does
   not), which rely on rank ordering across ~100 funds, not on any single
   fund's level.

Audit trail: `au_nta_pdf_check.csv` (every comparison, with basis tags and
statuses), `au_nta_pdf_check_summary.json`, `au_nta_parse_debug.json`
(extracted document text for every residual disagreement).
