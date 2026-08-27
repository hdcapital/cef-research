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
PDF_BUDGET = int(os.environ.get("NTA_PDF_BUDGET", "600"))       # new PDFs per run
DEADLINE_MIN = int(os.environ.get("NTA_DEADLINE_MIN", "180"))   # wall-clock cap
START = time.time()

CACHE = Path("data/asx_ann_cache/asx1")
CACHE.mkdir(parents=True, exist_ok=True)
INDEX_F = CACHE / "lic_announcement_index.parquet"
STATE_F = CACHE / "sweep_state.json"
# v2: caches the *extracted* text/table rows, so parser improvements can be
# re-applied to already-fetched PDFs without re-downloading anything
PARSE_DIR = CACHE / "pdf_extract"
PARSE_DIR.mkdir(exist_ok=True)

NTA_HEAD = re.compile(r"\bNTA\b|net tangible|net asset|\bNAV\b", re.I)
ASAT = re.compile(r"as at\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(20[0-9]{2})\b", re.I)
WEEKLY = re.compile(r"week|daily", re.I)

_NUM = r"([0-9]+(?:\.[0-9]{1,4})?)"
_UNIT = r"\s*(cents|cps|c\b|¢)?"
# strict per-share/pre-tax patterns only: the generic "net tangible
# assets ... $X" forms matched fund-level totals and boilerplate
NTA_PATTERNS = [
    re.compile(r"(?:pre|before)[- ]tax\s+NTA(?:\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit))?"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:per\s+(?:share|security|unit)\s+)?(?:before|pre)[- ]tax"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"net\s+tangible\s+assets?\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9%]{0,80}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NAV\s+per\s+(?:share|security|unit)[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
]
MILLIONS = re.compile(r"^\s*(?:million|billion|m\b|bn\b|'?000)", re.I)

# table-row labels, most specific first; "after tax" rows are rejected
ROW_PRETAX = re.compile(r"(?:pre|before)[- ]tax", re.I)
ROW_POSTTAX = re.compile(r"(?:post|after)[- ]tax", re.I)
ROW_PERSHARE = re.compile(r"per\s+(?:ordinary\s+)?(?:share|security|unit)", re.I)
ROW_NTA = re.compile(r"\bNTA\b|net\s+tangible|NAV\b|net\s+asset\s+value", re.I)
ROW_EXCLUDE = re.compile(r"premium|discount|total|million|change|return|%", re.I)
NOT_NTA = re.compile(r"dividend|distribution|paid|declared|buy[- ]?back|issue\s+price|exercise", re.I)
CELL_VAL = re.compile(r"(\$)?\s*([0-9]+(?:\.[0-9]{1,4})?)\s*(cents|cps|¢|c\b)?", re.I)

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
    """Up to two months per calendar year: June AND December when present.

    June is Australia's fiscal year-end, when cum/ex-dividend NTA effects
    peak - December pairs let genuine basis differences be told apart from
    June-specific timing artifacts.
    """
    by_year: dict[str, list[str]] = {}
    for m in months:
        by_year.setdefault(m[:4], []).append(m)
    picked = []
    for _, ms in sorted(by_year.items()):
        ms = sorted(ms)
        year_pick = [m for m in ms if m.endswith("-06")] + [m for m in ms if m.endswith("-12")]
        picked.extend(year_pick or ms[:1])
    return picked


def _classify_value(dollar: str | None, num: str, unit: str | None) -> dict:
    val = float(num)
    if unit:
        return {"stated_raw": val, "unit": "cents"}
    if dollar:
        return {"stated_raw": val, "unit": "dollars"}
    # bare number: LIC per-share NTAs are quoted both as dollars (1.23)
    # and cents (123.45); only context can separate them
    return {"stated_raw": val, "unit": "dollars" if val < 20 else "ambiguous"}


def parse_nta_rows(rows: list[list[str]]) -> dict | None:
    """Pre-tax per-share NTA from table rows: label cell -> value cell.

    Rows are ranked: pre-tax + per-share beats pre-tax beats plain
    NTA-per-share; 'after tax', premium/discount, totals and %-cells are
    never used.
    """
    best = None
    for row in rows:
        cells = [str(c) if c is not None else "" for c in row]
        label_end = 0
        label = ""
        for i, c in enumerate(cells):
            if CELL_VAL.search(c) and re.search(r"[0-9]", c):
                label_end = i
                break
            label += " " + c
        else:
            continue
        if not ROW_NTA.search(label) or ROW_POSTTAX.search(label) \
                or ROW_EXCLUDE.search(label):
            continue
        score = 1 + (2 if ROW_PRETAX.search(label) else 0) \
            + (1 if ROW_PERSHARE.search(label) else 0)
        if score < 2:   # bare "NTA" labels grab stray numbers ("Top 25...")
            continue
        for c in cells[label_end:]:
            if "%" in c or MILLIONS.search(c):
                continue
            m = CELL_VAL.search(c)
            if m:
                unit_hint = "cents" if re.search(r"cent|cps|¢", label, re.I) else None
                got = _classify_value(m.group(1), m.group(2), m.group(3) or unit_hint)
                if best is None or score > best[0]:
                    best = (score, got)
                break
    return best[1] if best else None


