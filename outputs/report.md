# UK Investment Trust Discount Strategies - Backtest Report

**Historical backtests are research tools, not investment advice. No missing financial data has been synthetically filled.**

Sample: 2007-01 to 2026-07, 304 securities, 39812 security-months. Universe: AIC member conventional investment companies (ordinary shares, London-listed, VCTs/splits/ZDPs excluded), taken point-in-time from the AIC Monthly Information Release archive.

## Return basis - read this first

All returns are **month-end to month-end share price returns excluding dividends**, built from AIC MIR month-end mid prices (the same point-in-time files that define the universe, so dead, merged and liquidated trusts are included up to their final published month). Free, point-in-time dividend histories covering delisted UK trusts do not exist, and per the project's data-integrity rules nothing was synthesised. Because discount strategies systematically hold higher-yielding trusts (see yield differentials below), **price-only returns understate the strategies' total returns relative to the benchmark, not overstate them** - the direction of the bias is conservative for the discount hypothesis, but CAGR levels are NOT total returns and must not be quoted as such.

## Executive summary - the ten questions

**1. Does buying discounted UK investment trusts generate excess returns (before costs)?** Cheapest-decile absolute discount (A): 49.0% price-return CAGR vs 145.9% for the equal-weight universe; annualised alpha 175.8% (t=1.26). The spread is positive.

**2/3. Absolute discount vs z-score (cheap vs own history)?** Strategy C (36m z-score): 67.4% CAGR, alpha 179.7% (t=1.36) vs A's alpha 175.8%. Relative (z-score) ranking adds value over absolute discount.

**4. Does rapid discount widening predict reversion?** Decile 1 of 3-month widening earned 13.17% average next-month return (t=1.35). See decile tables.

**5. Does sector-neutral construction retain the effect?** Sector-neutral z-score alpha: 169.6% vs plain z-score 179.7%.

**6. How much survives realistic costs?** Headline case (50bps one-way + 50bps stamp duty on dutiable buys): strategy A net CAGR 41.9% vs 49.0% gross. Full grid in cost_scenarios.csv.

**7. Does the effect persist in the 2022+ holdout?** Alpha vs EW universe in the holdout: A 15.6%, C 25.5%. Development/validation/holdout splits are in performance_summary.csv.

**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median discount), those with a trailing value-realization corporate action (tender/redemption/capital return/realisation policy/liquidation) earned 1.55% next month vs 12.78% without (n=96 vs 4098). IMPORTANT: the AIC archive records actions by effective month, not announcement date, so this is a 'trailing completed action' proxy - announcement-day catalyst alpha cannot be measured from this source and is not claimed.

**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as (1+r) = (1+NAV return)(1+discount movement): annualised NAV component -1.1%, discount-movement component 72.5%. Per-year detail in return_decomposition.csv and chart 14.

**How severe are drawdowns?** Strategy A max drawdown -44.4% (benchmark -44.7%). Deep-discount portfolios are NOT defensive; they draw down harder in crises and recover faster (stress_episodes.csv).

**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** The best pre-specified strategy delivered 67.4% price-return CAGR (175.8% to 179.7% annualised alpha over the trust universe) before costs. Observed trailing yields: portfolio 1.9% vs universe 2.8%. Adding the portfolio dividend yield to the price CAGR (a back-of-envelope, not a computed total return) still leaves systematic long-only discount capture well short of 15-20% per year. The evidence supports discounts as a real, exploitable return source of roughly mid-single-digit alpha, but 15-20% gross would require substantial additional return sources - leverage, activism/engagement to force realizations, security selection within NAVs, or concentrated catalyst timing on announcement dates - none of which this long-only monthly screen captures.

## Decile tests (Stage 9)

Average next-month price return by signal decile (1 = cheapest/most dislocated):

**absolute_discount**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 14.04% | 1.27 | 0.29 | 226 |
| 2 | 10.29% | 1.11 | 0.26 | 225 |
| 3 | 0.70% | 2.28 | 0.53 | 225 |
| 4 | 0.58% | 1.98 | 0.46 | 225 |
| 5 | 0.55% | 1.87 | 0.43 | 225 |
| 6 | 0.36% | 1.24 | 0.29 | 226 |
| 7 | 3590.53% | 1.00 | 0.23 | 226 |
| 8 | 0.22% | 0.86 | 0.20 | 226 |
| 9 | 5708.02% | 1.02 | 0.24 | 225 |
| 10 | -0.65% | -2.73 | -0.63 | 226 |
| 1-10 (long-short) | 14.73% | 1.32 | 0.31 | 225 |

