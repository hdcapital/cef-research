"""ASX probe 3: dividends endpoint, pagination, PDF pattern, modern XLSX.

1. Does the research API expose structured dividends?
   /companies/afi/dividends and /dividends/history variants.
2. Announcement pagination: which params actually page (itemsPerPage vs
   count vs pageSize; page vs pageNumber) - need full history depth (AFI
   has ~20+ years of announcements; we need back to 2016).
3. Announcement PDF URL pattern from a documentKey.
4. Correct modern report vintages: 2021 'jun-2021-abs' and 2026
   'apr-2026-abs' sheet structures.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path("data/probe/asx3")
API = "https://asx.api.markitdigital.com/asx-research/1.0"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
session = requests.Session()
session.headers["User-Agent"] = UA
_last = 0.0


def fetch(url: str, **kw):
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        r = session.get(url, timeout=60, **kw)
        print(f"GET {url} -> {r.status_code} ({len(r.content)}b)")
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"GET {url} FAILED: {exc}")
        return None


def jprobe(label: str, url: str, notes: dict) -> None:
    r = fetch(url, headers={"Accept": "application/json"})
    if r is None:
        notes[label] = "failed"
        return
    notes[label] = r.status_code
    (OUT / f"{label}.txt").write_bytes(r.content[:300_000])
    if r.status_code == 200:
        try:
            d = r.json()
            data = d.get("data", {})
            notes[f"{label}_data_keys"] = list(data)[:12] if isinstance(data, dict) else f"list[{len(data)}]"
            items = data.get("items") if isinstance(data, dict) else data
            if isinstance(items, list):
                notes[f"{label}_n"] = len(items)
                notes[f"{label}_first"] = items[:1]
                notes[f"{label}_last"] = items[-1:]
        except Exception as exc:  # noqa: BLE001
            notes[f"{label}_note"] = str(exc)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {}

    jprobe("div_afi", f"{API}/companies/afi/dividends", notes)
    jprobe("div_hist_afi", f"{API}/companies/afi/dividends/history?years=20", notes)
    jprobe("ann_p200", f"{API}/companies/afi/announcements?itemsPerPage=200", notes)
    jprobe("ann_count", f"{API}/companies/afi/announcements?count=50", notes)
    jprobe("ann_page2", f"{API}/companies/afi/announcements?itemsPerPage=50&page=2", notes)
    jprobe("ann_pagenum", f"{API}/companies/afi/announcements?pageSize=50&pageNumber=2", notes)
    jprobe("ann_types", f"{API}/companies/afi/announcements?itemsPerPage=50&announcementTypes=DISTRIBUTION%20ANNOUNCEMENT", notes)

    # PDF pattern from a known documentKey (2924-03126593-3A699922)
    for label, url in [
        ("pdf_display", "https://announcements.asx.com.au/asxpdf/20260826/pdf/3A699922.pdf"),
        ("pdf_api", f"{API}/announcements/2924-03126593-3A699922/document"),
    ]:
        r = fetch(url)
        if r is not None:
            notes[label] = {"status": r.status_code,
                            "type": r.headers.get("Content-Type", "")[:40],
                            "bytes": len(r.content)}
            if r.status_code == 200 and "pdf" in r.headers.get("Content-Type", ""):
                (OUT / f"{label}.pdf").write_bytes(r.content[:400_000])

    # modern report vintages (exact hrefs from the page)
    import openpyxl

    base = "https://www.asx.com.au/content/dam/asx/issuers/asx-investment-products-reports"
    for path, name in [
        ("2021/excel/asx-investment-products-jun-2021-abs.xlsx", "ipr_2021_06.xlsx"),
        ("2026/excel/asx-investment-products-apr-2026-abs.xlsx", "ipr_2026_04.xlsx"),
    ]:
        r = fetch(f"{base}/{path}")
        if r is None or r.status_code != 200:
            notes[f"report_{name}"] = r.status_code if r is not None else "failed"
            continue
        (OUT / name).write_bytes(r.content)
        wb = openpyxl.load_workbook(OUT / name, read_only=True)
        info = {}
        for ws in wb.worksheets:
            head = []
            for i, row in enumerate(ws.iter_rows(max_row=8, values_only=True)):
                head.append([str(c)[:26] for c in row if c is not None][:12])
            info[ws.title] = head
        (OUT / f"{name}.structure.json").write_text(json.dumps(info, indent=1, default=str))
        notes[f"{name}_sheets"] = list(info)

    (OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
    print("asx probe 3 complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
