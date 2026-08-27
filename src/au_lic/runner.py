"""AU backtest orchestration - the core strategy suite on the LIC/LIT
panel, reusing the uk_cef engine unchanged (signals, portfolio, costs,
deciles, performance). Returns here are the source's own 1-month TOTAL
returns, so results are total-return based from the start.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from uk_cef import costs as costs_mod
from uk_cef import performance as perf
from uk_cef.deciles import bucket_summary, monthly_bucket_returns
from uk_cef.portfolio import benchmark_universe, run_strategy
from uk_cef.signals import build_all_signals

from .panel import load_panel

log = logging.getLogger(__name__)


def _skip_month_panel(elig: pd.DataFrame) -> pd.DataFrame:
    """fwd_return at s replaced by the month s+2 total return (the
    measurement-error / execution-speed bound, as in the UK study)."""
    tr_map = {
        (s, m): r for s, m, r in
        zip(elig["security_id"], elig["fwd_return_month"], elig["fwd_return"])
        if pd.notna(r) and isinstance(m, str)
    }
    out = elig.copy()
    out["fwd_return"] = [
        tr_map.get((s, str(pd.Period(m, freq="M") + 2)), np.nan)
        for s, m in zip(out["security_id"], out["obs_month"])
    ]
    return out


def run_backtests(cfg: dict) -> None:
    out_dir = Path(cfg["paths"]["outputs_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    panel = load_panel(cfg)
    panel = panel[panel["obs_month"] >= cfg["project"]["start_month"]]
    elig = panel[panel["eligible"]].copy()
    elig["date"] = pd.to_datetime(elig["date"])
    log.info("AU eligible: %d rows, %d securities, %d months",
             len(elig), elig["security_id"].nunique(), elig["obs_month"].nunique())
    elig = build_all_signals(elig, cfg)
    elig.to_parquet(Path(cfg["paths"]["processed_dir"]) / "au_signal_panel.parquet", index=False)

    z = cfg["signals"]["zscore_window_months"]
    min_names = cfg["strategies"]["min_names"]
    specs = [
        dict(name="A_absolute_discount_decile", signal_col="discount", top_fraction=0.10),
        dict(name="B_absolute_discount_quintile", signal_col="discount", top_fraction=0.20),
        dict(name="C_discount_zscore", signal_col=f"discount_z_{z}m", top_fraction=0.10),
        dict(name="D_sector_neutral_zscore", signal_col=f"discount_z_{z}m",
             top_fraction=0.10, sector_neutral=True),
        dict(name="E_discount_overshoot", signal_col="overshoot_score", top_fraction=0.10),
    ]
    results = {}
    for spec in specs:
        results[spec["name"]] = run_strategy(elig, min_names=min_names, **spec)
    results["BM_equal_weight_universe"] = benchmark_universe(elig, "equal")
    cap = elig[elig["market_cap"].notna()]
    results["BM_cap_weight_universe"] = benchmark_universe(cap, "market_cap")

    bench = results["BM_equal_weight_universe"].gross_returns
    headline_bps = cfg["costs"]["headline_bps_one_way"]
    summary_rows = []
    monthly_frames = {}
    splits = {"full": None, **{k: tuple(v) for k, v in cfg["sample_splits"].items()}}
    for name, res in results.items():
        m = costs_mod.apply_costs(res.monthly, headline_bps, 0.0) if not res.monthly.empty else res.monthly
        monthly_frames[name] = m.assign(strategy=name) if not m.empty else m
        gross = res.gross_returns
        net = None
        if not m.empty:
            net = m.set_index("holding_month")["net_return"]
            net.index = pd.PeriodIndex(net.index, freq="M").to_timestamp(how="end").normalize()
        for split_name, bounds in splits.items():
            g = _slice(gross, bounds)
            b = _slice(bench, bounds)
            row = perf.summarize(g, b if name != "BM_equal_weight_universe" else None,
                                 name=f"{name}|{split_name}")
            row.update({"strategy": name, "period": split_name, "basis": "gross_TR"})
            summary_rows.append(row)
            if net is not None and split_name == "full":
                row = perf.summarize(_slice(net, bounds),
                                     b if name != "BM_equal_weight_universe" else None,
                                     name=f"{name}|net")
                row.update({"strategy": name, "period": split_name, "basis": "net_TR"})
                summary_rows.append(row)

    # skip-month variants
    skip = _skip_month_panel(elig)
    skip_bench = benchmark_universe(skip, "equal", name="bm_skip").gross_returns
    for spec in specs:
        s2 = dict(spec)
        s2["name"] = spec["name"] + "_SKIP1M"
        res = run_strategy(skip, min_names=min_names, **s2)
        row = perf.summarize(res.gross_returns, skip_bench, name=s2["name"])
        row.update({"strategy": s2["name"], "period": "full", "basis": "gross_TR_skip1m"})
        summary_rows.append(row)
        results[s2["name"]] = res

    pd.DataFrame(summary_rows).to_csv(out_dir / "au_performance_summary.csv", index=False)
    pd.concat([m for m in monthly_frames.values() if not m.empty], ignore_index=True).to_csv(
        out_dir / "au_monthly_returns.csv", index=False)
    holdings = pd.concat(
        [r.holdings for n, r in results.items()
         if not r.holdings.empty and not n.startswith("BM_")], ignore_index=True)
    holdings.to_csv(out_dir / "au_holdings.csv", index=False)

    # deciles
    decs = []
    for label, col in {"absolute_discount": "discount",
                       "discount_zscore": f"discount_z_{z}m",
                       "widening_3m": "discount_change_3m",
                       "overshoot": "overshoot_score"}.items():
        br = monthly_bucket_returns(elig, col, n_buckets=10, min_names=20)
        if br.empty:
            # ~90-name universe: quintiles where deciles are too thin
            br = monthly_bucket_returns(elig, col, n_buckets=5, min_names=15)
            if br.empty:
                continue
            s = bucket_summary(br, 5)
        else:
            s = bucket_summary(br, 10)
        s.insert(0, "signal", label)
        decs.append(s)
    if decs:
        pd.concat(decs, ignore_index=True).to_csv(out_dir / "au_decile_summary.csv", index=False)

    series = pd.DataFrame({n: r.gross_returns for n, r in results.items()})
    series.to_csv(out_dir / "au_strategy_returns.csv")

    meta = {"n_eligible_rows": int(len(elig)),
            "n_securities": int(elig["security_id"].nunique()),
            "months": [str(elig["obs_month"].min()), str(elig["obs_month"].max())],
            "return_basis": "1-month TOTAL returns as published in ASX monthly reports"}
    (out_dir / "au_backtest_meta.json").write_text(json.dumps(meta, indent=2))
    log.info("AU backtest complete: %s", meta)


def _slice(s: pd.Series, bounds) -> pd.Series:
    if s is None or s.empty or bounds is None:
        return s if s is not None else pd.Series(dtype=float)
    a = pd.Period(bounds[0], freq="M").to_timestamp(how="end").normalize()
    b = pd.Period(bounds[1], freq="M").to_timestamp(how="end").normalize() + pd.Timedelta(days=1)
    return s[(s.index >= a) & (s.index <= b)]