def parse_nta_text(text: str) -> dict:
    """Stated pre-tax per-share NTA from flowing text; unit from context.

    Evidence-driven forms (outputs/au/au_nta_parse_debug.json):
    - "The NTA as at 31 Dec 2016 was $7.63 per share"  (value THEN per-share)
    - "Before Tax * After Tax * 31 December 2016 $5.83" (label, then a date,
      then the value - adjacency fails, so lazily seek the first $-value)
    - "(1.28 cents * 4 quarters)" between label and value (require $ there)
    """
    # newsletters glue footnote markers to labels: "NTA per share1 $8.45"
    text = re.sub(r"(?i)\b(share|security|unit)s?(\d)\b", r"\1 ", text)
    # $-value immediately followed by "per share", NTA named just before it;
    # never a dividend/buy-back/issue amount, which use the same phrasing
    for m in re.finditer(r"\$\s*" + _NUM + r"\s*per\s+(?:ordinary\s+)?(?:share|security|unit)",
                         text, re.I):
        pre = text[max(0, m.start() - 150):m.start()]
        near = pre[-70:]
        if NTA_HEAD.search(pre) and not NOT_NTA.search(near) \
                and not (ROW_POSTTAX.search(near) and not ROW_PRETAX.search(near)):
            return {"stated_raw": float(m.group(1)), "unit": "dollars"}
    # "NAV per unit ... as at <date> was $1.96701" / "NTA) per share after
    # tax ... was $0.858" - tag the basis when only after-tax is published
    for m in re.finditer(r"(?:NAV|NTA)\)?\s+per\s+(?:share|security|unit)(.{0,140}?)"
                         r"was\s+\$\s*" + _NUM, text, re.I | re.S):
        out = {"stated_raw": float(m.group(2)), "unit": "dollars"}
        if ROW_POSTTAX.search(m.group(1)):
            out["basis"] = "post_tax"
        return out
    # "NTA backing per share ... 255.1 c 233.5 c" (cents, before-tax column
    # first) - lazy scan to the first cents-suffixed value
    for m in re.finditer(r"(?:NAV|NTA)\)?\s+(?:backing\s+)?per\s+(?:ordinary\s+)?"
                         r"(?:share|security|unit)[^%$]{0,220}?" + _NUM +
                         r"\s*(?:cents|cps|c)\b", text, re.I | re.S):
        return {"stated_raw": float(m.group(1)), "unit": "cents"}
    # before-tax label, lazy scan to the FIRST $-prefixed value; a % or an
    # intervening bare $ means we crossed into a returns/holdings table
    for m in re.finditer(r"(?:pre|before)[- ]tax[^%$]{0,300}?\$\s*" + _NUM, text, re.I | re.S):
        pre = text[max(0, m.start() - 200):m.start() + 12]
        tail = text[m.end():m.end() + 20]
        if NTA_HEAD.search(pre) and not MILLIONS.match(tail):
            return {"stated_raw": float(m.group(1)), "unit": "dollars"}
    for pat in NTA_PATTERNS:
        for m in pat.finditer(text):
            dollar, num, unit = m.group(1), m.group(2), m.group(3)
            tail = text[m.end():m.end() + 20]
            if MILLIONS.match(tail):    # a $-total, not a per-share figure
                continue
            # bare integers ("Top 25 Investments", "Top 20 Holdings") are
            # never NTA quotes - real ones carry decimals, $, or cents
            if "." not in num and not dollar and not unit:
                continue
            return _classify_value(dollar, num, unit)
    return {"stated_raw": None, "unit": None}


def derive_stated(extract: dict) -> dict:
    """Stated NTA from a cached extraction: table rows first, text second."""
    if extract.get("status") != "extracted":
        return extract
    got = parse_nta_rows(extract.get("rows") or [])
    if got is None:
        # mega-cell tables (whole layout in one cell) only surface via text,
        # so append the row text to the page text before the text pass
        rows_text = " ".join(" ".join(c for c in r if c)
                             for r in (extract.get("rows") or []))
        got = parse_nta_text((extract.get("text") or "") + " " + rows_text)
    status = "parsed" if got and got.get("stated_raw") is not None else "no_nta_in_pdf"
    return {"status": status, **(got or {})}


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
            pages = pdf.pages[:2]
            text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pages))
            rows = []
            for p in pages:
                try:
                    for tbl in p.extract_tables() or []:
                        for row in tbl:
                            joined = " ".join(str(c) for c in row if c)
                            if NTA_HEAD.search(joined):
                                rows.append([str(c) if c is not None else "" for c in row])
                except Exception:  # noqa: BLE001
                    pass
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
    res = {"status": "extracted", "text": text[:20000], "rows": rows[:40]}
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
            res = derive_stated(parse_pdf(s, str(cand["id"]), cand["url"], counters))
            prow = have_nta[(have_nta["code"] == code) & (have_nta["obs_month"] == month)]
            derived = float(prow["nta_derived"].iloc[0]) if len(prow) else None
            rec = {"code": code, "month": month, "ann_id": str(cand["id"]),
                   "headline": str(cand["headline"])[:120],
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
                    rec.update({"stated_nta": stated_dollars, "stated_unit": unit,
                                "stated_basis": res.get("basis", "pre_tax")})
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

    # commit the raw evidence for every disagreement/ambiguity so parser
    # iteration works from actual document text, not guesses
    debug = []
    for rec in rows:
        bad = rec.get("status") == "unit_ambiguous" or \
            (rec.get("abs_pct_diff") is not None and rec["abs_pct_diff"] > 0.05)
        if not bad or len(debug) >= 60:
            continue
        cf = PARSE_DIR / f"{rec.get('ann_id')}.json"
        if cf.exists():
            ext = json.loads(cf.read_text())
            debug.append({**{k: rec.get(k) for k in
                             ("code", "month", "derived_nta", "stated_nta", "status")},
                          "text_head": (ext.get("text") or "")[:1500],
                          "rows": ext.get("rows")})
    Path("outputs/au/au_nta_parse_debug.json").write_text(
        json.dumps(debug, indent=1, default=str))
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
