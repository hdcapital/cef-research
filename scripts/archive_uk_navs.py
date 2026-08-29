"""UK daily-NAV history backfill - the UK counterpart of the ASX archive.

The Investegate crawl cache already indexes every announcement (URL, date,
headline) for every fund the dividends crawler paged - including the
thousands of daily "Net Asset Value(s)" RNS notices per trust back to
~2006. This job walks that index newest-first, fetches each NAV
announcement page (1.5s throttle, resumable), parses cum/ex-income NAV
per share with the evidence-tested parser (cef_live.harvest_nav), and:

  - appends parsed observations to data/uk_nav_history.parquet
    (committed - the point-in-time daily NAV panel for backtesting the
    intramonth hypothesis directly);
  - archives the announcement text to S3 under
    uk/nav_announcements/{TICKER}/{date}_{annid}.json.gz (append-only);
  - keeps its done-set in the bucket (uk/nav_announcements/manifest.json)
    so any run resumes exactly where the last stopped.

Unparsed pages are recorded with status - never guessed. Budgets:
UK_NAV_BUDGET pages/run (default 7000), UK_NAV_DEADLINE_MIN (default 300).
Newest-first means the most decision-relevant history lands first.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

from cef_live.harvest_nav import parse_uk_nav_text  # evidence-tested parser

BUCKET = os.environ.get("S3_BUCKET", "")
# see archive_to_s3.py: shards split the queue deterministically so one run
# covers what would otherwise need many nightly restarts
SHARD = int(os.environ.get("SHARD_INDEX", "0"))
SHARDS = max(1, int(os.environ.get("SHARD_COUNT", "1")))
BUDGET = int(os.environ.get("UK_NAV_BUDGET", "7000"))
DEADLINE_MIN = int(os.environ.get("UK_NAV_DEADLINE_MIN", "300"))
START = time.time()
THROTTLE = 1.5
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
CACHE = Path("data/investegate_cache/listings")
HIST = Path("data/uk_nav_history.parquet" if SHARDS == 1
            else f"data/uk_nav_history_s{SHARD}of{SHARDS}.parquet")
MANIFEST_KEY = ("uk/nav_announcements/manifest.json" if SHARDS == 1
                else f"uk/nav_announcements/manifest_s{SHARD}of{SHARDS}.json")
NAV_PAT = re.compile(r"net asset value", re.I)

_last = 0.0


def get(s: requests.Session, url: str) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return s.get(url, timeout=60)


def main() -> int:
    from bs4 import BeautifulSoup

    s3 = None
    done: set[str] = set()
    if BUCKET:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
        try:
            done = set(json.loads(
                s3.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)["Body"].read()).get("ids", []))
        except Exception:  # noqa: BLE001
            done = set()
    print(f"manifest: {len(done)} NAV announcements already archived")

    # work queue from the existing listings index, newest first
    work = []
    for f in sorted(CACHE.glob("*.csv")) if CACHE.exists() else []:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if not {"ann_id", "date", "headline", "url"} <= set(df.columns):
            continue
        nav = df[df["headline"].fillna("").str.contains(NAV_PAT) & df["url"].notna()]
        for r in nav.itertuples(index=False):
            if str(r.ann_id) not in done:
                work.append({"ticker": f.stem, "ann_id": str(r.ann_id),
                             "date": r.date or "", "url": r.url})
    work.sort(key=lambda w: w["date"], reverse=True)
    if SHARDS > 1:
        import zlib
        work = [w for w in work
                if zlib.crc32(w["ann_id"].encode()) % SHARDS == SHARD]
    print(f"queue: {len(work)} NAV announcements to fetch (newest first, "
          f"shard {SHARD + 1}/{SHARDS})")

    hist_rows = []
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    fetched = failed = 0
    for w in work:
        if fetched >= BUDGET or (time.time() - START) > DEADLINE_MIN * 60:
            print("budget/deadline reached - stopping cleanly")
            break
        url = w["url"]
        if url.startswith("/"):
            url = "https://www.investegate.co.uk" + url
        try:
            r = get(sess, url)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        fetched += 1
        if r.status_code != 200:
            failed += 1
            done.add(w["ann_id"])       # permanent (dead page) - keep moving
            continue
        text = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" "))
        # trim page chrome: the RNS body sits between the headline block and
        # the RNS footer; keep a generous window rather than risk clipping
        m = re.search(r"(?:LEI:|the \"Company\"|announces|Net Asset Value)", text)
        body = text[max(0, (m.start() - 200) if m else 0):]
        body = body.split("This information is provided by RNS")[0][:12000]
        parsed = parse_uk_nav_text(body)
        rec = {"ticker": w["ticker"], "ann_id": w["ann_id"], "ann_date": w["date"],
               "nav_date": parsed.get("asat", w["date"]),
               "nav_cum_pence": parsed.get("nav_cum_pence"),
               "nav_ex_pence": parsed.get("nav_ex_pence"),
               "cum_assumed": bool(parsed.get("cum_assumed", False)),
               "status": "parsed" if "nav_cum_pence" in parsed else "no_nav_parsed"}
        hist_rows.append(rec)
        if s3 is not None:
            key = f"uk/nav_announcements/{w['ticker']}/{w['date']}_{w['ann_id']}.json.gz"
            payload = gzip.compress(json.dumps({**rec, "url": url, "text": body}).encode())
            s3.put_object(Bucket=BUCKET, Key=key, Body=payload)
        done.add(w["ann_id"])
        if fetched % 500 == 0 and s3 is not None:
            s3.put_object(Bucket=BUCKET, Key=MANIFEST_KEY,
                          Body=json.dumps({"ids": sorted(done)}).encode())
            print(f"  progress: {fetched} fetched this run, {len(done)} total archived")

    if s3 is not None:
        s3.put_object(Bucket=BUCKET, Key=MANIFEST_KEY,
                      Body=json.dumps({"ids": sorted(done)}).encode())

    new = pd.DataFrame(hist_rows)
    if len(new):
        if HIST.exists():
            old = pd.read_parquet(HIST)
            new = pd.concat([old, new], ignore_index=True).drop_duplicates("ann_id")
        new.to_parquet(HIST, index=False)

    parsed_n = int((new["status"] == "parsed").sum()) if len(new) else 0
    status = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fetched_this_run": fetched, "failed": failed,
              "archived_total": len(done), "queue_remaining": max(0, len(work) - fetched),
              "history_rows": int(len(new)) if len(new) else
              (int(len(pd.read_parquet(HIST))) if HIST.exists() else 0),
              "parse_rate_this_run": round(parsed_n / max(1, len(hist_rows)), 4)
              if hist_rows else None}
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    Path("outputs/live/uk_nav_archive_status.json" if SHARDS == 1
     else f"outputs/live/uk_nav_archive_status_s{SHARD}.json").write_text(json.dumps(status, indent=2))
    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
