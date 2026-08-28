"""One-shot corpus capture: the newest NAV announcement page for EVERY UK
NAV publisher, full text, committed to the repo - so parser iteration
happens offline against real RNS text instead of one guess per CI cycle.
~150 throttled fetches, ~8 minutes.
"""
import gzip
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests
from bs4 import BeautifulSoup

from cef_live import harvest_nav as H

CACHE = Path("data/investegate_cache")
OUT = Path("data/uk_nav_corpus.json.gz")

census = H.uk_frequency_census(CACHE)
s = requests.Session()
s.headers["User-Agent"] = H.P.UA
pat = re.compile(r"net asset value", re.I)
corpus = []
for tk in census["ticker"]:
    time.sleep(1.5)
    try:
        r = s.get(f"https://www.investegate.co.uk/company/{tk}", timeout=45)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:  # noqa: BLE001
        continue
    best = None
    for tr in soup.select("table.table-investegate tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[3].find("a", href=True)
        if a is None or "/announcement/" not in a.get("href", ""):
            continue
        if not pat.search(a.get_text(" ", strip=True)):
            continue
        href = a["href"]
        best = {"date": tds[0].get_text(" ", strip=True),
                "url": ("https://www.investegate.co.uk" + href)
                if href.startswith("/") else href,
                "headline": a.get_text(" ", strip=True)}
        break
    if best is None:
        continue
    time.sleep(1.5)
    try:
        r = s.get(best["url"], timeout=45)
        text = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" "))
        corpus.append({"ticker": tk, **best, "text": text[:20000]})
    except Exception:  # noqa: BLE001
        continue
    if len(corpus) % 25 == 0:
        print(f"{len(corpus)} pages captured")

OUT.write_bytes(gzip.compress(json.dumps(corpus).encode()))
print(f"corpus: {len(corpus)} pages -> {OUT} ({OUT.stat().st_size//1024} KB)")
