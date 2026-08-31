"""Index the live UK funds whose announcements were never listed.

Why there is a gap at all
-------------------------
The Investegate listings crawl was seeded from the AIC panel's *eligible*
universe. That universe is defined by the aggregator publishing a price and
a NAV for a fund - which is exactly the test the infrastructure, renewables,
property and private-equity trusts fail. The July 2026 MIR lists 282 UK
funds and prices 137 of them; the other 145 are name-only rows.

So the funds the aggregator declines to value were never indexed, were
therefore never archived, and therefore have no NAV history in the daily
panel. Measured 2026-08-31: 138 of 288 live funds have history; of the 150
that do not, 83 are `announcements_only` - HICL, Tritax Big Box, 3i
Infrastructure, BH Macro, Gresham House Energy Storage, the renewables
cohort. Not a long tail. The names an investor in this asset class most
wants a discount for.

This is the missing first link. It indexes WHICH announcements those funds
have published - not the bodies - which is all the NAV archiver needs to
queue them. The chain then completes with machinery that already exists:

    uk-index-gap  ->  uk-nav-archive  ->  uk-daily (nav stage)
    what exists       fetch + store       re-parse from S3 into the panel

Identity is verified, never assumed. Investegate reuses tickers - AIC is
Achilles Investment Company today and was something else before - and the
crawler checks the page's own H1 against the fund's registry name before
storing a single row. An unverifiable page is recorded as a mismatch, and
the fund keeps no index at all rather than another company's announcements.
"""

from __future__ import annotations

import json
import os
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import uk_nav_panel as NAV

CACHE = Path("data/investegate_cache")
REPORT = Path("reports/build/uk_index_gap.json")


def targets(universe: pd.DataFrame, panel: pd.DataFrame,
            cache_dir: Path = CACHE, include_vct: bool = False) -> pd.DataFrame:
    """Live funds with a verified ticker and no listing index.

    A fund that already has an index is not re-crawled here however thin its
    NAV history is: that is the archiver's queue to work, not this job's, and
    conflating the two is how a cheap job turns into an expensive one.
    """
    ready = NAV.archive_readiness(universe, panel, cache_dir)
    want = ready[ready["readiness"].isin(
        ["no_listing_index", "unknown_listing_index_not_pulled"])].copy()
    if not include_vct and "is_vct" in want.columns:
        want = want[~want["is_vct"].astype(bool)]
    # the funds with no other NAV route first: for them this crawl is the
    # only path to a discount at all, whereas a `registry` fund at least has
    # an aggregator print behind it
    want["_prio"] = (want.get("nav_route") != "announcements_only").astype(int)
    return want.sort_values(["_prio", "ticker"]).drop(columns=["_prio"])


def run(budget_minutes: float = 300.0, shard: int = 0, shards: int = 1,
        include_vct: bool = False, limit: int = 0) -> int:
    from uk_cef.data_sources.investegate import InvestegateCrawler

    universe = NAV.live_universe()
    panel = NAV.read_panel()
    todo = targets(universe, panel, include_vct=include_vct)
    if shards > 1:
        keep = todo["ticker"].astype(str).map(
            lambda t: zlib.crc32(t.encode()) % shards == shard)
        todo = todo[keep]
    if limit:
        todo = todo.head(limit)
    print(f"shard {shard + 1}/{shards}: {len(todo)} live funds to index")

    crawler = InvestegateCrawler(budget_minutes=budget_minutes,
                                 listings_only=True)
    results: list[dict] = []
    for r in todo.itertuples(index=False):
        name = getattr(r, "name", None)
        status = crawler.crawl_company(getattr(r, "ticker"), getattr(r, "ticker"),
                                       [name] if name else [])
        results.append({"ticker": r.ticker, "name": name, "status": status})
        print(f"  {r.ticker:6s} {status}")
        if status == "budget_exhausted":
            print("budget exhausted - state saved, next run resumes here")
            break

    # what the crawl actually produced, counted from the index it wrote
    import re
    pat = re.compile(r"net asset value", re.I)
    # A fund can be fully indexed and still publish no "Net Asset Value(s)"
    # RNS - UK REITs and several private-equity vehicles state EPRA NTA
    # inside their interim and annual results instead. That is a different
    # source, not a failed crawl, and the difference decides whether the fix
    # is "fetch the announcements" or "parse the results". So for any fund
    # with no NAV announcement, the headlines it DOES publish are recorded:
    # the explanation becomes a measurement rather than an inference.
    alt = re.compile(r"half[- ]?year|interim|annual (?:report|result)|"
                     r"final result|net tangible|nta\b|epra", re.I)
    for row in results:
        f = crawler.listings / f"{row['ticker']}.csv"
        row["rows_indexed"] = 0
        row["nav_announcements"] = 0
        row["top_headlines"] = None
        row["nav_bearing_reports"] = 0
        if f.exists():
            try:
                d = pd.read_csv(f, dtype=str)
                head = d["headline"].fillna("")
                row["rows_indexed"] = int(len(d))
                row["nav_announcements"] = int(head.str.contains(pat).sum())
                if row["nav_announcements"] == 0 and len(d):
                    row["nav_bearing_reports"] = int(head.str.contains(alt).sum())
                    row["top_headlines"] = " | ".join(
                        f"{h}({n})" for h, n in
                        head.str.slice(0, 45).value_counts().head(5).items())
            except Exception:  # noqa: BLE001
                pass

    res = pd.DataFrame(results)
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shard": f"{shard}of{shards}",
        "targets": int(len(todo)),
        "attempted": int(len(res)),
        "status_counts": res["status"].value_counts().to_dict() if len(res) else {},
        "funds_now_indexed": int((res["nav_announcements"] > 0).sum()) if len(res) else 0,
        "nav_announcements_found": int(res["nav_announcements"].sum()) if len(res) else 0,
        "indexed_but_no_nav_rns": int(((res["rows_indexed"] > 0)
                                       & (res["nav_announcements"] == 0)).sum())
        if len(res) else 0,
        "requests_made": crawler.requests_made,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    Path(f"reports/build/uk_index_gap_s{shard}of{shards}.json").write_text(
        json.dumps(summary, indent=2))
    out = Path("outputs/live")
    out.mkdir(parents=True, exist_ok=True)
    if len(res):
        res.sort_values("ticker").to_csv(
            out / f"uk_index_gap_s{shard}of{shards}.csv", index=False)
    print(json.dumps(summary, indent=2))
    return 0
