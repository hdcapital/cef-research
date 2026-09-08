"""Does what a fund said predict how it ended? The anticipation test.

Every feature, extracted or headline-derived, is carried to the panel's
(security_id, obs_month) keys point-in-time - a stance stated in a
document persists for `persist_months` and then lapses to silent - and
joined to the resolution labels. Two questions, answered per feature value:

1. resolution rate within N months against the base rate (a two-proportion
   z), split into the development period and the 2022+ holdout the rest of
   the research uses;
2. on the cheap cohort of the monthly panel (own z below -1 and below the
   sector median, the definition the announced-catalyst study already uses),
   next-month return with the feature non-silent versus silent (Welch t),
   the shape of uk_cef.runner._catalyst_analysis_announced.

No model is fitted here. A feature that does not move these two numbers
does not go near the live gates.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from learning import schema as S

HOLDOUT_START = "2022-01"


def features_by_month(rows: pd.DataFrame, months: pd.DataFrame,
                      persist_months: int = 6) -> pd.DataFrame:
    """Point-in-time feature values per (security_id, obs_month).

    For each feature, the latest non-silent value stated in a document dated
    at or before the month's end and within `persist_months`; silent
    otherwise. `months` holds the (security_id, obs_month) keys to fill."""
    out = months[["security_id", "obs_month"]].drop_duplicates().copy()
    for name in S.FEATURES:
        out[name] = S.SILENT[name]
    if rows is None or not len(rows):
        return out
    rows = rows.copy()
    rows["obs_month"] = rows["date"].astype(str).str[:7]
    by_sid = {sid: g.sort_values("date") for sid, g in rows.groupby("security_id")}
    vals = {name: [] for name in S.FEATURES}
    for sid, m in zip(out["security_id"], out["obs_month"]):
        g = by_sid.get(sid)
        p = pd.Period(m, freq="M")
        lo = str(p - persist_months)
        for name in S.FEATURES:
            v = S.SILENT[name]
            if g is not None and name in g.columns:
                w = g[(g["obs_month"] <= m) & (g["obs_month"] > lo)
                      & g[name].notna() & g[name].ne(S.SILENT[name])]
                if len(w):
                    v = w[name].iloc[-1]
            vals[name].append(v)
    for name in S.FEATURES:
        out[name] = vals[name]
    return out


def _z_two_prop(k1, n1, k0, n0) -> float | None:
    if min(n1, n0) == 0:
        return None
    p1, p0 = k1 / n1, k0 / n0
    p = (k1 + k0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return (p1 - p0) / se if se > 0 else None


def anticipation(labelled: pd.DataFrame, feature_cols: list[str],
                 horizons: tuple[int, ...] = (6, 12, 18),
                 holdout_start: str = HOLDOUT_START) -> pd.DataFrame:
    """Resolution rate by feature value against the base rate, per horizon
    and period."""
    rows = []
    df = labelled.copy()
    df["period"] = np.where(df["obs_month"] >= holdout_start, "holdout_2022+", "development")
    for period, g in list(df.groupby("period")) + [("all", df)]:
        for h in horizons:
            lab = f"resolved_within_{h}m"
            if lab not in g:
                continue
            base_k, base_n = int(g[lab].sum()), int(len(g))
            for f in feature_cols:
                if f not in g:
                    continue
                silent = S.SILENT.get(f)
                for val, gv in g.groupby(f, dropna=False):
                    k, n = int(gv[lab].sum()), int(len(gv))
                    rest = g[g[f].ne(val)]
                    rows.append({
                        "period": period, "horizon_months": h, "feature": f,
                        "value": val, "is_silent": val == silent, "n": n,
                        "resolved": k, "rate": k / n if n else None,
                        "base_rate": base_k / base_n if base_n else None,
                        "lift": (k / n) / (base_k / base_n) if n and base_k else None,
                        "z_vs_rest": _z_two_prop(k, n, int(rest[lab].sum()), int(len(rest)))})
    return pd.DataFrame(rows)


def cheap_cohort_returns(elig: pd.DataFrame, feature_cols: list[str],
                         z_col: str = "discount_z_36m") -> pd.DataFrame:
    """Next-month return on the cheap cohort with the feature non-silent
    versus silent. Needs the panel columns fwd_return, discount,
    sector_median_discount and the z column."""
    need = {"fwd_return", "discount", "sector_median_discount", z_col}
    if not need <= set(elig.columns):
        return pd.DataFrame()
    cheap = elig[elig[z_col].notna() & (elig[z_col] < -1) & elig["discount"].notna()
                 & elig["sector_median_discount"].notna()
                 & (elig["discount"] < elig["sector_median_discount"])]
    rows = []
    for f in feature_cols:
        if f not in cheap:
            continue
        flag = cheap[f].notna() & cheap[f].ne(S.SILENT.get(f))
        a = cheap.loc[flag, "fwd_return"].dropna()
        b = cheap.loc[~flag, "fwd_return"].dropna()
        row = {"feature": f, "n_with": int(len(a)), "n_without": int(len(b)),
               "mean_fwd_with": float(a.mean()) if len(a) else None,
               "mean_fwd_without": float(b.mean()) if len(b) else None}
        if len(a) > 10 and len(b) > 10:
            from scipy import stats
            t, p = stats.ttest_ind(a, b, equal_var=False)
            row.update({"difference": float(a.mean() - b.mean()), "t_welch": float(t),
                        "p_value": float(p)})
        rows.append(row)
    return pd.DataFrame(rows)


def headline_feature_flags(hf: pd.DataFrame) -> pd.DataFrame:
    """Headline features as silent/non-silent flags so the same tests apply."""
    out = hf[["security_id", "obs_month"]].copy()
    out["hf_buyback_active"] = np.where(hf["buyback_execs_3m"].fillna(0) > 0, "active", "none")
    out["hf_holder_churn"] = np.where(hf["holder_filings_3m"].fillna(0) >= 3, "churn", "none")
    out["hf_strategic_review"] = np.where(hf["months_since_strategic_review"].notna(),
                                          "seen", "none")
    out["hf_continuation"] = np.where(hf["months_since_continuation"].notna(), "seen", "none")
    out["hf_windup"] = np.where(hf["windup_headline_seen"].fillna(0) > 0, "seen", "none")
    return out


HEADLINE_SILENT = {"hf_buyback_active": "none", "hf_holder_churn": "none",
                   "hf_strategic_review": "none", "hf_continuation": "none",
                   "hf_windup": "none"}
S.SILENT.update(HEADLINE_SILENT)
