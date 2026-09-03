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
from . import units as U


def _busdays_since(anchor: pd.Timestamp, today: date) -> int:
    return int(np.busday_count(anchor.date(), today))


# A NAV that jumps implausibly from the fund's own previous NAV is far more
# likely to be a bad parse than a real event, and it produces the most
# attractive-looking opportunity in the table. Argo Global Listed
# Infrastructure anchored at $5.00 against a $2.55 price - a -49% discount
# and z = -10.2, the top signal in the whole system - while its real NTA
# series runs $2.31 to $2.75 and our own extractor had read June 2026 as
# $2.75 exactly. The unit check could not catch it: $5.00 against $2.55 is
# 1.8x, not the 100x a cents/dollars error produces.
#
# The threshold is deliberately loose. Measured over consecutive
# publications the median NAV change is 0.54% and the 90th percentile 1.8%,
# so 35% is far outside normal while still leaving room for a real event.
# A fund CAN legitimately move that far - a capital return, a wind-up
# distribution, a crash - so this quarantines the row from ALERTING and
# records why; it never deletes the observation or "corrects" the number.
NAV_JUMP_ALERT_LIMIT = 0.35
# A continuity check compares CONSECUTIVE observations. Comparing a 2026
# anchor against a 2008 panel print is not continuity, it is noise: NAVs
# legitimately move far more than 35% over eighteen years, and the first run
# quarantined Zero Preference Growth, M&G Equity and US Special
# Opportunities on comparators from 2008-2010. The panel lags by a month or
# two in normal operation, so 400 days is generous while still excluding a
# comparator too old to mean anything.
NAV_PRIOR_MAX_AGE_DAYS = 400


def nav_continuity(anchor_val, anchor_date, prior) -> dict:
    """Is this anchor plausible against the fund's own previous NAV?

    `prior` is (date, value) pairs in the SAME unit as the anchor - mixing
    units here would manufacture exactly the false alarm the check exists to
    prevent. Returns ok=True when there is nothing to compare against:
    absence of history is not evidence of a bad parse.
    """
    if anchor_val is None or not pd.notna(anchor_val) or anchor_val <= 0:
        return {"ok": True, "reason": "", "prev": None, "jump": None}
    usable = [(d, v) for d, v in prior
              if d is not None and v is not None and pd.notna(v) and v > 0
              and (anchor_date is None
                   or pd.Timestamp(d) < pd.Timestamp(anchor_date))
              and (anchor_date is None
                   or (pd.Timestamp(anchor_date) - pd.Timestamp(d)).days
                   <= NAV_PRIOR_MAX_AGE_DAYS)]
    if not usable:
        return {"ok": True, "reason": "no_recent_prior_nav",
                "prev": None, "jump": None}
    _d, prev = max(usable, key=lambda t: pd.Timestamp(t[0]))
    jump = abs(float(anchor_val) - float(prev)) / float(prev)
    if jump > NAV_JUMP_ALERT_LIMIT:
        # Distinguish the two causes, because they need different fixes. A
        # ratio near 100 (or 1/100) is a pence-vs-pounds mismatch, not a
        # NAV that moved: the panel is not reliably canonical either -
        # Lindsell Train's print of 7.09 is pounds against a 698.83 pence
        # anchor. Both are quarantined, since a number we cannot trust must
        # not alert, but calling a unit bug a "jump" would send anyone
        # looking at it in the wrong direction.
        ratio = float(anchor_val) / float(prev)
        unit_like = (70 <= ratio <= 130) or (1 / 130 <= ratio <= 1 / 70)
        return {"ok": False,
                "reason": (f"nav_unit_mismatch_{ratio:.0f}x_vs_prior" if unit_like
                           else f"nav_jump_{jump:.0%}_vs_prior"),
                "prev": float(prev), "jump": float(jump)}
    return {"ok": True, "reason": "", "prev": float(prev), "jump": float(jump)}


# NAV currencies that can be converted into a market's canonical unit
# through the FX levels (prices.fx_levels). Anything else stays excluded
# upstream rather than guessed at here.
FX_CONVERTIBLE = ("USD", "EUR", "CAD")


