"""Independent number-level NTA validation across the full panel history.

Probes 7/8 (browser capture of the public announcements page + follow-up
endpoint verification) established that www.asx.com.au/asx/1/announcement/list
serves the ENTIRE market's announcement history unauthenticated - roughly
2,000 rows per call paginated backwards by end_date, delisted issuers
included, each row carrying a direct, working PDF URL on
announcements.asx.com.au. That replaces both the 5-item capped per-company
API and the tokened Markit listing.

This script:
1. sweeps that index backwards to the panel start (2016-11), keeping only
   rows for LIC codes in our panel (cached incrementally - a full first
   sweep is ~700 throttled calls, later runs only fetch the new top);
2. picks month-end NTA statements from headlines (explicit "as at" dates,
   or month-year titles like "Monthly NTA Statement - July 2020");
3. samples up to one per code per year, fetches the PDF, parses the stated
   pre-tax NTA PER SHARE (per-share patterns take priority; $-totals in
   millions are excluded; cents vs dollars resolved from context only -
   ambiguity is flagged, never silently corrected);
4. compares with the panel's derived NTA for that month.

Writes outputs/au/au_nta_pdf_check.csv + au_nta_pdf_check_summary.json.
First run is budget-bounded; caches make later runs incremental.
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

INDEX_URL = "https://www.asx.com.au/asx/1/announcement/list"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
EARLIEST = pd.Timestamp("2016-11-01", tz="Australia/Sydney")
SWEEP_BUDGET = int(os.environ.get("NTA_SWEEP_BUDGET", "800"))   # index calls per run
PDF_BUDGET = int(os.environ.get("NTA_PDF_BUDGET", "400"))       # new PDFs per run
DEADLINE_MIN = int(os.environ.get("NTA_DEADLINE_MIN", "180"))   # wall-clock cap
START = time.time()

CACHE = Path("data/asx_ann_cache/asx1")
CACHE.mkdir(parents=True, exist_ok=True)
INDEX_F = CACHE / "lic_announcement_index.parquet"
STATE_F = CACHE / "sweep_state.json"
PARSE_DIR = CACHE / "pdf_parse"
PARSE_DIR.mkdir(exist_ok=True)

NTA_HEAD = re.compile(r"\bNTA\b|net tangible|net asset|\bNAV\b", re.I)
ASAT = re.compile(r"as at\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(20[0-9]{2})\b", re.I)
WEEKLY = re.compile(r"week|daily", re.I)

_NUM = r"([0-9]+(?:\.[0-9]{1,4})?)"
_UNIT = r"\s*(cents|cps|c\b|¢)?"
# priority order: per-share pre-tax first, generic totals last
NTA_PATTERNS = [
    re.compile(r"(?:pre|before)[- ]tax\s+NTA(?:\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit))?"
               r"[^0-9]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:per\s+(?:share|security|unit)\s+)?(?:before|pre)[- ]tax"
               r"[^0-9]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"net\s+tangible\s+asset\s+backing\s+per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9]{0,80}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NAV\s+per\s+(?:share|security|unit)[^0-9]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"net\s+tangible\s+assets?[^0-9]{0,100}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\b[^0-9]{0,50}(\$)\s*" + _NUM + _UNIT, re.I),
]
MILLIONS = re.compile(r"^\s*(?:million|billion|m\b|bn\b|'?000)", re.I)

_last = 0.0


def throttled_get(s: requests.Session, url: str, **kw) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return s.get(url, timeout=60, **kw)


def sweep_index(s: requests.Session, codes: set[str], counters: dict) -> pd.DataFrame:
    """Backward sweep of the market-wide announcement index; keep our codes."""
    frames = [pd.read_parquet(INDEX_F)] if INDEX_F.exists() else []
    state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    # two frontiers: history (sweep back to EARLIEST once) and the live top
    hist_done = state.get("hist_done", False)
    end_ms = state.get("earliest_ms")  # resume point for the history sweep
    if hist_done or end_ms is None:
        # the endpoint returns nothing without an end_date (probe 8):
        # start every pass from "now"; overlap is deduped by id
        end_ms = int(time.time() * 1000)
    new_rows: list[dict] = []
    top_pass_calls = 0
    while counters["index_calls"] < SWEEP_BUDGET:
        url = f"{INDEX_URL}?end_date={end_ms}"
        counters["index_calls"] += 1
        r = throttled_get(s, url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            counters["index_error"] = f"http_{r.status_code}"
            break
        txt = r.text
        m = re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", txt, re.S)
        data = json.loads(m.group(1) if m else txt)
        items = data.get("announcement_data") or []
        if not items:
            break
        for it in items:
            if it.get("issuer_code") in codes:
                new_rows.append({
                    "id": it.get("id"),
                    "code": it.get("issuer_code"),
                    "release_date": it.get("document_release_date"),
                    "headline": it.get("header"),
                    "url": it.get("url"),
                })
        dates = pd.to_datetime([i["document_release_date"] for i in items],
                               utc=True, errors="coerce")
        oldest = dates.min()
        end_ms = int(oldest.value // 10**6) - 1
        if hist_done:
            # top-up pass: stop once we overlap what the index already holds
            top_pass_calls += 1
            if frames and oldest < pd.to_datetime(
                    frames[0]["release_date"], utc=True, errors="coerce").max():
                break
            if top_pass_calls > 60:
                break
        else:
            state = {"hist_done": False, "earliest_ms": end_ms}
            STATE_F.write_text(json.dumps(state))
            if oldest.tz_convert("Australia/Sydney") < EARLIEST:
                state["hist_done"] = True
                STATE_F.write_text(json.dumps(state))
                break
    else:
        counters["sweep_budget_hit"] = True
    if new_rows:
        frames.append(pd.DataFrame(new_rows))
    if not frames:
        return pd.DataFrame(columns=["id", "code", "release_date", "headline", "url"])
    idx = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    idx.to_parquet(INDEX_F, index=False)
    counters["index_rows"] = len(idx)
    counters["hist_done"] = json.loads(STATE_F.read_text()).get("hist_done", False) \
        if STATE_F.exists() else False
    return idx


def headline_month(head: str) -> str | None:
    """Panel month an NTA headline refers to, or None."""
    if not NTA_HEAD.search(head) or WEEKLY.search(head):
        return None
    m = ASAT.search(head)
    if m:
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            return None
        return str(asat.to_period("M")) if asat.day >= 24 else None
    m = MONTH_YEAR.search(head)
    if m:  # "Monthly NTA Statement - July 2020" style: month-end implied
        return str(pd.Period(f"{m.group(2)}-{m.group(1)[:3]}", freq="M"))
    return None


def pick_candidates(idx: pd.DataFrame) -> pd.DataFrame:
    """One month-end NTA announcement per (code, month) - latest release."""
    rows = []
    for r in idx.itertuples(index=False):
        month = headline_month(r.headline or "")
        if month and r.url:
            rows.append({"code": r.code, "month": month, "id": r.id,
                         "headline": r.headline, "url": r.url,
                         "release_date": r.release_date})
    if not rows:
        return pd.DataFrame(columns=["code", "month", "id", "headline", "url"])
    df = pd.DataFrame(rows).sort_values("release_date")
    return df.groupby(["code", "month"], as_index=False).last()


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


def parse_nta_text(text: str) -> dict:
    """Stated pre-tax per-share NTA from PDF text; unit from context only."""
    for pat in NTA_PATTERNS:
        for m in pat.finditer(text):
            dollar, num, unit = m.group(1), m.group(2), m.group(3)
            tail = text[m.end():m.end() + 20]
            if MILLIONS.match(tail):    # a $-total, not a per-share figure
                continue
            val = float(num)
            if unit:
                return {"stated_raw": val, "unit": "cents"}
            if dollar:
                return {"stated_raw": val, "unit": "dollars"}
            # bare number: LIC per-share NTAs are quoted both as dollars
            # (1.23) and cents (123.45); only context can separate them
            return {"stated_raw": val,
                    "unit": "dollars" if val < 20 else "ambiguous"}
    return {"stated_raw": None, "unit": None}


def parse_pdf(s: requests.Session, ann_id: str, url: str, counters: dict) -> dict:
    import pdfplumber

    cf = PARSE_DIR / f"{ann_id}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    if counters["pdf_calls"] >= PDF_BUDGET:
        counters["pdf_budget_hit"] = True
        return {"status": "budget_deferred"}
    if time.time() - START > DEADLINE_MIN * 60:
        counters["deadline_hit"] = True
        return {"status": "budget_deferred"}
    counters["pdf_calls"] += 1
    try:
        r = throttled_get(s, url)
    except Exception as exc:  # noqa: BLE001
        return {"status": f"pdf_error:{exc}"}    # transient - do not cache
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        res = {"status": f"pdf_http_{r.status_code}"}
        cf.write_text(json.dumps(res))
        return res
    if len(r.content) > 15_000_000:
        res = {"status": "pdf_too_large"}
        cf.write_text(json.dumps(res))
        return res
    # pdfplumber can crawl on pathological PDFs; hard-cap each parse
    import signal

    def _timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError("pdf parse timeout")

    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(60)
    try:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pdf.pages[:3]))
    except Exception as exc:  # noqa: BLE001
        res = {"status": f"pdf_parse:{exc}"}
        cf.write_text(json.dumps(res))
        return res
    finally:
        signal.alarm(0)
    if len(text) < 50:                # scanned image, no text layer - honest gap
        res = {"status": "no_text_layer"}
        cf.write_text(json.dumps(res))
        return res
    parsed = parse_nta_text(text)
    res = {"status": "parsed" if parsed["stated_raw"] is not None else "no_nta_in_pdf",
           **parsed}
    cf.write_text(json.dumps(res))
    return res


def main() -> int:
    panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
    panel["code"] = panel["security_id"].str.replace("ASX:", "", regex=False)
    have_nta = panel[panel["nta_derived"].notna()]
    codes = set(have_nta["code"].unique())
    print(f"panel codes with any NTA: {len(codes)}")

    s = requests.Session()
    s.headers["User-Agent"] = UA
    counters = {"index_calls": 0, "pdf_calls": 0}

    idx = sweep_index(s, codes, counters)
    print(f"index rows for our codes: {len(idx)} "
          f"(hist_done={counters.get('hist_done')}, calls={counters['index_calls']})")
    cands = pick_candidates(idx)

    rows = []
    for code, grp in cands.groupby("code"):
        panel_months = set(have_nta.loc[have_nta["code"] == code, "obs_month"].astype(str))
        usable = sorted(m for m in grp["month"] if m in panel_months)
        if not usable:
            continue
        by_month = grp.set_index("month")
        for month in sample_months(usable):
            cand = by_month.loc[month]
            res = parse_pdf(s, str(cand["id"]), cand["url"], counters)
            prow = have_nta[(have_nta["code"] == code) & (have_nta["obs_month"] == month)]
            derived = float(prow["nta_derived"].iloc[0]) if len(prow) else None
            rec = {"code": code, "month": month, "headline": str(cand["headline"])[:120],
                   "derived_nta": round(derived, 4) if derived is not None else None,
                   "status": res.get("status")}
            stated, unit = res.get("stated_raw"), res.get("unit")
            if stated is not None and derived is not None:
                if unit == "ambiguous":
                    # decide nothing: report both readings for the audit file
                    rec.update({"stated_nta": stated, "stated_unit": "ambiguous",
                                "status": "unit_ambiguous"})
                else:
                    stated_dollars = stated / 100.0 if unit == "cents" else stated
                    rec.update({"stated_nta": stated_dollars, "stated_unit": unit})
                    diff = abs(derived / stated_dollars - 1)
                    # ~100x gap = unresolved units statement, not a numeric
                    # disagreement - flag, never silently correct
                    if diff > 0.5 and (abs(derived / (stated / 100.0) - 1) < 0.05
                                       or abs(derived / (stated * 100.0) - 1) < 0.05):
                        rec["status"] = "unit_ambiguous"
                    else:
                        rec["abs_pct_diff"] = diff
            rows.append(rec)

    out = pd.DataFrame(rows)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/au/au_nta_pdf_check.csv", index=False)
    if "abs_pct_diff" in out.columns:
        ok = out[(out["status"] == "parsed") & out["abs_pct_diff"].notna()]
    else:
        ok = out.iloc[0:0]
    summary = {
        "panel_codes": len(codes),
        "codes_compared": int(ok["code"].nunique()) if len(ok) else 0,
        "comparisons_parsed": int(len(ok)),
        "years_covered": sorted(ok["month"].str[:4].unique().tolist()) if len(ok) else [],
        "median_abs_pct_diff": float(ok["abs_pct_diff"].median()) if len(ok) else None,
        "p90_abs_pct_diff": float(ok["abs_pct_diff"].quantile(0.9)) if len(ok) else None,
        "within_1pct": float((ok["abs_pct_diff"] < 0.01).mean()) if len(ok) else None,
        "within_2pct": float((ok["abs_pct_diff"] < 0.02).mean()) if len(ok) else None,
        "status_counts": out["status"].value_counts().to_dict() if len(out) else {},
        "index_rows": counters.get("index_rows"),
        "index_calls": counters["index_calls"],
        "history_sweep_complete": counters.get("hist_done", False),
        "pdf_fetches": counters["pdf_calls"],
        "sweep_budget_hit": counters.get("sweep_budget_hit", False),
        "index_error": counters.get("index_error"),
        "pdf_budget_hit": counters.get("pdf_budget_hit", False),
        "deadline_hit": counters.get("deadline_hit", False),
        "note": "historical sample: up to one month-end NTA PDF per code per year "
                "vs panel-derived NTA; announcement index from the public "
                "unauthenticated market-wide listing (probe 7/8), delisted "
                "issuers included; per-share pre-tax parse, unit ambiguity "
                "flagged rather than corrected",
    }
    Path("outputs/au/au_nta_pdf_check_summary.json").write_text(json.dumps(summary, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
