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
    return panel


def load_panel(cfg: dict) -> pd.DataFrame:
    path = Path(cfg["paths"]["processed_dir"]) / "au_monthly_panel.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run au build-panel first")
    return pd.read_parquet(path)
