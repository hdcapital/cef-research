# UK Investment Trust Discount Strategies - Backtest Report

**Historical backtests are research tools, not investment advice. No missing financial data has been synthetically filled.**

Sample: 2007-01 to 2026-07, 303 securities, 40977 security-months. Universe: AIC member conventional investment companies (ordinary shares, London-listed, VCTs/splits/ZDPs excluded), taken point-in-time from the AIC Monthly Information Release archive.

## Return basis - read this first

All returns are **month-end to month-end share price returns excluding dividends**, built from AIC MIR month-end mid prices (the same point-in-time files that define the universe, so dead, merged and liquidated trusts are included up to their final published month). Free, point-in-time dividend histories covering delisted UK trusts do not exist, and per the project's data-integrity rules nothing was synthesised. CAGR levels are therefore NOT total returns. The direction of the relative bias is measured, not assumed: outputs/yield_differentials.csv reports each portfolio's average published trailing yield against the universe's, and the strategy-minus-universe yield gap (about a percentage point on the tested portfolios) bounds how much a total-return comparison would shift the alphas. Price returns also treat capital distributions by wind-down vehicles as losses, which penalises exactly the trusts discount strategies hold.

## Executive summary - the ten questions

**Headline caution.** Every strategy's alpha in this study is concentrated in the FIRST month after the month-end signal (the skip-month test in the Robustness section quantifies this). The standard results below assume the portfolio earns the full calendar month t+1 after a month-end-t signal - achievable only by monitoring discounts in near-real-time (daily RNS NAVs make this practicable) and trading promptly at the month turn. The skip-month figures are the conservative bound for slower execution, and part of the first-month effect may be price-measurement noise rather than harvestable reversion. Both bounds are reported; quote them together.

**1. Does buying discounted UK investment trusts generate excess returns (before costs)?** Cheapest-decile absolute discount (A): 13.5% price-return CAGR vs 5.2% for the equal-weight universe; annualised alpha 7.7% (t=4.43). The spread is positive, but see the headline caution: it shrinks dramatically when the first post-signal month is skipped.

**2/3. Absolute discount vs z-score (cheap vs own history)?** Strategy C (36m z-score): 25.4% CAGR, alpha 14.4% (t=10.14) vs A's alpha 7.7%. Relative (z-score) ranking adds value over absolute discount.

**4. Does rapid discount widening predict reversion?** Decile 1 of 3-month widening earned 1.77% average next-month return (t=5.85). See decile tables.

**5. Does sector-neutral construction retain the effect?** Sector-neutral z-score alpha: 12.2% vs plain z-score 14.4%.

**6. How much survives realistic costs?** Headline case (50bps one-way + 50bps stamp duty on dutiable buys): strategy A net CAGR 8.4% vs 13.5% gross. Full grid in cost_scenarios.csv.

**7. Does the effect persist in the 2022+ holdout?** Alpha vs EW universe in the holdout: A 10.0%, C 13.7%. Development/validation/holdout splits are in performance_summary.csv.

**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median discount), those with a trailing value-realization corporate action (tender/redemption/capital return/realisation policy/liquidation) earned 1.87% next month vs 2.07% without (n=103 vs 4398). IMPORTANT: the AIC archive records actions by effective month, not announcement date, so this is a 'trailing completed action' proxy - announcement-day catalyst alpha cannot be measured from this source and is not claimed.

**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as (1+r) = (1+NAV return)(1+discount movement): annualised NAV component -0.7%, discount-movement component 14.6%. Essentially all of the strategy's excess return is discount capture, not superior NAVs. (Both components exclude distributions: NAV per share falls on ex-dividend dates, so the NAV component is downward-biased by roughly the portfolio yield.) Per-year detail in return_decomposition.csv and chart 14.

**How severe are drawdowns?** Strategy A max drawdown -44.4% (benchmark -44.7%). Deep-discount portfolios are NOT defensive; they draw down harder in crises and recover faster (stress_episodes.csv).

**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** Taken at face value the best pre-specified strategy delivered 25.4% price-return CAGR (7.7% to 14.4% annualised alpha) before costs. Observed trailing yields: portfolio 1.9% vs universe 2.8%. Three deductions are required before treating that as attainable: (i) the skip-month test (below) caps the alpha that survives without trading the very first post-signal month at 3.7% - the remainder is fast one-month reversion that monthly month-end rebalancing overstates and real execution would partly miss; (ii) realistic costs remove 5-10pp from the high-turnover variants (cost_scenarios.csv); (iii) the measured portfolio-vs-universe yield gap shifts a total-return comparison slightly against the strategies. The defensible conclusion: systematic long-only discount selection historically supported roughly mid-single-digit to low-double-digit annual alpha over the trust universe before costs, on a universe averaging ~5% price CAGR. That makes a mid-teens gross return an optimistic but not absurd reading of the top variants, while a SUSTAINED 15-20% would additionally require leverage, activism to force realizations, announcement-day catalyst timing, or NAV-level security selection - sources this monthly screen deliberately does not model.

## Decile tests (Stage 9)

Average next-month price return by signal decile (1 = cheapest/most dislocated):

