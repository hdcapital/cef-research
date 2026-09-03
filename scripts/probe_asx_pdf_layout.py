"""Dump the text and table rows pdfplumber sees in named ASX PDFs.

Cadence Capital (CDM) and Cadence Opportunities (CDO) publish "NTA and
Investment Update" newsletters the Tier-0 harvest opens and reads no NTA
from; the figure sits in a layout the row/text parsers do not surface.
Only a runner has egress to announcements.asx.com.au. Set PDF_URLS
(comma list). Writes reports/build/asx_pdf_layout_probe.json. Evidence only.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, "src")

URLS = [u.strip() for u in os.environ.get("PDF_URLS", "").split(",") if u.strip()]
UA = {"User-Agent": "Mozilla/5.0 (research probe; contact: repo owner)"}


def main() -> int:
    import pdfplumber
    out = []
    s = requests.Session()
    s.headers.update(UA)
    for u in URLS:
        rec = {"url": u}
        try:
            r = s.get(u, timeout=60)
            rec["status"] = r.status_code
            rec["content_type"] = r.headers.get("content-type")
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                rec["head"] = r.text[:300]
                out.append(rec)
                time.sleep(2)
                continue
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                pages = []
                for i, p in enumerate(pdf.pages[:8]):
                    txt = p.extract_text() or ""
                    tables = []
                    try:
                        for tbl in p.extract_tables() or []:
                            tables.append([[str(c) if c is not None else "" for c in row] for row in tbl][:25])
                    except Exception as exc:  # noqa: BLE001
                        tables.append([["table_error", str(exc)]])
                    words = []
                    try:
                        for w in p.extract_words()[:400]:
                            if re.search(r"(?i)nta|tax|\$|\d\.\d", w.get("text", "")):
                                words.append((round(w["x0"]), round(w["top"]), w["text"]))
                    except Exception:  # noqa: BLE001
                        pass
                    pages.append({"page": i + 1, "text": txt[:6000], "tables": tables[:6],
                                  "nta_words": words[:120]})
                rec["pages"] = pages
        except Exception as exc:  # noqa: BLE001
            rec["error"] = str(exc)
        out.append(rec)
        time.sleep(2)
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/asx_pdf_layout_probe.json").write_text(json.dumps(out, indent=1, default=str))
    for r in out:
        print(r.get("url"), r.get("status"), r.get("error"), [len(p["tables"]) for p in r.get("pages", [])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
