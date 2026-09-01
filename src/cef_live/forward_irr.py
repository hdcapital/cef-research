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


def own_history_cagr(own_hist: pd.DataFrame | None,
                     years: int = 5, min_changes: int = 18,
                     max_abs_monthly: float = 0.18) -> pd.Series:
    """Robust per-fund NAV-per-share growth from the fund's own NAV history.

    Median monthly log-change over the trailing window, annualised.
    Monthly changes beyond `max_abs_monthly` (splits, unit slips,
    mis-parses) are EXCLUDED rather than corrected: a median over the
    ordinary months is robust to them, while a first/last-point CAGR is
    poisoned by a single 10-for-1 subdivision. NAV-per-share growth
    excludes distributions by construction, which is consistent with the
    IRR adding distributions separately.
    """
    if own_hist is None or not len(own_hist):
        return pd.Series(dtype=float, name="g_own")
    h = own_hist.dropna(subset=["nav_value"]).copy()
    h["nav_date"] = pd.to_datetime(h["nav_date"], errors="coerce")
    h = h.dropna(subset=["nav_date"])
    h = h[h["nav_value"] > 0]
    cutoff = h["nav_date"].max() - pd.DateOffset(years=years)
    h = h[h["nav_date"] >= cutoff]
    h["m"] = h["nav_date"].dt.to_period("M")
    monthly = (h.sort_values("nav_date")
               .groupby(["security_id", "m"])["nav_value"].last())
    out = {}
    for sid, s in monthly.groupby(level=0):
        lg = np.log(s.droplevel(0).astype(float)).diff().dropna()
        lg = lg[lg.abs() < max_abs_monthly]
        if len(lg) >= min_changes:
            out[sid] = float(np.expm1(lg.median() * 12.0))
    return pd.Series(out, name="g_own", dtype=float)


