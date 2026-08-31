"""The daily UK discount job: NAV from S3, prices from the exchange, join.

One command, three idempotent stages, each of which can be run alone:

  nav       re-parse the S3 NAV announcement archive + the nightly live
            snapshots into a point-in-time NAV panel (incremental: only
            announcements not already extracted are read)
  prices    top up the daily close panel for every live fund (incremental:
            a held fund costs one small request, a new one a full history)
  discount  join them into the daily discount panel and the small committed
            deliverables

Run it every evening after the London close and the whole thing costs a few
hundred requests and a couple of megabytes of new parquet. Run it on an
empty checkout with bucket credentials and it rebuilds from 2007.

Nothing here fabricates. A fund with no parseable NAV has no discount, a
fund Yahoo will not serve has no price, and both appear in
``uk_discount_coverage.csv`` as measured gaps rather than as absences.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import uk_discount as DISC
from . import uk_nav_panel as NAV
from . import uk_prices_history as PH

OUT_DIR = Path("outputs/live")
STATUS = OUT_DIR / "uk_daily_status.json"


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def stage_nav(universe: pd.DataFrame, bucket: str, *, deadline_min: float,
              shard: int = 0, shards: int = 1,
              use_snapshots: bool = True) -> tuple[pd.DataFrame, dict]:
    """Build/extend the NAV panel from S3 (plus the committed seed)."""
    tickers = set(universe["ticker"])
    sid_to_ticker = dict(zip(universe["security_id"], universe["ticker"]))

    held = NAV.read_panel()
    seed = NAV.extract_from_committed(tickers=tickers)
    stats: dict = {"held_rows": int(len(held)), "seed_rows": int(len(seed))}

    frames = [f for f in (held, seed) if len(f)]
    if bucket:
        skip = NAV.known_ann_ids() | set(seed["ann_id"].astype(str))
        arch, s1 = NAV.extract_from_archive(
            bucket, tickers=tickers, skip_ann_ids=skip,
            deadline_min=deadline_min, shard=shard, shards=shards)
        stats["archive"] = s1
        if len(arch):
            frames.append(arch)
        if use_snapshots:
            snap, s2 = NAV.extract_from_snapshots(
                bucket, tickers=tickers, sid_to_ticker=sid_to_ticker)
            stats["snapshots"] = s2
            if len(snap):
                frames.append(snap)
    else:
        stats["archive"] = "s3_skipped_no_bucket"

    frames = [f for f in frames if len(f)]
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=NAV.COLUMNS)
    stats["raw_rows"] = int(len(raw))
    stats["unparsed_rows"] = int((raw.get("quality") == "no_nav_parsed").sum()) \
        if "quality" in raw.columns else 0
    panel = NAV.normalise(raw)
    stats["panel_rows"] = int(len(panel))
    stats["panel_funds"] = int(panel["ticker"].nunique()) if len(panel) else 0
    NAV.write_panel(panel)
    per_fund, summary = NAV.quality_report(panel)
    if len(per_fund):
        _write_csv(per_fund, OUT_DIR / "uk_nav_quality.csv")
    stats["quality"] = summary
    return panel, stats


def stage_prices(universe: pd.DataFrame, *, deadline_min: float,
                 full: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    held = PH.read_prices()
    px, report = PH.update(list(universe["ticker"]), existing=held, full=full,
                           deadline_min=deadline_min)
    PH.write_prices(px)
    stats = {"held_bars": int(len(held)), "panel_bars": int(len(px)),
             "funds_with_prices": int(px["ticker"].nunique()) if len(px) else 0,
             "funds_no_history": int((report.get("status") == "no_history").sum())
             if len(report) else 0}
    return px, report, stats


def stage_discount(universe: pd.DataFrame, nav: pd.DataFrame,
                   px: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    freq = NAV.publication_frequency(nav)
    units = PH.reconcile_units(nav, px)
    panel = DISC.build(nav, px, frequency=freq, units=units)
    if len(panel):
        DISC.write_panel(panel)
    _write_csv(units, OUT_DIR / "uk_price_unit_reconciliation.csv")

    latest = DISC.latest_snapshot(DISC.with_zscores(panel), universe)
    cover = DISC.coverage_report(panel, universe, freq)
    ready = NAV.archive_readiness(universe, nav)
    _write_csv(freq, OUT_DIR / "uk_nav_frequency.csv")
    _write_csv(cover, OUT_DIR / "uk_discount_coverage.csv")
    _write_csv(ready, OUT_DIR / "uk_nav_archive_readiness.csv")
    if len(latest):
        keep = [c for c in ["ticker", "name", "sector", "date", "close_raw",
                            "price_pence", "price_ccy", "nav_pence", "nav_date",
                            "published_at", "nav_age_days", "nav_stale",
                            "discount", "discount_fresh", "disc_z", "quality",
                            "is_vct", "security_id"] if c in latest.columns]
        _write_csv(latest[keep].round(6), OUT_DIR / "uk_discount_latest.csv")

    stats = {
        "panel_rows": int(len(panel)),
        "funds": int(panel["ticker"].nunique()) if len(panel) else 0,
        "days_with_discount": int(panel["discount"].notna().sum()) if len(panel) else 0,
        "days_with_fresh_discount": int(panel["discount_fresh"].notna().sum())
        if len(panel) else 0,
        "first_date": str(panel["date"].min().date()) if len(panel) else None,
        "last_date": str(panel["date"].max().date()) if len(panel) else None,
        "funds_no_nav": int((cover["discount_days"] == 0).sum()) if len(cover) else 0,
        "unit_status": units["price_unit_status"].value_counts().to_dict()
        if len(units) else {},
        "nav_readiness": ready["readiness"].value_counts().to_dict(),
        "frequency_mix": freq["nav_frequency"].value_counts().to_dict()
        if len(freq) else {},
    }
    return panel, stats


def run(stages: tuple[str, ...] = ("nav", "prices", "discount"),
        deadline_min: float = 240.0, full_prices: bool = False,
        include_vct: bool = True, shard: int = 0, shards: int = 1) -> int:
    bucket = os.environ.get("S3_BUCKET", "")
    universe = NAV.live_universe(include_vct=include_vct)
    status: dict = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stages": list(stages),
        "live_funds_addressed": int(len(universe)),
        "bucket": bool(bucket),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(universe, OUT_DIR / "uk_live_universe.csv")

    nav = pd.DataFrame()
    if "nav" in stages:
        nav, status["nav"] = stage_nav(universe, bucket,
                                       deadline_min=deadline_min,
                                       shard=shard, shards=shards)
    else:
        nav = NAV.read_panel()

    px = pd.DataFrame()
    if "prices" in stages:
        px, report, status["prices"] = stage_prices(
            universe, deadline_min=deadline_min, full=full_prices)
        _write_csv(report, OUT_DIR / "uk_price_fetch_report.csv")
    else:
        px = PH.read_prices()

    if "discount" in stages:
        _, status["discount"] = stage_discount(universe, nav, px)

    STATUS.write_text(json.dumps(status, indent=2, default=str))
    print(json.dumps(status, indent=2, default=str))
    return 0
