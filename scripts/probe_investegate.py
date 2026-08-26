"""Probe 2: map Investegate's archive/date/category URL space so the
dividend crawler fetches the minimum necessary.

Questions:
1. /announcement-archive - what params exist (date? category? company?)
2. do date-filtered URLs work, and how far back?
3. are DEAD trusts reachable: /company/<TICKER> for ADIG (wound down 2024),
   SCIN (merged 2022); slug forms /company/<slug>--<ticker>;
4. how deep does company pagination go (CTY?page=60 ~ 2007 era)?

Budget ~14 requests at 1.5s.
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
OUT = Path("data/probe/investegate2")
UA = "uk-cef-research/0.1 (academic CEF dividend research; contact: danielconorsims@gmail.com; ~1 req/1.5s)"
THROTTLE = 1.5
session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str):
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


def describe(label: str, r, notes: dict, save_html: bool = True) -> None:
    if r is None:
        notes[label] = "failed"
        return
    notes[label] = r.status_code
    if save_html and r.status_code == 200:
        (OUT / f"{label}.html").write_bytes(r.content)
    if r.status_code != 200:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    anns = [
        {"href": urljoin(BASE, a.get("href", "")), "text": a.get_text(" ", strip=True)[:100]}
        for a in soup.select('a[href*="/announcement/"]')
    ]
    notes[f"{label}_n_announcements"] = len(anns)
    notes[f"{label}_first"] = anns[:4]
    notes[f"{label}_last"] = anns[-3:]
    # date strings visible on page (to date the content)
    dates = re.findall(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
                       soup.get_text(" ", strip=True))
    notes[f"{label}_dates_seen"] = sorted(set(dates))[:6]
    # forms & interesting query links
    notes[f"{label}_forms"] = [
        {"action": f.get("action"), "inputs": [i.get("name") for i in f.find_all(["input", "select"])][:12]}
        for f in soup.find_all("form")
    ][:4]
    qlinks = sorted({a["href"] for a in soup.find_all("a", href=True)
                     if ("?" in a["href"] or "archive" in a["href"].lower())
                     and "/announcement/" not in a["href"]})[:25]
    notes[f"{label}_query_links"] = qlinks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {}

    describe("archive_root", fetch(BASE + "/announcement-archive"), notes)

    # guess date-filter forms based on what the root shows; try common patterns
    for label, path in [
        ("archive_date_q", "/announcement-archive?date=2015-06-15"),
        ("archive_date_path", "/announcement-archive/2015-06-15"),
    ]:
        describe(label, fetch(BASE + path), notes, save_html=False)

    for label, path in [
        ("dead_ADIG", "/company/ADIG"),
        ("dead_SCIN", "/company/SCIN"),
        ("slug_CTY", "/company/city-of-london-inv-trust--cty"),
        ("slug_ADIG", "/company/aberdeen-diversified-income-and-growth-trust--adig"),
    ]:
        describe(label, fetch(BASE + path), notes, save_html=(label != "slug_CTY"))

    # pagination depth on a live trust
    for label, path in [
        ("cty_p30", "/company/CTY?page=30"),
        ("cty_p60", "/company/CTY?page=60"),
        ("cty_p120", "/company/CTY?page=120"),
    ]:
        describe(label, fetch(BASE + path), notes, save_html=False)

    (OUT / "notes.json").write_text(json.dumps(notes, indent=1))
    print("probe2 complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
