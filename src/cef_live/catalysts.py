"""Daily catalyst scan - the reason to read announcements beyond NAV.

The research phase classified catalysts only over the historical backfill.
This reads the SAME announcement pages the NAV harvester already fetches
each night and classifies them against the fixed taxonomy from the build
brief, so a wind-down, tender or continuation vote is seen the day it is
announced rather than at the next monthly rebuild.

Taxonomy is fixed and pre-specified: adding a class is a config change
with a dated rationale, never a reaction to something we just missed.
Headlines that match nothing are ignored, not bucketed into "other" -
a catalyst we cannot name is not a catalyst we can act on.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pandas as pd

# (class, weight, pattern). Weight orders the digest: structural events that
# force a discount to close outrank soft signals.
CATALYST_CLASSES: list[tuple[str, int, re.Pattern]] = [
    ("liquidation_wind_down", 5, re.compile(
        r"winding[\s\-]?up|wind[\s\-]?down|liquidat|members'? voluntary|"
        r"managed\s+wind|realisation\s+(?:policy|opportunity)|orderly\s+realisation", re.I)),
    ("scheme_merger_rollover", 5, re.compile(
        r"scheme\s+of\s+arrangement|proposed\s+merger|recommended\s+(?:cash\s+)?(?:offer|merger)"
        r"|combination\s+with|rollover\s+option|reconstruction", re.I)),
    ("tender_offer", 4, re.compile(
        r"tender\s+offer|off[\s\-]market\s+buy[\s\-]?back|redemption\s+(?:offer|facility)"
        r"|exit\s+opportunity|return\s+of\s+capital|capital\s+return", re.I)),
    ("continuation_vote", 4, re.compile(
        r"continuation\s+(?:vote|resolution)|discontinuation\s+(?:vote|resolution)", re.I)),
    ("strategic_review", 3, re.compile(
        r"strategic\s+review|review\s+of\s+(?:strategy|options|the\s+company)"
        r"|formal\s+sale\s+process", re.I)),
    ("manager_change", 3, re.compile(
        r"change\s+of\s+(?:investment\s+)?manager|manager\s+(?:change|transition|appointment)"
        r"|termination\s+of\s+(?:the\s+)?(?:investment\s+)?management\s+agreement"
        r"|appointment\s+of\s+(?:new\s+)?investment\s+manager", re.I)),
    ("discount_control", 3, re.compile(
        r"discount\s+control|buy[\s\-]?back\s+(?:programme|program|authority|of\s+shares)"
        r"|share\s+buy[\s\-]?back|repurchase\s+of\s+(?:ordinary\s+)?shares", re.I)),
    ("substantial_holder", 2, re.compile(
        r"holding\(s\)\s+in\s+company|substantial\s+(?:holder|shareholder|holding)"
        r"|notification\s+of\s+major\s+(?:holdings|interest)|becoming\s+a\s+substantial", re.I)),
    ("distribution_policy", 2, re.compile(
        r"dividend\s+policy|distribution\s+policy|revised\s+(?:dividend|distribution)"
        r"|target\s+dividend", re.I)),
]

# Routine filings that match a pattern above but carry no information -
# excluded so the digest stays worth reading.
NOISE = re.compile(r"total\s+voting\s+rights|transaction\s+in\s+own\s+shares"
                   r"|holding\(s\)\s+in\s+company\s*$", re.I)


def classify(headline: str) -> tuple[str, int] | None:
    """Return (class, weight) for the first taxonomy match, else None."""
    h = headline or ""
    if not h.strip():
        return None
    for name, weight, pat in CATALYST_CLASSES:
        if pat.search(h):
            # a bare buyback/holdings filing is routine housekeeping
            if weight <= 3 and NOISE.search(h):
                return None
            return name, weight
    return None


def scan_rows(rows: list[dict], days: int = 30) -> pd.DataFrame:
    """Classify announcement rows.

    rows: dicts with security_id, date (ISO), headline, url.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    out = []
    for r in rows:
        d = str(r.get("date") or "")[:10]
        if d < cutoff:
            continue
        got = classify(r.get("headline", ""))
        if got is None:
            continue
        out.append({"security_id": r.get("security_id"), "date": d,
                    "catalyst_class": got[0], "weight": got[1],
                    "headline": (r.get("headline") or "")[:160],
                    "url": r.get("url")})
    df = pd.DataFrame(out)
    if len(df):
        df = df.sort_values(["weight", "date"], ascending=[False, False])
    return df


def scan_au(index_path: str, codes: set[str] | None = None,
            days: int = 30) -> pd.DataFrame:
    """Classify recent AU announcements from the committed market index -
    zero extra fetches."""
    from pathlib import Path
    p = Path(index_path)
    if not p.exists():
        return pd.DataFrame()
    idx = pd.read_parquet(p)
    if codes:
        idx = idx[idx["code"].isin(codes)]
    idx = idx.copy()
    idx["date"] = pd.to_datetime(idx["release_date"], utc=True,
                                 errors="coerce").dt.strftime("%Y-%m-%d")
    rows = [{"security_id": f"ASX:{r.code}", "date": r.date,
             "headline": r.headline, "url": r.url}
            for r in idx.itertuples(index=False)]
    return scan_rows(rows, days=days)


def summarise(df: pd.DataFrame) -> dict:
    if df is None or not len(df):
        return {"catalysts": 0, "funds": 0, "by_class": {}}
    return {"catalysts": int(len(df)),
            "funds": int(df["security_id"].nunique()),
            "by_class": df["catalyst_class"].value_counts().to_dict()}
