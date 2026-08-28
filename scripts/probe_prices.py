"""CEF-LIVE probe: empirically verify free EOD price endpoints before any
adapter is wired in (build-brief rule: never hard-code an assumed endpoint
without a passing probe). Tests three candidate sources across the symbols
the Tier 1 factor models and daily marking need:

1. Stooq CSV (https://stooq.com/q/d/l/?s=SYM&i=d) - indices, FX, UK + AU
   listings.
2. ASX native price API (www.asx.com.au/asx/1/share/{code}/prices) - same
   host family as the open announcement index; AU funds only.
3. Yahoo chart API (query1/query2 v8 chart) - fallback candidate.

Writes data/probe/prices/notes.json with per-endpoint status, row counts,
date ranges, and a verdict per required series.
"""
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

OUT = Path("data/probe/prices")
OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
s = requests.Session()
s.headers["User-Agent"] = UA
notes = {}


def get(label, url, **kw):
    time.sleep(1.5)
    try:
        r = s.get(url, timeout=45, **kw)
        notes[label] = {"status": r.status_code, "bytes": len(r.content)}
        return r
    except Exception as exc:  # noqa: BLE001
        notes[label] = f"error: {exc}"
        return None


def probe_stooq(label, sym):
    r = get(f"stooq_{label}", f"https://stooq.com/q/d/l/?s={sym}&i=d")
    if r is None or r.status_code != 200 or len(r.content) < 200:
        return
    try:
        df = pd.read_csv(io.BytesIO(r.content))
        notes[f"stooq_{label}_rows"] = len(df)
        notes[f"stooq_{label}_range"] = [str(df["Date"].iloc[0]), str(df["Date"].iloc[-1])]
        notes[f"stooq_{label}_lastclose"] = float(df["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        notes[f"stooq_{label}_parse"] = str(exc)


# --- Stooq: factor indices, FX, sample funds (UK & AU, live & smaller) ---
for label, sym in [
    ("ftas", "^ftas"),        # FTSE All-Share (UK local factor)
    ("ukx", "^ukx"),          # FTSE 100 (fallback local factor)
    ("axjo", "^axjo"),        # S&P/ASX 200 (AU local factor)
    ("aord", "^aord"),        # All Ordinaries (fallback)
    ("msciworld_etf", "swda.uk"),   # iShares MSCI World (UK line) as world proxy
    ("msciworld_etf2", "urth.us"),  # iShares MSCI World (US line)
    ("gbpusd", "gbpusd"),
    ("audusd", "audusd"),
    ("cty_uk", "cty.uk"),     # City of London IT (UK fund sample)
    ("atst_uk", "atst.uk"),   # Alliance Trust
    ("afi_au", "afi.au"),     # AFIC (AU fund sample)
    ("wam_au", "wam.au"),     # WAM Capital
    ("glin_au", "gvf.au"),    # smaller AU fund
]:
    probe_stooq(label, sym)

# --- ASX native price API (open host, same family as announcement index) ---
for code in ("AFI", "WAM", "GVF"):
    r = get(f"asx1_prices_{code}",
            f"https://www.asx.com.au/asx/1/share/{code}/prices?interval=daily&count=15",
            headers={"Accept": "application/json"})
    if r is not None and r.status_code == 200:
        try:
            data = r.json().get("data", [])
            notes[f"asx1_prices_{code}_n"] = len(data)
            if data:
                notes[f"asx1_prices_{code}_last"] = {
                    k: data[0].get(k) for k in ("close_date", "close_price", "change_price")}
        except Exception as exc:  # noqa: BLE001
            notes[f"asx1_prices_{code}_parse"] = str(exc)

# single-quote endpoint too (intraday-ish last price)
r = get("asx1_share_afi", "https://www.asx.com.au/asx/1/share/AFI",
        headers={"Accept": "application/json"})
if r is not None and r.status_code == 200:
    try:
        j = r.json()
        notes["asx1_share_afi_fields"] = sorted(j.keys())[:20]
        notes["asx1_share_afi_last"] = j.get("last_price")
    except Exception as exc:  # noqa: BLE001
        notes["asx1_share_afi_parse"] = str(exc)

# --- Yahoo chart API fallback candidate ---
for label, sym in [("cty_l", "CTY.L"), ("afi_ax", "AFI.AX"), ("ftas_y", "%5EFTAS")]:
    r = get(f"yahoo_{label}",
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d")
    if r is not None and r.status_code == 200:
        try:
            j = r.json()["chart"]["result"][0]
            closes = j["indicators"]["quote"][0]["close"]
            notes[f"yahoo_{label}_n"] = len([c for c in closes if c is not None])
        except Exception as exc:  # noqa: BLE001
            notes[f"yahoo_{label}_parse"] = str(exc)

(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print(json.dumps(notes, indent=1, default=str)[:4000])
print("price probe done")
