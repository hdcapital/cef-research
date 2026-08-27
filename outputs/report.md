# UK Investment Trust Discount Strategies - Backtest Report

**Historical backtests are research tools, not investment advice. No missing financial data has been synthetically filled.**

Sample: 2007-01 to 2026-07, 303 securities, 41241 security-months. Universe: AIC member conventional investment companies (ordinary shares, London-listed, VCTs/splits/ZDPs excluded), taken point-in-time from the AIC Monthly Information Release archive.

## Return basis - read this first

All returns are **month-end to month-end share price returns excluding dividends**, built from AIC MIR month-end mid prices (the same point-in-time files that define the universe, so dead, merged and liquidated trusts are included up to their final published month). Free, point-in-time dividend histories covering delisted UK trusts do not exist, and per the project's data-integrity rules nothing was synthesised. CAGR levels are therefore NOT total returns. The direction of the relative bias is measured, not assumed: outputs/yield_differentials.csv reports each portfolio's average published trailing yield against the universe's, and the strategy-minus-universe yield gap (about a percentage point on the tested portfolios) bounds how much a total-return comparison would shift the alphas. Price returns also treat capital distributions by wind-down vehicles as losses, which penalises exactly the trusts discount strategies hold.

## Executive summary - the ten questions

**Headline caution.** Every strategy's alpha in this study is concentrated in the FIRST month after the month-end signal (the skip-month test in the Robustness section quantifies this). The standard results below assume the portfolio earns the full calendar month t+1 after a month-end-t signal - achievable only by monitoring discounts in near-real-time (daily RNS NAVs make this practicable) and trading promptly at the month turn. The skip-month figures are the conservative bound for slower execution, and part of the first-month effect may be price-measurement noise rather than harvestable reversion. Both bounds are reported; quote them together.

**1. Does buying discounted UK investment trusts generate excess returns (before costs)?** Cheapest-decile absolute discount (A): 13.7% price-return CAGR vs 5.1% for the equal-weight universe; annualised alpha 8.0% (t=4.79). The spread is positive, but see the headline caution: it shrinks dramatically when the first post-signal month is skipped.

**2/3. Absolute discount vs z-score (cheap vs own history)?** Strategy C (36m z-score): 25.6% CAGR, alpha 14.7% (t=10.39) vs A's alpha 8.0%. Relative (z-score) ranking adds value over absolute discount.

**4. Does rapid discount widening predict reversion?** Decile 1 of 3-month widening earned 1.77% average next-month return (t=5.92). See decile tables.

**5. Does sector-neutral construction retain the effect?** Sector-neutral z-score alpha: 12.6% vs plain z-score 14.7%.

**6. How much survives realistic costs?** Headline case (50bps one-way + 50bps stamp duty on dutiable buys): strategy A net CAGR 8.8% vs 13.7% gross. Full grid in cost_scenarios.csv.

**7. Does the effect persist in the 2022+ holdout?** Alpha vs EW universe in the holdout: A 9.3%, C 14.0%. Development/validation/holdout splits are in performance_summary.csv.

**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median discount), those with a trailing value-realization corporate action (tender/redemption/capital return/realisation policy/liquidation) earned 1.81% next month vs 2.06% without (n=103 vs 4460). The AIC archive records actions by effective month, so this is a 'trailing completed action' proxy - but the announcement-dated test (real RNS dates from Investegate; see the Announcement-dated catalysts section) reaches the same conclusion: trusts with a recently ANNOUNCED catalyst earned less, not more, the following month, consistent with the discount re-rating at the announcement itself before a monthly signal can react. Catalyst alpha, if harvestable, requires announcement-day execution or pre-announcement positioning, not a monthly screen.

**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as (1+r) = (1+NAV return)(1+discount movement): annualised NAV component -1.0%, discount-movement component 14.7%. Essentially all of the strategy's excess return is discount capture, not superior NAVs. (Both components exclude distributions: NAV per share falls on ex-dividend dates, so the NAV component is downward-biased by roughly the portfolio yield.) Per-year detail in return_decomposition.csv and chart 14.

