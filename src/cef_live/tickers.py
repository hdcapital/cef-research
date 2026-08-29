"""Resolve tickers for registry funds that never had a MIR row.

The AIC keyfacts/companies file carries a `ticker` column - and unlike the
MIR, it populates it for the funds the MIR leaves name-only. That is the
authoritative source: it is the AIC's own identifier for its own listing,
it needs no external request, and it covers the announcements-only cohort
(probe: 8 of 8 sampled funds resolved, matching Yahoo's symbols exactly).

Yahoo's search endpoint is kept only as a fallback, and always behind name
verification, because it fails dangerously on its own: searching "British
& American" returns British American Tobacco, and "Bluefield Solar Income
Fund" returns its Frankfurt line ahead of the London one. A wrong ticker
staples another company's share price onto this fund's NAV, so a candidate
that cannot be verified is recorded unresolved rather than accepted.

Results cache to config/resolved_tickers.csv (committed).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from uk_cef.data_sources.investegate import (BASE, H1_RE, UA, _tokens_compatible)

CACHE = Path("config/resolved_tickers.csv")
THROTTLE = 1.4
_last = 0.0


def _get(s: requests.Session, url: str) -> requests.Response | None:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        return s.get(url, timeout=45)
    except Exception:  # noqa: BLE001
        return None


def _candidates(s: requests.Session, name: str) -> list[str]:
    """Candidate slugs from Investegate's own search for this name."""
    r = _get(s, f"{BASE}/search?q={requests.utils.quote(name)}")
    if r is None or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    slugs = []
    for a in soup.select("a[href]"):
        href = a["href"]
        m = re.match(r"^/company/([A-Za-z0-9._-]{2,12})/?$", href)
        if m:
            slug = m.group(1).upper()
            if slug not in slugs:
                slugs.append(slug)
    return slugs[:6]


def verify(s: requests.Session, slug: str, names: list[str]) -> tuple[str, str] | None:
    """Confirm a slug belongs to one of `names`. Returns (ticker, h1_name)."""
    r = _get(s, f"{BASE}/company/{slug}")
    if r is None or r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    if not h1:
        return None
    m = H1_RE.match(h1.get_text(" ", strip=True))
    if not m:
        return None
    page_name, ticker = m.group(1), m.group(2).upper()
    if any(_tokens_compatible(page_name, n) for n in names if n):
        return ticker, page_name
    return None


def from_aic_keyfacts(registry: pd.DataFrame, cfg_uk: dict) -> pd.DataFrame:
    """Tickers from the AIC companies file, keyed by the SAME entity
    resolution the registry used - so the join is exact, not fuzzy."""
    from uk_cef.entities import EntityRegistry
    from uk_cef.panel import parse_all_companies, parse_all_corporate_activity

    raw = Path(cfg_uk["download"]["raw_dir"])
    comp = parse_all_companies(raw)
    if comp.empty or "ticker" not in comp.columns:
        return pd.DataFrame(columns=["security_id", "ticker", "verified_name",
                                     "method", "status"])
    comp = comp[comp["ticker"].notna() & (comp["ticker"].astype(str).str.len() >= 2)]
    reg_ent = EntityRegistry(cfg_uk["paths"].get("entity_overrides"))
    reg_ent.load_name_changes(parse_all_corporate_activity(raw))
    comp = comp.sort_values("obs_month")
    sids = [reg_ent.resolve(n, c, "Ordinary Share")
            for n, c in zip(comp["company_name"], comp.get("isin", pd.Series(dtype=str)))]
    comp = comp.assign(security_id=sids)
    latest = comp.groupby("security_id").last().reset_index()
    keep = set(registry["security_id"])
    latest = latest[latest["security_id"].isin(keep)]
    return pd.DataFrame({
        "security_id": latest["security_id"],
        "ticker": latest["ticker"].astype(str).str.upper().str.strip(),
        "verified_name": latest["company_name"],
        "method": "aic_keyfacts",
        "status": "verified",
    })


def seed_known(registry: pd.DataFrame, cfg_uk: dict | None) -> pd.DataFrame:
    """Pre-fill the cache from tickers the MIR-matched map already knows.

    Those funds were resolved by identifier match and are already verified;
    re-searching them would waste requests and risk a worse match.
    """
    cache = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame(
        columns=["security_id", "ticker", "verified_name", "method", "status"])
    if cfg_uk is None:
        return cache
    try:
        from uk_cef.data_sources.investegate import build_ticker_map
        tmap = build_ticker_map(cfg_uk)
    except Exception:  # noqa: BLE001
        return cache
    tmap = tmap[tmap["ticker"].notna()]
    known = set(cache["security_id"])
    rows = [{"security_id": r.security_id, "ticker": str(r.ticker).upper(),
             "verified_name": None, "method": "mir_identifier_match",
             "status": "verified"}
            for r in tmap.itertuples(index=False) if r.security_id not in known]
    # the AIC's own keyfacts ticker covers the funds the MIR leaves blank
    try:
        kf = from_aic_keyfacts(registry, cfg_uk)
        have = known | {r["security_id"] for r in rows}
        rows += [r for r in kf.to_dict("records") if r["security_id"] not in have]
    except Exception as exc:  # noqa: BLE001
        print(f"keyfacts ticker source unavailable ({exc})")
    if rows:
        cache = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True) \
                  .drop_duplicates("security_id", keep="last")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        cache.to_csv(CACHE, index=False)
    return cache


def resolve(registry: pd.DataFrame, budget: int = 400) -> pd.DataFrame:
    """Resolve tickers for live registry rows that lack one.

    Returns the full cache: security_id, ticker, verified_name, method,
    status. Unresolved funds are kept with status so a later run can retry
    them and so the gap is visible rather than silent.
    """
    cache = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame(
        columns=["security_id", "ticker", "verified_name", "method", "status"])
    # only skip funds already VERIFIED; unresolved ones are retried
    known = set(cache.loc[cache["status"] == "verified", "security_id"])

    need = registry[(registry["status"] == "live")
                    & (registry["market"] == "UK")
                    & (~registry["security_id"].isin(known))]
    if not len(need):
        return cache

    s = requests.Session()
    s.headers["User-Agent"] = UA
    rows = []
    for r in need.head(budget).itertuples(index=False):
        names = [n for n in [r.name] if isinstance(n, str)]
        rec = {"security_id": r.security_id, "ticker": None,
               "verified_name": None, "method": None, "status": "unresolved"}
        # 1. the fund's own name as a slug guess is worthless (slugs are
        #    tickers), so go through search
        for slug in _candidates(s, r.name or ""):
            got = verify(s, slug, names)
            if got:
                rec.update(ticker=got[0], verified_name=got[1],
                           method="search+h1", status="verified")
                break
        rows.append(rec)

    out = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True) \
            .drop_duplicates("security_id", keep="last")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE, index=False)
    return out
