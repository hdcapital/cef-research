"""Tier 0 harvester: issuer-published high-frequency NAV/NTA announcements.

AU: the open market-wide announcement index (validation work) already
carries every fund's announcements with direct PDF links. The research
validation deliberately EXCLUDED weekly/daily NTA statements; Tier 0 is
exactly those plus any-day month-end statements. This module selects them,
fetches + parses the PDFs through the same battle-tested extraction and
scored label parser as the validation (scripts/sample_nta_pdfs.py), and
returns published NAV observations - real numbers with real dates,
never estimates.

UK: Investegate "Net Asset Value(s)" RNS announcements; the frequency
census runs off the existing crawler cache, value harvesting lands with
the incremental crawler extension (see docs/RUNBOOK.md).
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "nta_parse", Path(__file__).resolve().parents[2] / "scripts" / "sample_nta_pdfs.py")
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)

# Tier 0 headline: an NTA/NAV statement with an as-at date at ANY day of
# month, or an explicit daily/weekly update. Amendments still excluded.
BAD = re.compile(r"amendment|amended|correction|withdraw", re.I)


DOTTED_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b")


def _asat_date(head: str) -> pd.Timestamp | None:
    m = P.ASAT.search(head or "")
    if m:
        try:
            return pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            return None
    # "NTA at 21.08.2026", "Daily Estimate NTA for 26.08.2026"
    m = DOTTED_DATE.search(head or "")
    if m:
        try:
            return pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def harvest_au(codes: set[str], lookback_days: int = 14,
               pdf_budget: int = 200) -> pd.DataFrame:
    """Published NAVs from recent AU NTA announcements.

    Returns DataFrame: security_id, nav_date, nav_value, unit, basis_note,
    source (announcement id), headline. Only successfully parsed,
    unambiguous values are returned - everything else is left absent.
    """
    import requests

    if not P.INDEX_F.exists():
        return pd.DataFrame(columns=["security_id", "nav_date", "nav_value",
                                     "unit", "basis_note", "source", "headline"])
    idx = pd.read_parquet(P.INDEX_F)
    idx = idx[idx["code"].isin(codes) & idx["url"].notna()]
    idx["release"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    idx = idx[idx["release"] >= cutoff]

    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    counters = {"pdf_calls": 0}
    rows = []
    for r in idx.sort_values("release", ascending=False).itertuples(index=False):
        head = r.headline or ""
        if not P.NTA_HEAD.search(head) or BAD.search(head):
            continue
        asat = _asat_date(head)
        if asat is None:
            # daily/weekly updates often carry no as-at in the headline;
            # use the release date as the observation date, labelled so
            if not re.search(r"daily|weekly|\bNTA\b|net tangible|\bNAV\b", head, re.I):
                continue
            asat = pd.Timestamp(r.release.date())
            date_src = "release_date"
        else:
            date_src = "as_at_headline"
        if counters["pdf_calls"] >= pdf_budget:
            break
        res = P.derive_stated(P.parse_pdf(s, str(r.id), r.url, counters))
        if res.get("status") != "parsed":
            continue
        val, unit = res["stated_raw"], res.get("unit")
        if unit == "ambiguous":
            continue                      # flagged, never guessed
        rows.append({"security_id": f"ASX:{r.code}",
                     "nav_date": asat.date().isoformat(),
                     "nav_value": val / 100.0 if unit == "cents" else val,
                     "unit": unit,
                     "basis_note": res.get("basis", "pre_tax") + f"|{date_src}",
                     "source": f"asx_ann:{r.id}", "headline": head[:120]})
    return pd.DataFrame(rows)


def uk_frequency_census(cache_dir: Path) -> pd.DataFrame:
    """Per-fund NAV-announcement publication frequency from the existing
    Investegate crawl cache (per-ticker listing CSVs - no new fetches).

    The crawler stores listings/{ticker}.csv with ann_id, date, headline
    (and url) for every announcement it has paged over. Counts 'Net Asset
    Value' titled rows per fund and classifies nav_frequency: daily /
    weekly / monthly / adhoc. Feeds the universe config; value harvesting
    follows via the same detail-page path the dividend crawler uses.
    """
    pat = re.compile(r"net asset value", re.I)
    listings = cache_dir / "listings"
    rows = []
    files = sorted(listings.glob("*.csv")) if listings.exists() else []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if "headline" not in df.columns:
            continue
        nav = df[df["headline"].fillna("").str.contains(pat)]
        for r in nav.itertuples(index=False):
            if getattr(r, "date", None):
                rows.append({"ticker": f.stem, "date": r.date,
                             "ann_id": getattr(r, "ann_id", None)})
    if not rows:
        return pd.DataFrame(columns=["ticker", "n_navs", "first", "last",
                                     "per_month", "nav_frequency"])
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker", "ann_id"])
    g = df.groupby("ticker")["date"].agg(["count", "min", "max"]).reset_index()
    months = ((pd.to_datetime(g["max"]) - pd.to_datetime(g["min"])).dt.days / 30.4).clip(lower=1)
    g["per_month"] = g["count"] / months
    g["nav_frequency"] = pd.cut(g["per_month"], [-1, 0.5, 2.5, 12, 1e9],
                                labels=["adhoc", "monthly", "weekly", "daily"])
    return g.rename(columns={"count": "n_navs", "min": "first", "max": "last"})
