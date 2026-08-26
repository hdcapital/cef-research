# UK Investment Trust Discount Strategies - Backtest Report

**Historical backtests are research tools, not investment advice. No missing financial data has been synthetically filled.**

Sample: 2007-01 to 2026-07, 303 securities, 41004 security-months. Universe: AIC member conventional investment companies (ordinary shares, London-listed, VCTs/splits/ZDPs excluded), taken point-in-time from the AIC Monthly Information Release archive.

## Return basis - read this first

All returns are **month-end to month-end share price returns excluding dividends**, built from AIC MIR month-end mid prices (the same point-in-time files that define the universe, so dead, merged and liquidated trusts are included up to their final published month). Free, point-in-time dividend histories covering delisted UK trusts do not exist, and per the project's data-integrity rules nothing was synthesised. Because discount strategies systematically hold higher-yielding trusts (see yield differentials below), **price-only returns understate the strategies' total returns relative to the benchmark, not overstate them** - the direction of the bias is conservative for the discount hypothesis, but CAGR levels are NOT total returns and must not be quoted as such.

## Executive summary - the ten questions

**1. Does buying discounted UK investment trusts generate excess returns (before costs)?** Cheapest-decile absolute discount (A): 14.2% price-return CAGR vs 5.2% for the equal-weight universe; annualised alpha 8.3% (t=4.89). The spread is positive.

**2/3. Absolute discount vs z-score (cheap vs own history)?** Strategy C (36m z-score): 25.6% CAGR, alpha 14.6% (t=10.39) vs A's alpha 8.3%. Relative (z-score) ranking adds value over absolute discount.

**4. Does rapid discount widening predict reversion?** Decile 1 of 3-month widening earned 1.79% average next-month return (t=5.94). See decile tables.

**5. Does sector-neutral construction retain the effect?** Sector-neutral z-score alpha: 12.7% vs plain z-score 14.6%.

**6. How much survives realistic costs?** Headline case (50bps one-way + 50bps stamp duty on dutiable buys): strategy A net CAGR 9.1% vs 14.2% gross. Full grid in cost_scenarios.csv.

**7. Does the effect persist in the 2022+ holdout?** Alpha vs EW universe in the holdout: A 10.1%, C 13.9%. Development/validation/holdout splits are in performance_summary.csv.

**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median discount), those with a trailing value-realization corporate action (tender/redemption/capital return/realisation policy/liquidation) earned 1.87% next month vs 2.08% without (n=103 vs 4399). IMPORTANT: the AIC archive records actions by effective month, not announcement date, so this is a 'trailing completed action' proxy - announcement-day catalyst alpha cannot be measured from this source and is not claimed.

**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as (1+r) = (1+NAV return)(1+discount movement): annualised NAV component -0.4%, discount-movement component 14.9%. Per-year detail in return_decomposition.csv and chart 14.

**How severe are drawdowns?** Strategy A max drawdown -44.4% (benchmark -44.7%). Deep-discount portfolios are NOT defensive; they draw down harder in crises and recover faster (stress_episodes.csv).

**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** The best pre-specified strategy delivered 25.6% price-return CAGR (8.3% to 14.6% annualised alpha over the trust universe) before costs. Observed trailing yields: portfolio 1.9% vs universe 2.8%. Adding the portfolio dividend yield to the price CAGR (a back-of-envelope, not a computed total return) still leaves systematic long-only discount capture well short of 15-20% per year. The evidence supports discounts as a real, exploitable return source of roughly mid-single-digit alpha, but 15-20% gross would require substantial additional return sources - leverage, activism/engagement to force realizations, security selection within NAVs, or concentrated catalyst timing on announcement dates - none of which this long-only monthly screen captures.

## Decile tests (Stage 9)

Average next-month price return by signal decile (1 = cheapest/most dislocated):

**absolute_discount**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.20% | 3.92 | 0.89 | 234 |
| 2 | 1.09% | 3.60 | 0.82 | 234 |
| 3 | 0.83% | 2.81 | 0.64 | 234 |
| 4 | 0.67% | 2.30 | 0.52 | 234 |
| 5 | 0.59% | 2.06 | 0.47 | 234 |
| 6 | 0.45% | 1.62 | 0.37 | 234 |
| 7 | 0.30% | 1.12 | 0.25 | 234 |
| 8 | 0.33% | 1.34 | 0.30 | 234 |
| 9 | 0.06% | 0.22 | 0.05 | 234 |
| 10 | -0.44% | -1.91 | -0.43 | 234 |
| 1-10 (long-short) | 1.64% | 8.93 | 2.02 | 234 |

