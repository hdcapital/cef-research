"""Cross-sectional decile analysis (Stage 9).

Each month, sort the eligible cross-section into quantile buckets on a
signal (ascending: bucket 1 = lowest signal = cheapest under our sign
conventions) and record the equal-weighted forward one-month return of
each bucket. Securities with missing forward returns are excluded from
that month's bucket return and counted, never zero-filled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .performance import cagr, sharpe, t_stat_mean, annual_volatility


def monthly_bucket_returns(
    panel: pd.DataFrame, signal_col: str, n_buckets: int = 10, min_names: int = 20
) -> pd.DataFrame:
    """Long frame: one row per (signal month, bucket) with the bucket's
    equal-weight forward return. Months with fewer than ``min_names``
    ranked names are skipped (a 10-way sort over 12 names is noise)."""
    rows = []
    panel = panel.copy()
    panel["__period"] = panel["date"].dt.to_period("M")
    for t, grp in panel.groupby("__period"):
        elig = grp.dropna(subset=[signal_col])
        if len(elig) < min_names:
            continue
        try:
            buckets = pd.qcut(elig[signal_col].rank(method="first"), n_buckets, labels=False) + 1
        except ValueError:
            continue
        for b in range(1, n_buckets + 1):
            members = elig[buckets == b]
            fr = members["fwd_return"].dropna()
            rows.append(
                {
                    "signal_month": t,
                    "holding_month": t + 1,
                    "bucket": b,
                    "n_members": len(members),
                    "n_with_return": len(fr),
                    "fwd_return": float(fr.mean()) if len(fr) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def bucket_summary(bucket_returns: pd.DataFrame, n_buckets: int = 10) -> pd.DataFrame:
    """Aggregate per-bucket statistics across all months, plus a long-short
    (bucket 1 minus bucket N) row."""
    out = []
    series = {}
    for b, grp in bucket_returns.groupby("bucket"):
        s = grp.set_index("holding_month")["fwd_return"].dropna()
        series[b] = s
        out.append(
            {
                "bucket": b,
                "avg_fwd_return_monthly": float(s.mean()) if len(s) else np.nan,
                "cagr": cagr(s),
                "volatility": annual_volatility(s),
                "sharpe": sharpe(s),
                "t_stat": t_stat_mean(s),
                "win_rate": float((s > 0).mean()) if len(s) else np.nan,
                "months": len(s),
                "avg_members": float(grp["n_members"].mean()),
            }
        )
    lo, hi = series.get(1), series.get(n_buckets)
    if lo is not None and hi is not None:
        ls = (lo - hi).dropna()
        out.append(
            {
                "bucket": f"1-{n_buckets} (long-short)",
                "avg_fwd_return_monthly": float(ls.mean()) if len(ls) else np.nan,
                "cagr": np.nan,  # a self-financing spread has no compounding CAGR
                "volatility": annual_volatility(ls),
                "sharpe": sharpe(ls),
                "t_stat": t_stat_mean(ls),
                "win_rate": float((ls > 0).mean()) if len(ls) else np.nan,
                "months": len(ls),
                "avg_members": np.nan,
            }
        )
    return pd.DataFrame(out)