**How severe are drawdowns?** Strategy A max drawdown -44.4% (benchmark -44.7%). Deep-discount portfolios are NOT defensive; they draw down harder in crises and recover faster (stress_episodes.csv).

**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** Taken at face value the best pre-specified strategy delivered 25.6% price-return CAGR (8.0% to 14.7% annualised alpha) before costs. Observed trailing yields: portfolio 2.0% vs universe 2.9%. Three deductions are required before treating that as attainable: (i) the skip-month test (below) caps the alpha that survives without trading the very first post-signal month at 2.8% - the remainder is fast one-month reversion that monthly month-end rebalancing overstates and real execution would partly miss; (ii) realistic costs remove 5-10pp from the high-turnover variants (cost_scenarios.csv); (iii) the measured portfolio-vs-universe yield gap shifts a total-return comparison slightly against the strategies. The defensible conclusion: systematic long-only discount selection historically supported roughly mid-single-digit to low-double-digit annual alpha over the trust universe before costs, on a universe averaging ~5% price CAGR. That makes a mid-teens gross return an optimistic but not absurd reading of the top variants, while a SUSTAINED 15-20% would additionally require leverage, activism to force realizations, announcement-day catalyst timing, or NAV-level security selection - sources this monthly screen deliberately does not model.

## Total returns (price + parsed dividends)

Real per-share dividends with ex-dates were recovered from the Investegate RNS archive (see outputs/investegate_coverage.csv). Total returns are computed only on the subset of security-years whose parsed dividends pass a cross-check against the AIC's independently published trailing yield (49% of eligible rows pass; the rest are excluded, never assumed dividend-free). These results are on that restricted universe and are labelled TR throughout; the price-only series remains the primary, broadest result.

| strategy (TR basis) | CAGR | Sharpe | alpha vs TR universe | t |
|---|---|---|---|---|
| BM_equal_weight_universe_TR | 7.9% | 0.61 | n/a | n/a |
| A_absolute_discount_decile_TR | 15.9% | 0.93 | 7.2% | 3.41 |
| C_discount_zscore_TR | 26.1% | 1.62 | 12.8% | 7.37 |
| E_discount_overshoot_TR | 30.0% | 1.76 | 15.6% | 8.41 |
| F_quality_only_TR | 10.3% | 0.75 | 0.7% | 0.43 |
| BM_5y_record_universe | 9.4% | 0.76 | n/a | n/a |
| F_value_only_TR | 14.5% | 1.11 | 4.6% | 6.92 |
| F_combined_z0.0_TR | 14.7% | 0.98 | 5.2% | 2.43 |
| F_combined_z-0.5_TR | 15.9% | 1.00 | 6.6% | 1.94 |
| F_combined_z-1.0_TR | 14.8% | 1.07 | 5.4% | 1.35 |

## Quality x value: top-quartile NAV compounders bought on dislocation

Strategy F buys trusts in the top quartile of trailing 5-year dividend-inclusive NAV CAGR, only when the discount is wider than the trust's own trailing norm (z<threshold). The benchmark is the EW portfolio of all trusts with a valid 5-year NAV record, so the comparison isolates the screen itself. Quality-only and value-only variants show whether the combination adds anything beyond its ingredients. Rolling 3/5/10-year NAV CAGRs for every fund are in nav_cagr_rolling.csv.

**Corrected findings (engine fix).** An earlier version of these results contained a carry-forward bug: in months with fewer than five qualifying trusts the engine relabelled the previous month's returns as the current month's, double-counting them (37 of 169 months for this small screen; broad strategies never trigger the path). With the fix: the combination earns ~7% annual alpha at the one-month horizon (t~2-3, stable across thresholds, better than either ingredient alone) - but NOTHING survives the skip-month test (alphas ~0, t~0), and the effect is ~zero from 2022 onward. The combination is therefore the same fast first-month discount snap-back as the simpler signals, concentrated in better companies, not a distinct slow anomaly. The decomposition identity (annual residuals under 3pp, monthly under 2pp) now verifies the attribution.