def _fx_convert(market: str, value, unit: str | None, when,
                price_ccy: str | None, fx_levels: dict | None):
    """A NAV stated in a foreign currency, in the unit the price is in.

    Returns (value, unit_label, note, fx_rate, fx_pair). Three cases:
      - the price is quoted in that same currency (a USD line): keep the
        NAV in it - price and NAV already agree, no conversion;
      - the market has an FX level for it: convert into the canonical
        unit at the level on the NAV's own date (GBP = value / rate, then
        pence);
      - otherwise: refuse - return None so the anchor is not used, because
        a dollar NAV over a pence price is an exchange rate wearing a
        discount.
    Sterling/canonical units pass straight through to units.normalise.
    """
    u = str(unit or "").strip().upper()
    if u not in FX_CONVERTIBLE:
        val, lbl, note = U.normalise(market, value, unit)
        return val, lbl, note, None, None
    pc = str(price_ccy or "").strip().upper()
    if pc == u:
        return float(value), u, "nav_and_price_same_foreign_ccy", None, None
    ser = (fx_levels or {}).get(u)
    if ser is None or not len(ser):
        return None, u, f"no_fx_level_for_{u}", None, None
    ts = pd.Timestamp(when)
    hist = ser[ser.index <= ts.normalize()]
    if not len(hist):
        return None, u, f"no_fx_level_before_{ts.date()}", None, None
    rate = float(hist.iloc[-1])            # units of foreign ccy per 1 GBP
    if not rate or rate <= 0:
        return None, u, "fx_level_not_positive", None, None
    canon = U.CANONICAL_UNIT.get(market, "")
    per_gbp = float(value) / rate
    val = per_gbp * 100.0 if canon == "GBX" else per_gbp
    return val, canon, f"fx_{u}_to_{canon}@{rate:.4f}", rate, f"GBP{u}=X"


