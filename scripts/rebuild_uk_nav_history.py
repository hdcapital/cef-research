"""Rebuild UK NAV history rows from the S3 announcement archive.

Why this exists
---------------
`archive_uk_navs.py` marks an announcement done in the S3 manifest as soon
as its text is PUT to S3, but the parsed row only becomes durable at the
end-of-run parquet commit. Any run that died between those two points -
and several did (the `nan or x` crash, the wholesale `git add -f` abort,
the push race) - permanently lost those rows from the parquet while the
manifest suppressed re-fetching them forever. The committed history
therefore has holes (2023-2024 is entirely absent) that no amount of
re-running the archiver can fill: its queue is legitimately empty.

The announcement TEXT is not lost - it is in the bucket under
uk/nav_announcements/{TICKER}/{date}_{annid}.json.gz. So the rows are
rebuilt from the archive itself. No Investegate request is made: this
re-derives already-collected observations from their stored source text,
so it adds zero load to the publisher and introduces no new data.

Rows whose stored status is `no_nav_parsed` are re-parsed from the stored
text, because the parser has improved since they were archived. A row that
still will not parse is written with that status - never guessed.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd

from cef_live.harvest_nav import parse_uk_nav_text

BUCKET = os.environ.get("S3_BUCKET", "")
PREFIX = "uk/nav_announcements/"
SHARD = int(os.environ.get("SHARD_INDEX", "0"))
SHARDS = max(1, int(os.environ.get("SHARD_COUNT", "1")))
if SHARD >= SHARDS:
    raise SystemExit(f"SHARD_INDEX {SHARD} out of range for SHARD_COUNT {SHARDS}")
DEADLINE_MIN = int(os.environ.get("UK_REBUILD_DEADLINE_MIN", "300"))
WORKERS = int(os.environ.get("UK_REBUILD_WORKERS", "24"))
START = time.time()
OUT = Path(f"data/uk_nav_history_rebuild_s{SHARD}of{SHARDS}.parquet")


def ann_id_from_key(key: str) -> str | None:
    """Recover the ann_id from an archive key, or None if it is not one.

    Keys are uk/nav_announcements/{TICKER}/{date}_{annid}.json.gz. The date
    prefix is stripped on the FIRST underscore only, because ann_ids
    themselves contain underscores.
    """
    if "manifest" in key or not key.endswith(".json.gz"):
        return None
    stem = key.rsplit("/", 1)[-1][: -len(".json.gz")]
    if "_" not in stem:
        return None
    return stem.split("_", 1)[1] or None


def known_ann_ids() -> set[str]:
    """Every ann_id already durable in a committed parquet, any layout."""
    ids: set[str] = set()
    for f in sorted(Path("data").glob("uk_nav_history*.parquet")):
        try:
            d = pd.read_parquet(f, columns=["ann_id"])
        except Exception:  # noqa: BLE001
            continue
        ids |= set(d["ann_id"].astype(str))
    return ids


def main() -> int:
    if not BUCKET:
        print("S3_BUCKET unset - nothing to rebuild from")
        return 0
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))

    known = known_ann_ids()
    print(f"committed history holds {len(known):,} ann_ids")

    # List the archive. Keys carry {TICKER}/{date}_{annid}.json.gz, so the
    # missing set is computable from the listing alone - only the bodies of
    # genuinely missing rows are ever fetched.
    missing: list[tuple[str, str]] = []
    seen = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=PREFIX):
        for o in page.get("Contents", []):
            key = o["Key"]
            ann_id = ann_id_from_key(key)
            if ann_id is None:
                continue
            seen += 1
            if ann_id in known:
                continue
            if zlib.crc32(ann_id.encode()) % SHARDS != SHARD:
                continue
            missing.append((key, ann_id))
    print(f"archive holds {seen:,} announcements; "
          f"{len(missing):,} missing from this shard's committed history")
    if not missing:
        return 0

    def load(item: tuple[str, str]) -> dict | None:
        key, ann_id = item
        try:
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            rec = json.loads(gzip.decompress(body))
        except Exception:  # noqa: BLE001
            return None
        out = {
            "ticker": rec.get("ticker"),
            "ann_id": str(rec.get("ann_id", ann_id)),
            "ann_date": rec.get("ann_date"),
            "nav_date": rec.get("nav_date"),
            "nav_cum_pence": rec.get("nav_cum_pence"),
            "nav_ex_pence": rec.get("nav_ex_pence"),
            "cum_assumed": bool(rec.get("cum_assumed", False)),
            "status": rec.get("status", "no_nav_parsed"),
            "source": "s3_rebuild",
        }
        # the parser has improved since these were archived; re-derive from
        # the stored text rather than leave a real observation unparsed
        if out["status"] != "parsed" and rec.get("text"):
            p = parse_uk_nav_text(rec["text"])
            if "nav_cum_pence" in p:
                out.update({
                    "nav_date": p.get("asat", out["nav_date"]),
                    "nav_cum_pence": p.get("nav_cum_pence"),
                    "nav_ex_pence": p.get("nav_ex_pence"),
                    "cum_assumed": bool(p.get("cum_assumed", False)),
                    "status": "parsed",
                    "source": "s3_rebuild_reparsed",
                })
        return out

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, rec in enumerate(ex.map(load, missing), start=1):
            if rec is not None:
                rows.append(rec)
            if i % 20000 == 0:
                print(f"  {i:,}/{len(missing):,} read, {len(rows):,} rebuilt")
            if (time.time() - START) > DEADLINE_MIN * 60:
                print("deadline reached - writing what is rebuilt so far")
                break

    if rows:
        new = pd.DataFrame(rows)
        if OUT.exists():
            new = pd.concat([pd.read_parquet(OUT), new],
                            ignore_index=True).drop_duplicates("ann_id")
        new.to_parquet(OUT, index=False)

    parsed = sum(1 for r in rows if r["status"] == "parsed")
    reparsed = sum(1 for r in rows if r["source"] == "s3_rebuild_reparsed")
    dt = pd.to_datetime([r["ann_date"] for r in rows], errors="coerce")
    status = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shard": f"{SHARD}of{SHARDS}",
        "archive_objects": seen,
        "missing_this_shard": len(missing),
        "rebuilt": len(rows),
        "parsed": parsed,
        "reparsed_from_text": reparsed,
        "ann_date_min": str(pd.Series(dt).min()),
        "ann_date_max": str(pd.Series(dt).max()),
    }
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    Path(f"outputs/live/uk_nav_rebuild_status_s{SHARD}of{SHARDS}.json").write_text(
        json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
