"""The nightly live-NTA table: one row per fund, honestly labelled.

Tier resolution per fund (config/params.yaml, pre-specified):
  0 - issuer-published high-frequency NAV (daily/weekly announcements)
  1 - proxy roll-forward from the last published NAV via the fund's
      factor model (factors.py), with est_error growing in staleness
  2 - holdings-based (flagged funds with holdings.yaml; later phase)
  3 - stale: last published value carried with a staleness flag only -
      excluded from z alerting beyond the staleness cap

Published values and estimates are structurally distinct: ``nav_anchor``
(+ ``anchor_date``, ``anchor_source``) is always a real published number;
``nta_est`` is the model output; ``basis`` says which tier produced it.
Nothing is interpolated - a fund with no qualifying model stays Tier 3.

The z-score is the validated research spec (own 36m discount history,
min 24m) with the error-sanity gate: a fund only clears dislocation if
|discount_est - mu| > k * est_error.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import factors as F


def _busdays_since(anchor: pd.Timestamp, today: date) -> int:
    return int(np.busday_count(anchor.date(), today))


def build_table(panel: pd.DataFrame, market: str, ret_col: str, nav_col: str,
                price_col: str, params: dict,
                tier0: pd.DataFrame | None = None,
                daily_factors: pd.DataFrame | None = None,
                market_factors: pd.DataFrame | None = None,
                live_prices: pd.DataFrame | None = None,
                registry: pd.DataFrame | None = None,
                own_nav_history: pd.DataFrame | None = None,
                today: date | None = None) -> pd.DataFrame:
    """Build the live table for one market, keyed on the REGISTRY.

    The aggregator files (AIC MIR, ASX monthly reports) say who exists. They
    do not price this table. Iterating the research panel meant a fund the
    aggregator never priced had no row for a harvested NAV to attach to, so
    113 tradeable funds - the whole offshore and alternatives cohort, HICL,
    TRIG, INPP, Pershing Square, Syncona, and 24 VCTs - had their NAVs
    fetched every night and then dropped, because the table was keyed on an
    aggregator's past coverage rather than on the fund's existence.

    So the universe is the registry, price comes from the live feed, and the
    NAV anchor is sourced in this order:

      1. a NAV the fund itself published (Tier 0, from its announcements)
      2. our own extracted NAV history
      3. the aggregator panel - LAST, and labelled `aggregator_panel:` so
         the number of funds still depending on it is countable rather than
         assumed

    registry: live funds for this market (security_id required). When given,
      it defines the universe; the panel supplies history only.
    own_nav_history: security_id, nav_date, nav_value from our own
      extraction - the UK announcement archive and the ASX deterministic
      pass.

    tier0: optional DataFrame (security_id, nav_date, nav_value, source)
      of issuer-published high-frequency NAVs from the harvester.
    daily_factors: optional daily factor returns since each anchor (from
      the probe-verified price layer); until available, Tier 1 rolls
      forward with the factor model's expected drift set to zero over the
      unmodelled gap - i.e. the anchor carries, but with the model's
      est_error still growing in staleness (honest wide band), and basis
      stays 1 only when a fitted model exists.
    """
    today = today or datetime.now(timezone.utc).date()
    live = params["live_nta"]
    zp = live["z_adjustment"]

    # Factor models are fitted from panel history. A market whose panel is
    # empty or lacks the fitting columns must still produce a table - those
    # funds get basis 3 (no model) rather than the whole run raising.
    need = {"security_id", "obs_month", "sector", ret_col}
    if len(panel) and need <= set(panel.columns):
        models = F.fit_fund_models(panel, ret_col, params, market_factors)
        models = models.set_index("security_id")
    else:
        models = pd.DataFrame(columns=["betas", "factors", "sigma_1m"]).set_index(
            pd.Index([], name="security_id"))

    panel_by_sid = {sid: g[g[nav_col].notna()] for sid, g
                    in panel.sort_values("obs_month").groupby("security_id")} \
        if len(panel) else {}
    # the universe is the registry when one is supplied; the panel only ever
    # ADDS history for funds it happens to cover
    if registry is not None and len(registry):
        universe = list(dict.fromkeys(
            list(registry["security_id"].astype(str))
            + [s_ for s_ in panel_by_sid]))
    else:
        universe = list(panel_by_sid)

    own = own_nav_history
    if own is not None and len(own):
        own = own.copy()
        own["nav_date"] = pd.to_datetime(own["nav_date"], errors="coerce")
        own = own.dropna(subset=["nav_date", "nav_value"])

    reg_meta = {}
    if registry is not None and len(registry):
        cols = [c for c in ("name", "sector", "currency", "is_vct")
                if c in registry.columns]
        reg_meta = {str(r["security_id"]): {c: r[c] for c in cols}
                    for _, r in registry.iterrows()}

    rows = []
    for sid in universe:
        meta = reg_meta.get(sid, {})
        g = panel_by_sid.get(sid, panel.iloc[0:0])
        anchor_val = anchor_date = None
        anchor_source = None
        basis = None
        last = g.iloc[-1] if len(g) else None

        # 3. aggregator LAST, and named so its use is countable
        if last is not None and pd.notna(last[nav_col]):
            anchor_val = float(last[nav_col])
            anchor_date = pd.Period(last["obs_month"], freq="M").to_timestamp(how="end")
            anchor_source = f"aggregator_panel:{last['obs_month']}"

        # 2. our own extracted NAV history beats the aggregator
        if own is not None and len(own):
            o = own[own["security_id"] == sid]
            if len(o):
                o = o.sort_values("nav_date").iloc[-1]
                if anchor_date is None or pd.Timestamp(o["nav_date"]) > anchor_date:
                    anchor_val = float(o["nav_value"])
                    anchor_date = pd.Timestamp(o["nav_date"])
                    anchor_source = f"own_nav_history:{o['nav_date'].date()}"

        # 1. a NAV the fund itself published wins outright
        if tier0 is not None and len(tier0):
            t0 = tier0[tier0["security_id"] == sid]
            if len(t0):
                t0 = t0.sort_values("nav_date").iloc[-1]
                if anchor_date is None or pd.Timestamp(t0["nav_date"]) >= anchor_date:
                    anchor_val = float(t0["nav_value"])
                    anchor_date = pd.Timestamp(t0["nav_date"])
                    anchor_source = str(t0["source"])
                    basis = 0

        if anchor_val is None or anchor_date is None:
            continue          # no NAV from any source - absent, and visibly so

        staleness = max(0, _busdays_since(anchor_date, today))
        m = models.loc[sid] if sid in models.index else None
        has_model = m is not None and m["betas"] is not None

        nta_est = anchor_val
        est_note = "anchor_carry"
        if basis is None:
            basis = 1 if has_model else 3
        if basis == 1 and daily_factors is not None and has_model:
            fac_cols = m["factors"].split("|")
            betas = m["betas"]
            df = daily_factors[daily_factors.index > anchor_date]
            avail = [c for c in fac_cols if c in df.columns]
            if avail and len(df):
                X = df[avail].fillna(0.0).to_numpy()
                b = np.array(betas[1:len(avail) + 1])
                nta_est = anchor_val * float(np.prod(1.0 + X @ b))
                est_note = "factor_rollforward"

        sigma = float(m["sigma_1m"]) if m is not None and pd.notna(m["sigma_1m"]) else np.nan
        est_error = sigma * np.sqrt(max(staleness, 1) / live["est_error"]["trading_days_per_month"]) \
            if pd.notna(sigma) else np.nan

        # price: live feed when available (probe-verified adapter), else
        # the last panel price - the source is always recorded
        price = float(last[price_col]) if last is not None \
            and pd.notna(last[price_col]) else np.nan
        price_asof = f"aggregator_panel" if pd.notna(price) else "none"
        if live_prices is not None and len(live_prices):
            lp = live_prices[live_prices["security_id"] == sid]
            if len(lp):
                price = float(lp["price"].iloc[0])
                price_asof = f"{lp['price_source'].iloc[0]}@{lp['price_date'].iloc[0]}"
        discount_est = price / nta_est - 1.0 if pd.notna(price) and nta_est else np.nan

        # own-history z on published discounts (validated spec)
        hist = g.tail(zp["window_months"])
        dcol = "discount" if "discount" in g.columns else None
        if dcol is None and {"share_price", "nta_derived"} <= set(g.columns):
            hist = hist.assign(discount=hist["share_price"] / hist["nta_derived"] - 1.0)
            dcol = "discount"
        z_adj, mu, sd = np.nan, np.nan, np.nan
        if dcol is not None:
            h = hist[dcol].dropna()
            if len(h) >= zp["min_months"] and h.std(ddof=1) > 0:
                mu, sd = float(h.mean()), float(h.std(ddof=1))
                if pd.notna(discount_est):
                    z_adj = (discount_est - mu) / sd
                    # error-sanity gate: anomaly must exceed the estimate's
                    # own error band, else the z is voided (NaN, not 0 -
                    # absence of signal, not a neutral one)
                    if pd.notna(est_error) and abs(discount_est - mu) <= \
                            zp["error_sanity_k"] * est_error:
                        z_adj = np.nan

        # vehicle-type flags travel WITH the row rather than removing it:
        # the live universe is deliberately wider than the backtest's, which
        # excluded VCTs/split-caps/non-sterling for strategy reasons that do
        # not apply to monitoring. research_eligible preserves the backtest's
        # definition so the two populations stay distinguishable.
        rows.append({
            "security_id": sid, "market": market,
            # identity comes from the REGISTRY, falling back to the panel.
            # A registry-only fund has no panel row at all, so reading these
            # from `last` raised KeyError and the fund vanished again - the
            # same failure one layer down.
            "name": meta.get("name") or (last.get("company_name", "")
                                         if last is not None else ""),
            "sector": meta.get("sector") or (last.get("sector")
                                             if last is not None else None),
            "currency": meta.get("currency") or (last.get("currency")
                                                 if last is not None else None),
            "is_vct": bool(meta.get("is_vct", False)
                           or (last.get("is_vct", False)
                               if last is not None else False)),
            "non_sterling": bool(last.get("non_gbx_quote", False))
                             if last is not None else False,
            # a registry-only fund has no panel eligibility flag; it is
            # eligible until something says otherwise, which is the whole
            # point of moving the universe off the aggregator
            "research_eligible": bool(last.get("eligible", True))
                                 if last is not None else True,
            "nav_anchor": anchor_val, "anchor_date": anchor_date.date().isoformat(),
            "anchor_source": anchor_source,
            "nta_est": round(nta_est, 6), "est_note": est_note,
            "basis": basis, "staleness_days": staleness,
            "sigma_1m": round(sigma, 6) if pd.notna(sigma) else np.nan,
            "sigma_source": m["sigma_source"] if m is not None else None,
            "est_error": round(est_error, 6) if pd.notna(est_error) else np.nan,
            "price": price, "price_asof": price_asof,
            "discount_est": round(discount_est, 6) if pd.notna(discount_est) else np.nan,
            "disc_mu_36m": round(mu, 6) if pd.notna(mu) else np.nan,
            "disc_sigma_36m": round(sd, 6) if pd.notna(sd) else np.nan,
            "z_adj": round(z_adj, 4) if pd.notna(z_adj) else np.nan,
            "alert_eligible": bool(
                pd.notna(z_adj)
                and staleness <= live["staleness"]["max_days_for_alerting"]
                and basis <= 2),
            "model_factors": m["factors"] if has_model else None,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return pd.DataFrame(rows)
