"""Backtest orchestration (Stages 7-9, 11-19, 28-29).

Consumes the point-in-time panel, computes signals, runs the pre-specified
strategies and benchmarks, decile tests, cost scenarios, sample-split and
robustness analyses, catalyst comparison, Fama-MacBeth regressions and
return decomposition. All results land under outputs/ as CSVs.

RETURN DEFINITION (repeated everywhere results are shown): returns are
month-end to month-end share PRICE returns from AIC MIR mid prices,
excluding dividends. Free point-in-time dividend histories for dead trusts
do not exist; we never synthesise them. Dividend-yield differentials
between portfolios are reported separately so the reader can judge the
total-return gap.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import costs as costs_mod
from . import performance as perf
from .deciles import bucket_summary, monthly_bucket_returns
from .panel import load_panel
from .portfolio import BacktestResult, benchmark_universe, run_strategy
from .signals import build_all_signals

log = logging.getLogger(__name__)

STRESS_EPISODES = {
    "GFC 2008-09": ("2008-01", "2009-03"),
    "Eurozone crisis": ("2011-05", "2012-06"),
    "2015-16 selloff": ("2015-06", "2016-02"),
    "Q4 2018": ("2018-10", "2018-12"),
    "COVID crash": ("2020-02", "2020-03"),
    "2022 rate shock": ("2022-01", "2022-12"),
    "2023-25 IT discount crisis": ("2023-01", "2025-12"),
}


def _prepare(cfg: dict) -> pd.DataFrame:
    panel = load_panel(cfg)
    panel = panel[panel["obs_month"] >= cfg["project"]["start_month"]]
    elig = panel[panel["eligible"]].copy()
    elig["date"] = pd.to_datetime(elig["date"])
    log.info("eligible universe: %d rows, %d securities, %d months",
             len(elig), elig["security_id"].nunique(), elig["obs_month"].nunique())
    elig = build_all_signals(elig, cfg)
    return elig


def _strategy_suite(cfg: dict) -> list[dict]:
    z = cfg["signals"]["zscore_window_months"]
    frac_d = cfg["strategies"]["top_fraction_decile"]
    frac_q = cfg["strategies"]["top_fraction_quintile"]
    return [
        dict(name="A_absolute_discount_decile", signal_col="discount", top_fraction=frac_d),
        dict(name="B_absolute_discount_quintile", signal_col="discount", top_fraction=frac_q),
        dict(name="C_discount_zscore", signal_col=f"discount_z_{z}m", top_fraction=frac_d),
        dict(name="D_sector_neutral_zscore", signal_col=f"discount_z_{z}m",
             top_fraction=frac_d, sector_neutral=True),
        dict(name="E_discount_overshoot", signal_col="overshoot_score", top_fraction=frac_d),
    ]


def run_backtests(cfg: dict) -> dict:
    out_dir = Path(cfg["paths"]["outputs_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    elig = _prepare(cfg)
    elig.to_parquet(Path(cfg["paths"]["processed_dir"]) / "signal_panel.parquet", index=False)

    min_names = cfg["strategies"]["min_names"]
    results: dict[str, BacktestResult] = {}

    for spec in _strategy_suite(cfg):
        res = run_strategy(elig, min_names=min_names, **spec)
        results[spec["name"]] = res
        log.info("strategy %s: %d months", spec["name"], len(res.monthly))

    results["BM_equal_weight_universe"] = benchmark_universe(elig, "equal")
    cap = elig[elig["market_cap"].notna()]
    results["BM_cap_weight_universe"] = benchmark_universe(cap, "market_cap")

    # ------------------------------------------------ costs & monthly returns
    duty_bps = cfg["costs"]["stamp_duty_bps_on_buys"] if cfg["costs"]["apply_stamp_duty"] else 0.0
    headline_bps = cfg["costs"]["headline_bps_one_way"]
    duty_frac = _duty_fraction(results, elig, cfg)
    monthly_frames = {}
    for name, res in results.items():
        if res.monthly.empty:
            continue
        m = res.monthly.copy()
        m = costs_mod.apply_costs(m, headline_bps, duty_bps, duty_frac.get(name, 1.0))
        m["strategy"] = name
        monthly_frames[name] = m
    monthly_all = pd.concat(monthly_frames.values(), ignore_index=True)
    monthly_all.to_csv(out_dir / "monthly_returns.csv", index=False)

    # ------------------------------------------------ performance summaries
    bench = results["BM_equal_weight_universe"].gross_returns
    summary_rows = []
    splits = {"full": None, **{k: tuple(v) for k, v in cfg["sample_splits"].items()}}
    for name, res in results.items():
        gross = res.gross_returns
        net = _net_series(monthly_frames.get(name))
        for split_name, bounds in splits.items():
            g = _slice(gross, bounds)
            b = _slice(bench, bounds)
            row = perf.summarize(g, b if name != "BM_equal_weight_universe" else None,
                                 name=f"{name}|{split_name}|gross")
            row.update({"strategy": name, "period": split_name, "basis": "gross"})
            summary_rows.append(row)
            if net is not None:
                n = _slice(net, bounds)
                row = perf.summarize(n, b if name != "BM_equal_weight_universe" else None,
                                     name=f"{name}|{split_name}|net")
                row.update({"strategy": name, "period": split_name, "basis": "net_headline"})
                summary_rows.append(row)
        m = res.monthly
        if not m.empty:
            avg_turnover = float((m["buy_turnover"] + m["sell_turnover"]).mean() / 2)
            summary_rows[-1]["avg_monthly_one_way_turnover"] = avg_turnover
            summary_rows[-1]["avg_holdings"] = float(m["n_holdings"].mean())
            summary_rows[-1]["median_holdings"] = float(m["n_holdings"].median())
            summary_rows[-1]["avg_missing_return_weight"] = float(m["missing_return_weight"].mean())
    pd.DataFrame(summary_rows).to_csv(out_dir / "performance_summary.csv", index=False)

    # annual returns
    annual = {}
    for name, res in results.items():
        annual[f"{name}_gross"] = perf.annual_returns(res.gross_returns)
        net = _net_series(monthly_frames.get(name))
        if net is not None:
            annual[f"{name}_net"] = perf.annual_returns(net)
    pd.DataFrame(annual).to_csv(out_dir / "annual_returns.csv")

    # cost scenario grid for the primary strategies
    cost_rows = []
    for name, res in results.items():
        if res.monthly.empty or name.startswith("BM_"):
            continue
        for bps in cfg["costs"]["scenarios_bps_one_way"]:
            for duty in ([0.0, duty_bps] if duty_bps else [0.0]):
                m = costs_mod.apply_costs(res.monthly, bps, duty, duty_frac.get(name, 1.0))
                s = _net_series(m)
                cost_rows.append(
                    {"strategy": name, "one_way_bps": bps, "stamp_duty_bps": duty,
                     "cagr_net": perf.cagr(s), "sharpe_net": perf.sharpe(s)}
                )
    pd.DataFrame(cost_rows).to_csv(out_dir / "cost_scenarios.csv", index=False)

    # ------------------------------------------------ holdings & trades
    # audit trail: strategy portfolios only (benchmark books are the whole
    # universe every month and would multiply the file size ~10x; they are
    # reconstructable from the panel)
    holdings = pd.concat(
        [r.holdings for n, r in results.items() if not r.holdings.empty and not n.startswith("BM_")],
        ignore_index=True,
    )
    holdings.to_csv(out_dir / "holdings.csv", index=False)
    _trades_from_holdings(holdings).to_csv(out_dir / "trades.csv", index=False)

    # ------------------------------------------------ decile tests
    z = cfg["signals"]["zscore_window_months"]
    decile_specs = {
        "absolute_discount": "discount",
        "discount_zscore": f"discount_z_{z}m",
        "widening_3m": "discount_change_3m",
        "overshoot": "overshoot_score",
    }
    decile_summaries = []
    for label, col in decile_specs.items():
        br = monthly_bucket_returns(elig, col, n_buckets=10)
        if br.empty:
            continue
        s = bucket_summary(br, 10)
        s.insert(0, "signal", label)
        decile_summaries.append(s)
        br.assign(signal=label).to_csv(out_dir / f"decile_monthly_{label}.csv", index=False)
    if decile_summaries:
        pd.concat(decile_summaries, ignore_index=True).to_csv(
            out_dir / "decile_summary.csv", index=False
        )

    # ------------------------------------------------ catalyst comparison
    _catalyst_analysis(elig, cfg).to_csv(out_dir / "catalyst_analysis.csv", index=False)

    # ------------------------------------------------ stress episodes
    stress_rows = []
    for label, (a, b) in STRESS_EPISODES.items():
        for name in list(results):
            r = _slice(results[name].gross_returns, (a, b))
            if r.empty:
                continue
            stress_rows.append(
                {"episode": label, "strategy": name, "months": len(r),
                 "cumulative_return": perf.cumulative_return(r),
                 "worst_month": float(r.min())}
            )
        # post-episode recovery: 12m after episode end for strategy A vs benchmark
        end = pd.Period(b, freq="M")
        post = (str(end + 1), str(end + 12))
        for name in ("A_absolute_discount_decile", "BM_equal_weight_universe"):
            r = _slice(results[name].gross_returns, post)
            if r.empty:
                continue
            stress_rows.append(
                {"episode": f"{label} +12m recovery", "strategy": name, "months": len(r),
                 "cumulative_return": perf.cumulative_return(r),
                 "worst_month": float(r.min())}
            )
    pd.DataFrame(stress_rows).to_csv(out_dir / "stress_episodes.csv", index=False)

    # ------------------------------------------------ robustness grid
    _robustness(elig, cfg, bench).to_csv(out_dir / "robustness_grid.csv", index=False)

    # ------------------------------------------------ regressions
    _fama_macbeth(elig, cfg).to_csv(out_dir / "regressions.csv", index=False)

    # ------------------------------------------------ decomposition
    decomp = _decomposition(elig, results)
    decomp.to_csv(out_dir / "return_decomposition.csv", index=False)

    # yield differentials (dividend context for price-only returns)
    _yield_differentials(elig, results).to_csv(out_dir / "yield_differentials.csv", index=False)

    # persist gross return series for reporting
    series = pd.DataFrame({n: r.gross_returns for n, r in results.items()})
    series.to_csv(out_dir / "strategy_gross_returns.csv")
    net_series = pd.DataFrame(
        {n: _net_series(m) for n, m in monthly_frames.items() if _net_series(m) is not None}
    )
    net_series.to_csv(out_dir / "strategy_net_returns.csv")

    # Stage 15: optional hypothetical leverage overlay - NEVER part of the
    # primary results. Flat assumed borrowing cost, clearly labelled.
    lev_rows = []
    borrow_annual = 0.03  # ASSUMPTION, documented in the report
    for name in ("A_absolute_discount_decile", "C_discount_zscore"):
        g = results[name].gross_returns
        for lev in (1.10, 1.25):
            r_lev = lev * g - (lev - 1) * (borrow_annual / 12)
            lev_rows.append(
                {"strategy": name, "exposure": lev,
                 "assumed_borrow_cost_annual": borrow_annual,
                 "cagr": perf.cagr(r_lev), "sharpe": perf.sharpe(r_lev),
                 "max_drawdown": perf.max_drawdown(r_lev),
                 "note": "hypothetical overlay on gross price returns; not a primary result"}
            )
    pd.DataFrame(lev_rows).to_csv(out_dir / "leverage_overlay.csv", index=False)

    meta = {
        "n_eligible_rows": int(len(elig)),
        "n_securities": int(elig["security_id"].nunique()),
        "months": [str(elig["obs_month"].min()), str(elig["obs_month"].max())],
        "return_basis": "share price return excluding dividends (see README)",
    }
    (out_dir / "backtest_meta.json").write_text(json.dumps(meta, indent=2))
    log.info("backtest complete: %s", meta)
    return {"results": results, "eligible": elig}


# ----------------------------------------------------------------- helpers
def _slice(s: pd.Series, bounds: tuple[str, str] | None) -> pd.Series:
    if s is None or s.empty or bounds is None:
        return s if s is not None else pd.Series(dtype=float)
    a = pd.Period(bounds[0], freq="M").to_timestamp(how="end")
    b = pd.Period(bounds[1], freq="M").to_timestamp(how="end") + pd.Timedelta(days=1)
    return s[(s.index >= a) & (s.index <= b)]


def _net_series(monthly: pd.DataFrame | None) -> pd.Series | None:
    if monthly is None or monthly.empty or "net_return" not in monthly.columns:
        return None
    s = monthly.set_index("holding_month")["net_return"]
    s.index = pd.PeriodIndex(s.index, freq="M").to_timestamp(how="end").normalize()
    return s


def _duty_fraction(results: dict, elig: pd.DataFrame, cfg: dict) -> dict[str, float]:
    """Fraction of each strategy's holdings subject to stamp duty, from
    observed domiciles (UK-domiciled companies dutiable; configured offshore
    domiciles exempt; unknown treated as dutiable - conservative)."""
    exempt = {d.upper()[:3] for d in cfg["costs"]["offshore_domiciles_exempt"]}
    exempt |= {"JER", "GUE", "IRE", "LUX", "BER", "CAY", "IOM", "GIB", "NET"}
    dom = elig[["security_id", "domicile"]].dropna().drop_duplicates("security_id") \
        if "domicile" in elig.columns else pd.DataFrame(columns=["security_id", "domicile"])
    dom_map = dict(zip(dom["security_id"], dom["domicile"].str.upper().str[:3]))
    out = {}
    for name, res in results.items():
        if res.holdings.empty:
            out[name] = 1.0
            continue
        h = res.holdings
        flags = h["security_id"].map(lambda s: 0.0 if dom_map.get(s) in exempt else 1.0)
        out[name] = float((h["weight"] * flags).sum() / h["weight"].sum())
    return out


def _trades_from_holdings(holdings: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strat, h in holdings.groupby("strategy"):
        prev: dict[str, float] = {}
        for month, grp in h.groupby("holding_month"):
            cur = dict(zip(grp["security_id"], grp["weight"]))
            for sid in set(prev) | set(cur):
                delta = cur.get(sid, 0.0) - prev.get(sid, 0.0)
                if abs(delta) > 1e-9:
                    rows.append(
                        {"strategy": strat, "holding_month": month, "security_id": sid,
                         "side": "buy" if delta > 0 else "sell", "weight_change": delta}
                    )
            prev = cur
    return pd.DataFrame(rows)


def _catalyst_analysis(elig: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Stage 11: among cheap trusts (z < -1 AND discount below sector
    median), compare those with a trailing value-realization corporate
    action against those without."""
    z = f"discount_z_{cfg['signals']['zscore_window_months']}m"
    cheap = elig[
        elig[z].notna() & (elig[z] < -1)
        & elig["discount"].notna() & elig["sector_median_discount"].notna()
        & (elig["discount"] < elig["sector_median_discount"])
    ]
    rows = []
    for flag, grp in cheap.groupby("catalyst_flag"):
        fr = grp["fwd_return"].dropna()
        rows.append(
            {"group": "cheap_with_catalyst" if flag else "cheap_no_catalyst",
             "n_obs": len(grp), "n_with_fwd_return": len(fr),
             "mean_fwd_return": float(fr.mean()) if len(fr) else np.nan,
             "median_fwd_return": float(fr.median()) if len(fr) else np.nan,
             "t_stat": perf.t_stat_mean(fr),
             "avg_discount": float(grp["discount"].mean()),
             "avg_zscore": float(grp[z].mean())}
        )
    if len(rows) == 2:
        a = cheap[cheap["catalyst_flag"]]["fwd_return"].dropna()
        b = cheap[~cheap["catalyst_flag"]]["fwd_return"].dropna()
        if len(a) > 10 and len(b) > 10:
            from scipy import stats

            t, p = stats.ttest_ind(a, b, equal_var=False)
            rows.append({"group": "difference (catalyst - none)", "n_obs": len(a) + len(b),
                         "mean_fwd_return": float(a.mean() - b.mean()),
                         "t_stat": float(t), "median_fwd_return": float(p)})
    return pd.DataFrame(rows)


