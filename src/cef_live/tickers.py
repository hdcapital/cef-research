"""Resolve tickers for registry funds that never had a MIR row.

A fund with no ticker has neither a NAV source (its Investegate
announcement page) nor a price source, so it cannot be priced at all. 105
live UK funds - the entire offshore/alternatives cohort the AIC lists but
never prices - were in that state.

Order of sources, identifier before name:

1. the MIR-matched map, already verified by identifier;
2. the AIC keyfacts/companies file, joined on ISIN (it carries a populated
   ticker column for 104 of the 105);
3. OpenFIGI's public ISIN -> exchange-code mapping, scoped to the London
   listing.

Name search is the last resort, never the first, because it fails
dangerously unaided: "British & American" returns British American
Tobacco, and "Bluefield Solar Income Fund" returns its Frankfurt line
ahead of the London one. An identifier join cannot make that class of
error at all.

Whatever the source, the candidate is put through the same check before
it is accepted: fetch the Investegate company page and confirm its H1
names this fund. A wrong ticker staples another company's share price onto
this fund's NAV, so a candidate that fails verification is recorded with
the disagreement (status unresolved_name_mismatch, the rejected candidate
kept in verified_name) rather than used.

Results cache to config/resolved_tickers.csv (committed).
"""

from __future__ import annotations

import json
import os
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


# ------------------------------------------------------------------ OpenFIGI
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
FIGI_BATCH = 10          # jobs per request, per OpenFIGI's documented limit
FIGI_SLEEP = 3.0         # unauthenticated allowance is 25 requests/minute


def _figi_pick(data: list[dict]) -> dict | None:
    """The London closed-end listing among an ISIN's FIGI records."""
    ln = [d for d in data if str(d.get("exchCode", "")).upper() == "LN"]
    pool = ln or data
    for d in pool:
        if "closed-end" in str(d.get("securityType", "")).lower():
            return d
    return pool[0] if pool else None


def from_openfigi(need: pd.DataFrame, session: requests.Session | None = None
                  ) -> dict[str, tuple[str, str, str]]:
    """ISIN -> (candidate ticker, FIGI name) for the London listing.

    OpenFIGI's public mapping endpoint takes the identifier we already hold
    for every registry fund and returns the exchange's own code. It is an
    identifier join, so it cannot make the class of error a name search
    makes - Yahoo returns British American Tobacco for "British & American"
    - and it is scoped with exchCode "LN" so it cannot return the Frankfurt
    line of a London trust either.

    The result is a CANDIDATE, not an answer: every one is put through the
    same Investegate H1 name check as any other source before it is
    accepted, because a wrong ticker staples another company's share price
    onto this fund's NAV.
    """
    s = session or requests.Session()
    s.headers.update({"Content-Type": "application/json", "User-Agent": UA})
    key = os.environ.get("OPENFIGI_API_KEY")
    if key:
        s.headers["X-OPENFIGI-APIKEY"] = key
    isins = [i for i in need["isin"].dropna().astype(str).str.strip().str.upper()
             if len(i) == 12]
    out: dict[str, tuple[str, str, str]] = {}
    # Pass 1 asks for the London listing directly. Pass 2 retries only the
    # ISINs that came back empty, unscoped, and still keeps LN records ONLY
    # - a trust's Frankfurt or Amsterdam line is not the security we price.
    queue = list(isins)
    for scoped in (True, False):
        if not queue:
            break
        queue = _figi_pass(s, queue, out, scoped=scoped)
    print(f"openfigi: {len(out)}/{len(isins)} ISINs returned a London ticker")
    return out


