"""Fetch the newest NAV-headline page for a named list of tickers.

The archive's failure sampler caps samples per ticker and never reaches
funds whose every NAV page fails - TwentyFour Income (TFIF) and Ruffer
(RICA) publish NAV RNS daily and have parsed 52 and 20 announcements in
their lives. Rule-writing needs their actual text, and only a runner has
egress. Set TICKERS (comma list); reads the listing caches (state group
uk_announcements); ~2 fetches per ticker at the crawler's throttle.
Writes reports/build/uk_nav_pages_probe.json. Evidence only.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, "src")
from cef_live.harvest_nav import UK_NAV_HEAD, parse_uk_nav_text  # noqa: E402

TICKERS = [t.strip().upper() for t in os.environ.get(
    "TICKERS", "TFIF,RICA,BHMG,CORD,THRG,DIVI,SCGT,AEWU,AA4").split(",") if t.strip()]
PER = int(os.environ.get("PER_TICKER", "2"))
UA = {"User-Agent": "uk-cef research probe (contact: repo owner)"}


def main() -> int:
    listings = Path("data/investegate_cache/listings")
    files = {f.stem.upper(): f for f in listings.glob("*.csv")} \
        if listings.exists() else {}
    s = requests.Session()
    s.headers.update(UA)
    out = []
    for t in TICKERS:
        f = files.get(t)
        rec = {"ticker": t, "listing_cache": f is not None, "pages": []}
        if f is None:
            out.append(rec)
            continue
        df = pd.read_csv(f, dtype=str)
        rec["listing_rows"] = int(len(df))
        rec["newest_listed"] = df["date"].max() if "date" in df.columns else None
        heads = df["headline"].fillna("") if "headline" in df.columns else pd.Series(dtype=str)
        nav = df[heads.str.contains(UK_NAV_HEAD) & df["url"].notna()] \
            if len(heads) else df.iloc[0:0]
        rec["nav_headline_rows"] = int(len(nav))
        rec["newest_nav_headline"] = nav["date"].max() if len(nav) else None
        for r in nav.sort_values("date", ascending=False).head(PER).itertuples(index=False):
            url = r.url if str(r.url).startswith("http") \
                else f"https://www.investegate.co.uk{r.url}"
            try:
                resp = s.get(url, timeout=30)
                text = " ".join(BeautifulSoup(resp.text, "html.parser")
                                .get_text(" ").split())
            except Exception as exc:  # noqa: BLE001
                rec["pages"].append({"date": r.date, "url": url, "error": str(exc)})
                time.sleep(1.5)
                continue
            got = parse_uk_nav_text(text)
            rec["pages"].append({"date": r.date, "headline": r.headline,
                                 "url": url, "parsed": got,
                                 "text_head": text[:6000]})
            time.sleep(1.5)
        out.append(rec)
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/uk_nav_pages_probe.json").write_text(
        json.dumps({"tickers": out}, indent=2, default=str))
    for r in out:
        print(r["ticker"], "cache" if r["listing_cache"] else "NO CACHE",
              "nav_rows", r.get("nav_headline_rows"), "newest",
              r.get("newest_nav_headline"),
              [p.get("parsed", {}).get("nav_cum_pence") for p in r["pages"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
