"""Durable working state in S3, not in the evictable Actions cache.

GitHub evicts caches after 7 days unused or when a repo exceeds 10GB. That
is fine for pip wheels and fatal for anything expensive: the UK listings
index represents ~750k throttled page fetches, the PDF extract cache ~700
downloads plus parsing, and the raw source files a decade of archive
downloads. None of that should live somewhere it can silently vanish.

So S3 is the system of record for working state, and the Actions cache is
demoted to a speed-up that nothing depends on. Each logical group is a
single tarball - one object beats thousands of tiny PUTs for a directory
of small JSON files - versioned by content hash so an unchanged group is
not re-uploaded.

  python scripts/sync_state.py pull      # restore everything at job start
  python scripts/sync_state.py push      # persist at job end
  python scripts/sync_state.py pull --groups uk_announcements
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BUCKET = os.environ.get("S3_BUCKET", "")
PREFIX = "state/"

# name -> paths. Grouped by what a job actually needs, so a UK job does not
# pull the ASX archive to read one index.
GROUPS: dict[str, list[str]] = {
    "uk_announcements": ["data/investegate_cache"],
    "asx_index": ["data/asx_ann_cache/asx1/lic_announcement_index.parquet",
                  "data/asx_ann_cache/asx1/sweep_state.json"],
    "asx_pdf_extract": ["data/asx_ann_cache/asx1/pdf_extract"],
    "raw_aic": ["data/raw/aic", "data/manifest.csv"],
    "raw_asx": ["data/raw/asx", "data/au_manifest.csv"],
    "tickers": ["config/resolved_tickers.csv"],
    "uk_daily": ["data/uk"],
}


def _client():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION"))


def _dir_hash(paths: list[str]) -> str:
    """Content hash over the group's files - cheap change detection."""
    h = hashlib.md5()
    for p in sorted(paths):
        path = Path(p)
        if path.is_file():
            h.update(path.name.encode())
            h.update(str(path.stat().st_size).encode())
        elif path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file():
                    h.update(str(f.relative_to(path)).encode())
                    h.update(str(f.stat().st_size).encode())
    return h.hexdigest()[:16]


def _merge_from_s3(s3, name: str) -> int:
    """Restore any file this runner does NOT have, before tarring the group.

    A group is one tarball, so a push REPLACES it. When several sharded jobs
    push the same group, each one uploads its own slice and the last to
    finish silently deletes the others' work. Measured on 2026-08-31: six
    uk-index-gap shards indexed 34,287 NAV announcements between them and
    the archiver's queue then contained 9,057 - exactly shard 4's total,
    because shard 4 pushed last. The other five shards' 25,230 were gone,
    with every job reporting success.

    So a push now starts by restoring whatever the bucket holds that this
    runner lacks. Local files win (this run's work is newer); remote-only
    files survive. Shards touch disjoint per-ticker files, so the union is
    exactly right for them.

    This narrows the race rather than closing it - two pushes seconds apart
    can still both read the old copy. A job whose shards must not lose each
    other's work should be one job, and uk-index-gap now is.
    """
    key = f"{PREFIX}{name}.tar.gz"
    restored = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            s3.download_file(BUCKET, key, tmp.name)
            with tarfile.open(tmp.name, "r:gz") as tar:
                for m in tar.getmembers():
                    if not m.isfile() or Path(m.name).exists():
                        continue
                    tar.extract(m, ".")
                    restored += 1
        Path(tmp.name).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        return 0
    return restored


def push(groups: list[str]) -> int:
    if not BUCKET:
        print("S3_BUCKET unset - nothing to push")
        return 0
    s3 = _client()
    for name in groups:
        if [p for p in GROUPS[name] if Path(p).exists()]:
            n = _merge_from_s3(s3, name)
            if n:
                print(f"  {name}: merged {n} file(s) held only in S3")
        paths = [p for p in GROUPS[name] if Path(p).exists()]
        if not paths:
            print(f"  {name}: nothing on disk, skipped")
            continue
        sig = _dir_hash(paths)
        key = f"{PREFIX}{name}.tar.gz"
        try:
            head = s3.head_object(Bucket=BUCKET, Key=key)
            if head.get("Metadata", {}).get("sig") == sig:
                print(f"  {name}: unchanged ({sig}), not re-uploaded")
                continue
        except Exception:  # noqa: BLE001
            pass
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            with tarfile.open(tmp.name, "w:gz") as tar:
                for p in paths:
                    tar.add(p, arcname=p)
            size = Path(tmp.name).stat().st_size
            s3.upload_file(tmp.name, BUCKET, key,
                           ExtraArgs={"Metadata": {"sig": sig}})
            print(f"  {name}: pushed {size/1e6:.1f} MB ({sig})")
        Path(tmp.name).unlink(missing_ok=True)
    return 0


def pull(groups: list[str]) -> int:
    if not BUCKET:
        print("S3_BUCKET unset - nothing to pull")
        return 0
    s3 = _client()
    for name in groups:
        key = f"{PREFIX}{name}.tar.gz"
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                s3.download_file(BUCKET, key, tmp.name)
                with tarfile.open(tmp.name, "r:gz") as tar:
                    tar.extractall(".")
                size = Path(tmp.name).stat().st_size
                print(f"  {name}: restored {size/1e6:.1f} MB")
            Path(tmp.name).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: not in S3 yet ({type(exc).__name__}) - will be "
                  "created by this run")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("pull", "push"):
        print(__doc__)
        return 1
    action = sys.argv[1]
    groups = list(GROUPS)
    if "--groups" in sys.argv:
        want = sys.argv[sys.argv.index("--groups") + 1].split(",")
        groups = [g for g in want if g in GROUPS]
        unknown = [g for g in want if g not in GROUPS]
        if unknown:
            print(f"unknown groups ignored: {unknown}")
    print(f"{action}ing state groups: {groups}")
    return push(groups) if action == "push" else pull(groups)


if __name__ == "__main__":
    sys.exit(main())