def _figi_pass(s: requests.Session, isins: list[str],
               out: dict[str, tuple[str, str, str]], scoped: bool) -> list[str]:
    """One sweep; returns the ISINs still unmapped."""
    for start in range(0, len(isins), FIGI_BATCH):
        chunk = isins[start:start + FIGI_BATCH]
        jobs = [{"idType": "ID_ISIN", "idValue": i,
                 **({"exchCode": "LN"} if scoped else {})} for i in chunk]
        try:
            r = s.post(OPENFIGI_URL, data=json.dumps(jobs), timeout=60)
        except Exception as exc:  # noqa: BLE001
            print(f"openfigi request failed ({exc}); {len(chunk)} ISINs unmapped")
            time.sleep(FIGI_SLEEP)
            continue
        if r.status_code == 429:
            # documented rate-limit response: wait out the window and retry once
            time.sleep(60)
            try:
                r = s.post(OPENFIGI_URL, data=json.dumps(jobs), timeout=60)
            except Exception:  # noqa: BLE001
                continue
        if r.status_code != 200:
            print(f"openfigi HTTP {r.status_code} for {len(chunk)} ISINs")
            time.sleep(FIGI_SLEEP)
            continue
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = []
        for isin, res in zip(chunk, body):
            data = (res.get("data") or []) if isinstance(res, dict) else []
            if not scoped:
                data = [d for d in data
                        if str(d.get("exchCode", "")).upper() == "LN"]
            hit = _figi_pick(data)
            if hit and hit.get("ticker"):
                out[isin] = (str(hit["ticker"]).upper(), str(hit.get("name") or ""),
                             str(hit.get("securityType") or ""))
        time.sleep(FIGI_SLEEP)
    return [i for i in isins if i not in out]


FUND_TYPES = ("closed-end fund", "fund", "investment trust", "unit trust",
              "mutual fund", "reit")


def _figi_self_consistent(cand: tuple[str, str, str], names: list[str]) -> bool:
    """Is an unconfirmed ISIN mapping safe to accept on its own?

    Only when BOTH hold:

    1. the record is typed as a fund. This is the check that carries the
       weight. The name matcher is deliberately lenient - it has to accept
       "Chenavari Toro Income Fund" for "CHENAVARI TORO INCOME FUND L" -
       and lenient enough that it also accepts "BRITISH AMERICAN TOBACCO"
       for "British & American". Security type does not: a tobacco company
       is Common Stock, never a Closed-End Fund.
    2. the name OpenFIGI returns for that ISIN is compatible with the name
       the registry holds, which catches a wrong or stale ISIN.

    Either alone would let something through; together they are what makes
    an unverified page acceptable, and the method label keeps the fact that
    the page was never confirmed on the record.
    """
    _ticker, figi_name, sec_type = cand
    st = sec_type.lower()
    if not any(t in st for t in FUND_TYPES):
        return False
    return any(_tokens_compatible(figi_name, n) for n in names if n)


def from_aic_keyfacts(registry: pd.DataFrame, cfg_uk: dict) -> pd.DataFrame:
    """Tickers from the AIC companies file, joined on ISIN.

    Deliberately does NOT re-run entity resolution. A fresh EntityRegistry
    fed the companies file produced different security_ids than the one fed
    the MIR - old numeric SEDOLs where the registry holds modern ones - so
    the join silently matched nothing for the very cohort it exists to
    serve. ISIN is carried by both sides and is unambiguous, so join on it;
    normalised name is the fallback for rows where the companies file omits
    the ISIN.
    """
    from uk_cef.entities import normalize_name
    from uk_cef.panel import parse_all_companies

    raw = Path(cfg_uk["download"]["raw_dir"])
    comp = parse_all_companies(raw)
    if comp.empty or "ticker" not in comp.columns:
        return pd.DataFrame(columns=["security_id", "ticker", "verified_name",
                                     "method", "status"])
    comp = comp[comp["ticker"].notna()
                & (comp["ticker"].astype(str).str.strip().str.len() >= 2)].copy()
    comp["ticker"] = comp["ticker"].astype(str).str.upper().str.strip()
    if "obs_month" in comp.columns:
        comp = comp.sort_values("obs_month")

    by_isin, by_name = {}, {}
    for r in comp.itertuples(index=False):
        isin = str(getattr(r, "isin", "") or "").strip().upper()
        if isin:
            by_isin[isin] = (r.ticker, r.company_name)
        nm = normalize_name(str(r.company_name or ""))
        if nm:
            by_name[nm] = (r.ticker, r.company_name)

    rows = []
    for r in registry.itertuples(index=False):
        isin = str(getattr(r, "isin", "") or "").strip().upper()
        hit = by_isin.get(isin) if isin else None
        method = "aic_keyfacts_isin"
        if hit is None:
            hit = by_name.get(normalize_name(str(getattr(r, "name", "") or "")))
            method = "aic_keyfacts_name"
        if hit is None:
            continue
        rows.append({"security_id": r.security_id, "ticker": hit[0],
                     "verified_name": hit[1], "method": method,
                     "status": "verified"})
    # visible, not silent: four attempts at this join each returned 0 for the
    # cohort it exists to serve, and none of them said so. A join that
    # matches nothing must announce it.
    print(f"keyfacts: {len(comp)} ticker-bearing rows, {len(by_isin)} ISINs, "
          f"{len(by_name)} names -> matched {len(rows)} of {len(registry)} "
          f"registry rows")
    return pd.DataFrame(rows)


