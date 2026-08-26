"""Discount-based signals.

All functions operate on a long panel DataFrame with one row per
security x month. Required columns are documented per function. The panel's
``date`` column must hold month-end pandas Timestamps.

Sign convention (fixed project-wide): ``discount = price / NAV - 1``.
Negative = trading at a discount; positive = premium.

Look-ahead safety: every signal at month t uses only rows dated <= t for
that security (trailing windows are aligned on a monthly PeriodIndex so
gaps in a series cannot smuggle future observations into the window).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_BASE_COLS = ("date", "security_id", "discount")


def compute_discount(price: pd.Series | float, nav: pd.Series | float):
    """discount = price / NAV - 1. Returns NaN where either input is missing
    or NAV is not strictly positive (a non-positive NAV cannot produce a
    meaningful discount and is flagged upstream, not fixed here)."""
    price = pd.Series(price) if not isinstance(price, pd.Series) else price
    nav = pd.Series(nav) if not isinstance(nav, pd.Series) else nav
    out = price / nav - 1.0
    out[(nav <= 0) | nav.isna() | price.isna()] = np.nan
    return out


def _check_panel(panel: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_BASE_COLS if c not in panel.columns]
    if missing:
        raise KeyError(f"panel missing required columns: {missing}")
    dupes = panel.duplicated(subset=["date", "security_id"])
    if dupes.any():
        raise ValueError(
            f"panel has {int(dupes.sum())} duplicate security/month rows; "
            "resolve duplicates before computing signals"
        )


def _monthly_series(group: pd.DataFrame, col: str) -> pd.Series:
    """Return ``col`` indexed by monthly Period with gaps preserved as NaN,
    so that .shift(k) means 'k calendar months ago', never 'k observations
    ago'."""
    s = group.set_index(group["date"].dt.to_period("M"))[col]
    full = pd.period_range(s.index.min(), s.index.max(), freq="M")
    return s.reindex(full)


def add_discount_zscore(
    panel: pd.DataFrame,
    window: int = 36,
    min_history: int = 24,
    out_col: str | None = None,
) -> pd.DataFrame:
    """Trailing z-score of the discount vs the security's own history.

    z_t = (d_t - mean(d_{t-window+1..t})) / std(d_{t-window+1..t})

    The trailing window includes the current month (CEFConnect-style
    z-stat). ``min_history`` actual observations are required inside the
    window, else NaN. Calendar-aligned: months with no observation count as
    gaps, not as compressed history.
    """
    _check_panel(panel)
    out_col = out_col or f"discount_z_{window}m"
    panel = panel.sort_values(["security_id", "date"]).copy()

    zs = []
    for sid, grp in panel.groupby("security_id", sort=False):
        s = _monthly_series(grp, "discount")
        roll = s.rolling(window, min_periods=min_history)
        mean, std = roll.mean(), roll.std(ddof=1)
        z = (s - mean) / std
        z[std == 0] = np.nan
        lookup = z.to_dict()
        zs.append(
            pd.Series(
                [lookup.get(p, np.nan) for p in grp["date"].dt.to_period("M")],
                index=grp.index,
            )
        )
    panel[out_col] = pd.concat(zs).reindex(panel.index)
    return panel


def add_discount_changes(
    panel: pd.DataFrame, horizons: tuple[int, ...] = (1, 3, 6)
) -> pd.DataFrame:
    """discount_change_{h}m = discount_t - discount_{t-h} (calendar months).
    Missing when the month t-h observation does not exist. Large negative
    values = abnormal recent widening."""
    _check_panel(panel)
    panel = panel.sort_values(["security_id", "date"]).copy()
    cols = {h: [] for h in horizons}
    for sid, grp in panel.groupby("security_id", sort=False):
        s = _monthly_series(grp, "discount")
        periods = grp["date"].dt.to_period("M")
        for h in horizons:
            lag = s.shift(h)
            chg = (s - lag).to_dict()
            cols[h].append(
                pd.Series([chg.get(p, np.nan) for p in periods], index=grp.index)
            )
    for h in horizons:
        panel[f"discount_change_{h}m"] = pd.concat(cols[h]).reindex(panel.index)
    return panel


def add_sector_relative(panel: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    """Per (date, sector): median discount, deviation from median, and
    percentile rank of the discount within the sector (0 = widest discount).
    Sectors with fewer than 3 members that month get NaN percentile (a
    percentile within a 1-2 name 'sector' is noise)."""
    _check_panel(panel)
    if sector_col not in panel.columns:
        raise KeyError(f"panel missing sector column {sector_col!r}")
    panel = panel.copy()
    grp = panel.groupby(["date", sector_col])["discount"]
    panel["sector_median_discount"] = grp.transform("median")
    panel["discount_vs_sector"] = panel["discount"] - panel["sector_median_discount"]
    panel["sector_discount_pct"] = grp.transform(
        lambda s: s.rank(pct=True) if s.notna().sum() >= 3 else pd.Series(np.nan, index=s.index)
    )
    return panel


def add_overshoot_score(
    panel: pd.DataFrame,
    weights: dict[str, float] | None = None,
    z_col: str = "discount_z_36m",
    widening_col: str = "discount_change_3m",
) -> pd.DataFrame:
    """PRE-SPECIFIED composite: 50% z-score rank + 25% absolute discount
    rank + 25% 3m widening rank, each an ascending cross-sectional
    percentile rank (lower raw value = cheaper = lower rank). Lower
    composite = more dislocated. Requires all three components; rows
    missing any component get NaN (never silently reweighted)."""
    weights = weights or {
        "zscore_rank": 0.50,
        "absolute_discount_rank": 0.25,
        "widening_3m_rank": 0.25,
    }
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("overshoot weights must sum to 1")
    _check_panel(panel)
    panel = panel.copy()

    def xrank(col: str) -> pd.Series:
        return panel.groupby("date")[col].rank(pct=True)

    r_z = xrank(z_col)
    r_d = xrank("discount")
    r_w = xrank(widening_col)
    panel["overshoot_score"] = (
        weights["zscore_rank"] * r_z
        + weights["absolute_discount_rank"] * r_d
        + weights["widening_3m_rank"] * r_w
    )
    return panel


def build_all_signals(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Convenience wrapper applying the full pre-specified signal stack."""
    scfg = cfg["signals"]
    for w in scfg["zscore_robustness_windows"]:
        panel = add_discount_zscore(
            panel, window=w, min_history=min(scfg["zscore_min_history_months"], w - 0)
        )
    panel = add_discount_changes(panel, horizons=tuple(scfg["widening_horizons_months"]))
    if "sector" in panel.columns:
        panel = add_sector_relative(panel)
    panel = add_overshoot_score(
        panel,
        weights=scfg["overshoot_weights"],
        z_col=f"discount_z_{scfg['zscore_window_months']}m",
    )
    return panel
