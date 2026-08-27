"""ASX probe 8: verify the two open routes the public announcements page was
observed (probe 7, browser capture) to use anonymously, using plain requests:

1. www.asx.com.au/asx/1/announcement/list - unauthenticated JSON with direct
   PDF URLs; test per-company params and historical end_date pagination.
2. asx.api.markitdigital.com with the page's embedded public Bearer token -
   test token discoverability (is it in the public page/JS?), per-company
   pagination depth, and PDF fetch for a historical documentKey.

Both replicate the anonymous public page's own calls; throttled 1.5s.
"""
import json, re, time
from pathlib import Path
import requests

OUT = Path("data/probe/asx8"); OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
CAPTURED_TOKEN = "83ff96335c2d45a094df02a206a39ff4"  # seen in probe 7 browser capture
API = "https://asx.api.markitdigital.com/asx-research/1.0"
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

def jbody(r):
    if r is None or r.status_code != 200:
        return None
    txt = r.text
    m = re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", txt, re.S)  # strip JSONP wrapper
    try:
        return json.loads(m.group(1) if m else txt)
    except Exception:
        return None

# ---- 1. token discoverability: is the Bearer token in public HTML/JS? ----
r = get("page_html", "https://www.asx.com.au/markets/trade-our-cash-market/announcements.uwc")
found_in = []
if r is not None and r.status_code == 200 and CAPTURED_TOKEN in r.text:
    found_in.append("page_html")
for name in ("common.js", "vendor.js"):
    r = get(f"js_{name}", f"https://content.markitcdn.com/asx.markitdigital.com/js/{name}")
    if r is not None and r.status_code == 200 and CAPTURED_TOKEN in r.text:
        found_in.append(name)
    # also collect any 32-hex candidates near 'Bearer'/'token'
    if r is not None and r.status_code == 200:
        cands = set()
        for m in re.finditer(r"[0-9a-f]{32}", r.text):
            seg = r.text[max(0, m.start()-120):m.end()+40]
            if re.search(r"bearer|token|apikey|authorization", seg, re.I):
                cands.add(m.group(0))
        if cands:
            notes[f"js_{name}_token_candidates"] = sorted(cands)[:10]
notes["captured_token_found_in"] = found_in

# ---- 2. asx/1 endpoint: per-company + historical depth, no auth ----
base = "https://www.asx.com.au/asx/1/announcement/list"
hist_ms = 1593475200000  # 2020-06-30
for label, url in [
    ("a1_recent", base + "?page_size=50"),
    ("a1_hist2020", base + f"?end_date={hist_ms}&page_size=50"),
    ("a1_code_afi", base + "?issuer_code=AFI&page_size=50"),
    ("a1_code2_afi", base + "?asx_code=AFI&page_size=50"),
    ("a1_company_afi", "https://www.asx.com.au/asx/1/company/AFI/announcements?count=50&market_sensitive=false"),
]:
    r = get(label, url, headers={"Accept": "application/json"})
    b = jbody(r)
    if b:
        items = b.get("announcement_data") or b.get("data") or []
        notes[label + "_n"] = len(items)
        notes[label + "_total"] = b.get("total_search_count")
        if items:
            notes[label + "_dates"] = [items[0].get("document_release_date", "")[:10],
                                       items[-1].get("document_release_date", "")[:10]]
            notes[label + "_codes"] = sorted({i.get("issuer_code", "") for i in items[:20]})[:8]
            notes[label + "_sample_url"] = items[0].get("url", "")[:120]

# a PDF from the historical window, direct asxpdf host
r = get("a1_hist2020_again", base + f"?end_date={hist_ms}&page_size=5",
        headers={"Accept": "application/json"})
b = jbody(r)
if b and b.get("announcement_data"):
    pdf_url = b["announcement_data"][0].get("url", "")
    notes["hist_pdf_url"] = pdf_url[:150]
    if pdf_url:
        r2 = get("hist_pdf_fetch", pdf_url)
        if r2 is not None:
            notes["hist_pdf_is_pdf"] = bool(r2.content.startswith(b"%PDF"))

# ---- 3. markit API with the page's public token ----
H = {"Accept": "application/json", "Authorization": f"Bearer {CAPTURED_TOKEN}",
     "Referer": "https://www.asx.com.au/", "Origin": "https://www.asx.com.au"}
r = get("mk_predictive", API + "/search/predictive?searchText=AFI&useBondsLookup=true", headers=H)
b = jbody(r)
xid = None
if b:
    items = (b.get("data") or {}).get("items") or []
    notes["mk_predictive_items"] = [{k: i.get(k) for k in ("symbol", "xidEntity", "displayName")}
                                    for i in items[:5]]
    for i in items:
        if i.get("symbol") == "AFI":
            xid = i.get("xidEntity"); break
notes["afi_xid"] = xid

if xid:
    for label, q in [
        ("mk_p0", f"?entityXids={xid}&page=0&itemsPerPage=100"),
        ("mk_p5", f"?entityXids={xid}&page=5&itemsPerPage=100"),
        ("mk_p10", f"?entityXids={xid}&page=10&itemsPerPage=100"),
    ]:
        r = get(label, API + "/markets/announcements" + q, headers=H)
        b = jbody(r)
        if b:
            d = b.get("data") or {}
            items = d.get("items") or []
            notes[label + "_n"] = len(items)
            notes[label + "_pages"] = {k: d.get(k) for k in ("totalItems", "totalPages", "pages", "count")}
            if items:
                notes[label + "_dates"] = [items[0].get("date", "")[:10], items[-1].get("date", "")[:10]]
                notes[label + "_lastkey"] = items[-1].get("documentKey", "")
    # deep-history PDF via file gateway, with and without token
    lastkey = notes.get("mk_p10_lastkey") or notes.get("mk_p5_lastkey")
    if lastkey:
        r = get("mk_hist_pdf_auth", f"{API}/file/{lastkey}", headers=H)
        if r is not None:
            notes["mk_hist_pdf_auth_is_pdf"] = bool(r.content.startswith(b"%PDF"))
        r = get("mk_hist_pdf_noauth", f"{API}/file/{lastkey}")
        if r is not None:
            notes["mk_hist_pdf_noauth_is_pdf"] = bool(r.content.startswith(b"%PDF"))
    # does the per-company endpoint uncap with the token?
    r = get("mk_company_afi", API + "/companies/afi/announcements?itemsPerPage=50", headers=H)
    b = jbody(r)
    if b:
        notes["mk_company_afi_n"] = len((b.get("data") or {}).get("items") or [])

(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print("probe 8 done")
