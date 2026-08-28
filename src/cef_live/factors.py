"""Tier 1 proxy roll-forward: per-fund factor models with honest errors.

Fits per-fund OLS of monthly NAV total returns on a small pre-specified
factor set (config/params.yaml -> live_nta.factor_model; max 4 factors,
never tuned):

- ``sector_ew``: equal-weight monthly NAV TR of the fund's own sector,
  excluding the fund itself - computed point-in-time from the existing
  research panel, so it is always available and needs no external feed.
- Optional market factors (local index, world proxy, FX) supplied as a
  monthly returns DataFrame by the prices layer once its endpoints have
  passed the probe (scripts/probe_prices.py). The fitted spec is recorded
  per fund, so an estimate always says which factors produced it.

Tracking error is mandatory: a walk-forward backtest (fit on the trailing
window, predict one month ahead, compare with the next published NAV)
produces per-fund ``sigma_1m``. Funds with too few walk-forward errors get
their sector's median sigma, flagged as such. Live estimates then carry
``est_error = sigma_1m * sqrt(staleness_days / 21)``.

No NAV observation is synthesized here: the model only ever produces
*estimates*, stored in estimate fields, anchored to a real published value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sector_ew_returns(panel: pd.DataFrame, ret_col: str) -> pd.DataFrame:
    """Equal-weight sector NAV TR per (sector, obs_month), leave-one-out safe.

    Returns per-(security_id, obs_month) the sector mean EXCLUDING the fund
    itself: (sum - own) / (n - 1). Funds with no sector peers that month get
    NaN - never a filled value.
    """
    df = panel[["security_id", "obs_month", "sector", ret_col]].dropna(
        subset=["sector", ret_col]).copy()
    grp = df.groupby(["sector", "obs_month"])[ret_col]
    stats = grp.agg(["sum", "count"]).rename(columns={"sum": "_sum", "count": "_n"})
    df = df.join(stats, on=["sector", "obs_month"])
    df["sector_ew"] = np.where(df["_n"] > 1, (df["_sum"] - df[ret_col]) / (df["_n"] - 1), np.nan)
    return df[["security_id", "obs_month", "sector_ew"]]


def _ols_beta(y: np.ndarray, X: np.ndarray) -> np.ndarray | None:
    """OLS with intercept; None if the system is degenerate."""
    Xc = np.column_stack([np.ones(len(y)), X])
    try:
        beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return beta


def fit_fund_models(panel: pd.DataFrame, ret_col: str, params: dict,
                    market_factors: pd.DataFrame | None = None) -> pd.DataFrame:
    """Fit per-fund models and walk-forward tracking errors.

    market_factors: optional DataFrame indexed by obs_month (str) with one
    column per factor (already monthly returns). May be None until the
    price layer's endpoints are probe-verified - the model then uses the
    sector_ew factor alone, and records that spec.

    Returns one row per fund: security_id, sector, n_months, factors (|-
    joined spec string), betas (json-ish list), sigma_1m, sigma_source
    (own|sector_median|universe_median), n_walkforward.
    """
    fm = params["live_nta"]["factor_model"]
    min_hist = int(fm["min_history_months"])
    window = int(fm["fit_window_months"])
    min_preds = int(fm["walkforward_min_preds"])
    max_factors = int(fm["max_factors"])

    sew = sector_ew_returns(panel, ret_col)
    df = panel[["security_id", "obs_month", "sector", ret_col]].merge(
        sew, on=["security_id", "obs_month"], how="left")
    if market_factors is not None:
        mf = market_factors.copy()
        mf.index = mf.index.astype(str)
        extra_cols = list(mf.columns)[: max_factors - 1]
        df = df.join(mf[extra_cols], on="obs_month")
    else:
        extra_cols = []
    factor_cols = ["sector_ew"] + extra_cols

    rows = []
    for sid, g in df.sort_values("obs_month").groupby("security_id"):
        g = g.dropna(subset=[ret_col])
        # a factor column is usable for this fund only if it is observed
        # alongside the fund's returns often enough to fit
        usable = [c for c in factor_cols if g[c].notna().sum() >= min_hist]
        gg = g.dropna(subset=usable) if usable else g.iloc[0:0]
        rec = {"security_id": sid,
               "sector": g["sector"].dropna().iloc[-1] if g["sector"].notna().any() else None,
               "n_months": len(gg), "factors": "|".join(usable),
               "betas": None, "sigma_1m": np.nan, "sigma_source": None,
               "n_walkforward": 0}
        if len(gg) >= min_hist and usable:
            y_all = gg[ret_col].to_numpy()
            X_all = gg[usable].to_numpy()
            beta = _ols_beta(y_all[-window:], X_all[-window:])
            if beta is not None:
                rec["betas"] = [round(float(b), 6) for b in beta]
                # walk-forward: fit trailing window, predict next month
                errs = []
                for t in range(min_hist, len(gg)):
                    lo = max(0, t - window)
                    b = _ols_beta(y_all[lo:t], X_all[lo:t])
                    if b is None:
                        continue
                    pred = b[0] + X_all[t] @ b[1:]
                    errs.append(y_all[t] - pred)
                rec["n_walkforward"] = len(errs)
                if len(errs) >= min_preds:
                    rec["sigma_1m"] = float(np.std(errs, ddof=1))
                    rec["sigma_source"] = "own"
        rows.append(rec)

    out = pd.DataFrame(rows)
    # sector-median sigma for funds without their own; universe median last
    sector_med = out[out["sigma_source"] == "own"].groupby("sector")["sigma_1m"].median()
    uni_med = out.loc[out["sigma_source"] == "own", "sigma_1m"].median()
    need = out["sigma_1m"].isna()
    out.loc[need, "sigma_1m"] = out.loc[need, "sector"].map(sector_med)
    out.loc[need & out["sigma_1m"].notna(), "sigma_source"] = "sector_median"
    still = out["sigma_1m"].isna()
    if pd.notna(uni_med):
        out.loc[still, "sigma_1m"] = uni_med
        out.loc[still, "sigma_source"] = "universe_median"
    return out
