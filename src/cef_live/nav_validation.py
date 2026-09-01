"""Cross-validate our extracted NAVs against an independent published source.

The parser's failure mode is not reading garbage - it is reading a
plausible WRONG number, which no downstream check can tell from a right
one. The only defence is a second, independently published figure for the
same fund and date. For the UK that is the AIC monthly panel: the
aggregator's NAV comes from the AIC's own collection, not from our parser,
which is exactly what qualifies it to contradict us. (The ASX equivalent
lives in au_lic.extract.runner validate mode, against the exchange's
monthly NTA.)

Verdicts per comparable fund-month, in tolerance order:

  agree       |own/panel - 1| <= 2%   - same figure within rounding drift
  basis_gap   |own/panel - 1| <= 6%   - the size of a cum/ex-income or
              debt-at-fair-value/par difference, NOT an error
  unit_error  ratio within 20% of x100 or /100 - the pence/pounds trap
  mismatch    anything else - the parser read the wrong number

Per fund, over the trailing window: agreement rate and a verdict.
`suspect` funds are REPORTED, never silently dropped - the live table's
own guards (nav_continuity, unit reconciliation) act on rows; this report
exists so a human and the loop can see parser quality fund by fund.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

AGREE_TOL = 0.02
BASIS_TOL = 0.06
WINDOW_MONTHS = 36
MIN_PAIRS = 3
VALIDATED_RATE = 0.8
SUSPECT_RATE = 0.5


def _verdict(ratio: float) -> str:
    if abs(ratio - 1.0) <= AGREE_TOL:
        return "agree"
    if abs(ratio - 1.0) <= BASIS_TOL:
        return "basis_gap"
    for scale in (100.0, 0.01):
        if abs(ratio / scale - 1.0) <= 0.2:
            return "unit_error"
    return "mismatch"


def compare(own: pd.DataFrame, panel: pd.DataFrame, nav_col: str,
            window_months: int = WINDOW_MONTHS) -> pd.DataFrame:
    """One row per comparable fund-month: our value, theirs, the verdict.

    own: security_id, nav_date, nav_value (canonical unit).
    panel: security_id, obs_month, `nav_col` (same unit).
    """
    cols = ["security_id", "obs_month", "own_nav", "panel_nav", "ratio",
            "verdict"]
    if own is None or not len(own) or panel is None or not len(panel) \
            or nav_col not in panel.columns:
        return pd.DataFrame(columns=cols)
    o = own.dropna(subset=["nav_value"]).copy()
    o["nav_date"] = pd.to_datetime(o["nav_date"], errors="coerce")
    o = o.dropna(subset=["nav_date"])
    o = o[o["nav_value"] > 0]
    o["obs_month"] = o["nav_date"].dt.to_period("M").astype(str)
    # month-END value: the panel states month-end, so comparing our
    # mid-month observation against it would manufacture disagreement
    o = (o.sort_values("nav_date")
         .groupby(["security_id", "obs_month"], as_index=False)
         .last())

    p = panel.dropna(subset=[nav_col]).copy()
    p["obs_month"] = p["obs_month"].astype(str)
    p = p[p[nav_col] > 0]
    months = sorted(p["obs_month"].unique())[-window_months:]
    p = p[p["obs_month"].isin(months)]

    m = o.merge(p[["security_id", "obs_month", nav_col]],
                on=["security_id", "obs_month"], how="inner")
    if not len(m):
        return pd.DataFrame(columns=cols)
    m = m.rename(columns={"nav_value": "own_nav", nav_col: "panel_nav"})
    m["ratio"] = m["own_nav"] / m["panel_nav"]
    m["verdict"] = m["ratio"].map(_verdict)
    return m[cols]


def per_fund(pairs: pd.DataFrame) -> pd.DataFrame:
    """Fund-level verdicts from the pairwise comparison."""
    cols = ["security_id", "pairs", "agree", "basis_gap", "unit_error",
            "mismatch", "agreement_rate", "median_ratio", "verdict"]
    if pairs is None or not len(pairs):
        return pd.DataFrame(columns=cols)
    rows = []
    for sid, g in pairs.groupby("security_id"):
        counts = g["verdict"].value_counts()
        n = int(len(g))
        # basis_gap counts as agreement for the RATE: a stable cum/ex or
        # debt-basis offset is a definition difference, not a parse error
        rate = float((counts.get("agree", 0) + counts.get("basis_gap", 0)) / n)
        if n < MIN_PAIRS:
            v = "insufficient_overlap"
        elif counts.get("unit_error", 0) > 0:
            v = "unit_suspect"
        elif rate >= VALIDATED_RATE:
            v = "validated"
        elif rate < SUSPECT_RATE:
            v = "suspect"
        else:
            v = "mixed"
        rows.append({"security_id": sid, "pairs": n,
                     "agree": int(counts.get("agree", 0)),
                     "basis_gap": int(counts.get("basis_gap", 0)),
                     "unit_error": int(counts.get("unit_error", 0)),
                     "mismatch": int(counts.get("mismatch", 0)),
                     "agreement_rate": round(rate, 4),
                     "median_ratio": round(float(g["ratio"].median()), 4),
                     "verdict": v})
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["verdict", "agreement_rate"]).reset_index(drop=True)


def run_uk(own: pd.DataFrame, panel: pd.DataFrame, nav_col: str,
           out_json: str = "reports/build/nav_validation_uk.json",
           out_csv: str = "outputs/live/nav_validation_uk.csv") -> dict:
    """The nightly UK validation: compare, summarise, write, return."""
    pairs = compare(own, panel, nav_col)
    funds = per_fund(pairs)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "comparable_pairs": int(len(pairs)),
        "funds_compared": int(funds["security_id"].nunique()) if len(funds) else 0,
        "pair_verdicts": pairs["verdict"].value_counts().to_dict()
        if len(pairs) else {},
        "fund_verdicts": funds["verdict"].value_counts().to_dict()
        if len(funds) else {},
        "agreement_rate_pairs": round(float(
            pairs["verdict"].isin(["agree", "basis_gap"]).mean()), 4)
        if len(pairs) else None,
        "suspect_funds": funds.loc[funds["verdict"].isin(
            ["suspect", "unit_suspect"]), "security_id"].tolist()
        if len(funds) else [],
    }
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    funds.to_csv(out_csv, index=False)
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(summary, indent=2, default=str))
    print("UK NAV validation:", json.dumps(
        {k: summary[k] for k in ("comparable_pairs", "funds_compared",
                                 "agreement_rate_pairs", "fund_verdicts")},
        default=str))
    return summary
