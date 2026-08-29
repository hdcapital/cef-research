"""Resolve tickers for registry funds that never had a MIR row.

The existing ticker map is built by matching AIC MIR identifiers, so the
110 funds the AIC lists but never prices have no ticker - and without one
they have neither a NAV source (Investegate slug) nor a price source
(Yahoo symbol). This module closes that gap.

Method, deliberately verify-don't-guess: search Investegate for the
registry name, then confirm the candidate page's H1 header - which reads
"<Name> (<TICKER>) RNS Announcements" - names the same company, using the
same lenient token matcher the dividends crawler already trusts. A
candidate that fails verification is recorded as unresolved, never
assumed: a wrong ticker would silently staple another fund's share price
onto this fund's NAV, which is worse than a missing row.

Results are cached to config/resolved_tickers.csv (committed) so the
throttled search runs once per fund, not once per night.
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


def resolve(registry: pd.DataFrame, budget: int = 150) -> pd.DataFrame:
    """Resolve tickers for live registry rows that lack one.

    Returns the full cache: security_id, ticker, verified_name, method,
    status. Unresolved funds are kept with status so a later run can retry
    them and so the gap is visible rather than silent.
    """
    cache = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame(
        columns=["security_id", "ticker", "verified_name", "method", "status"])
    known = set(cache["security_id"])

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