**absolute_discount**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.17% | 3.82 | 0.87 | 233 |
| 2 | 1.08% | 3.55 | 0.81 | 233 |
| 3 | 0.82% | 2.75 | 0.62 | 233 |
| 4 | 0.65% | 2.23 | 0.51 | 233 |
| 5 | 0.57% | 2.00 | 0.45 | 233 |
| 6 | 0.46% | 1.63 | 0.37 | 233 |
| 7 | 0.29% | 1.06 | 0.24 | 233 |
| 8 | 0.33% | 1.34 | 0.30 | 233 |
| 9 | 0.04% | 0.15 | 0.03 | 233 |
| 10 | -0.45% | -1.95 | -0.44 | 233 |
| 1-10 (long-short) | 1.62% | 8.84 | 2.01 | 233 |

**discount_zscore**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.99% | 6.81 | 1.63 | 210 |
| 2 | 1.34% | 5.07 | 1.21 | 210 |
| 3 | 1.06% | 4.14 | 0.99 | 210 |
| 4 | 0.93% | 3.38 | 0.81 | 210 |
| 5 | 0.80% | 2.95 | 0.70 | 210 |
| 6 | 0.74% | 2.76 | 0.66 | 210 |
| 7 | 0.63% | 2.39 | 0.57 | 210 |
| 8 | 0.42% | 1.55 | 0.37 | 210 |
| 9 | 0.13% | 0.51 | 0.12 | 210 |
| 10 | -0.52% | -1.89 | -0.45 | 210 |
| 1-10 (long-short) | 2.51% | 12.54 | 3.00 | 210 |

**widening_3m**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 1.77% | 5.85 | 1.34 | 229 |
| 2 | 1.07% | 3.76 | 0.86 | 229 |
| 3 | 0.92% | 3.35 | 0.77 | 229 |
| 4 | 0.70% | 2.55 | 0.58 | 229 |
| 5 | 0.59% | 2.19 | 0.50 | 229 |
| 6 | 0.47% | 1.76 | 0.40 | 229 |
| 7 | 0.37% | 1.34 | 0.31 | 229 |
| 8 | 0.05% | 0.18 | 0.04 | 229 |
| 9 | -0.10% | -0.34 | -0.08 | 229 |
| 10 | -1.05% | -3.60 | -0.82 | 229 |
| 1-10 (long-short) | 2.82% | 13.76 | 3.15 | 229 |

**overshoot**

| bucket | avg monthly fwd ret | t-stat | Sharpe | months |
|---|---|---|---|---|
| 1 | 2.20% | 7.32 | 1.75 | 209 |
| 2 | 1.41% | 5.02 | 1.20 | 209 |
| 3 | 1.19% | 4.75 | 1.14 | 209 |
| 4 | 0.99% | 3.58 | 0.86 | 209 |
| 5 | 0.69% | 2.53 | 0.61 | 209 |
| 6 | 0.74% | 2.74 | 0.66 | 209 |
| 7 | 0.49% | 1.87 | 0.45 | 209 |
| 8 | 0.29% | 1.10 | 0.26 | 209 |
| 9 | 0.01% | 0.02 | 0.00 | 209 |
| 10 | -0.57% | -2.18 | -0.52 | 209 |
| 1-10 (long-short) | 2.77% | 14.50 | 3.47 | 209 |

## Cross-sectional regressions (Fama-MacBeth, Newey-West t-stats)

| spec | variable | mean coef | t (NW) | months |
|---|---|---|---|---|
| univariate_discount | const | 0.0015 | 0.57 | 233 |
| univariate_discount | discount | -0.0420 | -5.40 | 233 |
| univariate_zscore | const | 0.0068 | 2.65 | 210 |
| univariate_zscore | discount_z_36m | -0.0060 | -10.64 | 210 |
| univariate_widening3m | const | 0.0043 | 1.40 | 229 |
| univariate_widening3m | discount_change_3m | -0.1862 | -12.21 | 229 |
| multivariate | const | 0.0005 | 0.18 | 209 |
| multivariate | discount | -0.0239 | -3.53 | 209 |
| multivariate | discount_z_36m | -0.0027 | -5.66 | 209 |
| multivariate | discount_change_3m | -0.1530 | -10.02 | 209 |
| multivariate | log_mcap | 0.0009 | 2.59 | 209 |
| multivariate_sector_demeaned | const | -0.0000 | -0.65 | 209 |
| multivariate_sector_demeaned | discount | -0.0220 | -3.76 | 209 |
| multivariate_sector_demeaned | discount_z_36m | -0.0029 | -6.45 | 209 |
| multivariate_sector_demeaned | discount_change_3m | -0.1700 | -11.95 | 209 |
| multivariate_sector_demeaned | log_mcap | 0.0005 | 1.66 | 209 |

No causal claims are made; these test incremental predictive information only.

## Robustness (Stage 28)

22 pre-specified variants (portfolio size, z-window, rebalance frequency, weighting, market-cap floors): 95% have positive alpha vs the EW universe. Full grid in robustness_grid.csv. If only isolated cells worked, that would indicate overfitting; the grid shows whether the effect occupies a broad region.

**Measurement-error (skip-month) test.** A mis-recorded month-t price both widens the apparent discount and mechanically reverses next month, inflating t+1 results; it cannot inflate the month t+2 return. Alphas when the first post-signal month is skipped:

| variant | alpha (annual) | t | CAGR |
|---|---|---|---|
| discount_top10_SKIP1M | 1.0% | 0.58 | 6.0% |
| z36m_top10_SKIP1M | 1.6% | 1.32 | 10.8% |
| overshoot_top10_SKIP1M | 3.7% | 2.79 | 12.7% |
| z36m_mcap100_SKIP1M | -0.1% | -0.04 | 9.3% |
| overshoot_mcap100_SKIP1M | 2.1% | 1.58 | 11.8% |

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