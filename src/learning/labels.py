"""Resolution labels for the monthly research panel.

For a (security_id, obs_month) row: did the fund's known ending fall
within the next N months? The label is the future; the features must not
be. Every label is joined on the panel's keys so the same row that carries
a feature at month t carries "resolved within 12 months of t".

`resolved_within_{N}m` counts every known ending (outcome != unresolved,
whichever exit type); `value_realising_within_{N}m` counts only endings
that paid NAV-ish value, and is NaN where the exit type is not yet known
(an AU delisting whose notice has not been read) so an unknown never
counts as a no.
"""

from __future__ import annotations

import pandas as pd

HORIZONS = (6, 12, 18)


def _months_between(later: pd.Series, earlier: pd.Series) -> pd.Series:
    a = pd.PeriodIndex(later.astype(str).str[:7], freq="M")
    b = pd.PeriodIndex(earlier.astype(str).str[:7], freq="M")
    return pd.Series((a - b).map(lambda d: d.n if d is not pd.NaT else None),
                     index=later.index, dtype="float")


def attach(panel: pd.DataFrame, episodes: pd.DataFrame,
           horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """Add label columns to a frame keyed (security_id, obs_month)."""
    out = panel.copy()
    ep = episodes[episodes["outcome"].ne("unresolved")]
    ep = ep.drop_duplicates("security_id").set_index("security_id")
    end = out["security_id"].map(ep["end_month"])
    vr = out["security_id"].map(ep["value_realising"])
    has = end.notna()
    gap = pd.Series(float("nan"), index=out.index)
    gap[has] = _months_between(end[has], out.loc[has, "obs_month"])
    out["months_to_end"] = gap
    out["outcome"] = out["security_id"].map(ep["outcome"])
    for h in horizons:
        within = has & (gap >= 0) & (gap <= h)
        out[f"resolved_within_{h}m"] = within.astype(int)
        col = pd.Series(0.0, index=out.index)
        col[within & vr.isna()] = float("nan")
        col[within & vr.eq(True)] = 1.0
        out[f"value_realising_within_{h}m"] = col
    # a row after the fund's end month is not a valid observation
    out.loc[has & (gap < 0), [f"resolved_within_{h}m" for h in horizons]] = 0
    return out


def base_rates(labelled: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> dict:
    """The unconditional resolution rate per horizon: what any feature has to beat."""
    return {f"resolved_within_{h}m": float(labelled[f"resolved_within_{h}m"].mean())
            for h in horizons if f"resolved_within_{h}m" in labelled}
