"""Probe 3: download representative samples of each AIC archive file family.

Driven by the real archive manifest (data-archive.json) captured by probe 2.
Samples span 2007..latest so parsers can handle format drift. Results are
committed under data/probe/samples/ for offline parser development.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import requests

BASE = "https://www.theaic.co.uk"
MANIFEST_URL = BASE + "/sites/default/files/data-archive/data-archive.json"
OUT = Path("data/probe")
SAMPLES = OUT / "samples"
UA = (
    "uk-cef-research/0.1 (open-source academic backtest research; "
    "contact: danielconorsims@gmail.com; respects robots.txt; ~1 req/s)"
)
THROTTLE = 1.2

session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    r = session.get(url, timeout=120)
    print(f"GET {url} -> {r.status_code} ({len(r.content)} bytes)")
    return r


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    manifest = fetch(MANIFEST_URL).json()
    (OUT / "data-archive.json").write_text(json.dumps(manifest, indent=1))

    # entries keyed by (type, year, month)
    by_type: dict[str, list[dict]] = {}
    for e in manifest:
        by_type.setdefault(e["type"], []).append(e)
    for t, entries in by_type.items():
        entries.sort(key=lambda e: e["datestamp"])

    picks: list[str] = []

    def add_entry_files(entry: dict, limit: int | None = None) -> None:
        files = entry.get("files", []) + entry.get("postFiles", [])
        for f in files[:limit]:
            picks.append(f)

    # MIR (type 109): all component CSVs for 2007-01 and latest; MIR only
    # for a few mid-sample years to observe schema drift.
    mir = by_type.get("109", [])
    if mir:
        add_entry_files(mir[0])
        add_entry_files(mir[-1])
        for year in ("2010", "2014", "2018", "2022"):
            for e in mir:
                if e["year"] == year and e["month"] == "June":
                    add_entry_files(e, limit=2)
                    break

    # Keyfacts (type 108): first + latest + two mid samples (all files -
    # includes the company-universe XLS)
    kf = by_type.get("108", [])
    if kf:
        add_entry_files(kf[0])
        add_entry_files(kf[-1])
        for year in ("2013", "2019"):
            for e in kf:
                if e["year"] == year and e["month"] == "June":
                    add_entry_files(e)
                    break

    # AIC Stats PDF (type 106): first + latest
    st = by_type.get("106", [])
    if st:
        add_entry_files(st[0])
        add_entry_files(st[-1])

    # Corporate Activity (type 107): first, middle, latest
    ca = by_type.get("107", [])
    if ca:
        add_entry_files(ca[0])
        add_entry_files(ca[len(ca) // 2])
        add_entry_files(ca[-1])

    notes = []
    seen = set()
    for path in picks:
        if path in seen:
            continue
        seen.add(path)
        url = path if path.startswith("http") else BASE + path
        try:
            r = fetch(url)
        except Exception as exc:  # noqa: BLE001
            notes.append({"url": url, "error": str(exc)})
            continue
        name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
        if r.status_code == 200:
            (SAMPLES / name).write_bytes(r.content)
        notes.append(
            {
                "url": url,
                "status": r.status_code,
                "bytes": len(r.content),
                "content_type": r.headers.get("Content-Type"),
                "saved_as": name if r.status_code == 200 else None,
            }
        )

    (OUT / "probe3_notes.json").write_text(json.dumps(notes, indent=1))
    print(f"probe3 complete: {len(seen)} files attempted")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
