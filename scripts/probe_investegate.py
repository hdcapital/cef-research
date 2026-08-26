"""Probe: verify Investegate's suitability as a dividend/catalyst source.

Checks (results committed under data/probe/investegate/):
1. robots.txt - what are we allowed to crawl?
2. company page for a LIVE trust (CTY, City of London) - structure, filters,
   pagination, how far back the archive goes.
3. company page for a DEAD trust (PLI, Perpetual Income & Growth, delisted
   2020) - do archives persist after delisting?
4. one dividend-declaration detail page - is the amount/ex-date/pay-date
   parseable?

Gentle: ~1.5s throttle, <15 requests total.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.investegate.co.uk"
OUT = Path("data/probe/investegate")
UA = "uk-cef-research/0.1 (academic CEF dividend research; contact: danielconorsims@gmail.com; ~1 req/1.5s)"
THROTTLE = 1.5

session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str) -> requests.Response | None:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        r = session.get(url, timeout=45)
        print(f"GET {url} -> {r.status_code} ({len(r.content)}b)")
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"GET {url} FAILED: {exc}")
        return None


def save(name: str, content: bytes) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(content)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {}

    r = fetch(BASE + "/robots.txt")
    if r is not None:
        save("robots.txt", r.content)
        notes["robots_status"] = r.status_code

    # live trust company page + a page-2 if discoverable
    for label, path in [
        ("company_CTY", "/company/CTY"),
        ("company_PLI_dead", "/company/PLI"),
        ("company_AIC", "/company/AIC"),
    ]:
        r = fetch(BASE + path)
        if r is None:
            notes[label] = "failed"
            continue
        notes[label] = r.status_code
        save(f"{label}.html", r.content)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.content, "html.parser")
        anns = []
        for a in soup.select('a[href*="/announcement/"]'):
            anns.append({"href": urljoin(BASE, a.get("href", "")),
                         "text": a.get_text(" ", strip=True)[:120]})
        notes[f"{label}_announcements_on_page"] = len(anns)
        notes[f"{label}_sample"] = anns[:12]
        # look for pagination / date filters / year links
        pag = [a.get("href") for a in soup.find_all("a", href=True)
               if re.search(r"page=|/\d{4}($|/)|older|next|archive", a["href"], re.I)][:15]
        notes[f"{label}_pagination_hints"] = pag
        forms = [{ "action": f.get("action"), "inputs": [i.get("name") for i in f.find_all(["input","select"])][:10]}
                 for f in soup.find_all("form")][:5]
        notes[f"{label}_forms"] = forms
        # find a dividend-looking announcement to fetch
        if label == "company_CTY":
            div_link = next((a["href"] for a in anns if re.search(r"dividend", a["text"], re.I)), None)
            notes["dividend_link_found"] = div_link
            if div_link:
                rd = fetch(div_link)
                if rd is not None and rd.status_code == 200:
                    save("dividend_detail.html", rd.content)
                    text = BeautifulSoup(rd.content, "html.parser").get_text(" ", strip=True)
                    m = re.search(r"(dividend[^.]{0,300}?(\d+\.?\d*)\s*(p|pence)[^.]{0,300}?\.)", text, re.I)
                    notes["dividend_regex_sample"] = m.group(0)[:400] if m else text[:400]

    (OUT / "notes.json").write_text(json.dumps(notes, indent=1))
    print("probe complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