**discount_zscore**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 14.88% | 1.34 | 0.33 | 203 |
| 2 | 1.15% | 4.40 | 1.07 | 202 |
| 3 | 1.03% | 3.91 | 0.95 | 202 |
| 4 | 11.28% | 1.09 | 0.27 | 201 |
| 5 | 0.92% | 2.90 | 0.70 | 204 |
| 6 | 0.55% | 2.02 | 0.49 | 202 |
| 7 | 0.50% | 1.85 | 0.45 | 202 |
| 8 | 0.41% | 1.46 | 0.35 | 203 |
| 9 | 6398.36% | 1.03 | 0.25 | 201 |
| 10 | 4245.93% | 1.00 | 0.24 | 203 |
| 1-10 (long-short) | -4252.07% | -1.00 | -0.24 | 202 |

**widening_3m**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 13.17% | 1.35 | 0.32 | 218 |
| 2 | 1.10% | 3.39 | 0.79 | 218 |
| 3 | 0.81% | 2.87 | 0.68 | 217 |
| 4 | 3515.68% | 1.00 | 0.23 | 218 |
| 5 | 138.80% | 1.00 | 0.24 | 217 |
| 6 | 0.44% | 1.58 | 0.37 | 217 |
| 7 | 10.53% | 1.03 | 0.24 | 216 |
| 8 | 0.04% | 0.13 | 0.03 | 218 |
| 9 | -0.23% | -0.75 | -0.18 | 217 |
| 10 | 5432.91% | 1.00 | 0.23 | 218 |
| 1-10 (long-short) | -5444.79% | -1.00 | -0.23 | 217 |

**overshoot**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 15.41% | 1.36 | 0.33 | 198 |
| 2 | 1.30% | 4.55 | 1.13 | 196 |
| 3 | 12.41% | 1.11 | 0.27 | 197 |
| 4 | 1.20% | 2.92 | 0.72 | 198 |
| 5 | 0.67% | 2.32 | 0.57 | 198 |
| 6 | 0.70% | 2.49 | 0.61 | 198 |
| 7 | 0.37% | 1.35 | 0.33 | 196 |
| 8 | 0.11% | 0.40 | 0.10 | 198 |
| 9 | 4524.30% | 1.00 | 0.25 | 198 |
| 10 | 6012.62% | 1.00 | 0.25 | 197 |
| 1-10 (long-short) | -5997.22% | -1.00 | -0.25 | 197 |

## Cross-sectional regressions (Fama-MacBeth, Newey-West t-stats)

| spec | variable | mean coef | t (NW) | months |
|---|---|---|---|---|
| univariate_discount | const | 16.1329 | 1.34 | 226 |
| univariate_discount | discount | 114.0843 | 1.23 | 226 |
| univariate_zscore | const | 9.1561 | 1.37 | 203 |
| univariate_zscore | discount_z_36m | 12.3883 | 1.43 | 203 |
| univariate_widening3m | const | 10.3219 | 1.38 | 219 |
| univariate_widening3m | discount_change_3m | 247.2440 | 0.96 | 219 |
| multivariate | const | 35.4276 | 1.39 | 197 |
| multivariate | discount | 87.3267 | 1.05 | 197 |
| multivariate | discount_z_36m | 7.7378 | 1.10 | 197 |
| multivariate | discount_change_3m | 137.5835 | 0.53 | 197 |
| multivariate | log_mcap | -3.6769 | -1.45 | 197 |
| multivariate_sector_demeaned | const | -0.0000 | -1.28 | 197 |
| multivariate_sector_demeaned | discount | 74.2183 | 1.41 | 197 |
| multivariate_sector_demeaned | discount_z_36m | 7.2393 | 1.04 | 197 |
| multivariate_sector_demeaned | discount_change_3m | 178.5285 | 0.60 | 197 |
| multivariate_sector_demeaned | log_mcap | 3.5745 | 0.69 | 197 |

No causal claims are made; these test incremental predictive information only.

## Robustness (Stage 28)

17 pre-specified variants (portfolio size, z-window, rebalance frequency, weighting, market-cap floors): 94% have positive alpha vs the EW universe. Full grid in robustness_grid.csv. If only isolated cells worked, that would indicate overfitting; the grid shows whether the effect occupies a broad region.

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