| variant | basis | CAGR | Sharpe | alpha vs 5y-record universe | t | avg holdings |
|---|---|---|---|---|---|---|
| F_quality_only | gross | 8.2% | 0.62 | 1.5% | 0.98 | 16 |
| BM_5y_record_universe | gross | 6.6% | 0.56 | n/a | n/a |  |
| F_value_only | gross | 11.8% | 0.93 | 4.8% | 7.35 | 33 |
| F_combined_z0.0 | gross | 13.1% | 0.91 | 6.8% | 3.19 | 8 |
| F_combined_z-0.5 | gross | 13.4% | 0.87 | 6.8% | 2.05 | 6 |
| F_combined_z-1.0 | gross | 11.9% | 0.87 | 7.5% | 2.03 | 5 |
| F_quality_only_TR | gross_TR | 10.3% | 0.75 | 0.7% | 0.43 | 15 |
| BM_5y_record_universe | gross_TR | 9.4% | 0.76 | n/a | n/a |  |
| F_value_only_TR | gross_TR | 14.5% | 1.11 | 4.6% | 6.92 | 31 |
| F_combined_z0.0_TR | gross_TR | 14.7% | 0.98 | 5.2% | 2.43 | 7 |
| F_combined_z-0.5_TR | gross_TR | 15.9% | 1.00 | 6.6% | 1.94 | 5 |
| F_combined_z-1.0_TR | gross_TR | 14.8% | 1.07 | 5.4% | 1.35 | 5 |
| F_quality_only_SKIP1M | gross_skip1m | 8.0% | 0.60 | 0.5% | 0.31 | 16 |
| F_value_only_SKIP1M | gross_skip1m | 8.2% | 0.68 | 1.0% | 1.38 | 33 |
| F_combined_z0.0_SKIP1M | gross_skip1m | 7.2% | 0.55 | -0.0% | -0.00 | 8 |
| F_combined_z-0.5_SKIP1M | gross_skip1m | 8.7% | 0.62 | -1.4% | -0.51 | 6 |
| F_combined_z-1.0_SKIP1M | gross_skip1m | 8.9% | 0.60 | -1.4% | -0.33 | 5 |

### The full quality x value surface (4x4 double sort)

Independent monthly quartile sorts: rows = 5y NAV total-return CAGR quartile (Q1 = best compounders), columns = discount z-score quartile (D1 = most dislocated vs own history). Cell = average next-month price return (t-stat). The t+2 panel repeats the sort but skips the first month - what survives there is the slow, harvestable component.

**Horizon t+1**

| | D1 (dislocated) | D2 | D3 | D4 (rich) |
|---|---|---|---|---|
| Q1 (best NAV) | 1.42% (t=4.0) | 0.89% (t=2.4) | 0.84% (t=2.3) | 0.27% (t=0.8) |
| Q2 | 1.09% (t=3.6) | 0.74% (t=2.4) | 0.52% (t=1.8) | -0.01% (t=-0.0) |
| Q3 | 1.53% (t=5.2) | 0.65% (t=2.2) | 0.37% (t=1.3) | -0.11% (t=-0.3) |
| Q4 (worst NAV) | 0.98% (t=2.7) | 0.80% (t=2.2) | 0.20% (t=0.6) | -0.34% (t=-1.0) |

**Horizon t+2_skip**

| | D1 (dislocated) | D2 | D3 | D4 (rich) |
|---|---|---|---|---|
| Q1 (best NAV) | 0.88% (t=2.2) | 0.92% (t=2.3) | 0.74% (t=2.0) | 0.69% (t=1.8) |
| Q2 | 0.66% (t=2.3) | 0.49% (t=1.6) | 0.74% (t=2.4) | 0.47% (t=1.5) |
| Q3 | 0.74% (t=2.6) | 0.73% (t=2.5) | 0.39% (t=1.3) | 0.95% (t=2.9) |
| Q4 (worst NAV) | 0.83% (t=2.5) | 0.59% (t=1.5) | 0.73% (t=1.9) | -0.10% (t=-0.3) |

