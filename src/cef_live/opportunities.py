"""Turn a catalyst into an idea - or explain why it isn't one.

Three gates, all pre-specified in config/params.yaml and code-checked
rather than judged:

  1 DISLOCATION  z_adj <= threshold (own 36m history only - the AU holdout
                 proved absolute discount level is an ANTI-signal), NAV
                 anchor no more than `max_staleness_days` old, basis <= 2.
                 A stale NAV cannot evidence a dislocation.
  2 CATALYST     an announcement in the fixed taxonomy within the window.
  3 RETURN       forward IRR >= trailing universe total return + hurdle,
                 expressed as excess so it survives a regime change.

Verdict ladder: NONE -> WATCH (2 of 3) -> OPPORTUNITY (all 3). Every
verdict records which gates passed and the numbers behind them, so a
rejected idea is auditable and a later change of mind is visible.

The horizon this serves is months to years: positions built over days or
weeks. Nothing here is time-critical within a session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


def universe_trailing_tr(hist: pd.DataFrame, years: int = 5) -> float | None:
    """Median annualised total return across the universe - the hurdle base.

    Computed from the panel rather than hard-coded so the hurdle tracks the
    regime. Returns None when the panel cannot support it; the caller then
    has no gate 3 rather than a guessed one.

    ONE MARKET AT A TIME. Passing a combined frame would compare an
    Australian LIC against a UK hurdle; callers pass each market's own
    panel and get that market's own hurdle (see cli._hurdle_by_market).
    The AU panel names its CAGR columns ``nta_tr_cagr_*`` and the UK one
    ``nav_tr_cagr_*``; both are recognised, because looking for only the UK
    spelling returned None for AU and silently disabled gate 3 there.
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
             hurdle_base: float | dict | None,
             catalyst_window_days: int = 30) -> pd.DataFrame:
    """One row per fund with a live catalyst OR a qualifying dislocation.

    hurdle_base: a single trailing-universe return, or a {market: return}
    mapping so each market is judged against its own universe. A UK number
    applied to Australian funds is not a hurdle, it is a different market's
    hurdle.
    """
    op = params["opportunity"]
    z_thr = float(op["z_threshold"])
    max_stale = int(op["max_staleness_days"])
    max_basis = int(op["max_basis"])
    excess = float(op["irr_hurdle_excess_pp"]) / 100.0
    need_watch = int(op["watch_gates_required"])

    def _hurdle_for(mkt) -> float | None:
        base = hurdle_base.get(mkt) if isinstance(hurdle_base, dict) else hurdle_base
        return None if base is None else float(base) + excess

    df = live.copy()
    # A vehicle the research policy excludes cannot produce an idea. The
    # research that justifies acting on a discount z-score was run on a
    # population without VCTs, split-capital classes or non-sterling lines,
    # so a verdict on one of those has nothing behind it. Excluded rows are
    # dropped from SCORING, never from the live table they came from.
    if "research_eligible" in df.columns:
        n_before = len(df)
        df = df[df["research_eligible"].fillna(True).astype(bool)]
        if len(df) < n_before:
            print(f"research-eligibility filter: {n_before} -> {len(df)} scored")
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
        g1 = bool(pd.notna(z) and z <= z_thr
                  and pd.notna(stale) and stale <= max_stale
                  and pd.notna(basis) and basis <= max_basis)
        g2 = bool(pd.notna(getattr(r, "catalyst_class", np.nan)))
        irr_v = getattr(r, "irr_central", np.nan)
        hurdle = _hurdle_for(getattr(r, "market", None))
        g3 = bool(hurdle is not None and pd.notna(irr_v) and irr_v >= hurdle)

        passed = int(g1) + int(g2) + int(g3)
        if passed == 3:
            verdict = "OPPORTUNITY"
        elif passed >= need_watch:
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
            "z_adj": None if pd.isna(z) else round(float(z), 2),
            "discount_est": None if pd.isna(getattr(r, "discount_est", np.nan))
                            else round(float(r.discount_est), 4),
            "basis": None if pd.isna(basis) else int(basis),
            "staleness_days": None if pd.isna(stale) else int(stale),
            "catalyst_class": getattr(r, "catalyst_class", None),
            "catalyst_date": getattr(r, "catalyst_date", None),
            "catalyst_headline": getattr(r, "headline", None),
            "irr_central": None if pd.isna(irr_v) else round(float(irr_v), 4),
            "hurdle": None if hurdle is None else round(hurdle, 4),
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
