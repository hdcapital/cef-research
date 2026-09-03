"""Why does Investegate's /company/{ticker} page serve the market-wide feed
for some live funds?

Tufton Assets (SHIP), US Solar (USF), RTW Biotech (RTW), Partners Group
(PEY), Chenavari Toro (TORO), Gabelli Merchant (GMP) and Channel Islands
Property (CIP) have listing caches holding only other companies' rows, so
they can never be harvested. For each ticker this fetches the company
page, records which company slugs its rows actually carry, and tries the
site's search routes with the fund's name to find the page that IS the
fund's. Evidence only; writes reports/build/uk_listing_route_probe.json.
Set TICKERS (comma list).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, "src")
from cef_live.harvest_nav import uk_row_matches_ticker  # noqa: E402

TICKERS = [t.strip().upper() for t in os.environ.get(
    "TICKERS", "SHIP,USF,RTW,PEY,TORO,GMP,CIP").split(",") if t.strip()]
UA = {"User-Agent": "uk-cef research probe (contact: repo owner)"}
SLUG = re.compile(r"/announcement/[a-z]+/([a-z0-9-]+--[a-z0-9.]+)/", re.I)


def names() -> dict[str, str]:
    p = Path("outputs/live/uk_live_universe.csv")
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str)
    return {str(t).upper(): n for t, n in zip(df.get("ticker", []), df.get("name", []))}


def page(s, url):
    try:
        r = s.get(url, timeout=30, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": str(exc)}
    soup = BeautifulSoup(r.text, "html.parser")
    slugs = {}
    for a in soup.select("a[href]"):
        m = SLUG.search(a["href"])
        if m:
            slugs[m.group(1)] = slugs.get(m.group(1), 0) + 1
    company_links = sorted({a["href"] for a in soup.select("a[href]")
                            if "/company/" in a["href"]})[:40]
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return {"url": url, "final_url": r.url, "status": r.status_code, "title": title[:120],
            "row_slugs": dict(sorted(slugs.items(), key=lambda kv: -kv[1])[:8]),
            "company_links": company_links}


def main() -> int:
    nm = names()
    s = requests.Session()
    s.headers.update(UA)
    out = []
    # how a known announcement page links to its company page, and what the
    # site's own search form posts to - the canonical route, not a guess
    ref = page(s, "https://www.investegate.co.uk/announcement/bzw/"
                  "pershing-square-holdings-ltd--psh/net-asset-value-s-/9750355")
    try:
        r0 = s.get("https://www.investegate.co.uk", timeout=30)
        soup0 = BeautifulSoup(r0.text, "html.parser")
        ref["home_forms"] = [{"action": f.get("action"), "inputs": [i.get("name") for i in f.select("input")]}
                             for f in soup0.select("form")][:10]
        ref["home_search_links"] = sorted({a["href"] for a in soup0.select("a[href]")
                                           if "search" in a["href"].lower()})[:20]
    except Exception as exc:  # noqa: BLE001
        ref["home_error"] = str(exc)
    time.sleep(1.5)
    # the site's own company-search pages: their forms, fields and any API
    # endpoint their scripts call - the route the page uses, not one guessed
    ref["search_pages"] = []
    for u in ("https://www.investegate.co.uk/search-company",
              "https://www.investegate.co.uk/advanced-search",
              "https://www.investegate.co.uk/search-company?q=Tufton",
              "https://www.investegate.co.uk/search-company?term=Tufton",
              "https://www.investegate.co.uk/search-company?name=Tufton",
              "https://www.investegate.co.uk/search-company?company=Tufton",
              "https://www.investegate.co.uk/search-company?search=Tufton"):
        try:
            r1 = s.get(u, timeout=30)
            soup1 = BeautifulSoup(r1.text, "html.parser")
            forms = [{"action": f.get("action"), "method": f.get("method"),
                      "fields": [(i.name, i.get("name"), i.get("type")) for i in f.select("input,select,textarea,button")]}
                     for f in soup1.select("form")][:6]
            apis = sorted(set(re.findall(r"[\"'](/[A-Za-z0-9_./-]*(?:api|search|compan)[A-Za-z0-9_./?=&-]*)[\"']", r1.text)))[:30]
            links = sorted({a["href"] for a in soup1.select("a[href]") if "/company/" in a["href"]})[:20]
            ref["search_pages"].append({"url": u, "status": r1.status_code, "final_url": r1.url,
                                        "forms": forms, "api_like": apis, "company_links": links,
                                        "text_head": " ".join(soup1.get_text(" ").split())[:600]})
        except Exception as exc:  # noqa: BLE001
            ref["search_pages"].append({"url": u, "error": str(exc)})
        time.sleep(1.5)
    # the advanced-search form's select options and its /draw endpoint,
    # queried by ISIN - the identity route that cannot collide on a code
    isins = {}
    up = Path("outputs/live/uk_live_universe.csv")
    if up.exists():
        u = pd.read_csv(up, dtype=str)
        icol = next((c for c in u.columns if c.lower() == "isin"), None)
        if icol:
            isins = {str(t).upper(): i for t, i in zip(u["ticker"], u[icol]) if isinstance(i, str)}
    try:
        ra = s.get("https://www.investegate.co.uk/advanced-search", timeout=30)
        sa = BeautifulSoup(ra.text, "html.parser")
        ref["advanced_selects"] = {sel.get("name"): [(o.get("value"), o.get_text(" ", strip=True)[:40])
                                                     for o in sel.select("option")][:12]
                                   for sel in sa.select("select")}
        ref["advanced_scripts"] = [m for m in re.findall(r"draw[^\n]{0,300}", ra.text)][:6]
    except Exception as exc:  # noqa: BLE001
        ref["advanced_error"] = str(exc)
    time.sleep(1.5)
    ref["draw_trials"] = []
    t0 = TICKERS[0] if TICKERS else "SHIP"
    isin0 = isins.get(t0, "")
    for method, params in (("GET", {"search_for": "isin", "search_word": isin0, "page": 1}),
                           ("GET", {"search_for": "2", "search_word": isin0, "page": 1}),
                           ("GET", {"search_for": "1", "search_word": t0, "page": 1}),
                           ("POST", {"search_for": "isin", "search_word": isin0, "page": 1}),
                           ("GET", {"words": nm.get(t0) or t0})):
        u = "https://www.investegate.co.uk/search" if "words" in params else "https://www.investegate.co.uk/advanced-search/draw"
        try:
            r = s.get(u, params=params, timeout=30) if method == "GET" else s.post(u, data=params, timeout=30)
            body = r.text
            slugs = sorted(set(SLUG.findall(body)))[:10]
            ref["draw_trials"].append({"method": method, "url": r.url, "status": r.status_code,
                                       "content_type": r.headers.get("content-type"),
                                       "slugs": slugs, "head": " ".join(body.split())[:700]})
        except Exception as exc:  # noqa: BLE001
            ref["draw_trials"].append({"method": method, "params": params, "error": str(exc)})
        time.sleep(1.5)
    for t in TICKERS:
        rec = {"ticker": t, "name": nm.get(t), "isin": isins.get(t)}
        rec["company_page"] = page(s, f"https://www.investegate.co.uk/company/{t}")
        rows_ok = sum(n for sl, n in rec["company_page"].get("row_slugs", {}).items()
                      if uk_row_matches_ticker(f"/announcement/rns/{sl}/x/1", t))
        rec["company_page"]["rows_matching_ticker"] = rows_ok
        time.sleep(1.5)
        q = quote_plus((nm.get(t) or t).split("(")[0].strip())
        rec["searches"] = []
        slug = re.sub(r"[^a-z0-9]+", "-", (nm.get(t) or "").lower()).strip("-")
        for u in (f"https://www.investegate.co.uk/search?q={q}",
                  f"https://www.investegate.co.uk/advanced-search?companyName={q}",
                  f"https://www.investegate.co.uk/company/{slug}--{t.lower()}",
                  f"https://www.investegate.co.uk/company/{slug}-limited--{t.lower()}",
                  f"https://www.investegate.co.uk/company/{slug}-ltd--{t.lower()}",
                  f"https://www.investegate.co.uk/company/{slug}-plc--{t.lower()}",
                  f"https://www.investegate.co.uk/company/{t.lower()}"):
            rec["searches"].append(page(s, u))
            time.sleep(1.5)
        out.append(rec)
        print(t, rec["company_page"].get("status"), "matching rows", rows_ok,
              "slugs", list(rec["company_page"].get("row_slugs", {}))[:3])
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/uk_listing_route_probe.json").write_text(
        json.dumps({"reference": ref, "tickers": out}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