## Announcement-dated catalysts (Investegate)

Unlike the AIC completion-month proxy, these use REAL announcement dates (tender offers, wind-downs, strategic reviews, reconstructions announced in the trailing 6 months, knowable at signal time):

| group | n | mean next-month return | t |
|---|---|---|---|
| cheap_no_announced_catalyst | 4452 | 2.08% | 21.25 |
| cheap_with_announced_catalyst | 133 | 1.18% | 2.05 |
| difference (announced - none) | 4563 | -0.91% | -1.56 |

## Decile tests (Stage 9)

Average next-month price return by signal decile (1 = cheapest/most dislocated):

**absolute_discount**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.18% | 3.86 | 0.87 | 234 |
| 2 | 1.07% | 3.52 | 0.80 | 234 |
| 3 | 0.83% | 2.79 | 0.63 | 234 |
| 4 | 0.64% | 2.22 | 0.50 | 234 |
| 5 | 0.58% | 2.06 | 0.47 | 234 |
| 6 | 0.46% | 1.63 | 0.37 | 234 |
| 7 | 0.29% | 1.09 | 0.25 | 234 |
| 8 | 0.36% | 1.47 | 0.33 | 234 |
| 9 | 0.04% | 0.15 | 0.03 | 234 |
| 10 | -0.45% | -1.97 | -0.45 | 234 |
| 1-10 (long-short) | 1.63% | 8.98 | 2.03 | 234 |

**discount_zscore**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.99% | 6.85 | 1.63 | 211 |
| 2 | 1.33% | 5.01 | 1.19 | 211 |
| 3 | 1.08% | 4.25 | 1.01 | 211 |
| 4 | 0.90% | 3.33 | 0.79 | 211 |
| 5 | 0.80% | 2.98 | 0.71 | 211 |
| 6 | 0.77% | 2.88 | 0.69 | 211 |
| 7 | 0.61% | 2.32 | 0.55 | 211 |
| 8 | 0.45% | 1.67 | 0.40 | 211 |
| 9 | 0.14% | 0.53 | 0.13 | 211 |
| 10 | -0.50% | -1.83 | -0.44 | 211 |
| 1-10 (long-short) | 2.50% | 12.50 | 2.98 | 211 |

**widening_3m**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.77% | 5.92 | 1.35 | 231 |
| 2 | 1.11% | 3.90 | 0.89 | 231 |
| 3 | 0.93% | 3.41 | 0.78 | 231 |
| 4 | 0.73% | 2.69 | 0.61 | 231 |
| 5 | 0.60% | 2.25 | 0.51 | 231 |
| 6 | 0.50% | 1.87 | 0.43 | 231 |
| 7 | 0.38% | 1.38 | 0.31 | 231 |
| 8 | 0.07% | 0.25 | 0.06 | 231 |
| 9 | -0.09% | -0.30 | -0.07 | 231 |
| 10 | -1.04% | -3.60 | -0.82 | 231 |
| 1-10 (long-short) | 2.81% | 13.86 | 3.16 | 231 |

**overshoot**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 2.21% | 7.41 | 1.77 | 211 |
| 2 | 1.43% | 5.16 | 1.23 | 211 |
| 3 | 1.18% | 4.79 | 1.14 | 211 |
| 4 | 1.03% | 3.76 | 0.90 | 211 |
| 5 | 0.73% | 2.70 | 0.64 | 211 |
| 6 | 0.73% | 2.73 | 0.65 | 211 |
| 7 | 0.51% | 1.97 | 0.47 | 211 |
| 8 | 0.29% | 1.11 | 0.26 | 211 |
| 9 | 0.04% | 0.16 | 0.04 | 211 |
| 10 | -0.56% | -2.12 | -0.51 | 211 |
| 1-10 (long-short) | 2.76% | 14.55 | 3.47 | 211 |

## Cross-sectional regressions (Fama-MacBeth, Newey-West t-stats)

