"""Archive the AU LIC raw source documents to S3.

Stores, permanently and independently of GitHub caches:
1. every ASX Investment Products monthly report XLSX (2017->present);
2. every announcement PDF for every LIC/LIT code in the panel universe,
   including delisted funds, using the committed market-wide announcement
   index (data/asx_ann_cache/asx1/lic_announcement_index.parquet) and the
   open announcements.asx.com.au PDF host;
3. the announcement index parquet itself.

Layout:
  s3://$S3_BUCKET/asx/monthly-reports/<file>.xlsx
  s3://$S3_BUCKET/asx/announcements/<CODE>/<YYYY-MM-DD>_<id>.pdf
  s3://$S3_BUCKET/asx/index/lic_announcement_index.parquet
  s3://$S3_BUCKET/asx/index/uploaded_manifest.json   (resume state)

Fully resumable: the manifest of uploaded announcement ids lives in the
bucket, so any run continues where the last stopped. Downloads keep the
project's 1.5s throttle toward ASX; wall-clock and count budgets keep each
run inside CI limits. ~86.5k PDFs total => roughly a week of daily runs.

Env: S3_BUCKET, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY;
optional ARCHIVE_PDF_BUDGET (default 12000), ARCHIVE_DEADLINE_MIN (330).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import boto3
import pandas as pd
import requests

BUCKET = os.environ.get("S3_BUCKET", "")
# Sharding: a GitHub Actions job is capped at 6h, and the full backfill is
# ~36h of throttled requests. Rather than restart nightly, the work is split
# deterministically across parallel jobs in ONE run - each shard owns a
# disjoint slice (crc32(id) %% count) and its OWN manifest key, so concurrent
# shards can never clobber each other's progress.
SHARD = int(os.environ.get("SHARD_INDEX", "0"))
SHARDS = max(1, int(os.environ.get("SHARD_COUNT", "1")))
THROTTLE = 1.5
PDF_BUDGET = int(os.environ.get("ARCHIVE_PDF_BUDGET", "20000"))
DEADLINE_MIN = int(os.environ.get("ARCHIVE_DEADLINE_MIN", "330"))
START = time.time()
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
INDEX_F = Path("data/asx_ann_cache/asx1/lic_announcement_index.parquet")
MANIFEST_KEY = ("asx/index/uploaded_manifest.json" if SHARDS == 1
                else f"asx/index/uploaded_manifest_s{SHARD}of{SHARDS}.json")

_last = 0.0


def throttled_get(s: requests.Session, url: str) -> requests.Response:
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return s.get(url, timeout=90)


def main() -> int:
    if not BUCKET:
        print("S3_BUCKET not set - nothing to do")
        return 0
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    stats = {"xlsx_uploaded": 0, "pdf_uploaded": 0, "pdf_failed": 0, "skipped_existing": 0}

    # ---- 1. monthly report XLSX files (small; idempotent re-upload check) ----
    raw = Path("data/raw/asx")
    existing = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix="asx/monthly-reports/"):
        existing.update(o["Key"] for o in page.get("Contents", []))
    for f in (sorted(raw.glob("*.xlsx")) if (raw.exists() and SHARD == 0) else []):
        key = f"asx/monthly-reports/{f.name}"
        if key in existing:
            continue
        s3.upload_file(str(f), BUCKET, key)
        stats["xlsx_uploaded"] += 1
    print(f"monthly reports: {stats['xlsx_uploaded']} new uploaded")

    # ---- 2. the announcement index itself ----
    if INDEX_F.exists():
        s3.upload_file(str(INDEX_F), BUCKET, "asx/index/lic_announcement_index.parquet")
    else:
        print(f"WARNING: {INDEX_F} missing - announcement stage skipped")
        return 0

    # ---- 3. announcement PDFs, resumable via bucket-held manifest ----
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)
        done = set(json.loads(obj["Body"].read()).get("ids", []))
    except s3.exceptions.NoSuchKey:
        # first sharded run: seed from the pre-sharding single manifest so
        # already-archived documents are never re-fetched
        done = set()
        if SHARDS > 1:
            try:
                legacy = s3.get_object(Bucket=BUCKET,
                                       Key="asx/index/uploaded_manifest.json")
                done = set(json.loads(legacy["Body"].read()).get("ids", []))
                print(f"seeded shard from legacy manifest: {len(done)} ids")
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        print(f"manifest read failed ({exc}); assuming empty")
        done = set()
    print(f"manifest: {len(done)} PDFs already archived")

    idx = pd.read_parquet(INDEX_F)
    idx = idx[idx["url"].notna() & (idx["url"] != "")]
    idx["day"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce") \
        .dt.strftime("%Y-%m-%d")
    todo = idx[~idx["id"].astype(str).isin(done)]
    if SHARDS > 1:
        import zlib
        mine = todo["id"].astype(str).map(
            lambda i: zlib.crc32(i.encode()) % SHARDS == SHARD)
        todo = todo[mine]
    print(f"announcements: {len(idx)} indexed, {len(todo)} to fetch "
          f"(shard {SHARD + 1}/{SHARDS})")

    def flush_manifest():
        s3.put_object(Bucket=BUCKET, Key=MANIFEST_KEY,
                      Body=json.dumps({"ids": sorted(done)}).encode())

    fetched = 0
    for row in todo.itertuples(index=False):
        if fetched >= PDF_BUDGET or (time.time() - START) > DEADLINE_MIN * 60:
            print("budget/deadline reached - stopping cleanly")
            break
        try:
            r = throttled_get(sess, row.url)
        except Exception:  # noqa: BLE001
            stats["pdf_failed"] += 1
            continue
        fetched += 1
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            # recorded as done with a marker key so we don't loop on it
            # forever; the marker preserves WHAT failed for later audit
            s3.put_object(Bucket=BUCKET,
                          Key=f"asx/announcements/_unavailable/{row.id}.json",
                          Body=json.dumps({"id": str(row.id), "code": row.code,
                                           "url": row.url, "headline": row.headline,
                                           "http": r.status_code}).encode())
            done.add(str(row.id))
            stats["pdf_failed"] += 1
            continue
        key = f"asx/announcements/{row.code}/{row.day}_{row.id}.pdf"
        s3.put_object(Bucket=BUCKET, Key=key, Body=r.content,
                      Metadata={"headline": str(row.headline)[:1000],
                                "source-url": str(row.url)[:1000]})
        done.add(str(row.id))
        stats["pdf_uploaded"] += 1
        if stats["pdf_uploaded"] % 500 == 0:
            flush_manifest()
            print(f"  progress: {stats['pdf_uploaded']} uploaded this run, "
                  f"{len(done)}/{len(idx)} total")
    flush_manifest()

    stats["archived_total"] = len(done)
    stats["remaining"] = int(len(idx) - len(done))
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    Path("outputs/au/au_s3_archive_status.json" if SHARDS == 1
     else f"outputs/au/au_s3_archive_status_s{SHARD}.json").write_text(json.dumps(stats, indent=2))
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
