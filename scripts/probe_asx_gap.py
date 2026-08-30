"""Why the top-up sweep stops before it reaches the gap.

Three attempts to fill the 2024-2025 hole have each ended without the index
changing, and the reasons were different every time (a missing dependency, a
status that was never committed). Rather than a fourth patch-and-wait cycle
at ~40 minutes each, this walks the sweep's own loop step by step and prints
what it sees, so one run answers it.

For each call it records the end_date requested, how many items came back,
the oldest and newest release dates in the batch, and whether the loop's
stop conditions would fire - and it does NOT stop, so a premature break
shows up as a condition that was true when it should not have been.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pandas as pd
import requests

spec = importlib.util.spec_from_file_location(
    "nta", Path("scripts/sample_nta_pdfs.py"))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

out: dict = {"index_file": str(P.INDEX_F), "exists": P.INDEX_F.exists()}
idx = pd.read_parquet(P.INDEX_F) if P.INDEX_F.exists() else pd.DataFrame()
if len(idx):
    d = pd.to_datetime(idx["release_date"], utc=True, errors="coerce").dropna()
    out["index_rows"] = int(len(idx))
    out["index_span"] = f"{d.min().date()} -> {d.max().date()}"
    out["rows_by_year"] = {str(k): int(v) for k, v in
                           d.dt.year.value_counts().sort_index().items()}
    fr = P.contiguous_frontier(idx)
    out["contiguous_frontier"] = str(fr.date()) if fr is not None else None
    out["global_max"] = str(d.max().date())

out["sweep_state"] = json.loads(P.STATE_F.read_text()) if P.STATE_F.exists() else None
out["SWEEP_BUDGET"] = P.SWEEP_BUDGET
out["codes_in_registry"] = None

s = requests.Session()
s.headers["User-Agent"] = P.UA
frontier = P.contiguous_frontier(idx) if len(idx) else None

# Is the endpoint down, slow, or refusing? Every sweep has stopped on its
# first call with a 60s ReadTimeout, so before assuming anything about the
# sweep logic, establish whether the source answers at all - and whether the
# Markit host verified earlier still does. This only asks documented public
# endpoints the project already uses; nothing here works around an access
# control, and if the ASX is deliberately refusing then the gap cannot be
# filled from this source and that is the finding.
out["reachability"] = {}
MARKIT = ("https://asx.api.markitdigital.com/asx-research/1.0/companies/"
          "AFI/announcements?access_token=83ff96335c2d45a094df02a206a39ff4"
          "&page=0&itemsPerPage=5")
for label, url, tmo in (
        ("asx1_no_params", P.INDEX_URL, 25),
        ("asx1_recent", f"{P.INDEX_URL}?end_date={int(time.time() * 1000)}", 25),
        ("asx1_2024", f"{P.INDEX_URL}?end_date=1719792000000", 25),
        ("asx_home", "https://www.asx.com.au/", 20),
        ("markit_afi", MARKIT, 25)):
    rec = {"url": url[:90]}
    t0 = time.time()
    try:
        r = s.get(url, timeout=tmo, headers={"Accept": "application/json"})
        rec.update(http=r.status_code, secs=round(time.time() - t0, 1),
                   bytes=len(r.content), head=r.text[:120])
    except Exception as exc:  # noqa: BLE001
        rec.update(error=f"{type(exc).__name__}", secs=round(time.time() - t0, 1))
    out["reachability"][label] = rec

calls = []
end_ms = int(time.time() * 1000)
for i in range(12):                     # a dozen calls is enough to see the shape
    url = f"{P.INDEX_URL}?end_date={end_ms}"
    rec = {"call": i + 1, "end_date_ms": end_ms}
    try:
        r = P.throttled_get(s, url, headers={"Accept": "application/json"})
        rec["http"] = r.status_code
        import re as _re
        m = _re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", r.text, _re.S)
        data = json.loads(m.group(1) if m else r.text)
        items = data.get("announcement_data") or []
        rec["items"] = len(items)
        if not items:
            rec["stop_reason_would_be"] = "empty_items"
            rec["body_keys"] = sorted(data.keys())[:8]
            rec["body_head"] = r.text[:300]
            calls.append(rec)
            break
        dates = pd.to_datetime([it.get("document_release_date") for it in items],
                               utc=True, errors="coerce")
        rec["newest"] = str(dates.max().date())
        rec["oldest"] = str(dates.min().date())
        rec["days_covered"] = int((dates.max() - dates.min()).days)
        if frontier is not None:
            rec["reaches_frontier"] = bool(dates.min() <= frontier)
        end_ms = int(dates.min().value // 10**6) - 1
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        calls.append(rec)
        break
    calls.append(rec)

out["calls"] = calls

# The market-wide endpoint returns 2000 items per call and covers ONE day,
# so the 1,023-day gap needs ~1,000 heavy calls - and it times out on most of
# them, which is a rate limit expressed as a timeout. Hammering it would be
# working around an access control, so test the per-company endpoint instead:
# it answered in 0.6s, and we only ever keep ~1% of a market-wide page. Does
# it paginate back far enough to cover 2024-2025?
out["markit_pagination"] = {}
for code in ("AFI", "ARG", "WAM"):
    pages = []
    for pg in range(4):
        u = ("https://asx.api.markitdigital.com/asx-research/1.0/companies/"
             f"{code}/announcements?access_token=83ff96335c2d45a094df02a206a39ff4"
             f"&page={pg}&itemsPerPage=50")
        try:
            r = s_sess.get(u, timeout=30) if False else requests.get(
                u, timeout=30, headers={"User-Agent": P.UA})
            if r.status_code != 200:
                pages.append({"page": pg, "http": r.status_code}); break
            body = r.json().get("data", {})
            items = body.get("items") or body.get("announcements") or []
            if not items:
                pages.append({"page": pg, "items": 0, "keys": sorted(body.keys())[:8]})
                break
            ds = pd.to_datetime([it.get("documentDate") or it.get("date")
                                 for it in items], utc=True, errors="coerce").dropna()
            pages.append({"page": pg, "items": len(items),
                          "newest": str(ds.max().date()) if len(ds) else None,
                          "oldest": str(ds.min().date()) if len(ds) else None,
                          "sample_keys": sorted(items[0].keys())[:10]})
            time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            pages.append({"page": pg, "error": f"{type(exc).__name__}"}); break
    out["markit_pagination"][code] = pages

# `page` is ignored - pages 0-3 returned identical items - so either this
# endpoint only serves the latest few announcements, or history is reached by
# a parameter we have not found. Dump the raw response shape once: any
# pagination metadata in it will say which, and guessing parameter names
# against a third-party API is how you end up hammering it.
probe_urls = {
    "items200": ("https://asx.api.markitdigital.com/asx-research/1.0/companies/"
                 "AFI/announcements?access_token=83ff96335c2d45a094df02a206a39ff4"
                 "&page=0&itemsPerPage=200"),
    "page10": ("https://asx.api.markitdigital.com/asx-research/1.0/companies/"
               "AFI/announcements?access_token=83ff96335c2d45a094df02a206a39ff4"
               "&page=10&itemsPerPage=50"),
}
out["markit_shape"] = {}
for label, u in probe_urls.items():
    try:
        r = requests.get(u, timeout=30, headers={"User-Agent": P.UA})
        body = r.json()
        data = body.get("data", body)
        items = data.get("items") or data.get("announcements") or []
        ds = pd.to_datetime([it.get("date") for it in items], utc=True,
                            errors="coerce").dropna() if items else []
        out["markit_shape"][label] = {
            "http": r.status_code,
            "top_keys": sorted(body.keys()),
            "data_keys": sorted(data.keys()) if isinstance(data, dict) else None,
            "n_items": len(items),
            "span": (f"{ds.min().date()} -> {ds.max().date()}") if len(ds) else None,
            "pagination_block": {k: v for k, v in data.items()
                                 if "page" in k.lower() or "count" in k.lower()
                                 or "total" in k.lower()}
                                if isinstance(data, dict) else None,
        }
        time.sleep(1.5)
    except Exception as exc:  # noqa: BLE001
        out["markit_shape"][label] = {"error": f"{type(exc).__name__}: {exc}"}
if len(calls) > 1 and "days_covered" in calls[0]:
    per = sum(c.get("days_covered", 0) for c in calls if "days_covered" in c)
    n = len([c for c in calls if "days_covered" in c])
    out["avg_days_per_call"] = round(per / max(1, n), 2)
    if frontier is not None and "newest" in calls[0]:
        gap_days = (pd.Timestamp(calls[0]["newest"], tz="UTC") - frontier).days
        out["gap_days"] = int(gap_days)
        out["calls_needed_estimate"] = int(gap_days / max(0.1, per / max(1, n)))

Path("reports/build").mkdir(parents=True, exist_ok=True)
Path("reports/build/asx_gap_probe.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps(out, indent=2, default=str)[:4000])
