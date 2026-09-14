"""Monthly discount aggregates at market-wide, per-market and sub-segment level.

Conventions
-----------
* `discount` is a decimal on the panel convention: negative = trading below
  NAV. Every output keeps that sign, so a falling line is a WIDENING
  discount. A `*_pct` twin is emitted for readability (-0.15 -> -15.0).
* The equal-weighted mean is the headline ("the average fund's discount").
  The cap-weighted mean ("the average dollar's discount") is reported
  beside it because the two diverge sharply when large trusts trade
  differently from small ones - which is itself a finding, not noise.
* Distribution stats are suppressed (NaN) for groups thinner than
  `min_funds`, so a one-fund month never renders as a sub-segment average.
  The `n_funds` count is always kept so the thinness is visible.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_FUNDS_DEFAULT = 3

# Emitted for every grouping, in this order.
STAT_COLUMNS = [
    "n_funds", "sufficient",
    "mean_discount", "median_discount", "cap_weighted_discount",
    "p10_discount", "p25_discount", "p75_discount", "p90_discount",
    "std_discount", "iqr_discount",
    "pct_at_discount", "pct_wider_than_10", "pct_wider_than_20", "pct_at_premium",
    "n_with_market_cap", "total_market_cap_local",
]


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    """Cap-weighted mean over rows where BOTH the value and a positive
    weight exist. Returns NaN rather than silently falling back to the
    equal-weighted mean when no usable weight is present - the caller can
    then see that cap weighting was not possible for that group."""
    ok = values.notna() & weights.notna() & (weights > 0)
    if not ok.any():
        return np.nan
    v = values[ok].to_numpy(dtype="float64")
    w = weights[ok].to_numpy(dtype="float64")
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        return np.nan
    return float(np.dot(v, w) / total)


def _stats(group: pd.DataFrame, min_funds: int) -> pd.Series:
    d = group["discount"]
    n = int(d.notna().sum())
    enough = n >= min_funds
    mc = group["market_cap_local"]

    def q(p: float) -> float:
        return float(d.quantile(p)) if enough else np.nan

    p25, p75 = q(0.25), q(0.75)
    return pd.Series({
        "n_funds": n,
        "sufficient": bool(enough),
        "mean_discount": float(d.mean()) if enough else np.nan,
        "median_discount": float(d.median()) if enough else np.nan,
        "cap_weighted_discount": _weighted_mean(d, mc) if enough else np.nan,
        "p10_discount": q(0.10),
        "p25_discount": p25,
        "p75_discount": q(0.75),
        "p90_discount": q(0.90),
        "std_discount": float(d.std(ddof=1)) if n >= 2 and enough else np.nan,
        "iqr_discount": (p75 - p25) if enough else np.nan,
        "pct_at_discount": float((d < 0).mean()) if enough else np.nan,
        "pct_wider_than_10": float((d <= -0.10).mean()) if enough else np.nan,
        "pct_wider_than_20": float((d <= -0.20).mean()) if enough else np.nan,
        "pct_at_premium": float((d > 0).mean()) if enough else np.nan,
        "n_with_market_cap": int((mc.notna() & (mc > 0)).sum()),
        "total_market_cap_local": float(mc[mc > 0].sum()) if mc.notna().any() else np.nan,
    })


def _add_pct_twins(df: pd.DataFrame) -> pd.DataFrame:
    """Percentage-point twins of every decimal discount column, for readers
    and charts. Shares (`pct_at_discount`) are already fractions of funds
    and are converted too, but named `_share_pct` so the two never blur."""
    out = df.copy()
    for col in [c for c in out.columns if c.endswith("_discount")]:
        out[f"{col}_pct"] = out[col] * 100.0
    for col in [c for c in out.columns if c.startswith("pct_") and not c.endswith("_pct")]:
        out[f"{col}_share_pct"] = out[col] * 100.0
    return out


def by_group(panel: pd.DataFrame, keys: list[str],
             min_funds: int = MIN_FUNDS_DEFAULT) -> pd.DataFrame:
    """Monthly stats grouped by `month` + `keys` (keys may be empty)."""
    if panel.empty:
        return pd.DataFrame(columns=["month"] + keys + STAT_COLUMNS)
    grouping = ["month"] + list(keys)
    rows = (panel.groupby(grouping, dropna=False, sort=True)
                 .apply(lambda g: _stats(g, min_funds), include_groups=False)
                 .reset_index())
    rows["n_funds"] = rows["n_funds"].astype(int)
    rows["month_end"] = (
        pd.PeriodIndex(rows["month"], freq="M").to_timestamp(how="end").normalize()
    )
    ordered = ["month", "month_end"] + list(keys) + STAT_COLUMNS
    return _add_pct_twins(rows[ordered])


def market_wide(panel: pd.DataFrame, min_funds: int = MIN_FUNDS_DEFAULT) -> pd.DataFrame:
    """Both markets pooled, one row per month.

    Two means are reported and they answer different questions:
      mean_discount          every fund one vote -> dominated by whichever
                             market lists more funds (the UK, ~4x the ASX).
      mean_discount_balanced each MARKET one vote -> a like-for-like
                             cross-market read.
    `markets_included` records composition, because the ASX panel starts a
    decade after the UK one and a pooled series that silently changes
    constituents in 2017 would read as a market move.
    """
    wide = by_group(panel, keys=[], min_funds=min_funds)
    if wide.empty:
        return wide
    per_market = by_group(panel, keys=["market"], min_funds=min_funds)
    balanced = (per_market.groupby("month")["mean_discount"].mean()
                          .rename("mean_discount_balanced"))
    composition = (per_market[per_market["n_funds"] > 0]
                   .groupby("month")["market"]
                   .agg(lambda s: "+".join(sorted(set(s))))
                   .rename("markets_included"))
    n_markets = (per_market[per_market["n_funds"] > 0]
                 .groupby("month")["market"].nunique().rename("n_markets"))
    wide = (wide.merge(balanced, on="month", how="left")
                .merge(composition, on="month", how="left")
                .merge(n_markets, on="month", how="left"))
    wide["mean_discount_balanced_pct"] = wide["mean_discount_balanced"] * 100.0
    return wide


def by_market(panel: pd.DataFrame, min_funds: int = MIN_FUNDS_DEFAULT) -> pd.DataFrame:
    return by_group(panel, keys=["market"], min_funds=min_funds)


def by_segment(panel: pd.DataFrame, min_funds: int = MIN_FUNDS_DEFAULT) -> pd.DataFrame:
    return by_group(panel, keys=["market", "segment"], min_funds=min_funds)


def by_sector(panel: pd.DataFrame, min_funds: int = MIN_FUNDS_DEFAULT) -> pd.DataFrame:
    return by_group(panel, keys=["market", "sector_canon"], min_funds=min_funds)


def segment_summary(seg_monthly: pd.DataFrame) -> pd.DataFrame:
    """Whole-sample summary per market x segment, from the monthly series.

    Averages the monthly means (each month one vote) so a month when a
    segment happened to list more funds does not dominate the period
    average.
    """
    if seg_monthly.empty:
        return seg_monthly
    usable = seg_monthly[seg_monthly["sufficient"]]
    if usable.empty:
        return pd.DataFrame()
    out = (usable.groupby(["market", "segment"])
                 .agg(first_month=("month", "min"),
                      last_month=("month", "max"),
                      months=("month", "nunique"),
                      avg_funds=("n_funds", "mean"),
                      max_funds=("n_funds", "max"),
                      avg_discount=("mean_discount", "mean"),
                      median_of_monthly_means=("mean_discount", "median"),
                      widest_month_discount=("mean_discount", "min"),
                      narrowest_month_discount=("mean_discount", "max"),
                      volatility_of_discount=("mean_discount", "std"))
                 .reset_index())
    widest = usable.loc[usable.groupby(["market", "segment"])["mean_discount"].idxmin(),
                        ["market", "segment", "month"]].rename(columns={"month": "widest_month"})
    narrowest = usable.loc[usable.groupby(["market", "segment"])["mean_discount"].idxmax(),
                           ["market", "segment", "month"]].rename(columns={"month": "narrowest_month"})
    # "Latest" must be resolved PER MARKET and PER SEGMENT, never against one
    # global last month. The two markets end on different months (different
    # publication lags, and the ASX report lands later than the AIC file), so
    # a single global month nulls out every segment of whichever market does
    # not reach it - which silently emptied the whole UK column.
    last_rows = usable.loc[usable.groupby(["market", "segment"])["month"].idxmax(),
                           ["market", "segment", "month", "mean_discount", "n_funds"]]
    last_rows = last_rows.rename(columns={"month": "latest_month",
                                          "mean_discount": "latest_discount",
                                          "n_funds": "latest_n_funds"})
    # A segment that stopped reporting years ago still gets its own honest
    # "latest", but it is marked stale so it is never read as current.
    market_last = usable.groupby("market")["month"].max().rename("market_latest_month")
    out = (out.merge(widest, on=["market", "segment"], how="left")
              .merge(narrowest, on=["market", "segment"], how="left")
              .merge(last_rows, on=["market", "segment"], how="left")
              .merge(market_last, on="market", how="left"))
    out["is_current"] = out["latest_month"] == out["market_latest_month"]
    out["latest_vs_own_average_pp"] = (out["latest_discount"] - out["avg_discount"]) * 100.0
    for col in ("avg_discount", "median_of_monthly_means", "widest_month_discount",
                "narrowest_month_discount", "latest_discount", "volatility_of_discount"):
        out[f"{col}_pct"] = out[col] * 100.0
    return out.sort_values(["market", "avg_discount"])
