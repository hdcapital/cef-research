"""Which vehicles the research policy says we may act on - deterministically.

Two different questions, deliberately kept apart:

  research_eligible    would the backtest's universe have held this? The
                       backtest excludes VCTs, split-capital share classes,
                       ZDPs and other non-ordinary lines, and non-sterling
                       UK quotes. Those exclusions are pre-specified in
                       config/default.yaml -> universe and are the reason a
                       signal derived from the research is meaningful at all.

  monitoring_eligible  do we intend to carry live data for it? That is
                       research eligibility AND being alive.

The live universe is deliberately WIDER than the research one - a VCT still
gets a row, a price and a NAV, because knowing what it trades at costs
nothing and hiding it would make the denominator a lie. What an excluded
vehicle must never do is become signal-ready or alert-eligible: the
research that justifies acting on a discount z-score was run on a
population that excluded it.

Everything here is a pure function of registry columns and the config. No
model, no judgement, no LLM.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

DEFAULT_EXCLUDED_TYPES = (
    "zero dividend preference", "zdp", "preference", "split", "income share",
    "capital share", "package", "unit", "c share", "subscription", "warrant",
)
STERLING = {"GBX", "GBP", "GBP_PENCE", "STG"}


def research_policy(cfg_path: str = "config/default.yaml") -> dict:
    """The pre-specified universe filters, read from the research config."""
    p = Path(cfg_path)
    if not p.exists():
        return {"exclude_vcts": True,
                "exclude_security_types": list(DEFAULT_EXCLUDED_TYPES)}
    cfg = yaml.safe_load(p.read_text()) or {}
    uni = cfg.get("universe", {}) or {}
    return {
        "exclude_vcts": bool(uni.get("exclude_vcts", True)),
        "exclude_security_types": [str(x).lower() for x in
                                   uni.get("exclude_security_types",
                                           DEFAULT_EXCLUDED_TYPES)],
    }


def classify(reg: pd.DataFrame, live_statuses, policy: dict | None = None,
             cfg_path: str = "config/default.yaml") -> pd.DataFrame:
    """Add research_eligible / monitoring_eligible / exclusion_reason.

    reg: registry rows carrying market, sector, share_type, currency,
    is_vct, is_split and the liveness `status`.
    live_statuses: the statuses that count as alive.

    An excluded fund keeps its row and gains a reason. Nothing is deleted -
    the audit's Excluded tab is built from exactly these rows, so a vehicle
    that leaves the monitored universe leaves an explanation behind.
    """
    pol = policy or research_policy(cfg_path)
    out = reg.copy()
    sector = out.get("sector", pd.Series("", index=out.index)).fillna("").str.lower()
    stype = out.get("share_type", pd.Series("", index=out.index)).fillna("").str.lower()
    ccy = out.get("currency", pd.Series("", index=out.index)).fillna("").str.upper()
    market = out.get("market", pd.Series("", index=out.index)).fillna("")

    is_vct = (out["is_vct"].fillna(False).astype(bool) if "is_vct" in out.columns
              else pd.Series(False, index=out.index))
    is_vct = is_vct | sector.str.contains("venture capital") | sector.str.startswith("vct")
    is_split = (out["is_split"].fillna(False).astype(bool) if "is_split" in out.columns
                else pd.Series(False, index=out.index))
    is_split = is_split | sector.str.contains("split capital")

    bad_type = pd.Series(False, index=out.index)
    for pat in pol["exclude_security_types"]:
        bad_type |= stype.str.contains(pat, regex=False)
    # "ordinary" wins over a substring hit: "Class A Ordinary" contains no
    # excluded token, but "Ordinary Unit" would trip "unit" - the ordinary
    # label is the fund's own description of its main line
    is_ordinary = stype.str.contains("ordinary") | (stype == "share") | (stype == "")
    bad_type &= ~is_ordinary

    # UK research is a sterling-quote study; a USD/EUR line is out of that
    # population. AU rows are AUD by construction.
    non_sterling = (market == "UK") & ccy.ne("") & ~ccy.isin(STERLING)

    # The ASX monthly reports carry benchmark accumulation series
    # (S&P/ASX 200 Accumulation and friends) in the same tables as the LICs,
    # so the registry inherited four index rows that are not funds, cannot
    # have a discount, and were entering the monitored universe as
    # permanently unpriceable. Their sector says exactly what they are.
    benchmark = sector.str.contains(r"\bindices\b", regex=True)

    alive = out["status"].isin(tuple(live_statuses))

    reasons = []
    for vct, split, bad, nster, ok_ord, bench, live in zip(
            is_vct, is_split, bad_type, non_sterling, is_ordinary, benchmark, alive):
        r = []
        if bench:
            r.append("benchmark_index_not_a_fund")
        if vct and pol["exclude_vcts"]:
            r.append("vct_excluded_by_research_policy")
        if split:
            r.append("split_capital_excluded_by_research_policy")
        if bad:
            r.append("non_ordinary_share_class")
        if not ok_ord and not bad:
            r.append("non_ordinary_share_class")
        if nster:
            r.append("non_sterling_quote")
        reasons.append(r)

    out["research_eligible"] = [not r for r in reasons]
    out["research_exclusion_reason"] = ["|".join(r) for r in reasons]
    out["is_alive"] = alive.values
    out["monitoring_eligible"] = out["research_eligible"] & out["is_alive"]
    out["exclusion_reason"] = [
        (rr if rr else ("not_live:" + st)) if not me else ""
        for rr, st, me in zip(out["research_exclusion_reason"], out["status"],
                              out["monitoring_eligible"])]
    return out
