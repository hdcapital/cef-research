"""Where the extracted ASX facts live, and how to get them.

The deterministic pass (`python -m au_lic.extract.runner deterministic`)
writes `data/asx_extract/facts_det_*.parquet` and uploads each shard to S3
under `asx/extract/`. S3 is the system of record; the local directory is a
cache that a fresh CI runner does not have.

That mattered more than it looks. `cef_live.cli._own_nav_history("AU")`
reads exactly those files, and the nightly workflow restored the raw
sources, the announcement index and the tickers - but never the extracted
facts. So the directory was always empty on a runner, the "our own extracted
NAV history" tier silently had nothing in it for Australia, and every ASX
fund fell back to the aggregator's monthly panel print. 26,274 extracted NAV
observations across 147 tickers were sitting in S3 while the live table said
the funds had no NAV route of their own.

Nothing here parses or fetches an announcement; it only locates facts that
have already been extracted.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

LOCAL_DIR = Path("data/asx_extract")
S3_PREFIX = "asx/extract/facts_det_"
GLOB = "facts_det_*.parquet"


def local_paths(local_dir: Path = LOCAL_DIR) -> list[Path]:
    return sorted(local_dir.glob(GLOB))


def fetch_from_s3(local_dir: Path = LOCAL_DIR, bucket: str | None = None) -> int:
    """Download the deterministic fact shards from S3. Returns files fetched.

    A missing bucket, missing credentials or an S3 error is reported and
    returns 0 - the caller then has no AU NAV history, which is a visible
    gap, rather than an exception that takes the whole nightly down.
    """
    bucket = bucket if bucket is not None else os.environ.get("S3_BUCKET", "")
    if not bucket:
        log.info("S3_BUCKET unset - no extracted ASX facts to restore")
        return 0
    try:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
        local_dir.mkdir(parents=True, exist_ok=True)
        got = 0
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=S3_PREFIX):
            for o in page.get("Contents", []):
                dest = local_dir / Path(o["Key"]).name
                if dest.exists() and dest.stat().st_size == o.get("Size", -1):
                    continue
                s3.download_file(bucket, o["Key"], str(dest))
                got += 1
        log.info("restored %d extracted ASX fact shard(s) from s3://%s/%s",
                 got, bucket, S3_PREFIX)
        return got
    except Exception as exc:  # noqa: BLE001
        log.warning("could not restore extracted ASX facts (%s: %s)",
                    type(exc).__name__, exc)
        return 0


def load(local_dir: Path = LOCAL_DIR, allow_s3: bool = True) -> pd.DataFrame:
    """Every extracted fact row, restoring from S3 when the cache is empty."""
    paths = local_paths(local_dir)
    if not paths and allow_s3:
        fetch_from_s3(local_dir)
        paths = local_paths(local_dir)
    frames = []
    for f in paths:
        try:
            frames.append(pd.read_parquet(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("unreadable fact shard %s (%s)", f, exc)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # A reparse re-extracts announcements the corpus already holds - its
    # corrected rows must REPLACE the old ones, never sit beside them as a
    # second NAV for the same document. Per announcement, only the rows
    # from the newest extraction survive; rows from before the
    # `extracted_at` stamp existed rank oldest.
    if "announcement_id" in out.columns:
        if "extracted_at" in out.columns:
            ts = pd.to_datetime(out["extracted_at"], errors="coerce", utc=True)
        else:
            ts = pd.Series(pd.NaT, index=out.index,
                           dtype="datetime64[ns, UTC]")
        ts = ts.fillna(pd.Timestamp(0, tz="UTC"))
        out = out.assign(_ts=ts)
        latest = out.groupby("announcement_id")["_ts"].transform("max")
        out = out[out["_ts"] == latest].drop(columns="_ts")
    return out.reset_index(drop=True)


def nav_observations(local_dir: Path = LOCAL_DIR,
                     allow_s3: bool = True) -> pd.DataFrame:
    """security_id, nav_date, nav_value, nav_unit - one row per observation.

    Values are per-share NTA in AUD as the extractor recorded them; the unit
    is stated so units.normalise does any conversion once, in one place.
    """
    cols = ["security_id", "nav_date", "nav_value", "nav_unit"]
    facts = load(local_dir, allow_s3=allow_s3)
    if not len(facts) or "section" not in facts.columns:
        return pd.DataFrame(columns=cols)
    nav = facts[facts["section"] == "nav_observations"]
    if not len(nav) or "ticker" not in nav.columns:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "security_id": "ASX:" + nav["ticker"].astype(str).str.upper(),
        "nav_date": nav.get("valuation_date"),
        "nav_value": pd.to_numeric(nav.get("nav_per_share"), errors="coerce"),
        "nav_unit": "AUD",
    })
    out = out.dropna(subset=["nav_value"])
    # Quarantine: funds whose extracted series the exchange's own published
    # NTA contradicts most of the time (validate mode writes the list, e.g.
    # HCF's constant 4.12 face value). The rows stay in the store - this
    # only keeps a known-wrong series out of the LIVE feed until a parser
    # or rule fix clears it through validation.
    qf = Path("outputs/au/au_nta_quarantine.csv")
    if qf.exists():
        try:
            q = {f"ASX:{str(t).upper()}"
                 for t in pd.read_csv(qf).get("ticker", [])}
        except Exception:  # noqa: BLE001
            q = set()
        if q:
            before = len(out)
            out = out[~out["security_id"].isin(q)]
            if before != len(out):
                log.info("quarantined %d NAV rows across %d funds "
                         "(au_nta_quarantine.csv)", before - len(out), len(q))
    return out
