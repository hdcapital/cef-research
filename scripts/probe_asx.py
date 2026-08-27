"""Probe ASX data sources for the Australian LIC/LIT study.

Questions (answered from a CI runner; the dev sandbox has no ASX egress):
1. robots.txt - what may we crawl?
2. The Investment Products monthly report page: how are monthly files
   linked, how far back does the archive go, what formats (xlsx/pdf)?
3. A sample monthly report file: sheet/column structure for the LIC/LIT
   universe (NTA, premium/discount, market cap).
4. Announcements: does the public JSON API
   (/asx/1/company/<code>/announcements) respond? Structure for a live LIC
   (AFI), and does a delisted LIC still resolve? Also fetch the
   announcements.uwc page to map its query surface.

Gentle: ~1.5s throttle, <20 requests. Results committed under
data/probe/asx/.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.asx.com.au"
OUT = Path("data/probe/asx")
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str, accept_json=False):
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    headers = {"Accept": "application/json"} if accept_json else {}
    try:
        r = session.get(url, timeout=60, headers=headers)
        print(f"GET {url} -> {r.status_code} ({len(r.content)}b, {r.headers.get('Content-Type','')[:40]})")
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"GET {url} FAILED: {exc}")
        return None


def describe_page(label: str, r, notes: dict) -> BeautifulSoup | None:
    if r is None:
        notes[label] = "failed"
        return None
    notes[label] = r.status_code
    (OUT / f"{label}.html").write_bytes(r.content[:600_000])
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.content, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE, a["href"])
        text = a.get_text(" ", strip=True)[:100]
        if re.search(r"\.(pdf|xlsx?|csv)($|\?)|monthly|report|investment[- ]product", href + " " + text, re.I):
            links.append({"href": href, "text": text})
    notes[f"{label}_doc_links"] = links[:60]
    notes[f"{label}_n_doc_links"] = len(links)
    return soup


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {}

    r = fetch(BASE + "/robots.txt")
    if r is not None:
        (OUT / "robots.txt").write_bytes(r.content)
        notes["robots"] = r.status_code

    # 1. monthly report landing page
    describe_page("monthly_report_page",
                  fetch(BASE + "/issuers/investment-products/asx-investment-products-monthly-report"),
                  notes)
    # possible archive/statistics pages
    describe_page("inv_products_stats",
                  fetch(BASE + "/issuers/investment-products"), notes)

    # 2. announcements.uwc page
    r = fetch(BASE + "/markets/trade-our-cash-market/announcements.uwc")
    if r is not None:
        notes["announcements_uwc"] = r.status_code
        (OUT / "announcements_uwc.html").write_bytes(r.content[:600_000])
        soup = BeautifulSoup(r.content, "html.parser")
        notes["announcements_forms"] = [
            {"action": f.get("action"), "method": f.get("method"),
             "inputs": [(i.get("name"), i.get("type")) for i in f.find_all(["input", "select"])][:15]}
            for f in soup.find_all("form")
        ][:5]

    # 3. JSON announcement API - live LIC (AFI), live LIT-era (WAM),
    #    delisted LIC candidates
    for code in ("AFI", "WAM", "MLT", "AUI"):
        r = fetch(f"{BASE}/asx/1/company/{code}/announcements?count=20&market_sensitive=false",
                  accept_json=True)
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                (OUT / f"api_ann_{code}.json").write_text(json.dumps(data, indent=1)[:200_000])
                items = data.get("data", data if isinstance(data, list) else [])
                notes[f"api_{code}"] = {"status": 200, "n": len(items),
                                        "sample": items[:3]}
            except Exception as exc:  # noqa: BLE001
                notes[f"api_{code}"] = f"json error: {exc}"
        else:
            notes[f"api_{code}"] = r.status_code if r is not None else "failed"

    # 4. also probe the header/company API for NTA-ish fields
    r = fetch(f"{BASE}/asx/1/company/AFI?fields=primary_share", accept_json=True)
    if r is not None and r.status_code == 200:
        (OUT / "api_company_AFI.json").write_text(r.text[:100_000])
        notes["api_company_AFI"] = 200

    (OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
    print("asx probe complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
