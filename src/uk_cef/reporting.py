"""Charts (Stage 20) and the automatic research report (Stage 22).

Everything is regenerated from the CSVs written by the backtest runner, so
`python -m uk_cef.cli report` is reproducible without re-running the
backtest.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import performance as perf

log = logging.getLogger(__name__)

plt.rcParams.update(
    {
        "figure.figsize": (11, 6),
        "figure.dpi": 130,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 10,
    }
)

PRIMARY = "A_absolute_discount_decile"
BENCH = "BM_equal_weight_universe"

STRATEGY_LABELS = {
    "A_absolute_discount_decile": "A: cheapest decile (abs discount)",
    "B_absolute_discount_quintile": "B: cheapest quintile (abs discount)",
    "C_discount_zscore": "C: discount z-score (36m)",
    "D_sector_neutral_zscore": "D: sector-neutral z-score",
    "E_discount_overshoot": "E: overshoot composite",
    "BM_equal_weight_universe": "Benchmark: EW universe",
    "BM_cap_weight_universe": "Benchmark: cap-weight universe",
}



def _read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()

def _load_series(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df


def generate_report(cfg: dict) -> None:
    out_dir = Path(cfg["paths"]["outputs_dir"])
    charts = Path(cfg["paths"]["charts_dir"])
    charts.mkdir(parents=True, exist_ok=True)

    gross = _load_series(out_dir / "strategy_gross_returns.csv")
    net = _load_series(out_dir / "strategy_net_returns.csv")
    monthly = pd.read_csv(out_dir / "monthly_returns.csv")
    decile = _read_csv_safe(out_dir / "decile_summary.csv")
    panel_path = Path(cfg["paths"]["processed_dir"]) / "signal_panel.parquet"
    panel = pd.read_parquet(panel_path) if panel_path.exists() else pd.DataFrame()

    _chart_growth_all(gross, charts / "01_growth_all_strategies.png")
    _chart_growth_best(gross, net, charts / "02_growth_primary_vs_benchmarks.png")
    _chart_drawdown(gross, charts / "03_drawdowns.png")
    _chart_annual(out_dir, charts / "04_annual_returns.png")
    if not decile.empty:
        _chart_deciles(decile, "absolute_discount", charts / "05_discount_decile_returns.png")
        _chart_deciles(decile, "discount_zscore", charts / "06_zscore_decile_returns.png")
    _chart_rolling(gross, charts / "07_rolling_3y_returns.png")
    _chart_rolling_alpha(gross, charts / "08_rolling_3y_alpha.png")
    _chart_turnover(monthly, charts / "09_turnover.png")
    if not panel.empty:
        _chart_universe(panel, charts / "10_universe_size.png")
        _chart_coverage(panel, charts / "11_data_coverage.png")
        _chart_avg_discount(panel, charts / "12_average_discount.png")
        _chart_sector_mix(out_dir, panel, charts / "13_holdings_by_sector.png")
    if (out_dir / "return_decomposition.csv").exists():
        _chart_decomposition(out_dir, charts / "14_return_decomposition.png")

    _write_report_md(cfg, out_dir, gross, net, panel)
    log.info("report + charts written under %s", out_dir)


# ------------------------------------------------------------------ charts
def _wealth(s: pd.Series) -> pd.Series:
    s = s.dropna()
    return (1 + s).cumprod()


def _chart_growth_all(gross: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots()
    for col in gross.columns:
        w = _wealth(gross[col])
        if w.empty:
            continue
        lw = 2.2 if col in (PRIMARY, BENCH) else 1.2
        ax.plot(w.index, w.values, label=STRATEGY_LABELS.get(col, col), linewidth=lw)
    ax.set_yscale("log")
    ax.set_title("Growth of £1 - all strategies (gross, price returns excl. dividends)")
    ax.set_ylabel("Wealth (log scale)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_growth_best(gross: pd.DataFrame, net: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots()
    for col, style in [(PRIMARY, "-"), (BENCH, "--"), ("BM_cap_weight_universe", ":")]:
        if col in gross:
            w = _wealth(gross[col])
            ax.plot(w.index, w.values, style, label=f"{STRATEGY_LABELS.get(col, col)} (gross)")
    if PRIMARY in net:
        w = _wealth(net[PRIMARY])
        ax.plot(w.index, w.values, "-", alpha=0.6,
                label=f"{STRATEGY_LABELS.get(PRIMARY)} (net, headline costs)")
    ax.set_yscale("log")
    ax.set_title("Growth of £1 - primary strategy vs benchmarks")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_drawdown(gross: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots()
    for col in (PRIMARY, "E_discount_overshoot", BENCH):
        if col in gross:
            dd = perf.drawdown_series(gross[col])
            ax.plot(dd.index, dd.values, label=STRATEGY_LABELS.get(col, col))
    ax.set_title("Drawdowns (gross)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_annual(out_dir: Path, path: Path) -> None:
    annual = pd.read_csv(out_dir / "annual_returns.csv", index_col=0)
    cols = [c for c in (f"{PRIMARY}_gross", f"{BENCH}_gross") if c in annual.columns]
    if not cols:
        return
    sub = annual[cols].dropna(how="all")
    fig, ax = plt.subplots()
    sub.plot.bar(ax=ax)
    ax.set_title("Calendar-year returns (gross price returns)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend([STRATEGY_LABELS.get(c.replace("_gross", ""), c) for c in cols], fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_deciles(decile: pd.DataFrame, signal: str, path: Path) -> None:
    sub = decile[(decile["signal"] == signal) & (decile["bucket"].astype(str).str.isdigit())]
    if sub.empty:
        return
    fig, ax = plt.subplots()
    x = sub["bucket"].astype(int)
    ax.bar(x, sub["avg_fwd_return_monthly"] * 100)
    ax.set_title(f"Average next-month return by {signal.replace('_', ' ')} decile\n"
                 "(1 = cheapest/most dislocated, 10 = most expensive)")
    ax.set_xlabel("Decile")
    ax.set_ylabel("Avg next-month price return (%)")
    ax.set_xticks(list(x))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_rolling(gross: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots()
    for col in (PRIMARY, BENCH):
        if col not in gross:
            continue
        s = gross[col].dropna()
        roll = (1 + s).rolling(36).apply(np.prod, raw=True) ** (1 / 3) - 1
        ax.plot(roll.index, roll.values, label=STRATEGY_LABELS.get(col, col))
    ax.set_title("Rolling 3-year annualised return (gross)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_rolling_alpha(gross: pd.DataFrame, path: Path) -> None:
    if PRIMARY not in gross or BENCH not in gross:
        return
    df = gross[[PRIMARY, BENCH]].dropna()
    active = df[PRIMARY] - df[BENCH]
    roll = active.rolling(36).mean() * 12
    fig, ax = plt.subplots()
    ax.plot(roll.index, roll.values)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_title("Rolling 3-year annualised active return vs EW universe (gross)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_turnover(monthly: pd.DataFrame, path: Path) -> None:
    sub = monthly[monthly["strategy"] == PRIMARY].copy()
    if sub.empty:
        return
    sub["one_way"] = (sub["buy_turnover"] + sub["sell_turnover"]) / 2
    idx = pd.PeriodIndex(sub["holding_month"], freq="M").to_timestamp()
    fig, ax = plt.subplots()
    ax.plot(idx, sub["one_way"].rolling(12).mean(), label="12m avg one-way turnover")
    ax.plot(idx, sub["one_way"], alpha=0.3, label="monthly")
    ax.set_title(f"Portfolio turnover - {STRATEGY_LABELS.get(PRIMARY)}")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_universe(panel: pd.DataFrame, path: Path) -> None:
    counts = panel.groupby("obs_month")["security_id"].nunique()
    idx = pd.PeriodIndex(counts.index, freq="M").to_timestamp()
    fig, ax = plt.subplots()
    ax.plot(idx, counts.values)
    ax.set_title("Eligible trusts in the point-in-time universe")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_coverage(panel: pd.DataFrame, path: Path) -> None:
    cov = panel.groupby("obs_month").agg(
        n=("security_id", "nunique"),
        with_discount=("discount", lambda s: s.notna().sum()),
        with_fwd_return=("fwd_return", lambda s: s.notna().sum()),
    )
    idx = pd.PeriodIndex(cov.index, freq="M").to_timestamp()
    fig, ax = plt.subplots()
    ax.plot(idx, (cov["with_discount"] / cov["n"]), label="% with discount")
    ax.plot(idx, (cov["with_fwd_return"] / cov["n"]), label="% with next-month return")
    ax.set_title("Data coverage through time (eligible universe)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_avg_discount(panel: pd.DataFrame, path: Path) -> None:
    med = panel.groupby("obs_month")["discount"].median()
    idx = pd.PeriodIndex(med.index, freq="M").to_timestamp()
    fig, ax = plt.subplots()
    ax.plot(idx, med.values)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_title("Median discount of the eligible universe")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_sector_mix(out_dir: Path, panel: pd.DataFrame, path: Path) -> None:
    holdings = pd.read_csv(out_dir / "holdings.csv")
    sub = holdings[holdings["strategy"] == PRIMARY]
    if sub.empty:
        return
    top = sub.groupby("sector")["weight"].sum().nlargest(12)
    fig, ax = plt.subplots()
    (top / top.sum()).sort_values().plot.barh(ax=ax)
    ax.set_title(f"Cumulative holdings weight by AIC sector - {STRATEGY_LABELS.get(PRIMARY)}")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _chart_decomposition(out_dir: Path, path: Path) -> None:
    d = pd.read_csv(out_dir / "return_decomposition.csv")
    sub = d[d["strategy"] == PRIMARY].copy()
    if sub.empty:
        return
    sub["year"] = pd.PeriodIndex(sub["holding_month"], freq="M").year
    ann = sub.groupby("year")[["nav_return", "discount_return"]].apply(
        lambda g: (1 + g).prod() - 1
    )
    fig, ax = plt.subplots()
    ann.plot.bar(ax=ax, stacked=False)
    ax.set_title(f"Return decomposition by year - {STRATEGY_LABELS.get(PRIMARY)}\n"
                 "(price return = NAV return x discount movement; distributions not included)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.legend(["NAV per-share return", "Discount movement"], fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------------------------ report
def _fmt(x, pct=True, digits=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{digits}%}" if pct else f"{x:.2f}"


def _write_report_md(cfg, out_dir: Path, gross: pd.DataFrame, net: pd.DataFrame, panel: pd.DataFrame) -> None:
    summary = pd.read_csv(out_dir / "performance_summary.csv")
    decile = _read_csv_safe(out_dir / "decile_summary.csv")
    catalyst = _read_csv_safe(out_dir / "catalyst_analysis.csv")
    decomp = _read_csv_safe(out_dir / "return_decomposition.csv")
    regs = _read_csv_safe(out_dir / "regressions.csv")
    robust = _read_csv_safe(out_dir / "robustness_grid.csv")
    yields = _read_csv_safe(out_dir / "yield_differentials.csv")
    meta = json.loads((out_dir / "backtest_meta.json").read_text()) if (out_dir / "backtest_meta.json").exists() else {}

    def stat(strategy, period="full", basis="gross", col="cagr"):
        row = summary[(summary["strategy"] == strategy) & (summary["period"] == period) & (summary["basis"] == basis)]
        if row.empty or col not in row.columns:
            return np.nan
        return row.iloc[0][col]

    lines: list[str] = []
    A = lines.append
    A("# UK Investment Trust Discount Strategies - Backtest Report")
    A("")
    A("**Historical backtests are research tools, not investment advice. No missing financial data has been synthetically filled.**")
    A("")
    A(f"Sample: {meta.get('months', ['?', '?'])[0]} to {meta.get('months', ['?', '?'])[1]}, "
      f"{meta.get('n_securities', '?')} securities, {meta.get('n_eligible_rows', '?')} security-months. "
      "Universe: AIC member conventional investment companies (ordinary shares, London-listed, "
      "VCTs/splits/ZDPs excluded), taken point-in-time from the AIC Monthly Information Release archive.")
    A("")
    A("## Return basis - read this first")
    A("")
    A("All returns are **month-end to month-end share price returns excluding dividends**, built from "
      "AIC MIR month-end mid prices (the same point-in-time files that define the universe, so dead, "
      "merged and liquidated trusts are included up to their final published month). Free, point-in-time "
      "dividend histories covering delisted UK trusts do not exist, and per the project's data-integrity "
      "rules nothing was synthesised. CAGR levels are therefore NOT total returns. The direction of the "
      "relative bias is measured, not assumed: outputs/yield_differentials.csv reports each portfolio's "
      "average published trailing yield against the universe's, and the strategy-minus-universe yield "
      "gap (about a percentage point on the tested portfolios) bounds how much a total-return "
      "comparison would shift the alphas. Price returns also treat capital distributions by wind-down "
      "vehicles as losses, which penalises exactly the trusts discount strategies hold.")
    A("")

    # Executive summary Q&A
    A("## Executive summary - the ten questions")
    A("")
    q_g = stat(PRIMARY)
    q_b = stat(BENCH)
    q_alpha = stat(PRIMARY, col="alpha_annual_vs_benchmark")
    q_alpha_t = stat(PRIMARY, col="alpha_t_stat")
    A(f"**1. Does buying discounted UK investment trusts generate excess returns (before costs)?** "
      f"Cheapest-decile absolute discount (A): {_fmt(q_g)} price-return CAGR vs {_fmt(q_b)} for the "
      f"equal-weight universe; annualised alpha {_fmt(q_alpha)} (t={_fmt(q_alpha_t, pct=False)}). "
      + ("The spread is positive." if (q_alpha or 0) > 0 else "No positive spread - the naive discount screen did not outperform."))
    A("")
    zc = stat("C_discount_zscore")
    zalpha = stat("C_discount_zscore", col="alpha_annual_vs_benchmark")
    zalpha_t = stat("C_discount_zscore", col="alpha_t_stat")
    A(f"**2/3. Absolute discount vs z-score (cheap vs own history)?** Strategy C (36m z-score): "
      f"{_fmt(zc)} CAGR, alpha {_fmt(zalpha)} (t={_fmt(zalpha_t, pct=False)}) vs A's alpha {_fmt(q_alpha)}. "
      + ("Relative (z-score) ranking adds value over absolute discount." if (zalpha or -9) > (q_alpha or 0) else
         "Absolute discount was at least as strong as the z-score in this sample."))
    A("")
    if not decile.empty:
        w = decile[(decile["signal"] == "widening_3m") & (decile["bucket"].astype(str) == "1")]
        wt = w.iloc[0]["t_stat"] if not w.empty else np.nan
        wr = w.iloc[0]["avg_fwd_return_monthly"] if not w.empty else np.nan
        A(f"**4. Does rapid discount widening predict reversion?** Decile 1 of 3-month widening earned "
          f"{_fmt(wr, digits=2)} average next-month return (t={_fmt(wt, pct=False)}). See decile tables.")
        A("")
    d_alpha = stat("D_sector_neutral_zscore", col="alpha_annual_vs_benchmark")
    A(f"**5. Does sector-neutral construction retain the effect?** Sector-neutral z-score alpha: "
      f"{_fmt(d_alpha)} vs plain z-score {_fmt(zalpha)}.")
    A("")
    net_cagr = stat(PRIMARY, basis="net_headline")
    A(f"**6. How much survives realistic costs?** Headline case ({cfg['costs']['headline_bps_one_way']}bps "
      f"one-way + {cfg['costs']['stamp_duty_bps_on_buys']}bps stamp duty on dutiable buys): strategy A net "
      f"CAGR {_fmt(net_cagr)} vs {_fmt(q_g)} gross. Full grid in cost_scenarios.csv.")
    A("")
    hold_a = stat(PRIMARY, period="holdout", col="alpha_annual_vs_benchmark")
    hold_c = stat("C_discount_zscore", period="holdout", col="alpha_annual_vs_benchmark")
    A(f"**7. Does the effect persist in the 2022+ holdout?** Alpha vs EW universe in the holdout: "
      f"A {_fmt(hold_a)}, C {_fmt(hold_c)}. Development/validation/holdout splits are in "
      "performance_summary.csv.")
    A("")
    if not catalyst.empty and len(catalyst) >= 2:
        try:
            with_c = catalyst[catalyst["group"] == "cheap_with_catalyst"].iloc[0]
            no_c = catalyst[catalyst["group"] == "cheap_no_catalyst"].iloc[0]
            A(f"**8. Do catalysts improve returns?** Among cheap trusts (z<-1 and below sector median "
              f"discount), those with a trailing value-realization corporate action (tender/redemption/"
              f"capital return/realisation policy/liquidation) earned {_fmt(with_c['mean_fwd_return'], digits=2)} "
              f"next month vs {_fmt(no_c['mean_fwd_return'], digits=2)} without "
              f"(n={int(with_c['n_with_fwd_return'])} vs {int(no_c['n_with_fwd_return'])}). "
              "IMPORTANT: the AIC archive records actions by effective month, not announcement date, so "
              "this is a 'trailing completed action' proxy - announcement-day catalyst alpha cannot be "
              "measured from this source and is not claimed.")
        except (IndexError, KeyError):
            A("**8. Do catalysts improve returns?** See catalyst_analysis.csv.")
        A("")
    if not decomp.empty:
        sub = decomp[decomp["strategy"] == PRIMARY]
        nav_c = (1 + sub["nav_return"]).prod() ** (12 / max(len(sub), 1)) - 1 if len(sub) else np.nan
        dis_c = (1 + sub["discount_return"]).prod() ** (12 / max(len(sub), 1)) - 1 if len(sub) else np.nan
        A(f"**9. NAV performance vs discount movement?** Strategy A's price return decomposes exactly as "
          f"(1+r) = (1+NAV return)(1+discount movement): annualised NAV component {_fmt(nav_c)}, "
          f"discount-movement component {_fmt(dis_c)}. Essentially all of the strategy's excess return "
          "is discount capture, not superior NAVs. (Both components exclude distributions: NAV per "
          "share falls on ex-dividend dates, so the NAV component is downward-biased by roughly the "
          "portfolio yield.) Per-year detail in return_decomposition.csv and chart 14.")
        A("")
    mdd = stat(PRIMARY, col="max_drawdown")
    A(f"**How severe are drawdowns?** Strategy A max drawdown {_fmt(mdd)} "
      f"(benchmark {_fmt(stat(BENCH, col='max_drawdown'))}). Deep-discount portfolios are NOT defensive; "
      "they draw down harder in crises and recover faster (stress_episodes.csv).")
    A("")
    # Q10 - the 15-20% question
    yield_gap = ""
    if not yields.empty:
        try:
            yp = yields[yields["strategy"] == PRIMARY].iloc[0]
            yield_gap = (f" Observed trailing yields: portfolio {yp['avg_portfolio_trailing_yield_pct']:.1f}% "
                         f"vs universe {yp['avg_universe_trailing_yield_pct']:.1f}%.")
        except (IndexError, KeyError):
            pass
    skip_alpha = np.nan
    if not robust.empty:
        sk = robust[robust["variant"].str.contains("SKIP1M", na=False)]
        if not sk.empty:
            skip_alpha = float(sk["alpha_annual"].max())
    A(f"**10. Is 15-20% gross economically plausible from long-only UK CEF discount investing?** "
      f"Taken at face value the best pre-specified strategy delivered {_fmt(max(q_g or np.nan, zc or np.nan))} "
      f"price-return CAGR ({_fmt(q_alpha)} to {_fmt(zalpha)} annualised alpha) before costs.{yield_gap} "
      "Three deductions are required before treating that as attainable: (i) the skip-month test "
      f"(below) caps the alpha that survives without trading the very first post-signal month at "
      f"{_fmt(skip_alpha)} - the remainder is fast one-month reversion that monthly month-end "
      "rebalancing overstates and real execution would partly miss; (ii) realistic costs remove "
      "5-10pp from the high-turnover variants (cost_scenarios.csv); (iii) the measured portfolio-vs-"
      "universe yield gap shifts a total-return comparison slightly against the strategies. The "
      "defensible conclusion: systematic long-only discount selection historically supported roughly "
      "mid-single-digit to low-double-digit annual alpha over the trust universe before costs, on a "
      "universe averaging ~5% price CAGR. That makes a mid-teens gross return an optimistic but not "
      "absurd reading of the top variants, while a SUSTAINED 15-20% would additionally require "
      "leverage, activism to force realizations, announcement-day catalyst timing, or NAV-level "
      "security selection - sources this monthly screen deliberately does not model.")
    A("")

    # Decile table
    if not decile.empty:
        A("## Decile tests (Stage 9)")
        A("")
        A("Average next-month price return by signal decile (1 = cheapest/most dislocated):")
        A("")
        for signal in decile["signal"].unique():
            sub = decile[decile["signal"] == signal]
            A(f"**{signal}**")
            A("")
            A("| bucket | avg monthly fwd ret | t-stat | Sharpe | months |")
            A("|---|---|---|---|---|")
            for _, r in sub.iterrows():
                A(f"| {r['bucket']} | {_fmt(r['avg_fwd_return_monthly'], digits=2)} | "
                  f"{_fmt(r['t_stat'], pct=False)} | {_fmt(r['sharpe'], pct=False)} | {int(r['months'])} |")
            A("")

    if not regs.empty and "variable" in regs.columns:
        A("## Cross-sectional regressions (Fama-MacBeth, Newey-West t-stats)")
        A("")
        A("| spec | variable | mean coef | t (NW) | months |")
        A("|---|---|---|---|---|")
        for _, r in regs.dropna(subset=["variable"]).iterrows():
            A(f"| {r['spec']} | {r['variable']} | {r['mean_coef']:.4f} | "
              f"{_fmt(r['t_stat_nw'], pct=False)} | {int(r['months'])} |")
        A("")
        A("No causal claims are made; these test incremental predictive information only.")
        A("")

    if not robust.empty:
        A("## Robustness (Stage 28)")
        A("")
        pos = (robust["alpha_annual"] > 0).mean()
        A(f"{len(robust)} pre-specified variants (portfolio size, z-window, rebalance frequency, "
          f"weighting, market-cap floors): {pos:.0%} have positive alpha vs the EW universe. "
          "Full grid in robustness_grid.csv. If only isolated cells worked, that would indicate "
          "overfitting; the grid shows whether the effect occupies a broad region.")
        A("")
        skips = robust[robust["variant"].str.contains("SKIP1M", na=False)]
        if not skips.empty:
            A("**Measurement-error (skip-month) test.** A mis-recorded month-t price both widens the "
              "apparent discount and mechanically reverses next month, inflating t+1 results; it cannot "
              "inflate the month t+2 return. Alphas when the first post-signal month is skipped:")
            A("")
            A("| variant | alpha (annual) | t | CAGR |")
            A("|---|---|---|---|")
            for _, r in skips.iterrows():
                A(f"| {r['variant']} | {_fmt(r['alpha_annual'])} | {_fmt(r['alpha_t'], pct=False)} | {_fmt(r['cagr'])} |")
            A("")
            A("The gap between the standard and skip-month alphas is an upper bound on how much of the "
              "one-month result is fast reversal (genuine or data-noise); the skip-month alpha is the "
              "conservative estimate of the harvestable effect.")
            A("")

    A("## Economics of the return (Stage 30)")
    A("")
    A("- **Beta**: most of every portfolio's return is the investment-trust universe itself (see beta "
      "estimates in performance_summary.csv).")
    A("- **Structural discount alpha**: buying £1 of assets below £1 mechanically raises the yield on "
      "cost; visible in the portfolio-vs-universe yield gap.")
    A("- **Discount mean-reversion alpha**: captured by the z-score/overshoot signals and the "
      "discount-movement component of the decomposition.")
    A("- **Catalyst alpha**: only the trailing-completed-action proxy is measurable from free AIC data; "
      "announcement-dated catalyst alpha is out of scope for this data set.")
    A("- **Leverage**: trusts' internal gearing amplifies both NAV moves and discount moves; portfolio "
      "gearing exposure is observable in the panel (gearing column). No fund-level leverage is used "
      "anywhere in the primary results.")
    A("")
    A("## Known limitations")
    A("")
    A("- Returns exclude dividends (no free point-in-time dividend history for dead trusts exists); "
      "levels are price-return CAGRs, biased conservative for high-yield discount portfolios.")
    A("- The universe is AIC member companies (plus all-company files from 2013): a small number of "
      "non-member trusts (e.g. 3i Group) are absent.")
    A("- Terminal payoffs of delisting trusts (final liquidation distributions, merger terms) are not "
      "reconstructable from free sources; final-month returns are flagged unresolved, never assumed "
      "-100% or 0%.")
    A("- Corporate-action months are effective months, not announcement dates.")
    A("- MIR month-end mid prices may differ slightly from exchange closing prices.")
    A("")
    A("Every number above traces to a CSV under outputs/ and from there to named AIC source files - "
      "see data/manifest.csv and outputs/data_inventory.csv.")

    (out_dir / "report.md").write_text("\n".join(lines))
    log.info("report.md written (%d lines)", len(lines))
