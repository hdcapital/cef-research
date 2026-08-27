"""Monthly point-in-time panel for ASX LICs/LITs.

One row per security x month from the monthly investment-products reports.
Unlike the UK study, the source publishes each fund's 1-MONTH TOTAL RETURN
(distributions included) and its premium/discount to pre-tax NTA directly,
so fwd_return here is a genuine total return taken from the following
month's report - nothing is reconstructed. Share-price and NTA columns
provide an independent calculated discount for cross-checking where both
exist (later vintages).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .parsers import parse_ipr_lic_sheet, report_observation_month

log = logging.getLogger(__name__)

EXCLUDED_TYPES = {"Index", "CDI"}


def parse_all_reports(raw_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(raw_dir.glob("*_ipr_*.xlsx")):
        try:
            rows = parse_ipr_lic_sheet(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed %s: %s", path.name, exc)
            continue
        if not rows:
            continue
        obs = report_observation_month(path, rows)
        if obs is None:
            log.warning("%s: cannot determine observation month", path.name)
            continue
        df = pd.DataFrame(rows)
        df["obs_month"] = obs
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    log.info("parsed %d fund-month rows from %d reports", len(out), len(frames))
    return out


def build_panel(cfg: dict) -> pd.DataFrame:
    raw_dir = Path(cfg["download"]["raw_dir"])
    processed = Path(cfg["paths"]["processed_dir"])
    outputs = Path(cfg["paths"]["outputs_dir"])
    processed.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)

    panel = parse_all_reports(raw_dir)
    if panel.empty:
        raise RuntimeError(f"no reports parsed from {raw_dir}; run au download first")

    panel["security_id"] = "ASX:" + panel["code"]
    # one row per security-month (adjacent vintages can map to the same
    # modal month if a report was re-issued; keep the later file's row)
    panel = panel.sort_values(["security_id", "obs_month", "source_file"])
    panel = panel.groupby(["security_id", "obs_month"], as_index=False).last()

    panel["discount"] = panel["published_discount"]
    if "nta_price" in panel.columns:
        with np.errstate(all="ignore"):
            panel["calculated_discount"] = np.where(
                panel["share_price"].notna() & panel["nta_price"].notna() & (panel["nta_price"] > 0),
                panel["share_price"] / panel["nta_price"] - 1.0,
                np.nan,
            )
    else:
        panel["calculated_discount"] = np.nan

    # NTA staleness: months between the row's NTA date and its obs month
    nta = pd.to_datetime(panel["nta_date"], errors="coerce")
    obs_end = pd.PeriodIndex(panel["obs_month"], freq="M").to_timestamp(how="end")
    panel["nta_staleness_days"] = (obs_end.normalize() - nta.dt.normalize()).dt.days

    # eligibility
    q = cfg["quality"]
    ok_type = ~panel["product_type"].isin(EXCLUDED_TYPES) & panel["product_type"].notna()
    panel["eligible"] = (
        ok_type
        & panel["discount"].notna()
        & (panel["discount"] >= q.get("eligibility_discount_floor", -0.85))
        & (panel["discount"] <= q.get("premium_ceiling", 1.0))
    )

    # forward TOTAL return: next month's report publishes the return earned
    # over that month, so fwd_return at signal month s = tr_1m of month s+1
    tr = panel[["security_id", "obs_month", "tr_1m", "share_price"]].copy()
    tr["signal_month"] = (pd.PeriodIndex(tr["obs_month"], freq="M") - 1).astype(str)
    panel = panel.merge(
        tr.rename(columns={"tr_1m": "fwd_return", "obs_month": "fwd_return_month",
                           "share_price": "fwd_price"})[
            ["security_id", "signal_month", "fwd_return", "fwd_return_month", "fwd_price"]],
        left_on=["security_id", "obs_month"], right_on=["security_id", "signal_month"],
        how="left",
    ).drop(columns=["signal_month"])
    panel["fwd_return_status"] = np.where(panel["fwd_return"].notna(), "observed", "missing_next_report")

    # price-only cross-check return (validation, not the primary series)
    panel["fwd_price_return"] = np.where(
        panel["share_price"].notna() & panel["fwd_price"].notna() & (panel["share_price"] > 0),
        panel["fwd_price"] / panel["share_price"] - 1.0,
        np.nan,
    )
    # a fund's TR minus its price return approximates the month's
    # distribution yield; grossly negative gaps flag data problems
    panel["tr_minus_price"] = panel["fwd_return"] - panel["fwd_price_return"]

    # extreme-return guard mirroring the UK rules: |TR| > 100% flagged,
    # never deleted; TR inconsistent with price return by >25pp invalidated
    incons = panel["tr_minus_price"].abs() > 0.25
    panel.loc[incons, "fwd_return_status"] = "invalid_tr_price_inconsistent"
    panel.loc[incons, "fwd_return"] = np.nan

    # ---------------- manager performance: NTA total returns ----------------
    # NTA per share = price / (1 + published discount) - arithmetic on two
    # published values (explicit NTA Price columns only exist in later
    # vintages; where present they cross-check this derivation).
    # Manager (NTA) total return adds back implied distributions:
    #   implied_div_m = (fund TR_m - price return_m) x price_{m-1}
    #   nta_tr_m = nta_m/nta_{m-1} - 1 + implied_div_m / nta_{m-1}
    with np.errstate(all="ignore"):
        panel["nta_derived"] = np.where(
            panel["share_price"].notna() & panel["discount"].notna()
            & (panel["discount"] > -0.95),
            panel["share_price"] / (1.0 + panel["discount"]),
            np.nan,
        )
    frames = []
    for sid, g in panel.sort_values("obs_month").groupby("security_id"):
        periods = pd.PeriodIndex(g["obs_month"], freq="M")
        full = pd.period_range(periods.min(), periods.max(), freq="M")
        px = pd.Series(g["share_price"].values, index=periods).reindex(full)
        nta = pd.Series(g["nta_derived"].values, index=periods).reindex(full)
        tr = pd.Series(g["tr_1m"].values, index=periods).reindex(full)
        px_ret = px / px.shift(1) - 1
        implied_div = ((tr - px_ret) * px.shift(1)).clip(lower=0)
        nta_tr = nta / nta.shift(1) - 1 + (implied_div / nta.shift(1)).fillna(0.0)
        # guard: reject months where derived NTA moves >4x against the
        # fund's own TR (a discount misprint), mirroring the UK rules
        bad = ((1 + nta_tr) / (1 + tr)).abs()
        nta_tr = nta_tr.where(~(bad > 4) & ~(bad < 0.25))
        log1p = np.log1p(nta_tr)
        obs = nta_tr.notna().astype(float)
        rec = {"security_id": sid, "obs_month": [str(p) for p in full],
               "nta_total_return": nta_tr.values}
        for w in (36, 60):
            ssum = log1p.rolling(w, min_periods=int(0.9 * w)).sum()
            n = obs.rolling(w, min_periods=1).sum()
            cagr = np.expm1(ssum * (12.0 / n.where(n > 0)))
            rec[f"nta_tr_cagr_{w // 12}y"] = cagr.where(n >= 0.9 * w).values
        frames.append(pd.DataFrame(rec))
    roll = pd.concat(frames, ignore_index=True)
    panel = panel.merge(roll, on=["security_id", "obs_month"], how="left")

    panel["date"] = pd.PeriodIndex(panel["obs_month"], freq="M").to_timestamp(how="end").normalize()
    panel["market_cap"] = panel.get("market_cap")

    out_path = processed / "au_monthly_panel.parquet"
    panel.to_parquet(out_path, index=False)

    elig = panel[panel["eligible"]]
    log.info("AU panel: %d rows, %d securities, %s..%s | eligible %d rows, "
             "%d invalidated returns",
             len(panel), panel["security_id"].nunique(),
             panel["obs_month"].min(), panel["obs_month"].max(),
             len(elig), int(incons.sum()))

    counts = elig.groupby("obs_month")["security_id"].nunique()
    counts.to_csv(outputs / "au_universe_counts.csv")

    # per-fund NTA (manager) performance deliverables
    nav_out = panel[panel["eligible"]][
        ["security_id", "company_name", "obs_month", "nta_derived",
         "nta_total_return", "nta_tr_cagr_3y", "nta_tr_cagr_5y"]
    ].dropna(subset=["nta_total_return", "nta_tr_cagr_3y", "nta_tr_cagr_5y"], how="all")
    nav_out.to_csv(outputs / "au_nta_performance_rolling.csv", index=False)
    latest = panel["obs_month"].max()
    leaders = (panel[(panel["obs_month"] == latest) & panel["eligible"]]
               [["code", "company_name", "sector", "discount", "market_cap",
                 "nta_tr_cagr_3y", "nta_tr_cagr_5y"]]
               .sort_values("nta_tr_cagr_5y", ascending=False))
    leaders.to_csv(outputs / "au_nta_leaderboard.csv", index=False)
    return panel


def load_panel(cfg: dict) -> pd.DataFrame:
    path = Path(cfg["paths"]["processed_dir"]) / "au_monthly_panel.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run au build-panel first")
    return pd.read_parquet(path)
