"""ASX probe 4: why do some funds' announcement listings come back nearly
empty? Fetch raw announcements.do pages for a low-coverage LIT (MXT), a
high-volume LIC (WMI), and AFI as control; test pagination params; save
raw HTML for offline parser forensics."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path("data/probe/asx4")
LIST_URL = "https://www.asx.com.au/asx/v2/statistics/announcements.do"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(params: dict):
    global _last
    wait = 1.5 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    r = session.get(LIST_URL, params=params, timeout=60)
    print("GET", r.url, "->", r.status_code, len(r.content))
    return r


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes = {}
    cases = [
        ("mxt_2023", {"by": "asxCode", "asxCode": "MXT", "timeframe": "Y", "year": 2023}),
        ("wmi_2023", {"by": "asxCode", "asxCode": "WMI", "timeframe": "Y", "year": 2023}),
        ("afi_2023", {"by": "asxCode", "asxCode": "AFI", "timeframe": "Y", "year": 2023}),
        ("mxt_2023_p2", {"by": "asxCode", "asxCode": "MXT", "timeframe": "Y", "year": 2023, "page": 2}),
    ]
    for label, params in cases:
        r = fetch(params)
        notes[label] = r.status_code
        (OUT / f"{label}.html").write_bytes(r.content[:900_000])
        import re
        trs = len(re.findall(r"<tr", r.text))
        pdfs = len(re.findall(r"displayAnnouncement", r.text))
        notes[f"{label}_trs"] = trs
        notes[f"{label}_pdf_links"] = pdfs
        nav = sorted(set(re.findall(r'href="([^"]*announcements\.do[^"]*)"', r.text)))[:8]
        notes[f"{label}_nav_links"] = nav
    (OUT / "notes.json").write_text(json.dumps(notes, indent=1))
    print("probe 4 complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