def seed_known(registry: pd.DataFrame, cfg_uk: dict | None) -> pd.DataFrame:
    """Pre-fill the cache from tickers the MIR-matched map already knows.

    Those funds were resolved by identifier match and are already verified;
    re-searching them would waste requests and risk a worse match.
    """
    cache = pd.read_csv(CACHE) if CACHE.exists() else pd.DataFrame(
        columns=["security_id", "ticker", "verified_name", "method", "status"])
    if cfg_uk is None:
        return cache
    # Each source is attempted INDEPENDENTLY and reports its own failure.
    # A bare `return cache` here is what actually caused four consecutive
    # 0-of-105 runs: the MIR map raised (this workflow restores only the
    # small raw_aic state group, so the MIR files are not on disk), the
    # function returned before the keyfacts join was ever reached, and
    # nothing said so. One source being unavailable must never silently
    # cancel the others.
    try:
        from uk_cef.data_sources.investegate import build_ticker_map
        tmap = build_ticker_map(cfg_uk)
        tmap = tmap[tmap["ticker"].notna()]
    except Exception as exc:  # noqa: BLE001
        print(f"mir ticker map unavailable ({type(exc).__name__}: {exc})")
        tmap = pd.DataFrame(columns=["security_id", "ticker"])
    # skip only funds already VERIFIED - a row cached as unresolved by an
    # earlier attempt must not block a better source from filling it in,
    # which is exactly how 105 keyfacts tickers were silently discarded
    known = set(cache.loc[cache["status"] == "verified", "security_id"])
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

    todo = need.head(budget)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    # Identifier first: every registry fund carries an ISIN, and mapping it
    # to the London listing cannot mis-identify the company the way a name
    # search can. The search endpoint stays as the fallback for the rows
    # OpenFIGI does not cover.
    figi = from_openfigi(todo)
    rows = []
    for r in todo.itertuples(index=False):
        names = [n for n in [r.name] if isinstance(n, str)]
        rec = {"security_id": r.security_id, "ticker": None,
               "verified_name": None, "method": None, "status": "unresolved"}
        isin = str(getattr(r, "isin", "") or "").strip().upper()
        cand = figi.get(isin)
        if cand:
            got = verify(s, cand[0], names)
            if got:
                rec.update(ticker=got[0], verified_name=got[1],
                           method="openfigi_isin+h1", status="verified")
            elif _figi_self_consistent(cand, names):
                # Investegate did not confirm the page - it may not carry
                # this fund under that slug - but the mapping is internally
                # consistent: the ISIN is this fund's own identifier, the
                # record is typed a fund rather than an operating company,
                # and the name it returns for that ISIN is this fund's name.
                # Accepted, and labelled so the unconfirmed page is visible:
                # the NAV harvester may still find nothing under this slug.
                rec.update(ticker=cand[0], verified_name=cand[1],
                           method="openfigi_isin+figi_name", status="verified")
            else:
                # the mapping returned something the checks reject - keep
                # WHAT it returned so the disagreement is auditable, but
                # never let an unverified ticker price this fund
                rec["verified_name"] = f"figi_candidate:{cand[0]} ({cand[1]})"
                rec["status"] = "unresolved_name_mismatch"
        if rec["status"] != "verified":
            # the fund's own name as a slug guess is worthless (slugs are
            # tickers), so go through search
            for slug in _candidates(s, r.name or ""):
                got = verify(s, slug, names)
                if got:
                    rec.update(ticker=got[0], verified_name=got[1],
                               method="search+h1", status="verified")
                    break
        rows.append(rec)
    done = sum(1 for x in rows if x["status"] == "verified")
    print(f"resolve: {done}/{len(rows)} verified "
          f"({sum(1 for x in rows if x['method'] == 'openfigi_isin+h1')} via ISIN)")

    out = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True) \
            .drop_duplicates("security_id", keep="last")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE, index=False)
    return out
