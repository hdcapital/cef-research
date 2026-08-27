"""ASX probe 2: announcement API shape + monthly report XLSX structure.

1. The announcements UI calls asx.api.markitdigital.com/asx-research/1.0 -
   establish working endpoint paths/params for per-company announcements
   (live LIC AFI; WAM; a delisted LIC candidate), date-range depth, and
   whether PDFs are fetchable.
2. Download three monthly investment-products XLSX vintages (2017-01,
   2021-06, 2026-04) and dump their sheet names + header rows so the
   LIC/LIT universe parser is written against reality.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

OUT = Path("data/probe/asx2")
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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    notes: dict = {}

    # ---- announcement API candidates
    candidates = [
        ("ann_afi", "https://asx.api.markitdigital.com/asx-research/1.0/companies/afi/announcements?itemsPerPage=25&page=0"),
        ("ann_afi_dates", "https://asx.api.markitdigital.com/asx-research/1.0/companies/afi/announcements?itemsPerPage=25&fromDate=2017-01-01&toDate=2017-12-31"),
        ("ann_wam", "https://asx.api.markitdigital.com/asx-research/1.0/companies/wam/announcements?itemsPerPage=10"),
        ("about_afi", "https://asx.api.markitdigital.com/asx-research/1.0/companies/afi/about"),
        ("ann_dead_alf", "https://asx.api.markitdigital.com/asx-research/1.0/companies/alf/announcements?itemsPerPage=10"),
        ("key_stats_afi", "https://asx.api.markitdigital.com/asx-research/1.0/companies/afi/key-statistics"),
        ("legacy_ann", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=AFI&timeframe=Y&year=2020"),
    ]
    for label, url in candidates:
        r = fetch(url, headers={"Accept": "application/json"})
        if r is None:
            notes[label] = "failed"
            continue
        notes[label] = r.status_code
        body = r.content[:250_000]
        (OUT / f"{label}.txt").write_bytes(body)
        if r.status_code == 200:
            try:
                d = r.json()
                notes[f"{label}_keys"] = list(d)[:8]
                items = d.get("data", {})
                if isinstance(items, dict):
                    notes[f"{label}_data_keys"] = list(items)[:10]
                    rows = items.get("items") or items.get("announcements") or []
                    notes[f"{label}_n"] = len(rows)
                    notes[f"{label}_sample"] = rows[:2]
            except Exception as exc:  # noqa: BLE001
                notes[f"{label}_note"] = f"not json: {exc}"

    # try fetching one announcement PDF if a sample gave us a document key
    # (inspected offline; deferred to the crawler design)

    # ---- monthly report samples
    base = "https://www.asx.com.au/content/dam/asx/issuers/asx-investment-products-reports"
    samples = [
        ("2017/excel/asx-investment-products-january-2017.xlsx", "ipr_2017_01.xlsx"),
        ("2021/excel/asx-investment-products-june-2021.xlsx", "ipr_2021_06.xlsx"),
        ("2026/excel/asx-investment-products-april-2026.xlsx", "ipr_2026_04.xlsx"),
    ]
    import openpyxl

    for path, name in samples:
        r = fetch(f"{base}/{path}")
        if r is None or r.status_code != 200:
            notes[f"report_{name}"] = r.status_code if r is not None else "failed"
            continue
        (OUT / name).write_bytes(r.content)
        notes[f"report_{name}"] = len(r.content)
        try:
            wb = openpyxl.load_workbook(OUT / name, read_only=True)
            info = {}
            for ws in wb.worksheets:
                rows = []
                for i, row in enumerate(ws.iter_rows(max_row=8, values_only=True)):
                    rows.append([str(c)[:28] for c in row if c is not None][:12])
                info[ws.title] = {"dims": ws.calculate_dimension(), "head": rows}
            (OUT / f"{name}.structure.json").write_text(json.dumps(info, indent=1, default=str))
            notes[f"{name}_sheets"] = list(info)
        except Exception as exc:  # noqa: BLE001
            notes[f"{name}_error"] = str(exc)

    (OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
    print("asx probe 2 complete")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
