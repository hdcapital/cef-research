"""Independent number-level NTA validation across the full panel history.

Probe 7 (browser capture of the public announcements page) + probe 8
established two open routes the anonymous public page itself uses:

1. The Markit listing API paginates each company's FULL announcement
   history (AFI: 1,271 items back to 2011) when called with the public
   page's embedded bearer token - the same anonymous handshake a browser
   visitor performs. Unauthenticated calls cap at 5 items.
2. The file gateway serves any announcement PDF by documentKey with no
   authentication at all (verified on a 2011 document).

So: for every LIC code in the panel, page its announcement history,
pick month-end NTA statements (sampled - up to one per code-year, June
or December preferred), fetch the PDF, parse the stated pre-tax NTA per
share, and compare with the panel's derived NTA for that month. This
extends the independent check from "most recent month only" to the whole
2017->2026 history. Throttled ~1.5s; listings and parses are cached under
data/asx_ann_cache so reruns only fetch what is new.

If the page token has rotated (401/403), falls back to the 5-item
unauthenticated listing per code and records that in the summary.

Writes outputs/au/au_nta_pdf_check.csv + au_nta_pdf_check_summary.json.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

API = "https://asx.api.markitdigital.com/asx-research/1.0"
FILE_GW = API + "/file/{key}"
# Public token embedded in www.asx.com.au's announcements page JS bundle,
# captured from the anonymous page's own network traffic (data/probe/asx7).
# Re-run scripts/probe_asx_browser.py to re-capture if it rotates.
PAGE_TOKEN = "83ff96335c2d45a094df02a206a39ff4"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
EARLIEST = "2016-11"          # panel starts 2016-12 (first report 2017-01)
PDF_BUDGET = int(os.environ.get("NTA_PDF_BUDGET", "400"))   # new PDFs per run
LIST_BUDGET = int(os.environ.get("NTA_LIST_BUDGET", "1200"))  # listing calls per run

CACHE = Path("data/asx_ann_cache/markit")
CACHE.mkdir(parents=True, exist_ok=True)
PARSE_CACHE = CACHE / "pdf_parse"
PARSE_CACHE.mkdir(exist_ok=True)

NTA_HEAD = re.compile(r"\bNTA\b|net tangible|net asset|\bNAV\b", re.I)
ASAT = re.compile(r"as at\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
# each pattern captures the number; unit (cents vs $) resolved from context
NTA_PATTERNS = [
    re.compile(r"pre[- ]tax\s+NTA[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\s+(?:per\s+(?:share|unit)\s+)?(?:before|pre)[- ]tax[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"net\s+tangible\s+assets?[^0-9$]{0,100}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NTA\b[^0-9$]{0,50}\$\s*([0-9]+\.[0-9]{2,4})", re.I),
    re.compile(r"NAV\s+per\s+(?:share|unit)[^0-9$]{0,80}\$?\s*([0-9]+\.[0-9]{2,4})", re.I),
]

_last = 0.0


def throttled_get(s: requests.Session, url: str, **kw) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return s.get(url, timeout=60, **kw)


def fetch_listing(s: requests.Session, code: str, counters: dict) -> list[dict]:
    """Full announcement listing for one code, cached page-by-page."""
    xid_f = CACHE / f"{code}_xid.json"
    if xid_f.exists():
        xid = json.loads(xid_f.read_text()).get("xid")
    else:
        if counters["list_calls"] >= LIST_BUDGET:
            return []
        counters["list_calls"] += 1
        r = throttled_get(s, f"{API}/search/predictive?searchText={code}&useBondsLookup=true",
                          headers=_auth_headers())
        xid = None
        if r.status_code == 200:
            for it in (r.json().get("data") or {}).get("items") or []:
                if it.get("symbol") == code:
                    xid = it.get("xidEntity")
                    break
            xid_f.write_text(json.dumps({"xid": xid}))
        elif r.status_code in (401, 403):
            counters["token_rejected"] = True
            return _fallback_listing(s, code, counters)
    if xid is None:
        return []

    items: list[dict] = []
    page = 0
    while True:
        pf = CACHE / f"{code}_p{page}.json"
        if pf.exists():
            got = json.loads(pf.read_text())
        else:
            if counters["list_calls"] >= LIST_BUDGET:
                counters["list_budget_hit"] = True
                break
            counters["list_calls"] += 1
            r = throttled_get(
                s, f"{API}/markets/announcements?entityXids={xid}&page={page}&itemsPerPage=100",
                headers=_auth_headers())
            if r.status_code in (401, 403):
                counters["token_rejected"] = True
                return items or _fallback_listing(s, code, counters)
            if r.status_code != 200:
                break
            got = [{k: it.get(k) for k in ("date", "documentKey", "headline")}
                   for it in ((r.json().get("data") or {}).get("items") or [])]
            # cache a page only once it is complete (100 items) or clearly
            # final; the newest partial page must refresh on later runs
            if len(got) == 100 or (got and got[-1].get("date", "")[:7] < EARLIEST):
                pf.write_text(json.dumps(got))
        items.extend(got)
        if len(got) < 100 or (got and got[-1].get("date", "")[:7] < EARLIEST):
            break
        page += 1
    return items


def _auth_headers() -> dict:
    return {"Accept": "application/json", "Authorization": f"Bearer {PAGE_TOKEN}",
            "Referer": "https://www.asx.com.au/", "Origin": "https://www.asx.com.au"}


def _fallback_listing(s: requests.Session, code: str, counters: dict) -> list[dict]:
    """Unauthenticated 5-item listing - the pre-probe-7 behavior."""
    if counters["list_calls"] >= LIST_BUDGET:
        return []
    counters["list_calls"] += 1
    r = throttled_get(s, f"{API}/companies/{code.lower()}/announcements?itemsPerPage=5",
                      headers={"Accept": "application/json"})
    if r.status_code != 200:
        return []
    return [{k: it.get(k) for k in ("date", "documentKey", "headline")}
            for it in ((r.json().get("data") or {}).get("items") or [])]


def pick_candidates(items: list[dict]) -> dict[str, dict]:
    """Month-end NTA announcements keyed by panel month, one per month."""
    out: dict[str, dict] = {}
    for it in items:
        head = it.get("headline") or ""
        m = ASAT.search(head)
        if not (NTA_HEAD.search(head) and m and it.get("documentKey")):
            continue
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            continue
        if asat.day < 24:      # only month-end statements compare to the panel
            continue
        month = str(asat.to_period("M"))
        if month not in out:   # keep first (most recent release) per month
            out[month] = {"documentKey": it["documentKey"], "headline": head,
                          "asat_month": month}
    return out


def sample_months(months: list[str]) -> list[str]:
    """Up to one month per calendar year, preferring June then December."""
    by_year: dict[str, list[str]] = {}
    for m in months:
        by_year.setdefault(m[:4], []).append(m)
    picked = []
    for _, ms in sorted(by_year.items()):
        ms = sorted(ms)
        pref = [m for m in ms if m.endswith("-06")] or [m for m in ms if m.endswith("-12")] or ms
        picked.append(pref[0])
    return picked


def parse_pdf(s: requests.Session, key: str, counters: dict) -> dict:
    """Fetch + parse one NTA PDF (cached by documentKey)."""
    import pdfplumber

    cf = PARSE_CACHE / f"{key}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    if counters["pdf_calls"] >= PDF_BUDGET:
        counters["pdf_budget_hit"] = True
        return {"status": "budget_deferred"}
    counters["pdf_calls"] += 1
    try:
        r = throttled_get(s, FILE_GW.format(key=key))
    except Exception as exc:  # noqa: BLE001
        return {"status": f"pdf_error:{exc}"}       # transient: not cached
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        res = {"status": f"pdf_http_{r.status_code}"}
        cf.write_text(json.dumps(res))
        return res
    try:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pdf.pages[:3]))
    except Exception as exc:  # noqa: BLE001
        res = {"status": f"pdf_parse:{exc}"}
        cf.write_text(json.dumps(res))
        return res
    stated = None
    unit = "dollars"
    for pat in NTA_PATTERNS:
        m = pat.search(text)
        if m:
            stated = float(m.group(1))
            tail = text[m.end():m.end() + 15]
            if re.match(r"\s*(?:cents|cps|c\b)", tail, re.I):
                unit = "cents"
            break
    res = {"status": "parsed" if stated is not None else "no_nta_in_pdf",
           "stated_raw": stated, "unit": unit}
    cf.write_text(json.dumps(res))
    return res


def main() -> int:
    panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
    panel["code"] = panel["security_id"].str.replace("ASX:", "", regex=False)
    have_nta = panel[panel["nta_derived"].notna()]
    counts = have_nta.groupby("code")["obs_month"].nunique()
    codes = sorted(counts[counts >= 6].index)
    print(f"codes with >=6 NTA months: {len(codes)}")

    s = requests.Session()
    s.headers["User-Agent"] = UA
    counters = {"list_calls": 0, "pdf_calls": 0}
    rows = []

    for code in codes:
        try:
            items = fetch_listing(s, code, counters)
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "status": f"listing_error:{exc}"})
            continue
        if not items:
            rows.append({"code": code, "status": "no_listing"})
            continue
        cands = pick_candidates(items)
        panel_months = set(have_nta.loc[have_nta["code"] == code, "obs_month"].astype(str))
        usable = sorted(m for m in cands if m in panel_months)
        if not usable:
            rows.append({"code": code, "status": "no_monthend_nta_in_history",
                         "listing_items": len(items)})
            continue
        for month in sample_months(usable):
            cand = cands[month]
            res = parse_pdf(s, cand["documentKey"], counters)
            prow = have_nta[(have_nta["code"] == code) & (have_nta["obs_month"] == month)]
            derived = float(prow["nta_derived"].iloc[0]) if len(prow) else None
            rec = {"code": code, "month": month, "headline": cand["headline"][:120],
                   "derived_nta": round(derived, 4) if derived is not None else None,
                   "status": res.get("status")}
            stated = res.get("stated_raw")
            if stated is not None and derived is not None:
                unit = res.get("unit", "dollars")
                stated_dollars = stated / 100.0 if unit == "cents" else stated
                rec["stated_nta"] = stated_dollars
                rec["stated_unit"] = unit
                diff = abs(derived / stated_dollars - 1)
                # a ~100x gap in either direction is a units statement the
                # text did not resolve, not a numeric disagreement - flag,
                # never silently correct
                if diff > 0.5 and (abs(derived / (stated / 100.0) - 1) < 0.05
                                   or abs(derived / (stated * 100.0) - 1) < 0.05):
                    rec["status"] = "unit_ambiguous"
                else:
                    rec["abs_pct_diff"] = diff
            rows.append(rec)

    out = pd.DataFrame(rows)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/au/au_nta_pdf_check.csv", index=False)
    ok = out[(out["status"] == "parsed") & out.get("abs_pct_diff", pd.Series(dtype=float)).notna()] \
        if "abs_pct_diff" in out.columns else out.iloc[0:0]
    summary = {
        "codes_targeted": len(codes),
        "comparisons_parsed": int(len(ok)),
        "months_covered": sorted(ok["month"].str[:4].unique().tolist()) if len(ok) else [],
        "median_abs_pct_diff": float(ok["abs_pct_diff"].median()) if len(ok) else None,
        "p90_abs_pct_diff": float(ok["abs_pct_diff"].quantile(0.9)) if len(ok) else None,
        "within_1pct": float((ok["abs_pct_diff"] < 0.01).mean()) if len(ok) else None,
        "within_2pct": float((ok["abs_pct_diff"] < 0.02).mean()) if len(ok) else None,
        "listing_calls": counters["list_calls"],
        "pdf_fetches": counters["pdf_calls"],
        "list_budget_hit": counters.get("list_budget_hit", False),
        "pdf_budget_hit": counters.get("pdf_budget_hit", False),
        "token_rejected": counters.get("token_rejected", False),
        "note": "historical sample: up to one month-end NTA PDF per code per "
                "year, compared with panel-derived NTA; announcement listing "
                "via the public page's own anonymous API handshake (probe 7/8)",
    }
    Path("outputs/au/au_nta_pdf_check_summary.json").write_text(json.dumps(summary, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