| spec | variable | mean coef | t (NW) | months |
|---|---|---|---|---|
| univariate_discount | const | 0.0016 | 0.60 | 234 |
| univariate_discount | discount | -0.0416 | -5.37 | 234 |
| univariate_zscore | const | 0.0069 | 2.68 | 211 |
| univariate_zscore | discount_z_36m | -0.0059 | -10.53 | 211 |
| univariate_widening3m | const | 0.0044 | 1.46 | 231 |
| univariate_widening3m | discount_change_3m | -0.1831 | -12.38 | 231 |
| multivariate | const | -0.0001 | -0.03 | 211 |
| multivariate | discount | -0.0241 | -3.63 | 211 |
| multivariate | discount_z_36m | -0.0028 | -5.75 | 211 |
| multivariate | discount_change_3m | -0.1490 | -9.90 | 211 |
| multivariate | log_mcap | 0.0010 | 3.03 | 211 |
| multivariate_sector_demeaned | const | -0.0000 | -0.03 | 211 |
| multivariate_sector_demeaned | discount | -0.0224 | -3.82 | 211 |
| multivariate_sector_demeaned | discount_z_36m | -0.0029 | -6.66 | 211 |
| multivariate_sector_demeaned | discount_change_3m | -0.1661 | -12.15 | 211 |
| multivariate_sector_demeaned | log_mcap | 0.0006 | 2.25 | 211 |

No causal claims are made; these test incremental predictive information only.

## Robustness (Stage 28)

22 pre-specified variants (portfolio size, z-window, rebalance frequency, weighting, market-cap floors): 95% have positive alpha vs the EW universe. Full grid in robustness_grid.csv. If only isolated cells worked, that would indicate overfitting; the grid shows whether the effect occupies a broad region.

**Measurement-error (skip-month) test.** A mis-recorded month-t price both widens the apparent discount and mechanically reverses next month, inflating t+1 results; it cannot inflate the month t+2 return. Alphas when the first post-signal month is skipped:

| variant | alpha (annual) | t | CAGR |
|---|---|---|---|
| discount_top10_SKIP1M | 0.9% | 0.51 | 6.0% |
| z36m_top10_SKIP1M | 1.4% | 1.17 | 10.3% |
| overshoot_top10_SKIP1M | 2.8% | 2.28 | 12.0% |
| z36m_mcap100_SKIP1M | -0.3% | -0.23 | 8.9% |
| overshoot_mcap100_SKIP1M | 1.6% | 1.26 | 11.1% |

The gap between the standard and skip-month alphas is an upper bound on how much of the one-month result is fast reversal (genuine or data-noise); the skip-month alpha is the conservative estimate of the harvestable effect.

## Economics of the return (Stage 30)

- **Beta**: most of every portfolio's return is the investment-trust universe itself (see beta estimates in performance_summary.csv).
- **Structural discount alpha**: buying £1 of assets below £1 mechanically raises the yield on cost; visible in the portfolio-vs-universe yield gap.
- **Discount mean-reversion alpha**: captured by the z-score/overshoot signals and the discount-movement component of the decomposition.
- **Catalyst alpha**: only the trailing-completed-action proxy is measurable from free AIC data; announcement-dated catalyst alpha is out of scope for this data set.
- **Leverage**: trusts' internal gearing amplifies both NAV moves and discount moves; portfolio gearing exposure is observable in the panel (gearing column). No fund-level leverage is used anywhere in the primary results.

## Known limitations

- Returns exclude dividends (no free point-in-time dividend history for dead trusts exists); levels are price-return CAGRs, biased conservative for high-yield discount portfolios.
- The universe is AIC member companies (plus all-company files from 2013): a small number of non-member trusts (e.g. 3i Group) are absent.
- Terminal payoffs of delisting trusts (final liquidation distributions, merger terms) are not reconstructable from free sources; final-month returns are flagged unresolved, never assumed -100% or 0%.
- Corporate-action months are effective months, not announcement dates.
- MIR month-end mid prices may differ slightly from exchange closing prices.

Every number above traces to a CSV under outputs/ and from there to named AIC source files - see data/manifest.csv and outputs/data_inventory.csv.