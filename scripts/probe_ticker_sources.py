"""Which source can turn a fund NAME or ISIN into a TIDM?

The Investegate /search?q= guess returned nothing for all 150 funds, so
this tests the realistic alternatives against real names from the
announcements-only cohort before any of them is wired in:

1. the AIC keyfacts/companies file - it may already carry a TIDM for the
   funds the MIR leaves name-only (free and authoritative if so);
2. Yahoo's search endpoint (proven reachable from CI) - name -> symbol,
   e.g. "HICL Infrastructure" -> HICL.L;
3. Investegate's actual search - captured, not assumed, so we can see the
   real URL shape and result markup.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

OUT = Path("data/probe/tickers")
OUT.mkdir(parents=True, exist_ok=True)
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
s = requests.Session(); s.headers["User-Agent"] = UA
notes = {}

reg = pd.read_parquet("data/universe/registry.parquet")
need = reg[(reg["status"] == "live") & (reg["market"] == "UK")
           & (reg["nav_route"] == "announcements_only")]
sample = need.head(8)[["security_id", "name", "isin"]].to_dict("records")
notes["sample"] = sample

# --- 1. does the AIC companies/keyfacts file carry tickers? ---
try:
    from uk_cef.panel import parse_all_companies
    comp = parse_all_companies(Path("data/raw/aic"))
    notes["companies_rows"] = int(len(comp))
    notes["companies_cols"] = sorted(comp.columns.tolist())
    tick_cols = [c for c in comp.columns
                 if re.search(r"tidm|ticker|epic|symbol", c, re.I)]
    notes["companies_ticker_cols"] = tick_cols
    if tick_cols and "company_name" in comp.columns:
        tc = tick_cols[0]
        latest = comp.sort_values("obs_month").groupby("company_name").last() \
            if "obs_month" in comp.columns else comp.groupby("company_name").last()
        hits = {}
        for r in sample:
            m = latest[latest.index.str.contains(
                re.escape(str(r["name"])[:18]), case=False, na=False)]
            if len(m):
                hits[r["name"]] = str(m.iloc[0][tc])
        notes["companies_ticker_hits"] = hits
except Exception as exc:  # noqa: BLE001
    notes["companies_error"] = f"{type(exc).__name__}: {exc}"

# --- 2. Yahoo search: name -> symbol ---
yah = {}
for r in sample:
    time.sleep(1.5)
    try:
        q = requests.utils.quote(str(r["name"]))
        rr = s.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}"
                   "&quotesCount=6&newsCount=0", timeout=30)
        if rr.status_code != 200:
            yah[r["name"]] = f"http_{rr.status_code}"
            continue
        quotes = rr.json().get("quotes", [])
        yah[r["name"]] = [{k: qq.get(k) for k in
                           ("symbol", "shortname", "exchange", "quoteType")}
                          for qq in quotes[:4]]
    except Exception as exc:  # noqa: BLE001
        yah[r["name"]] = f"error: {exc}"
notes["yahoo_search"] = yah

# --- 3. what does Investegate search actually do? ---
for label, url in (
    ("ig_search_q", "https://www.investegate.co.uk/search?q=HICL"),
    ("ig_search_term", "https://www.investegate.co.uk/search/?term=HICL"),
    ("ig_company_direct", "https://www.investegate.co.uk/company/HICL"),
):
    time.sleep(1.5)
    try:
        rr = s.get(url, timeout=30)
        body = rr.text
        rec = {"status": rr.status_code, "bytes": len(body)}
        m = re.search(r"<h1[^>]*>(.{0,120}?)</h1>", body, re.S | re.I)
        if m:
            rec["h1"] = re.sub(r"\s+", " ", m.group(1)).strip()[:100]
        rec["company_links"] = sorted(set(re.findall(
            r'/company/([A-Za-z0-9._-]{2,12})"', body)))[:10]
        notes[label] = rec
    except Exception as exc:  # noqa: BLE001
        notes[label] = f"error: {exc}"

(OUT / "notes.json").write_text(json.dumps(notes, indent=1, default=str))
print(json.dumps(notes, indent=1, default=str)[:4000])
