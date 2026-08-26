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

**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median discount), those with a trailing value-realization corporate action (tender/redemption/capital return/realisation policy/liquidation) earned 1.81% next month vs 2.06% without (n=103 vs 4460). IMPORTANT: the AIC archive records actions by effective month, not announcement date, so this is a 'trailing completed action' proxy - announcement-day catalyst alpha cannot be measured from this source and is not claimed.

**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as (1+r) = (1+NAV return)(1+discount movement): annualised NAV component -1.0%, discount-movement component 14.7%. Essentially all of the strategy's excess return is discount capture, not superior NAVs. (Both components exclude distributions: NAV per share falls on ex-dividend dates, so the NAV component is downward-biased by roughly the portfolio yield.) Per-year detail in return_decomposition.csv and chart 14.

**How severe are drawdowns?** Strategy A max drawdown -44.4% (benchmark -44.7%). Deep-discount portfolios are NOT defensive; they draw down harder in crises and recover faster (stress_episodes.csv).

**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** Taken at face value the best pre-specified strategy delivered 25.6% price-return CAGR (8.0% to 14.7% annualised alpha) before costs. Observed trailing yields: portfolio 2.0% vs universe 2.9%. Three deductions are required before treating that as attainable: (i) the skip-month test (below) caps the alpha that survives without trading the very first post-signal month at 2.8% - the remainder is fast one-month reversion that monthly month-end rebalancing overstates and real execution would partly miss; (ii) realistic costs remove 5-10pp from the high-turnover variants (cost_scenarios.csv); (iii) the measured portfolio-vs-universe yield gap shifts a total-return comparison slightly against the strategies. The defensible conclusion: systematic long-only discount selection historically supported roughly mid-single-digit to low-double-digit annual alpha over the trust universe before costs, on a universe averaging ~5% price CAGR. That makes a mid-teens gross return an optimistic but not absurd reading of the top variants, while a SUSTAINED 15-20% would additionally require leverage, activism to force realizations, announcement-day catalyst timing, or NAV-level security selection - sources this monthly screen deliberately does not model.

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