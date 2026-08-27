"""Independent number-level NTA validation - recent months, full breadth.

Historical announcement PDFs are gated (legacy endpoint no longer serves
them; the modern API exposes only each company's 5 most recent items
without authentication), so this validates what is openly available: for
every live code, take the most recent NTA-type announcement with a
month-end 'as at' date, fetch its PDF via the public Markit file gateway,
parse the stated NTA per share, and compare with the panel's derived NTA
for that month. Writes outputs/au/au_nta_pdf_check.csv + summary.

Within-source validation (derived vs the report's explicit NTA Price
column: 98.9% exact) covers the historical depth; this check adds an
INDEPENDENT source for the recent cross-section.
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

API = "https://asx.api.markitdigital.com/asx-research/1.0"
FILE_GW = API + "/file/{key}"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5

NTA_HEAD = re.compile(r"\bNTA\b|net tangible|net asset|\bNAV\b", re.I)
ASAT = re.compile(r"as at\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
NTA_PATTERNS = [
    re.compile(r"pre[- ]tax\s+NTA[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\s+(?:per\s+(?:share|unit)\s+)?(?:before|pre)[- ]tax[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"net\s+tangible\s+assets?[^0-9$]{0,100}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\b[^0-9$]{0,50}\$\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NAV\s+per\s+(?:share|unit)[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
]


def main() -> int:
    import pdfplumber

    panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
    panel["code"] = panel["security_id"].str.replace("ASX:", "", regex=False)
    latest_by_code = panel[panel["nta_derived"].notna()].sort_values("obs_month") \
        .groupby("code").tail(3)
    codes = sorted(latest_by_code[latest_by_code["obs_month"] >= "2026-01"]["code"].unique())
    print(f"live codes to check: {len(codes)}")

    s = requests.Session()
    s.headers["User-Agent"] = UA
    rows = []
    last = 0.0

    def throttled_get(url, **kw):
        nonlocal last
        wait = THROTTLE - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        return s.get(url, timeout=60, **kw)

    for code in codes:
        try:
            r = throttled_get(f"{API}/companies/{code.lower()}/announcements?itemsPerPage=5",
                              headers={"Accept": "application/json"})
            items = r.json().get("data", {}).get("items", []) if r.status_code == 200 else []
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "status": f"api_error:{exc}"})
            continue
        cand = None
        for it in items:
            head = it.get("headline", "")
            m = ASAT.search(head)
            if NTA_HEAD.search(head) and m and it.get("documentKey"):
                try:
                    asat = pd.to_datetime(m.group(1), dayfirst=True)
                except Exception:  # noqa: BLE001
                    continue
                # only month-end as-at dates compare cleanly to the panel
                if (asat + pd.Timedelta(days=3)).month != asat.month or True:
                    if asat.day >= 26 or asat == asat + pd.offsets.MonthEnd(0):
                        cand = (it, asat)
                        break
        if cand is None:
            rows.append({"code": code, "status": "no_recent_monthend_nta"})
            continue
        it, asat = cand
        month = str(asat.to_period("M"))
        prow = panel[(panel["code"] == code) & (panel["obs_month"] == month)]
        if prow.empty or pd.isna(prow["nta_derived"].iloc[0]):
            rows.append({"code": code, "status": f"no_panel_month:{month}"})
            continue
        try:
            pr = throttled_get(FILE_GW.format(key=it["documentKey"]))
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "status": f"pdf_error:{exc}"})
            continue
        if pr.status_code != 200 or not pr.content.startswith(b"%PDF"):
            rows.append({"code": code, "status": f"pdf_http_{pr.status_code}"})
            continue
        try:
            with pdfplumber.open(io.BytesIO(pr.content)) as pdf:
                text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pdf.pages[:3]))
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "status": f"pdf_parse:{exc}"})
            continue
        stated = None
        for pat in NTA_PATTERNS:
            m2 = pat.search(text)
            if m2:
                stated = float(m2.group(1))
                break
        derived = float(prow["nta_derived"].iloc[0])
        rec = {"code": code, "month": month, "headline": it.get("headline", "")[:120],
               "derived_nta": round(derived, 4), "stated_nta": stated,
               "status": "parsed" if stated else "no_nta_in_pdf"}
        if stated:
            rec["abs_pct_diff"] = abs(derived / stated - 1)
        rows.append(rec)

    out = pd.DataFrame(rows)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/au/au_nta_pdf_check.csv", index=False)
    ok = out[out["status"] == "parsed"]
    summary = {
        "codes_checked": len(out),
        "pdfs_parsed": int(len(ok)),
        "median_abs_pct_diff": float(ok["abs_pct_diff"].median()) if len(ok) else None,
        "p90_abs_pct_diff": float(ok["abs_pct_diff"].quantile(0.9)) if len(ok) else None,
        "within_2pct": float((ok["abs_pct_diff"] < 0.02).mean()) if len(ok) else None,
        "note": "independent recent-month check; historical depth covered by the "
                "within-source explicit-NTA-column comparison (98.9% exact)",
    }
    Path("outputs/au/au_nta_pdf_check_summary.json").write_text(json.dumps(summary, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
