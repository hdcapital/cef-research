"""ASX probe 6: extract the Markit announcements API's real params from the
site's JS bundles, then test full-history pagination."""
import json, re, time
from pathlib import Path
import requests

OUT = Path("data/probe/asx6"); OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
s = requests.Session(); s.headers["User-Agent"] = UA
notes = {}

def get(label, url, **kw):
    time.sleep(1.5)
    try:
        r = s.get(url, timeout=60, **kw)
        notes[label] = {"status": r.status_code, "bytes": len(r.content)}
        print(label, notes[label])
        return r
    except Exception as exc:
        notes[label] = f"error: {exc}"; print(label, exc); return None

# 1. JS bundles - find announcements query construction
for name in ("common.js", "vendor.js", "manifest.js"):
    r = get(f"js_{name}", f"https://content.markitcdn.com/asx.markitdigital.com/js/{name}")
    if r is not None and r.status_code == 200:
        txt = r.text
        hits = set()
        for m in re.finditer(r'announcements', txt):
            seg = txt[max(0,m.start()-300):m.end()+300]
            for q in re.findall(r'["\'][a-zA-Z]{3,30}["\']\s*[:,]', seg):
                hits.add(q.strip('"\':, '))
            for u in re.findall(r'["\'][/a-z0-9\-]{4,60}announcements[/a-z0-9\-]{0,40}["\']', seg):
                hits.add(u.strip('"\''))
        notes[f"js_{name}_hits"] = sorted(hits)[:60]

# 2. param variants on the announcements endpoint
base = "https://asx.api.markitdigital.com/asx-research/1.0/companies/afi/announcements"
variants = [
    ("v_pagenum", base + "?itemsPerPage=50&pageNumber=1"),
    ("v_page0", base + "?itemsPerPage=50&pageNumber=0"),
    ("v_offset", base + "?limit=50&offset=50"),
    ("v_datefilter", base + "?fromDate=2020-01-01&toDate=2020-12-31&itemsPerPage=50"),
    ("v_dates2", base + "?dateFrom=2020-01-01&dateTo=2020-12-31&itemsPerPage=50"),
    ("v_year", base + "?year=2020&itemsPerPage=50"),
]
for label, url in variants:
    r = get(label, url, headers={"Accept": "application/json"})
    if r is not None and r.status_code == 200:
        try:
            items = r.json().get("data", {}).get("items", [])
            notes[label+"_n"] = len(items)
            notes[label+"_dates"] = [i.get("date","")[:10] for i in items[:3]] + ["..."] + [i.get("date","")[:10] for i in items[-2:]] if items else []
        except Exception as exc:
            notes[label+"_err"] = str(exc)
(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print("probe 6 done")