def _robustness(elig: pd.DataFrame, cfg: dict, bench: pd.Series) -> pd.DataFrame:
    rob = cfg["robustness"]
    rows = []
    z_def = cfg["signals"]["zscore_window_months"]
    grid = []
    for f in rob["top_fractions"]:
        grid.append(dict(signal_col="discount", top_fraction=f, label=f"discount_top{int(f*100)}"))
    for w in rob["zscore_windows"]:
        grid.append(dict(signal_col=f"discount_z_{w}m", top_fraction=0.10, label=f"z{w}m_top10"))
    for freq in rob["rebalance_frequencies"]:
        grid.append(dict(signal_col=f"discount_z_{z_def}m", top_fraction=0.10,
                         rebalance=freq, label=f"z{z_def}m_{freq}"))
    for wt in rob["weightings"]:
        grid.append(dict(signal_col=f"discount_z_{z_def}m", top_fraction=0.10,
                         weighting=wt, label=f"z{z_def}m_{wt}"))
    for mc in rob["min_market_caps_gbp_m"]:
        grid.append(dict(signal_col=f"discount_z_{z_def}m", top_fraction=0.10,
                         min_market_cap=mc, label=f"z{z_def}m_mcap{mc or 0}"))
    for spec in grid:
        label = spec.pop("label")
        try:
            res = run_strategy(elig, name=label, min_names=cfg["strategies"]["min_names"], **spec)
        except Exception as exc:  # noqa: BLE001
            log.warning("robustness %s failed: %s", label, exc)
            continue
        g = res.gross_returns
        a, b, t_a, n = perf.alpha_beta(g, bench)
        rows.append(
            {"variant": label, **spec, "months": len(g), "cagr": perf.cagr(g),
             "sharpe": perf.sharpe(g), "max_drawdown": perf.max_drawdown(g),
             "alpha_annual": a, "alpha_t": t_a,
             "avg_holdings": float(res.monthly["n_holdings"].mean()) if not res.monthly.empty else np.nan}
        )
    return pd.DataFrame(rows)


