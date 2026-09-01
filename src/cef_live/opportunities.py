"""Turn a catalyst into an idea - or explain why it isn't one.

Three gates, all pre-specified in config/params.yaml and code-checked
rather than judged:

  1 DISLOCATION  z_adj <= threshold (own 36m history only - the AU holdout
                 proved absolute discount level is an ANTI-signal), NAV
                 anchor no more than `max_staleness_days` old, basis <= 2.
                 A stale NAV cannot evidence a dislocation.
  2 CATALYST     an announcement in the fixed taxonomy within the window.
  3 RETURN       forward IRR >= `min_irr_central`, a fixed absolute hurdle
                 (owner instruction 2026-09-01: 15% - see
                 config/CHANGELOG.md), on a NAV fresh and sound enough to
                 support an IRR at all.

Verdict ladder: OPPORTUNITY = all three gates on fully sound data.
WATCH = two gates, or any single gate strong enough to act on alone -
a dislocation, a passing IRR, or a catalyst of weight
`standalone_catalyst_weight` or more (tender / scheme / continuation
vote / wind-down). A routine buyback or holder notice on its own is not
an idea; it still counts toward a two-gate WATCH and stays visible in
the universe workbook's Catalysts sheet. Every verdict records which
gates passed and the numbers behind them, so a rejected idea is
auditable and a later change of mind is visible.

The horizon this serves is months to years: positions built over days or
weeks. Nothing here is time-critical within a session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def universe_trailing_tr(hist: pd.DataFrame, years: int = 5) -> float | None:
    """Median annualised total return across the universe - context only.

    Gate 3 no longer uses this (the hurdle is a fixed absolute
    `min_irr_central` since 2026-09-01); it is kept for reporting so an
    email can state what the universe itself has been returning.

    ONE MARKET AT A TIME. Passing a combined frame would compare an
    Australian LIC against a UK number. The AU panel names its CAGR
    columns ``nta_tr_cagr_*`` and the UK one ``nav_tr_cagr_*``; both are
    recognised, because looking for only the UK spelling returned None for
    AU and silently disabled the old gate 3 there.
    """
    for col in ("nav_tr_cagr_5y", "nav_tr_cagr_3y",
                "nta_tr_cagr_5y", "nta_tr_cagr_3y"):
        if col in hist.columns:
            v = hist.sort_values("obs_month").groupby("security_id")[col].last()
            v = v.dropna()
            if len(v) >= 20:
                return float(v.median())
    return None


def evaluate(live: pd.DataFrame, cats: pd.DataFrame | None,
             irr: pd.DataFrame | None, params: dict,
             catalyst_window_days: int = 30) -> pd.DataFrame:
    """One row per fund with a qualifying dislocation, return or catalyst.

    The IRR hurdle is the fixed absolute `opportunity.min_irr_central` -
    the same number for every market, because it expresses the owner's
    required return, not a market's trailing performance.
    """
    op = params["opportunity"]
    z_thr = float(op["z_threshold"])
    max_stale = int(op["max_staleness_days"])
    max_basis = int(op["max_basis"])
    min_irr = float(op["min_irr_central"])
    solo_cat_w = float(op["standalone_catalyst_weight"])

    df = live.copy()

    # ---- GATES THAT RUN BEFORE ANY SIGNAL LOGIC ----
    # Each of these disqualifies a row on grounds that have nothing to do
    # with how attractive it looks, and each is applied FIRST for that
    # reason. A 100x unit error is not a dislocation, and it produces a
    # more extreme z-score than any real one: Lindsell Train reached
    # z = -19.3 on a price and a NAV in different units, Benjamin Hornigold
    # -6.5, Thorney Tech -6.0. Ranking by z would have put all three at the
    # top. Gating after scoring is gating too late.
    gates = [
        # the research that justifies acting on a discount z-score was run
        # on a population without VCTs, split-capital classes or
        # non-sterling lines
        ("research_eligible", "research-policy exclusion"),
        # a price and a NAV that cannot both be right
        ("data_quality_ok", "data-quality failure"),
        # a ticker whose live quote may belong to another company
        ("identity_ok", "unresolved ticker identity"),
    ]
    for col, label in gates:
        if col not in df.columns:
            continue
        n_before = len(df)
        df = df[df[col].fillna(True).astype(bool)]
        if len(df) < n_before:
            print(f"gate [{label}]: {n_before} -> {len(df)} scored")
    if irr is not None and len(irr):
        df = df.merge(irr, on="security_id", how="left")
    else:
        df["irr_central"] = np.nan

    cutoff = (datetime.now(timezone.utc) - timedelta(days=catalyst_window_days)) \
        .date().isoformat()
    if cats is not None and len(cats):
        recent = cats[cats["date"] >= cutoff]
        top = (recent.sort_values(["weight", "date"], ascending=[False, False])
                     .groupby("security_id").head(1))
        df = df.merge(top[["security_id", "catalyst_class", "date", "headline",
                           "weight"]].rename(columns={"date": "catalyst_date"}),
                      on="security_id", how="left")
    else:
        for c in ("catalyst_class", "catalyst_date", "headline", "weight"):
            df[c] = np.nan

    rows = []
    for r in df.itertuples(index=False):
        z = getattr(r, "z_adj", np.nan)
        stale = getattr(r, "staleness_days", np.nan)
        basis = getattr(r, "basis", np.nan)
        # a z inside the NAV estimate's own error band is a real number for
        # pricing, never evidence of a dislocation (nta_live.z_within_error)
        zwe = getattr(r, "z_within_error", False)
        zwe = bool(zwe) if pd.notna(zwe) else False
        # freshness is per fund: a quarterly publisher's 60-day-old NAV is
        # current by its own cadence (nta_live.staleness_limit_days); the
        # flat cap remains the floor for rows without a measured cadence
        lim = getattr(r, "staleness_limit_days", np.nan)
        lim = float(lim) if pd.notna(lim) else float(max_stale)
        # a NAV is usable when fresh by the fund's OWN cadence
        # (nav_current); basis is provenance, and a modelless quarterly
        # publisher's fresh NAV (basis 3, current) is a real observation.
        # Rows from an older table without the column fall back to the
        # basis cap.
        navc = getattr(r, "nav_current", None)
        if navc is None or pd.isna(navc):
            fresh = bool(pd.notna(stale) and stale <= lim
                         and pd.notna(basis) and basis <= max_basis)
        else:
            fresh = bool(navc)
        g1 = bool(pd.notna(z) and z <= z_thr and not zwe and fresh)
        g2 = bool(pd.notna(getattr(r, "catalyst_class", np.nan)))
        cat_w = getattr(r, "weight", np.nan)
        irr_v = getattr(r, "irr_central", np.nan)
        # An IRR built on a stale or carried NAV is not evidence of a
        # return; gate 3 demands the same anchor freshness as gate 1.
        g3 = bool(pd.notna(irr_v) and irr_v >= min_irr and fresh)

        passed = int(g1) + int(g2) + int(g3)
        # Strong enough to stand alone: a dislocation, a passing IRR, or a
        # high-weight catalyst. A routine buyback headline by itself is not.
        standalone = g1 or g3 or (g2 and pd.notna(cat_w) and cat_w >= solo_cat_w)
        # OPPORTUNITY is reserved for a row whose DATA is fully sound.
        # alert_eligible is that verdict, computed on the row itself: a
        # current z-score, a NAV inside the staleness cap, basis <= 2, and
        # the data-quality, identity and research gates all clear. A row
        # that clears three gates on a rolled-forward or somewhat stale NAV
        # is a real observation and may be WATCHed - it is not something to
        # act on, and calling it an opportunity would say it was.
        clean = bool(getattr(r, "alert_eligible", True))
        if passed == 3 and clean:
            verdict = "OPPORTUNITY"
        elif passed >= 2 or standalone:
            verdict = "WATCH"
        else:
            verdict = "NONE"
        if verdict == "NONE":
            continue

        rows.append({
            "security_id": r.security_id, "name": getattr(r, "name", ""),
            "market": getattr(r, "market", ""), "verdict": verdict,
            "gates_passed": passed,
            "gate1_dislocation": g1, "gate2_catalyst": g2, "gate3_return": g3,
            "data_fully_sound": clean,
            "z_adj": None if pd.isna(z) else round(float(z), 2),
            "discount_est": None if pd.isna(getattr(r, "discount_est", np.nan))
                            else round(float(r.discount_est), 4),
            "basis": None if pd.isna(basis) else int(basis),
            "staleness_days": None if pd.isna(stale) else int(stale),
            "catalyst_class": getattr(r, "catalyst_class", None),
            "catalyst_date": getattr(r, "catalyst_date", None),
            "catalyst_headline": getattr(r, "headline", None),
            "catalyst_weight": None if pd.isna(cat_w) else float(cat_w),
            "irr_central": None if pd.isna(irr_v) else round(float(irr_v), 4),
            "hurdle": round(min_irr, 4),
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["verdict", "gates_passed", "z_adj"],
                              ascending=[True, False, True])
    return out


def append_ledger(verdicts: pd.DataFrame, path: str) -> int:
    """Point-in-time record of every WATCH/OPPORTUNITY, appended never edited.

    Signal emission and the ledger write are one step by construction: the
    caller cannot email an idea without this having been written, because
    the ledger row IS the record the email is generated from.
    """
    from pathlib import Path
    if verdicts is None or not len(verdicts):
        return 0
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).date().isoformat()
    rec = verdicts.copy()
    rec.insert(0, "signal_date", stamp)
    if p.exists():
        old = pd.read_parquet(p)
        # one row per fund per day - a re-run must not double-count
        key = ["signal_date", "security_id"]
        rec = pd.concat([old, rec], ignore_index=True).drop_duplicates(key, keep="last")
    rec.to_parquet(p, index=False)
    return int(len(rec))
