"""CEF-LIVE price probe, round 2.

Round 1 verdicts (data/probe/prices/notes.json history):
- Yahoo v8 chart API works unauthenticated from GH runners (23-24 daily
  closes for CTY.L / AFI.AX).
- Stooq returned HTTP 200 with ~2-row bodies - suspected shared-runner
  daily-quota block; this round captures the body text to confirm.
- www.asx.com.au/asx/1/share/... returns 404 - endpoint retired.

This round verifies exactly what the Tier 1 factor layer needs from Yahoo:
proper index symbols (^FTAS, ^AXJO), FX (GBPUSD=X, AUDUSD=X), a world
proxy, monthly history depth via range=max&interval=1mo (factor fitting),
and daily recency via range=3mo for funds large and small.
"""
import json
import time
from pathlib import Path

import requests

OUT = Path("data/probe/prices")
OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
s = requests.Session()
s.headers["User-Agent"] = UA
notes = {}


def yahoo(label, sym, rng, interval):
    time.sleep(1.5)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?range={rng}&interval={interval}")
    try:
        r = s.get(url, timeout=45)
    except Exception as exc:  # noqa: BLE001
        notes[label] = f"error: {exc}"
        return
    rec = {"status": r.status_code}
    if r.status_code == 200:
        try:
            j = r.json()["chart"]["result"][0]
            ts = j.get("timestamp") or []
            closes = j["indicators"]["quote"][0].get("close") or []
            good = [c for c in closes if c is not None]
            rec["n"] = len(good)
            if ts:
                import datetime as dt
                rec["first"] = dt.datetime.utcfromtimestamp(ts[0]).date().isoformat()
                rec["last"] = dt.datetime.utcfromtimestamp(ts[-1]).date().isoformat()
            if good:
                rec["last_close"] = round(float(good[-1]), 4)
            rec["ccy"] = j.get("meta", {}).get("currency")
        except Exception as exc:  # noqa: BLE001
            rec["parse"] = str(exc)
    notes[label] = rec


# factor series: monthly depth for fitting
for label, sym in [
    ("ftas", "^FTAS"), ("ukx", "^FTSE"), ("axjo", "^AXJO"), ("aord", "^AORD"),
    ("world_acwi", "ACWI"), ("world_urth", "URTH"),
    ("gbpusd", "GBPUSD=X"), ("audusd", "AUDUSD=X"),
]:
    yahoo(f"m_{label}", sym, "max", "1mo")

# daily recency: funds big and small, both markets
for label, sym in [
    ("cty_l", "CTY.L"), ("atst_l", "ATST.L"), ("bsif_l", "BSIF.L"),
    ("afi_ax", "AFI.AX"), ("wam_ax", "WAM.AX"), ("gvf_ax", "GVF.AX"),
    ("ftas_d", "^FTAS"), ("axjo_d", "^AXJO"),
]:
    yahoo(f"d_{label}", sym, "3mo", "1d")

# stooq body capture to confirm the quota theory
time.sleep(1.5)
try:
    r = s.get("https://stooq.com/q/d/l/?s=cty.uk&i=d", timeout=45)
    notes["stooq_body_head"] = r.text[:300]
    notes["stooq_status"] = r.status_code
except Exception as exc:  # noqa: BLE001
    notes["stooq_body_head"] = f"error: {exc}"

(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print(json.dumps(notes, indent=1, default=str)[:5000])
print("price probe r2 done")