**discount_zscore**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 2.01% | 6.90 | 1.64 | 211 |
| 2 | 1.33% | 5.09 | 1.21 | 211 |
| 3 | 1.08% | 4.22 | 1.01 | 211 |
| 4 | 0.95% | 3.46 | 0.83 | 211 |
| 5 | 0.83% | 3.06 | 0.73 | 211 |
| 6 | 0.74% | 2.78 | 0.66 | 211 |
| 7 | 0.63% | 2.40 | 0.57 | 211 |
| 8 | 0.46% | 1.68 | 0.40 | 211 |
| 9 | 0.15% | 0.58 | 0.14 | 211 |
| 10 | -0.52% | -1.89 | -0.45 | 211 |
| 1-10 (long-short) | 2.52% | 12.65 | 3.02 | 211 |

**widening_3m**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.79% | 5.94 | 1.35 | 231 |
| 2 | 1.09% | 3.85 | 0.88 | 231 |
| 3 | 0.94% | 3.45 | 0.79 | 231 |
| 4 | 0.75% | 2.72 | 0.62 | 231 |
| 5 | 0.61% | 2.29 | 0.52 | 231 |
| 6 | 0.51% | 1.93 | 0.44 | 231 |
| 7 | 0.38% | 1.39 | 0.32 | 231 |
| 8 | 0.07% | 0.28 | 0.06 | 231 |
| 9 | -0.10% | -0.34 | -0.08 | 231 |
| 10 | -1.01% | -3.47 | -0.79 | 231 |
| 1-10 (long-short) | 2.79% | 13.59 | 3.10 | 231 |

**overshoot**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 2.21% | 7.42 | 1.77 | 211 |
| 2 | 1.46% | 5.22 | 1.25 | 211 |
| 3 | 1.21% | 4.87 | 1.16 | 211 |
| 4 | 1.02% | 3.71 | 0.88 | 211 |
| 5 | 0.72% | 2.67 | 0.64 | 211 |
| 6 | 0.77% | 2.86 | 0.68 | 211 |
| 7 | 0.50% | 1.94 | 0.46 | 211 |
| 8 | 0.31% | 1.18 | 0.28 | 211 |
| 9 | 0.03% | 0.11 | 0.03 | 211 |
| 10 | -0.55% | -2.12 | -0.51 | 211 |
| 1-10 (long-short) | 2.76% | 14.57 | 3.48 | 211 |

## Cross-sectional regressions (Fama-MacBeth, Newey-West t-stats)

| spec | variable | mean coef | t (NW) | months |
|---|---|---|---|---|
| univariate_discount | const | 0.0016 | 0.61 | 234 |
| univariate_discount | discount | -0.0421 | -5.44 | 234 |
| univariate_zscore | const | 0.0069 | 2.71 | 211 |
| univariate_zscore | discount_z_36m | -0.0060 | -10.74 | 211 |
| univariate_widening3m | const | 0.0045 | 1.49 | 231 |
| univariate_widening3m | discount_change_3m | -0.1859 | -12.27 | 231 |
| multivariate | const | 0.0005 | 0.20 | 211 |
| multivariate | discount | -0.0234 | -3.48 | 211 |
| multivariate | discount_z_36m | -0.0028 | -5.89 | 211 |
| multivariate | discount_change_3m | -0.1512 | -9.90 | 211 |
| multivariate | log_mcap | 0.0009 | 2.74 | 211 |
| multivariate_sector_demeaned | const | -0.0000 | -0.28 | 211 |
| multivariate_sector_demeaned | discount | -0.0212 | -3.58 | 211 |
| multivariate_sector_demeaned | discount_z_36m | -0.0031 | -6.73 | 211 |
| multivariate_sector_demeaned | discount_change_3m | -0.1695 | -11.79 | 211 |
| multivariate_sector_demeaned | log_mcap | 0.0005 | 1.93 | 211 |

No causal claims are made; these test incremental predictive information only.

## Robustness (Stage 28)

20 pre-specified variants (portfolio size, z-window, rebalance frequency, weighting, market-cap floors): 100% have positive alpha vs the EW universe. Full grid in robustness_grid.csv. If only isolated cells worked, that would indicate overfitting; the grid shows whether the effect occupies a broad region.

**Measurement-error (skip-month) test.** A mis-recorded month-t price both widens the apparent discount and mechanically reverses next month, inflating t+1 results; it cannot inflate the month t+2 return. Alphas when the first post-signal month is skipped:

| variant | alpha (annual) | t | CAGR |
|---|---|---|---|
| discount_top10_SKIP1M | 0.7% | 0.41 | 5.7% |
| z36m_top10_SKIP1M | 1.3% | 1.11 | 10.2% |
| overshoot_top10_SKIP1M | 3.1% | 2.50 | 12.2% |

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