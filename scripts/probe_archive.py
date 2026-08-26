"""Probe 2: locate and query the AIC data-archive React app's API.

The data-archive page mounts a React app (/modules/custom/aic_misc/react-da/)
into #root. This probe downloads the app bundle, extracts candidate API
endpoint strings, queries the promising ones, and saves the JSON so the
real discovery module can be written against the actual API schema.

Gentle by design: ~1 req/s, small budget, robots.txt respected (the probe-1
run confirmed /research-tools/ and /modules/ are not disallowed).
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

BASE = "https://www.theaic.co.uk"
ARCHIVE_PAGE = BASE + "/research-tools/data-archive"
OUT = Path("data/probe")
UA = (
    "uk-cef-research/0.1 (open-source academic backtest research; "
    "contact: danielconorsims@gmail.com; respects robots.txt; ~1 req/s)"
)
THROTTLE = 1.2

session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str, **kw) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    r = session.get(url, timeout=60, **kw)
    print(f"GET {url} -> {r.status_code} ({len(r.content)} bytes, {r.headers.get('Content-Type')})")
    return r


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {"bundle": None, "candidates": [], "responses": []}

    # 1. find the current react-da bundle name from the page (hash changes)
    page = fetch(ARCHIVE_PAGE)
    m = re.search(r'src="(/modules/custom/aic_misc/react-da/[^"]+\.js[^"]*)"', page.text)
    if not m:
        print("react-da bundle not found on page")
        (OUT / "probe2_notes.json").write_text(json.dumps(notes, indent=2))
        return 0
    bundle_url = BASE + m.group(1).replace("&amp;", "&")
    notes["bundle"] = bundle_url

    js = fetch(bundle_url)
    (OUT / "react_da_bundle.js").write_bytes(js.content)

    text = js.text
    # 2. extract candidate endpoints: quoted strings with a slash
    strings = set(re.findall(r'["\'`](/[A-Za-z0-9_\-./?=&{}$:%]{2,120})["\'`]', text))
    strings |= set(re.findall(r'["\'`](https?://[A-Za-z0-9_\-./?=&{}$:%]{2,160})["\'`]', text))
    interesting = sorted(
        s
        for s in strings
        if re.search(r"api|json|archive|file|download|node|views|search", s, re.I)
    )
    notes["candidates"] = interesting
    (OUT / "bundle_strings.txt").write_text("\n".join(sorted(strings)))
    print(f"{len(strings)} strings, {len(interesting)} interesting")

    # 3. query the most promising candidates (GET only, small budget)
    (OUT / "api").mkdir(exist_ok=True)
    budget = 8
    for cand in interesting:
        if budget <= 0:
            break
        if "{" in cand or "$" in cand:
            continue  # templated - needs params we don't know yet
        url = cand if cand.startswith("http") else BASE + cand
        host = urllib.parse.urlparse(url).netloc
        if not host.endswith("theaic.co.uk"):
            continue
        try:
            r = fetch(url, headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001
            notes["responses"].append({"url": url, "error": str(exc)})
            continue
        budget -= 1
        slug = re.sub(r"\W+", "_", cand)[:80].strip("_")
        body = r.content[:400_000]
        (OUT / "api" / f"{slug}.txt").write_bytes(body)
        notes["responses"].append(
            {
                "url": url,
                "status": r.status_code,
                "content_type": r.headers.get("Content-Type"),
                "bytes": len(r.content),
                "saved_as": f"api/{slug}.txt",
            }
        )

    (OUT / "probe2_notes.json").write_text(json.dumps(notes, indent=2))
    print("probe2 complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