def build_table(panel: pd.DataFrame, market: str, ret_col: str, nav_col: str,
                price_col: str, params: dict,
                tier0: pd.DataFrame | None = None,
                daily_factors: pd.DataFrame | None = None,
                market_factors: pd.DataFrame | None = None,
                live_prices: pd.DataFrame | None = None,
                registry: pd.DataFrame | None = None,
                own_nav_history: pd.DataFrame | None = None,
                aux_discount_history: pd.DataFrame | None = None,
                fx_levels: dict | None = None,
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

    # Fallback discount history (monthly, security_id/obs_month/discount)
    # for funds the aggregator panel never priced - the UK daily panel
    # resampled to the same monthly spec the z was validated on. The panel
    # stays primary; this only ever ADDS history where the panel has too
    # little to z-score.
    aux_by_sid: dict[str, pd.DataFrame] = {}
    if aux_discount_history is not None and len(aux_discount_history):
        a = aux_discount_history.dropna(subset=["discount"])
        aux_by_sid = {str(s_): g_.sort_values("obs_month")
                      for s_, g_ in a.groupby("security_id")}

    # Per-fund publication cadence, measured from the fund's own NAV
    # history. A quarterly publisher whose NAV is 60 days old is CURRENT by
    # its own cadence; judging it on a daily publisher's 45-day cap wrote
    # off the whole infrastructure/property cohort as stale.
    # The cadence is the fund's CURRENT one: the last nine publications
    # (eight gaps), not the last twenty-four. Schroders Capital Global
    # Innovation and Syncona published daily for years and quarterly since,
    # and a 24-observation window still saw mostly daily gaps - a 45-day
    # limit for a quarterly publisher, so their fresh quarterly NAVs read
    # as stale.
    cadence_days: dict[str, float] = {}
    if own is not None and len(own):
        for s_, g_ in own.groupby("security_id"):
            d_ = g_["nav_date"].sort_values().drop_duplicates().tail(9)
            if len(d_) >= 5:
                gaps = d_.diff().dt.days.dropna()
                gaps = gaps[gaps > 0]
                if len(gaps) >= 4:
                    cadence_days[str(s_)] = float(gaps.median())

    reg_meta = {}
    if registry is not None and len(registry):
        cols = [c for c in ("name", "sector", "currency", "is_vct",
                            "research_eligible", "status", "isin",
                            "ticker", "identity_status", "identity_ok",
                            "identity_incumbent_id")
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
        # every NAV source states the unit it is in; the canonical one for
        # the market is the default, and units.normalise does any conversion
        # explicitly rather than by convention
        nav_unit = U.CANONICAL_UNIT.get(market, "")
        last = g.iloc[-1] if len(g) else None
        # the price's currency is needed BEFORE the anchor is chosen: a NAV
        # stated in dollars is converted only if the price is not also in
        # dollars (see _fx_convert)
        _pc = None
        if live_prices is not None and len(live_prices) and "price_ccy" in live_prices.columns:
            _lp = live_prices[live_prices["security_id"] == sid]
            if len(_lp) and pd.notna(_lp["price_ccy"].iloc[0]):
                _pc = str(_lp["price_ccy"].iloc[0])
                if _pc.upper() in ("GBP", "GBX", "STG"):   # Yahoo's GBp = pence
                    _pc = "GBX"
        fx_rate = fx_pair = nav_unit_original = None

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
                    val, unit, _note, _r, _p = _fx_convert(
                        market, o["nav_value"], o.get("nav_unit"),
                        o["nav_date"], _pc, fx_levels)
                    if val is not None:
                        anchor_val = float(val)
                        nav_unit = unit
                        anchor_date = pd.Timestamp(o["nav_date"])
                        anchor_source = f"own_nav_history:{o['nav_date'].date()}"
                        fx_rate, fx_pair = _r, _p
                        nav_unit_original = (str(o.get("nav_unit")).upper()
                                             if _r is not None else None)

        # 1. a NAV the fund itself published wins outright
        if tier0 is not None and len(tier0):
            t0 = tier0[tier0["security_id"] == sid]
            if len(t0):
                t0 = t0.sort_values("nav_date").iloc[-1]
                if anchor_date is None or pd.Timestamp(t0["nav_date"]) >= anchor_date:
                    # harvest_au already reduces cents to dollars and records
                    # `unit`; harvest_uk returns pence. Either way the unit is
                    # stated, converted here if needed, and carried on the row.
                    val, unit, _note, _r, _p = _fx_convert(
                        market, t0["nav_value"],
                        t0["unit"] if "unit" in t0.index else None,
                        t0["nav_date"], _pc, fx_levels)
                    if val is not None:
                        anchor_val = float(val)
                        nav_unit = unit
                        anchor_date = pd.Timestamp(t0["nav_date"])
                        anchor_source = str(t0["source"])
                        basis = 0
                        fx_rate, fx_pair = _r, _p
                        nav_unit_original = (str(t0["unit"]).upper()
                                             if _r is not None else None)

        # A fund with NO NAV from any source used to be dropped here. That
        # hid it twice over: it left the table with no row, so its FETCHED
        # PRICE was thrown away too, and "we hold no NAV for this fund"
        # became indistinguishable from "this fund does not exist". The row
        # is now kept with the NAV fields empty - absence recorded, never
        # filled - which is what makes the coverage denominator honest.
        has_nav = anchor_val is not None and anchor_date is not None

        staleness = (max(0, _busdays_since(anchor_date, today)) if has_nav
                     else np.nan)
        # freshness is judged against the fund's OWN publication cadence:
        # the flat cap remains the floor, 3x the fund's median gap the
        # limit, bounded so nothing is called fresh past the cadence cap
        base_limit = float(live["staleness"]["max_days_for_alerting"])
        stale_limit = base_limit
        if sid in cadence_days:
            stale_limit = float(np.clip(
                3.0 * cadence_days[sid], base_limit,
                float(live["staleness"].get("max_days_cadence_cap", 200))))
        m = models.loc[sid] if sid in models.index else None
        has_model = m is not None and m["betas"] is not None

        nta_est = anchor_val
        est_note = "anchor_carry" if has_nav else "no_nav_from_any_source"
        if basis is None and has_nav:
            basis = 1 if has_model else 3
        if has_nav and basis == 1 and daily_factors is not None and has_model:
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
        est_error = sigma * np.sqrt(
            max(staleness, 1) / live["est_error"]["trading_days_per_month"]) \
            if pd.notna(sigma) and has_nav else np.nan

        # price: live feed when available (probe-verified adapter), else
        # the last panel price - the source is always recorded.
        #
        # The panel price is kept in its OWN column even when the live feed
        # supplies one, so "we fell back to a month-old aggregator print"
        # is a fact a reader can see rather than infer from a string, and so
        # a live quote can be sanity-checked against the fund's own history.
        panel_price = float(last[price_col]) if last is not None \
            and pd.notna(last[price_col]) else np.nan
        panel_month = str(last["obs_month"]) if last is not None \
            and pd.notna(panel_price) else None
        price = panel_price
        price_ccy = None
        price_date = panel_month
        price_source = "aggregator_panel" if pd.notna(price) else "none"
        price_is_fallback = bool(pd.notna(price))
        price_asof = "aggregator_panel" if pd.notna(price) else "none"
        if live_prices is not None and len(live_prices):
            lp = live_prices[live_prices["security_id"] == sid]
            if len(lp):
                price = float(lp["price"].iloc[0])
                price_source = str(lp["price_source"].iloc[0])
                price_date = str(lp["price_date"].iloc[0])
                price_ccy = (str(lp["price_ccy"].iloc[0])
                             if "price_ccy" in lp.columns
                             and pd.notna(lp["price_ccy"].iloc[0]) else None)
                price_is_fallback = False
                price_asof = f"{price_source}@{price_date}"
        discount_est = (price / nta_est - 1.0
                        if pd.notna(price) and has_nav and nta_est else np.nan)

        # own-history z on published discounts (validated spec)
        hist = g.tail(zp["window_months"])
        dcol = "discount" if "discount" in g.columns else None
        if dcol is None and {"share_price", "nta_derived"} <= set(g.columns):
            hist = hist.assign(discount=hist["share_price"] / hist["nta_derived"] - 1.0)
            dcol = "discount"
        # z, and - separately - WHY there is no z. "We could not compute
        # one" and "we computed one and it was not significant" are
        # different facts about a fund, and reporting both as a blank
        # z_adj sent 42 perfectly well covered funds to the coverage
        # audit's failure list with no way to tell them apart.
        z_adj, z_raw, mu, sd = np.nan, np.nan, np.nan, np.nan
        z_status = "no_discount_history"
        z_within_error = False
        z_source = None
        z_window = 0
        z_floor = int(zp.get("min_months_floor", zp["min_months"]))
        h = hist[dcol].dropna() if dcol is not None else pd.Series(dtype=float)
        if len(h):
            z_source = "aggregator_panel"
        # the panel stays primary; the daily-panel resample only ADDS
        # history where the panel's series is the shallower of the two
        if len(h) < zp["min_months"] and sid in aux_by_sid:
            ah = (aux_by_sid[sid]["discount"].tail(zp["window_months"])
                  .dropna())
            if len(ah) > len(h):
                # adopt the deeper series even below the floor: no z is
                # computed there, but "insufficient_history_4m" names how
                # close the fund is, where "no_discount_history" hides it
                h, z_source = ah, "own_daily_panel"
        if len(h) or z_source is not None:
            z_window = int(len(h))
            if len(h) < z_floor:
                z_status = f"insufficient_history_{len(h)}m"
            elif not h.std(ddof=1) > 0:
                z_status = "zero_variance_history"
            else:
                mu, sd = float(h.mean()), float(h.std(ddof=1))
                if not pd.notna(discount_est):
                    z_status = "no_current_discount"
                else:
                    z_raw = (discount_est - mu) / sd
                    z_adj = z_raw
                    # error-sanity gate: an anomaly smaller than the
                    # estimate's own error band must not ALERT - but the z
                    # itself is a real, computed number the universe pricing
                    # needs. It used to be voided to NaN here, which made 76
                    # perfectly priced funds indistinguishable from funds
                    # with no history at all. The z stays; the flag travels
                    # with it, and every alerting consumer (alert_eligible
                    # below, opportunities gate 1) refuses a within-error z.
                    if pd.notna(est_error) and abs(discount_est - mu) <= \
                            zp["error_sanity_k"] * est_error:
                        z_within_error = True
                        z_status = "within_error_band"
                    elif z_window < zp["min_months"]:
                        # a GROWING z: short of the depth the alert evidence
                        # was built on, real enough to price with, and it
                        # matures into the full spec as live grabs accumulate
                        # (owner instruction 2026-09-02, CHANGELOG.md)
                        z_status = f"computed_growing_{z_window}m"
                    else:
                        z_status = "computed"

        # One data-quality verdict, computed once, on the values this row
        # actually carries. It gates the alert BEFORE any z/catalyst/IRR
        # logic runs, because a 100x unit error produces a spectacular
        # z-score and nothing downstream can tell it from a real one:
        # Lindsell Train came through at z=-19.3, Benjamin Hornigold at
        # -6.5, Thorney Tech at -6.0, all three from a price and a NAV in
        # different units, all three alert_eligible.
        diag = U.scale_diagnosis(price, nta_est if has_nav else None)
        # Prior NAVs for THIS fund, normalised into the anchor's unit, so the
        # continuity check compares like with like. Only our own extracted
        # sources are used: the aggregator panel is monthly and lags, so a
        # month-end print is not the right predecessor for a fresh
        # announcement, and mixing the two would fire on ordinary staleness.
        # The comparator is the AGGREGATOR PANEL only, and deliberately so.
        # Our own sources do not agree on units: normalise() passes an
        # unstated unit through untouched while labelling it canonical, so a
        # UK NAV held in pounds and one held in pence both come back tagged
        # GBX. Comparing across them fired on 32 funds - RIT at 3106 against
        # 39, HICL at 158.20 against 1.20 - every one a unit artefact rather
        # than a bad parse, while ALI, the fund this check exists for, had no
        # own-source predecessor at all and sailed through.
        #
        # The panel is monthly and lags, but it is in the market's canonical
        # unit by construction and it is INDEPENDENT of our parser, which is
        # what makes it able to contradict it. A two-month-old NAV is a
        # perfectly good comparator against a 35% threshold: NAVs do not
        # ordinarily move that far in two months.
        _prior = []
        if len(g):
            for _, _r in g.iterrows():
                _v = _r.get(nav_col)
                if pd.notna(_v):
                    try:
                        _prior.append((pd.Period(_r["obs_month"], freq="M")
                                       .to_timestamp(how="end"), float(_v)))
                    except Exception:  # noqa: BLE001
                        continue
        # an anchor carrying a non-canonical unit is not comparable to the
        # panel; skip rather than manufacture a jump
        cont = (nav_continuity(anchor_val if has_nav else None, anchor_date, _prior)
                if nav_unit == U.CANONICAL_UNIT.get(market, "")
                else {"ok": True, "reason": "unit_not_canonical",
                      "prev": None, "jump": None})
        nav_positive = bool(has_nav and pd.notna(anchor_val) and anchor_val > 0)
        # A MONTH-END AGGREGATOR PRINT IS NOT A CURRENT MARKET PRICE, and a
        # discount computed against one is not a current discount. European
        # Opportunities Trust came through the first gated run on a July
        # panel price with z = +2.15 and alert_eligible True: every other
        # check passed, because every other check was about the NUMBER
        # rather than about what the number is. This is the same distinction
        # the audit's usable_price already makes, now made once, here, where
        # the alert is decided.
        dq_ok = bool(nav_positive
                     and pd.notna(price) and price > 0
                     and not price_is_fallback
                     and pd.notna(discount_est)
                     and diag["unit_check_status"] in ("ok", "extreme")
                     and cont["ok"])
        dq_reason = "" if dq_ok else (
            "nav_not_positive" if not nav_positive else
            "no_price" if not (pd.notna(price) and price > 0) else
            "stale_panel_price_only" if price_is_fallback else
            "no_discount" if not pd.notna(discount_est) else
            cont["reason"] if not cont["ok"] else
            f"unit_{diag['unit_check_status']}")
        identity_ok = bool(meta.get("identity_ok", True))

        research_ok = bool(
            meta["research_eligible"] if "research_eligible" in meta
            and pd.notna(meta.get("research_eligible"))
            else (last.get("eligible", True) if last is not None else True))

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
            # Research eligibility comes from the REGISTRY's vehicle flags
            # when they are supplied (eligibility.classify writes them), and
            # from the panel otherwise. Defaulting a registry-only fund to
            # True is what let VCTs and ZDP lines - which the research
            # universe excludes by pre-specified policy - become
            # alert_eligible: 15 rows, 4 of them VCTs, on the last run.
            "research_eligible": research_ok,
            "nav_anchor": anchor_val if has_nav else np.nan,
            "anchor_date": anchor_date.date().isoformat() if has_nav else None,
            "anchor_source": anchor_source,
            "nta_est": round(nta_est, 6) if has_nav else np.nan,
            "est_note": est_note,
            "basis": basis, "staleness_days": staleness,
            "has_nav": has_nav,
            "sigma_1m": round(sigma, 6) if pd.notna(sigma) else np.nan,
            "sigma_source": m["sigma_source"] if m is not None else None,
            "est_error": round(est_error, 6) if pd.notna(est_error) else np.nan,
            "price": price, "price_asof": price_asof,
            "price_source": price_source, "price_date": price_date,
            "price_ccy": price_ccy,
            "price_is_fallback": price_is_fallback,
            "price_panel": panel_price, "price_panel_month": panel_month,
            "nav_unit": nav_unit,
            "nav_unit_original": nav_unit_original,
            "fx_rate": fx_rate, "fx_pair": fx_pair,
            "discount_est": round(discount_est, 6) if pd.notna(discount_est) else np.nan,
            "disc_mu_36m": round(mu, 6) if pd.notna(mu) else np.nan,
            "disc_sigma_36m": round(sd, 6) if pd.notna(sd) else np.nan,
            "z_adj": round(z_adj, 4) if pd.notna(z_adj) else np.nan,
            # A vehicle the research universe excludes may be MONITORED but
            # must never be alerted on: the discount-z evidence was
            # established on a population that excluded VCTs, split-capital
            # classes and non-sterling lines, so a signal on one of those is
            # a signal with no research behind it.
            "unit_check_status": diag["unit_check_status"],
            "nav_prev": cont["prev"], "nav_jump": cont["jump"],
            "nav_continuity_ok": cont["ok"],
            "price_nav_ratio": diag["price_nav_ratio"],
            "suspected_scale_factor": diag["suspected_scale_factor"],
            "data_quality_ok": dq_ok, "data_quality_reason": dq_reason,
            "identity_status": meta.get("identity_status"),
            "identity_ok": identity_ok,
            "z_raw": round(z_raw, 4) if pd.notna(z_raw) else np.nan,
            "z_status": z_status,
            "z_within_error": z_within_error,
            "z_source": z_source,
            "z_window_months": z_window,
            "staleness_limit_days": stale_limit,
            # basis states PROVENANCE (0 = the fund's own announcement,
            # 1 = factor roll-forward, 3 = carried anchor); nav_current
            # states FRESHNESS by the fund's own cadence. They are
            # different facts: a modelless quarterly publisher's 22-day-old
            # NAV is basis 3 AND current - EJF failed every gate keyed on
            # `basis <= 2` while holding a perfectly fresh published NAV.
            "nav_current": bool(has_nav and pd.notna(staleness)
                                and staleness <= stale_limit),
            # Every clause here is a REASON A FUND MUST NOT ALERT, and the
            # data-quality and identity clauses come first by intent: a
            # reused ticker or a unit error must be stopped before any
            # signal logic gets to look at the number.
            "alert_eligible": bool(
                dq_ok and identity_ok and research_ok
                and pd.notna(z_adj) and not z_within_error
                # a growing z prices the fund; only a full-depth z - the
                # depth the alert evidence was validated on - may alert
                and z_window >= int(zp["min_months"])
                and pd.notna(staleness) and staleness <= stale_limit
                and pd.notna(basis)),
            "model_factors": m["factors"] if has_model else None,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return pd.DataFrame(rows)
