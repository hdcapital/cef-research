"""Forward expected IRR - a ranking/screening device, not a forecast.

Pre-specified in config/params.yaml (never tuned against outcomes):

  NAV growth g   fund's 5y NAV TR CAGR (min 3y, else sector median),
                 shrunk 50% toward its sector median, capped [-5%, +15%].
                 Extrapolated momentum is the failure mode; the shrinkage
                 and cap are guardrails, not knobs.
  Terminal disc  the fund's own trailing 5y median discount (own-norm
                 reversion, consistent with the z-score finding), reached
                 by linear narrowing over 5 years from today's estimate.
  Distributions  trailing 12m distribution yield, held flat.

IRR solves from today's price over a 5y NAV path x discount path plus
distributions. Sensitivities re-run the same solve at terminal discount =
{own median, own median -5pp (wider), 0}.

A fund with no usable growth input or no current discount gets no IRR -
absence, never a filled default.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Each market's panel names its NAV/NTA total-return CAGR columns
# differently, and each market's history belongs only to its own funds.
# Running the UK panel against the combined live table gave every ASX LIC a
# NaN growth input - so no IRR, so gate 3 could never pass in Australia -
# while looking like a single tidy call.
CAGR_COLS = {"UK": ("nav_tr_cagr_5y", "nav_tr_cagr_3y"),
             "AU": ("nta_tr_cagr_5y", "nta_tr_cagr_3y")}


def _irr(price: float, nav0: float, g: float, d_now: float, d_term: float,
         dist_yield: float, years: int = 5) -> float | None:
    """Annualised return from price today to the modelled exit."""
    if not price or price <= 0 or not nav0 or nav0 <= 0:
        return None
    nav_t = nav0 * (1 + g) ** years
    exit_price = nav_t * (1 + d_term)
    if exit_price <= 0:
        return None
    # distributions accrue on the modelled price path, discount narrowing
    # linearly from d_now to d_term
    cash = 0.0
    for yr in range(1, years + 1):
        d_y = d_now + (d_term - d_now) * (yr / years)
        p_y = nav0 * (1 + g) ** yr * (1 + d_y)
        cash += dist_yield * p_y
    total = (exit_price + cash) / price
    if total <= 0:
        return None
    return total ** (1 / years) - 1


def build(live: pd.DataFrame, panel_hist: pd.DataFrame, params: dict,
          nav_cagr_col: str = "nav_tr_cagr_5y",
          nav_cagr_3y: str = "nav_tr_cagr_3y",
          discount_col: str = "discount") -> pd.DataFrame:
    """Forward IRR per fund.

    live: the nta_live table (price, nta_est, discount_est, sector).
    panel_hist: monthly panel supplying 5y NAV CAGR, trailing discount
    history and trailing distribution yield.
    """
    p = params["forward_irr"]
    ng = p["nav_growth"]
    yrs = int(p["narrowing_years"])

    # --- growth input, per fund, with sector median for the shrink target
    last = (panel_hist.sort_values("obs_month").groupby("security_id").last()
            .reset_index())
    g_raw = last[nav_cagr_col] if nav_cagr_col in last.columns else pd.Series(dtype=float)
    if nav_cagr_3y in last.columns:
        g_raw = g_raw.fillna(last[nav_cagr_3y])
    last = last.assign(g_raw=g_raw)
    sector_med = last.groupby("sector")["g_raw"].median()
    last["g_sector"] = last["sector"].map(sector_med)
    # shrink toward sector median, then cap
    shrink = float(ng["shrink_to_sector"])
    g = last["g_raw"].fillna(last["g_sector"])
    g = (1 - shrink) * g + shrink * last["g_sector"].fillna(g)
    last["g"] = g.clip(float(ng["cap_low"]), float(ng["cap_high"]))

    # --- own trailing 5y median discount + trailing 12m distribution yield
    hist = panel_hist.copy()
    hist["obs_month"] = hist["obs_month"].astype(str)
    recent = hist.sort_values("obs_month").groupby("security_id").tail(60)
    dmed = recent.groupby("security_id")[discount_col].median().rename("d_term_own")
    dist = None
    for c in ("dividend_yield", "trailing_yield", "distribution_yield"):
        if c in hist.columns:
            dist = (hist.sort_values("obs_month").groupby("security_id")[c]
                    .last().rename("dist_yield"))
            break

    base = last[["security_id", "sector", "g", "g_raw", "g_sector"]].merge(
        dmed, on="security_id", how="left")
    if dist is not None:
        base = base.merge(dist, on="security_id", how="left")
    else:
        base["dist_yield"] = np.nan

    df = live.merge(base, on="security_id", how="left", suffixes=("", "_h"))
    rows = []
    for r in df.itertuples(index=False):
        d_now = getattr(r, "discount_est", np.nan)
        d_own = getattr(r, "d_term_own", np.nan)
        g_i = getattr(r, "g", np.nan)
        dy = getattr(r, "dist_yield", np.nan)
        dy = 0.0 if pd.isna(dy) else float(dy) / (100.0 if dy > 1 else 1.0)
        rec = {"security_id": r.security_id,
               "irr_central": None, "irr_own_median": None,
               "irr_wider_5pp": None, "irr_zero_discount": None,
               "g_used": None if pd.isna(g_i) else round(float(g_i), 4),
               "terminal_discount_own": None if pd.isna(d_own) else round(float(d_own), 4),
               "dist_yield_used": round(dy, 4)}
        # no growth input or no current discount -> no IRR, never a default
        if not (pd.isna(g_i) or pd.isna(d_now) or pd.isna(d_own)):
            price = getattr(r, "price", np.nan)
            nav0 = getattr(r, "nta_est", np.nan)
            for key, dt in (("irr_own_median", float(d_own)),
                            ("irr_wider_5pp", float(d_own) - 0.05),
                            ("irr_zero_discount", 0.0)):
                v = _irr(price, nav0, float(g_i), float(d_now), dt, dy, yrs)
                rec[key] = None if v is None else round(v, 4)
            rec["irr_central"] = rec["irr_own_median"]
        rows.append(rec)
    return pd.DataFrame(rows)


def build_by_market(live: pd.DataFrame, panels: dict[str, pd.DataFrame],
                    params: dict) -> pd.DataFrame:
    """Forward IRR per fund, each market against ITS OWN panel.

    panels: {market: monthly panel}. A market with no panel gets no IRR
    rows - absence, never another market's numbers.
    """
    out = []
    for mkt, panel in (panels or {}).items():
        if panel is None or not len(panel):
            continue
        sub = live[live["market"] == mkt] if "market" in live.columns else live
        if not len(sub):
            continue
        c5, c3 = CAGR_COLS.get(mkt, ("nav_tr_cagr_5y", "nav_tr_cagr_3y"))
        if "discount" not in panel.columns:
            continue
        out.append(build(sub, panel, params, nav_cagr_col=c5, nav_cagr_3y=c3))
    if not out:
        return pd.DataFrame(columns=["security_id", "irr_central"])
    return pd.concat(out, ignore_index=True)