def build(live: pd.DataFrame, panel_hist: pd.DataFrame, params: dict,
          nav_cagr_col: str = "nav_tr_cagr_5y",
          nav_cagr_3y: str = "nav_tr_cagr_3y",
          discount_col: str = "discount",
          own_hist: pd.DataFrame | None = None,
          aux_discount_hist: pd.DataFrame | None = None) -> pd.DataFrame:
    """Forward IRR per fund.

    live: the nta_live table (price, nta_est, discount_est, sector).
    panel_hist: monthly panel supplying 5y NAV CAGR, trailing discount
    history and trailing distribution yield.
    own_hist: the fund's own extracted NAV history (security_id, nav_date,
    nav_value) - the growth fallback for funds the panel never priced.
    aux_discount_hist: monthly discount history (security_id, obs_month,
    discount) from the daily panel - the terminal-discount fallback for
    the same cohort.

    Growth source chain, recorded per fund in `g_source`: panel TR CAGR ->
    own NAV-per-share history -> sector median -> market median. Every
    fallback still passes through the same shrink and caps.
    """
    p = params["forward_irr"]
    ng = p["nav_growth"]
    yrs = int(p["narrowing_years"])

    # --- growth input, per fund, with sector median for the shrink target
    last = (panel_hist.sort_values("obs_month").groupby("security_id").last()
            .reset_index()) if len(panel_hist) else pd.DataFrame(
        columns=["security_id", "sector"])
    g_raw = last[nav_cagr_col] if nav_cagr_col in last.columns \
        else pd.Series(np.nan, index=last.index)
    if nav_cagr_3y in last.columns:
        g_raw = g_raw.fillna(last[nav_cagr_3y])
    last = last.assign(g_raw=g_raw)
    sector_med = last.groupby("sector")["g_raw"].median() if len(last) \
        else pd.Series(dtype=float)
    market_med = float(last["g_raw"].median()) if last["g_raw"].notna().any() \
        else np.nan
    g_own = own_history_cagr(own_hist)

    # --- own trailing 5y median discount + trailing 12m distribution yield
    hist = panel_hist.copy()
    if len(hist):
        hist["obs_month"] = hist["obs_month"].astype(str)
        recent = hist.sort_values("obs_month").groupby("security_id").tail(60)
        dmed = recent.groupby("security_id")[discount_col].median().rename("d_term_own")
    else:
        dmed = pd.Series(dtype=float, name="d_term_own",
                         index=pd.Index([], name="security_id"))
    aux_dmed = pd.Series(dtype=float, name="d_term_aux",
                         index=pd.Index([], name="security_id"))
    if aux_discount_hist is not None and len(aux_discount_hist):
        aux_dmed = (aux_discount_hist.sort_values("obs_month")
                    .groupby("security_id").tail(60)
                    .groupby("security_id")["discount"].median()
                    .rename("d_term_aux"))
    dist = None
    for c in ("dividend_yield", "trailing_yield", "distribution_yield"):
        if c in hist.columns:
            dist = (hist.sort_values("obs_month").groupby("security_id")[c]
                    .last().rename("dist_yield"))
            break

    base = last[["security_id", "sector", "g_raw"]].merge(
        dmed, on="security_id", how="left")
    if dist is not None:
        base = base.merge(dist, on="security_id", how="left")
    else:
        base["dist_yield"] = np.nan

    df = live.merge(base, on="security_id", how="left", suffixes=("", "_h"))
    df = df.merge(aux_dmed, on="security_id", how="left")
    shrink = float(ng["shrink_to_sector"])
    rows = []
    for r in df.itertuples(index=False):
        d_now = getattr(r, "discount_est", np.nan)
        d_own = getattr(r, "d_term_own", np.nan)
        if pd.isna(d_own):
            d_own = getattr(r, "d_term_aux", np.nan)
        # growth chain: panel CAGR -> own NAV history -> sector -> market.
        # The sector comes from the live row too, so a fund the panel never
        # covered still shrinks toward ITS OWN sector's median.
        sid = r.security_id
        sec = getattr(r, "sector", None)
        g_sec = sector_med.get(sec, np.nan) if sec is not None else np.nan
        g_i, g_source = getattr(r, "g_raw", np.nan), "panel_tr_cagr"
        if pd.isna(g_i) and sid in g_own.index:
            g_i, g_source = float(g_own[sid]), "own_nav_history"
        if pd.isna(g_i):
            g_i, g_source = g_sec, "sector_median"
        if pd.isna(g_i):
            g_i, g_source = market_med, "market_median"
        if pd.notna(g_i):
            g_i = (1 - shrink) * g_i + shrink * (g_sec if pd.notna(g_sec)
                                                 else g_i)
            g_i = float(np.clip(g_i, float(ng["cap_low"]),
                                float(ng["cap_high"])))
        else:
            g_source = None
        dy = getattr(r, "dist_yield", np.nan)
        dy = 0.0 if pd.isna(dy) else float(dy) / (100.0 if dy > 1 else 1.0)
        rec = {"security_id": r.security_id,
               "irr_central": None, "irr_own_median": None,
               "irr_wider_5pp": None, "irr_zero_discount": None,
               "g_used": None if pd.isna(g_i) else round(float(g_i), 4),
               "g_source": g_source,
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
                    params: dict,
                    own_hist: dict[str, pd.DataFrame] | None = None,
                    aux_discount_hist: dict[str, pd.DataFrame] | None = None
                    ) -> pd.DataFrame:
    """Forward IRR per fund, each market against ITS OWN panel.

    panels: {market: monthly panel}. A market with no panel gets no IRR
    rows - absence, never another market's numbers. own_hist and
    aux_discount_hist are per-market too, for the same reason.
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
        out.append(build(sub, panel, params, nav_cagr_col=c5, nav_cagr_3y=c3,
                         own_hist=(own_hist or {}).get(mkt),
                         aux_discount_hist=(aux_discount_hist or {}).get(mkt)))
    if not out:
        return pd.DataFrame(columns=["security_id", "irr_central"])
    return pd.concat(out, ignore_index=True)
