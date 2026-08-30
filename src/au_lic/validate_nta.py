"""Cross-check extracted NTA against the ASX's own published figures.

The monthly investment-products reports carry an NTA per security per month,
published by the exchange. The extractor reads NTA from each fund's own
announcements. Where both exist for the same fund-month they should agree,
and where they do not, the disagreement is evidence about the extractor -
independent evidence, from a source that was not involved in producing the
number being tested.

This is the only check in the pipeline that can catch a WRONG value rather
than a missing one. A parse rate says how often the parser produced
something; it says nothing about whether what it produced is right. The
specific errors this catches:

  unit_error      cents read as dollars, or the reverse - a 100x error that
                  looks entirely plausible in isolation and would put a fund
                  on a -99% discount
  wrong_fund      an announcement attributed to the wrong code
  basis_gap       a CONSISTENT per-fund offset, which is usually not an error
                  at all: the exchange may publish post-tax where the fund
                  announces pre-tax. Classified, never "corrected" - silently
                  adjusting one source to match another destroys the evidence
                  that they measure different things
  mismatch        everything else, listed for inspection

Nothing here overwrites an extracted value. The extractor's output is what
the fund published; this reports where that disagrees with the exchange.
"""

from __future__ import annotations

import pandas as pd

MATCH_TOL = 0.005          # 0.5% - published NTA carries 3-4 decimals
UNIT_LO, UNIT_HI = 50.0, 200.0
BASIS_MIN_OBS = 3          # a per-fund offset needs repetition to be a basis


def _month(s: pd.Series) -> pd.Series:
    """Month key, with undated rows as NaN rather than the string "NaT".

    Period.astype(str) renders a missing value as the literal "NaT", which
    dropna() then keeps - so undated NAVs survived as a month named "NaT"
    and silently joined against nothing.
    """
    per = pd.to_datetime(s, errors="coerce").dt.to_period("M")
    return per.astype(str).where(per.notna())


def compare(extracted: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """One row per fund-month where both sources have a value.

    Joined on the VALUATION month, not the publication month: the exchange's
    March figure is about March, and an announcement published on 8 April
    about 31 March is the same observation. This is a data-quality check, so
    it compares like with like; the point-in-time discipline belongs in the
    discount builder, which is a different question.
    """
    if extracted is None or extracted.empty or panel is None or panel.empty:
        return pd.DataFrame()
    e = extracted.dropna(subset=["nav_per_share"]).copy()
    e["ticker"] = e["ticker"].astype(str).str.upper()
    e["month"] = _month(e["valuation_date"])
    e = e.dropna(subset=["month"])
    # several announcements can cover one month; the last valuation wins
    e = e.sort_values("valuation_date").groupby(["ticker", "month"], as_index=False).last()

    p = panel.copy()
    if "nta_price" not in p.columns:
        return pd.DataFrame()
    # BOTH sides upper-cased. The extracted side was upper-cased and this one
    # was not, so any case difference made every key miss - and the
    # diagnostic, which upper-cased both, reported a 147-of-147 overlap that
    # the join itself could never see.
    p["ticker"] = (p["security_id"].astype(str)
                   .str.replace(r"(?i)^ASX:", "", regex=True).str.upper())
    p["month"] = p["obs_month"].astype(str)
    p = p.dropna(subset=["nta_price"])[["ticker", "month", "nta_price"]]

    m = e.merge(p, on=["ticker", "month"], how="inner")
    if m.empty:
        return m
    m["ratio"] = m["nav_per_share"] / m["nta_price"].replace(0, pd.NA)
    m["rel_diff"] = (m["nav_per_share"] - m["nta_price"]).abs() / m["nta_price"].abs()
    return m


def classify(cmp_df: pd.DataFrame) -> pd.DataFrame:
    """Label each disagreement by the kind of error it implies."""
    if cmp_df is None or cmp_df.empty:
        return cmp_df
    d = cmp_df.copy()
    d["verdict"] = "mismatch"
    d.loc[d["rel_diff"] <= MATCH_TOL, "verdict"] = "match"
    unit = (d["verdict"] != "match") & (
        d["ratio"].between(UNIT_LO, UNIT_HI)
        | d["ratio"].between(1 / UNIT_HI, 1 / UNIT_LO))
    d.loc[unit, "verdict"] = "unit_error"

    # a consistent per-fund offset is a basis difference, not a parse error:
    # the exchange may publish post-tax where the fund announces pre-tax
    still = d[d["verdict"] == "mismatch"]
    for ticker, g in still.groupby("ticker"):
        if len(g) < BASIS_MIN_OBS:
            continue
        spread = g["ratio"].max() - g["ratio"].min()
        if spread < 0.02 and 0.5 < g["ratio"].median() < 1.5:
            d.loc[g.index, "verdict"] = "basis_gap"
    return d


def summarise(classified: pd.DataFrame, extracted_total: int | None = None) -> dict:
    """Agreement, and how much of the extraction this could check at all.

    The exchange file starts in 2017 and lists only the funds it covers, so
    the comparable subset is smaller than the extraction. Reporting the
    agreement rate without the coverage would overstate what has been
    verified.
    """
    if classified is None or classified.empty:
        return {"comparable_observations": 0,
                "note": "no fund-month had a value from both sources"}
    vc = classified["verdict"].value_counts().to_dict()
    n = int(len(classified))
    out = {
        "comparable_observations": n,
        "by_verdict": {k: int(v) for k, v in vc.items()},
        "agreement_rate": round(int(vc.get("match", 0)) / max(1, n), 4),
        "unit_error_rate": round(int(vc.get("unit_error", 0)) / max(1, n), 4),
        "median_ratio": round(float(classified["ratio"].median()), 6),
        "funds_compared": int(classified["ticker"].nunique()),
        "worst": classified.nlargest(min(15, n), "rel_diff")[
            ["ticker", "month", "nav_per_share", "nta_price", "ratio", "verdict"]
        ].to_dict("records"),
    }
    if extracted_total:
        out["extracted_observations"] = int(extracted_total)
        out["check_coverage"] = round(n / max(1, extracted_total), 4)
        out["note"] = ("the exchange file starts in 2017 and lists only the "
                       "funds it covers, so agreement is measured on this "
                       "subset, not on the whole extraction")
    return out
