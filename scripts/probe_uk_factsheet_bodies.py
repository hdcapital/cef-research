"""What do the no-NAV-headline funds' update announcements actually say?

The reach probe established that ~28 live UK funds publish no NAV-shaped
RNS headline at all (BSIF, HGT, ICGT, 3IN, the REIT cohort): their NAV
lives inside portfolio updates, factsheet announcements and results. This
probe fetches a SMALL sample of those bodies - the newest few
factsheet-shaped announcements per fund, ~1.5s throttle, well inside the
crawler's existing footprint - and records the text heads plus any
factsheet/document URLs found in them, so extraction rules are written
against real text rather than guessed.

Needs: state group uk_announcements (listing caches). Network: Investegate
announcement pages only, same paths the dividends crawler already uses.
Output: reports/build/uk_factsheet_probe.json. Evidence only.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# headline shapes that plausibly carry or link a NAV for this cohort
FACTSHEET_HEAD = re.compile(
    r"portfolio update|monthly update|quarterly update|interim update|"
    r"fact\s?sheet|investor (?:update|report)|trading update|"
    r"quarterly report|business update|EPRA|"
    r"(?:half[- ]year|annual|interim|final) (?:results|report)|results\b",
    re.I)
NOISE = re.compile(r"result of|notice of|agm|dividend declaration|"
                   r"holding|voting rights|director", re.I)
SINCE = "2025-06-01"
PER_FUND = 3
THROTTLE_S = 1.5
UA = {"User-Agent": "uk-cef research probe (contact via repo)"}

# URLs inside bodies that look like hosted factsheets/documents
DOC_URL = re.compile(r"https?://[^\s\"'<>]+?"
                     r"(?:factsheet|fact-sheet|nav|investor|document)"
                     r"[^\s\"'<>]*", re.I)


def main() -> int:
    probe = json.loads(Path("reports/build/uk_nav_reach_probe.json").read_text())
    cohort = [p for p in probe["per_fund"]
              if p.get("status") == "ok"
              and p.get("matched_by_current_pattern", 0) == 0]
    print(f"cohort: {len(cohort)} funds with no NAV-shaped headline")

    listings = Path("data/investegate_cache/listings")
    files = {f.stem.upper(): f for f in listings.glob("*.csv")} \
        if listings.exists() else {}
    sess = requests.Session()
    sess.headers.update(UA)

    out = []
    for p in cohort:
        t = (p.get("ticker") or "").upper()
        f = files.get(t)
        if f is None:
            out.append({"ticker": t, "status": "no_listing_cache"})
            continue
        df = pd.read_csv(f, dtype=str)
        if "headline" not in df.columns:
            out.append({"ticker": t, "status": "no_headline_column"})
            continue
        recent = df[df["date"].fillna("") >= SINCE]
        heads = recent["headline"].fillna("")
        cand = recent[heads.str.contains(FACTSHEET_HEAD)
                      & ~heads.str.contains(NOISE)
                      & recent["url"].notna()]
        cand = cand.sort_values("date", ascending=False).head(PER_FUND)
        fund = {"ticker": t, "name": p.get("name"), "status": "ok",
                "candidates": int(len(cand)), "samples": []}
        for r in cand.itertuples(index=False):
            url = r.url if str(r.url).startswith("http") \
                else f"https://www.investegate.co.uk{r.url}"
            try:
                resp = sess.get(url, timeout=30)
                html = resp.text
            except Exception as exc:  # noqa: BLE001
                fund["samples"].append({"date": r.date,
                                        "headline": r.headline,
                                        "error": str(exc)})
                time.sleep(THROTTLE_S)
                continue
            soup = BeautifulSoup(html, "html.parser")
            text = " ".join(soup.get_text(" ").split())
            links = sorted(set(DOC_URL.findall(html)))[:6]
            # keep the stretch around NAV-ish words so the rule-writer sees
            # the phrasing, not just the page head
            snips = []
            for m in re.finditer(r"(?:NAV|net asset|EPRA|NTA|net tangible)",
                                 text, re.I):
                snips.append(text[max(0, m.start() - 120):m.start() + 240])
                if len(snips) >= 4:
                    break
            fund["samples"].append({
                "date": r.date, "headline": r.headline, "url": url,
                "text_head": text[:1200], "nav_context": snips,
                "doc_links": links})
            time.sleep(THROTTLE_S)
        out.append(fund)

    res = {"since": SINCE, "cohort": len(cohort), "funds": out}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/uk_factsheet_probe.json").write_text(
        json.dumps(res, indent=2, default=str))
    n_ok = sum(1 for f in out if f.get("status") == "ok")
    n_ctx = sum(1 for f in out for s in f.get("samples", [])
                if s.get("nav_context"))
    print(f"fetched samples for {n_ok} funds; "
          f"{n_ctx} samples carry NAV-ish context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
