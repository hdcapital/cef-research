"""ASX probe 5: which route actually serves announcement PDFs?"""
import json, time
from pathlib import Path
import requests

OUT = Path("data/probe/asx5"); OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
s = requests.Session(); s.headers["User-Agent"] = UA
notes = {}

def get(label, url, **kw):
    time.sleep(1.5)
    try:
        r = s.get(url, timeout=60, **kw)
        head = r.content[:8]
        notes[label] = {"status": r.status_code, "bytes": len(r.content),
                        "ctype": r.headers.get("Content-Type", "")[:60],
                        "is_pdf": head.startswith(b"%PDF")}
        print(label, notes[label])
        if not head.startswith(b"%PDF"):
            (OUT / f"{label}.html").write_bytes(r.content[:60_000])
        return r
    except Exception as exc:
        notes[label] = f"error: {exc}"
        print(label, exc)

# idsId 02318071 = AFI 'NTA & Top 25 as at 30 Nov 2020' (from crawled listing)
base = "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId=02318071"
get("legacy_plain", base)
get("legacy_referer", base, headers={"Referer": "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=AFI&timeframe=Y&year=2020"})
# older v1 route
get("legacy_v1", "https://www.asx.com.au/asx/statistics/displayAnnouncement.do?display=pdf&idsId=02318071")
# modern markit file gateway with a documentKey from the modern API (AFI recent)
get("markit_file", "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/2924-03124861-3A699580")
get("markit_file2", "https://asx.api.markitdigital.com/asx-research/1.0/file/2924-03124861-3A699580")
(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print("probe 5 done")
