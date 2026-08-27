"""Sampled numeric validation of derived NTAs against announcement PDFs.

Picks ~400 random (fund, month) NTA announcements from the crawled listing
archive, fetches each PDF, parses the stated NTA per share (pre-tax
preferred), and compares against the panel's derived NTA
(price / (1 + published discount)). Writes outputs/au/au_nta_pdf_check.csv
with per-observation differences and a summary.

Run in CI (ASX egress). ~400 fetches at 1.5s ~= 12 min.
"""

from __future__ import annotations

import io
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

PDF_URL = "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
N_SAMPLE = 400
THROTTLE = 1.5

# NTA-per-share phrasings observed in LIC statements
NTA_PATTERNS = [
    re.compile(r"pre[- ]tax\s+NTA[^0-9$]{0,60}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\s+(?:per\s+share\s+)?(?:before|pre)[- ]tax[^0-9$]{0,60}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"net\s+tangible\s+assets?\s+(?:per\s+(?:share|unit))?[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\b[^0-9$]{0,40}\$\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NAV\s+per\s+(?:share|unit)[^0-9$]{0,60}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
]


def main() -> int:
    ann = pd.read_csv("data/asx_ann_cache/announcements.csv")
    panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
    panel["code"] = panel["security_id"].str.replace("ASX:", "", regex=False)

    nta_ann = ann[ann["headline"].str.contains(
        r"\bNTA\b|net tangible|net asset", case=False, na=True, regex=True)].copy()
    nta_ann = nta_ann[nta_ann["asat_date"].notna() & nta_ann["pdf_id"].notna()]
    nta_ann["asat_month"] = pd.to_datetime(nta_ann["asat_date"]).dt.to_period("M").astype(str)
    joined = nta_ann.merge(
        panel[["code", "obs_month", "nta_derived", "share_price", "discount"]],
        left_on=["code", "asat_month"], right_on=["code", "obs_month"], how="inner")
    joined = joined[joined["nta_derived"].notna()]
    print(f"candidate (announcement, panel-month) pairs: {len(joined)}")
    random.seed(7)
    sample = joined.sample(n=min(N_SAMPLE, len(joined)), random_state=7)

    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber required")
        return 1

    session = requests.Session()
    session.headers["User-Agent"] = UA
    rows = []
    last = 0.0
    for _, r in sample.iterrows():
        wait = THROTTLE - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        try:
            resp = session.get(PDF_URL, params={"display": "pdf", "idsId": int(r["pdf_id"])},
                               timeout=60)
        except Exception as exc:  # noqa: BLE001
            rows.append({**r[["code", "asat_month"]].to_dict(), "status": f"fetch_error:{exc}"})
            continue
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            rows.append({**r[["code", "asat_month"]].to_dict(), "status": f"http_{resp.status_code}"})
            continue
        try:
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = " ".join((p.extract_text() or "") for p in pdf.pages[:3])
            text = re.sub(r"\s+", " ", text)
        except Exception as exc:  # noqa: BLE001
            rows.append({**r[["code", "asat_month"]].to_dict(), "status": f"pdf_error:{exc}"})
            continue
        stated = None
        for pat in NTA_PATTERNS:
            m = pat.search(text)
            if m:
                stated = float(m.group(1))
                break
        rec = {"code": r["code"], "asat_month": r["asat_month"],
               "derived_nta": round(float(r["nta_derived"]), 4),
               "stated_nta": stated,
               "status": "parsed" if stated else "no_nta_found"}
        if stated:
            rec["abs_pct_diff"] = abs(rec["derived_nta"] / stated - 1)
        rows.append(rec)

    out = pd.DataFrame(rows)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/au/au_nta_pdf_check.csv", index=False)
    ok = out[out["status"] == "parsed"]
    summary = {
        "sampled": len(out),
        "pdfs_parsed": int(len(ok)),
        "median_abs_pct_diff": float(ok["abs_pct_diff"].median()) if len(ok) else None,
        "p90_abs_pct_diff": float(ok["abs_pct_diff"].quantile(0.9)) if len(ok) else None,
        "within_2pct": float((ok["abs_pct_diff"] < 0.02).mean()) if len(ok) else None,
    }
    Path("outputs/au/au_nta_pdf_check_summary.json").write_text(json.dumps(summary, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
