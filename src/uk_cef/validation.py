"""Data-quality checks (Stage 27).

Checks flag anomalies; they never delete or repair observations. Each check
returns rows for outputs/data_quality_report.csv with columns:
check, severity, date, security_id, company_name, detail.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rows(df: pd.DataFrame, check: str, severity: str, detail_fn) -> list[dict]:
    out = []
    for _, r in df.iterrows():
        out.append(
            {
                "check": check,
                "severity": severity,
                "date": r.get("date"),
                "security_id": r.get("security_id"),
                "company_name": r.get("company_name"),
                "detail": detail_fn(r),
            }
        )
    return out


def run_quality_checks(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    q = cfg["quality"]
    issues: list[dict] = []

    if "nav_per_share" in panel.columns:
        bad_nav = panel[panel["nav_per_share"].notna() & (panel["nav_per_share"] <= 0)]
        issues += _rows(bad_nav, "impossible_nav", "error", lambda r: f"NAV={r['nav_per_share']}")

    if "share_price" in panel.columns:
        bad_px = panel[panel["share_price"].notna() & (panel["share_price"] <= 0)]
        issues += _rows(bad_px, "non_positive_price", "error", lambda r: f"price={r['share_price']}")

    if "discount" in panel.columns:
        deep = panel[panel["discount"].notna() & (panel["discount"] < q["discount_floor"])]
        issues += _rows(deep, "discount_below_floor", "warning", lambda r: f"discount={r['discount']:.3f}")
        prem = panel[panel["discount"].notna() & (panel["discount"] > q["premium_ceiling"])]
        issues += _rows(prem, "extreme_premium", "warning", lambda r: f"discount={r['discount']:.3f}")

    if "fwd_return" in panel.columns:
        big = panel[panel["fwd_return"].notna() & (panel["fwd_return"].abs() > q["max_abs_monthly_return"])]
        issues += _rows(big, "extreme_monthly_return", "warning", lambda r: f"fwd_return={r['fwd_return']:.3f}")

    dupes = panel[panel.duplicated(subset=["date", "security_id"], keep=False)]
    issues += _rows(dupes, "duplicate_security_month", "error", lambda r: "duplicate row")

    # stale NAV: identical NAV for > stale_nav_months consecutive months
    if "nav_per_share" in panel.columns:
        p = panel.sort_values(["security_id", "date"])
        for sid, grp in p.groupby("security_id"):
            nav = grp["nav_per_share"]
            run_id = (nav != nav.shift()).cumsum()
            run_len = run_id.map(run_id.value_counts())
            stale = grp[(run_len > q["stale_nav_months"]) & nav.notna()]
            if len(stale):
                issues += _rows(
                    stale.tail(1), "stale_nav", "warning",
                    lambda r, n=int(run_len.max()): f"NAV unchanged {n} months",
                )

    # calculated vs published discount disagreement
    if {"calculated_discount", "published_discount"}.issubset(panel.columns):
        both = panel[panel["calculated_discount"].notna() & panel["published_discount"].notna()]
        dis = both[
            (both["calculated_discount"] - both["published_discount"]).abs()
            > q["discount_disagreement_threshold"]
        ]
        issues += _rows(
            dis, "discount_disagreement", "warning",
            lambda r: f"calc={r['calculated_discount']:.3f} pub={r['published_discount']:.3f}",
        )

    # disappearance from universe with no recorded corporate-action outcome
    if "date" in panel.columns:
        last_month = panel["date"].max()
        lasts = panel.sort_values("date").groupby("security_id").tail(1)
        vanished = lasts[lasts["date"] < last_month]
        if "outcome" in panel.columns:
            vanished = vanished[vanished["outcome"].isna()]
        issues += _rows(
            vanished, "unexplained_disappearance", "info",
            lambda r: f"last seen {r['date']:%Y-%m}; no corporate-action outcome recorded",
        )

    report = pd.DataFrame(issues, columns=["check", "severity", "date", "security_id", "company_name", "detail"])
    return report


def check_no_lookahead(panel: pd.DataFrame) -> list[str]:
    """Structural look-ahead assertions used by tests and the validate CLI.

    fwd_return at signal month t must equal the return over month t+1: we
    verify the panel builder recorded fwd_return_month == t+1 where present.
    """
    problems = []
    if "fwd_return_month" in panel.columns:
        p = panel[panel["fwd_return_month"].notna()]
        sig = pd.PeriodIndex(p["date"].dt.to_period("M"))
        fwd = pd.PeriodIndex(p["fwd_return_month"], freq="M")
        bad = (fwd - sig) != 1
        # (fwd - sig) yields offsets; compare via astype
        bad = [(f - s).n != 1 for f, s in zip(fwd, sig)]
        n_bad = int(np.sum(bad))
        if n_bad:
            problems.append(f"{n_bad} rows where fwd_return_month != signal month + 1")
    return problems