def _fama_macbeth(elig: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Monthly cross-sectional regressions of next-month return on signals;
    Newey-West (4 lags) t-stats on the coefficient time series."""
    import statsmodels.api as sm

    z = f"discount_z_{cfg['signals']['zscore_window_months']}m"
    specs = {
        "univariate_discount": ["discount"],
        "univariate_zscore": [z],
        "univariate_widening3m": ["discount_change_3m"],
        "multivariate": ["discount", z, "discount_change_3m", "log_mcap"],
        "multivariate_sector_demeaned": ["discount", z, "discount_change_3m", "log_mcap"],
    }
    df = elig.copy()
    df["log_mcap"] = np.log(df["market_cap"].where(df["market_cap"] > 0))
    out_rows = []
    for spec_name, cols in specs.items():
        demean = "sector_demeaned" in spec_name
        coefs = []
        for month, grp in df.groupby("obs_month"):
            sub = grp.dropna(subset=cols + ["fwd_return"])
            if demean and "sector" in sub.columns:
                sub = sub.copy()
                for c in cols + ["fwd_return"]:
                    sub[c] = sub[c] - sub.groupby("sector")[c].transform("mean")
            if len(sub) < len(cols) + 10:
                continue
            X = sm.add_constant(sub[cols].values)
            try:
                beta = np.linalg.lstsq(X, sub["fwd_return"].values, rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            coefs.append(beta)
        if len(coefs) < 24:
            out_rows.append({"spec": spec_name, "months": len(coefs), "note": "insufficient months"})
            continue
        C = np.array(coefs)
        names = ["const"] + cols
        for j, nm in enumerate(names):
            series = pd.Series(C[:, j])
            model = sm.OLS(series.values, np.ones(len(series))).fit(
                cov_type="HAC", cov_kwds={"maxlags": 4}
            )
            out_rows.append(
                {"spec": spec_name, "variable": nm, "months": len(series),
                 "mean_coef": float(series.mean()), "t_stat_nw": float(model.tvalues[0])}
            )
    return pd.DataFrame(out_rows)


def _decomposition(elig: pd.DataFrame, results: dict) -> pd.DataFrame:
    """Exact multiplicative decomposition of the price return:
    (1+r) = (1+r_nav) x (1+d_t)/(1+d_{t-1}).
    Computed per security over the holding month, aggregated at portfolio
    weights for strategy A and the EW benchmark. Distributions are NOT
    included on either side (see README)."""
    pan = elig.sort_values(["security_id", "date"]).copy()
    pan["period"] = pan["date"].dt.to_period("M")
    nav_next, disc_next = {}, {}
    for sid, g in pan.groupby("security_id"):
        s_nav = g.set_index("period")["nav_per_share"]
        s_disc = g.set_index("period")["discount"]
        full = pd.period_range(s_nav.index.min(), s_nav.index.max(), freq="M")
        s_nav, s_disc = s_nav.reindex(full), s_disc.reindex(full)
        nav_r = s_nav.shift(-1) / s_nav - 1
        disc_r = (1 + s_disc.shift(-1)) / (1 + s_disc) - 1
        for p in g["period"]:
            nav_next[(sid, p)] = nav_r.get(p, np.nan)
            disc_next[(sid, p)] = disc_r.get(p, np.nan)
    pan["fwd_nav_return"] = [nav_next.get((s, p), np.nan) for s, p in zip(pan["security_id"], pan["period"])]
    pan["fwd_discount_return"] = [disc_next.get((s, p), np.nan) for s, p in zip(pan["security_id"], pan["period"])]

    rows = []
    for name in ("A_absolute_discount_decile", "E_discount_overshoot", "BM_equal_weight_universe"):
        res = results.get(name)
        if res is None or res.holdings.empty:
            continue
        # holding_month is t+1; the decomposition is keyed at signal month t:
        h2 = res.holdings.copy()
        h2["signal_month"] = (pd.PeriodIndex(h2["holding_month"], freq="M") - 1).astype(str)
        h2 = h2.merge(
            pan.assign(obs_str=pan["obs_month"])[
                ["security_id", "obs_str", "fwd_nav_return", "fwd_discount_return"]
            ],
            left_on=["security_id", "signal_month"],
            right_on=["security_id", "obs_str"],
            how="left",
        )
        for month, grp in h2.groupby("holding_month"):
            ok = grp.dropna(subset=["fwd_nav_return", "fwd_discount_return", "weight"])
            if ok.empty:
                continue
            w = ok["weight"] / ok["weight"].sum()
            rows.append(
                {"strategy": name, "holding_month": month,
                 "nav_return": float((w * ok["fwd_nav_return"]).sum()),
                 "discount_return": float((w * ok["fwd_discount_return"]).sum()),
                 "coverage_weight": float(ok["weight"].sum() / grp["weight"].sum())}
            )
    return pd.DataFrame(rows)


def _yield_differentials(elig: pd.DataFrame, results: dict) -> pd.DataFrame:
    rows = []
    uni_yield = elig.groupby("obs_month")["dividend_yield"].mean()
    for name, res in results.items():
        if res.holdings.empty:
            continue
        h = res.holdings.copy()
        h["signal_month"] = (pd.PeriodIndex(h["holding_month"], freq="M") - 1).astype(str)
        merged = h.merge(
            elig[["security_id", "obs_month", "dividend_yield"]],
            left_on=["security_id", "signal_month"], right_on=["security_id", "obs_month"],
            how="left",
        )
        port_yield = merged.groupby("signal_month").apply(
            lambda g: (g["weight"] * g["dividend_yield"]).sum() / g["weight"].sum()
            if g["dividend_yield"].notna().any() else np.nan,
            include_groups=False,
        )
        rows.append(
            {"strategy": name,
             "avg_portfolio_trailing_yield_pct": float(port_yield.mean()),
             "avg_universe_trailing_yield_pct": float(uni_yield.mean()),
             "note": "trailing published yields (PFY); indicative of the dividend gap in price-only returns"}
        )
    return pd.DataFrame(rows)
