"""The UK listings cache stopped at 2022-05-17 and nothing noticed."""

from __future__ import annotations

import sys

import pandas as pd
import pytest

sys.path.insert(0, "src")

from uk_cef.data_sources.investegate import InvestegateCrawler


def _crawler(tmp_path):
    c = InvestegateCrawler(cache_dir=tmp_path)
    c.listings.mkdir(parents=True, exist_ok=True)
    return c


def test_a_stale_completed_company_is_reopened(tmp_path):
    """`done` meant done forever, and the cache froze in May 2022.

    The crawler pages from page 1 (newest) backward and marks a company
    `done` - correct for a one-time backfill, wrong every run after. The
    nightly archive then processed a four-year-old index while reporting an
    empty queue, which read as "finished" rather than "finished with a stale
    list".
    """
    c = _crawler(tmp_path)
    (c.listings / "OLD.csv").write_text("ann_id,date\n1,2022-05-17\n")
    (c.listings / "FRESH.csv").write_text(
        f"ann_id,date\n2,{pd.Timestamp.now('UTC').date()}\n")
    assert c._needs_refresh("OLD") is True
    assert c._needs_refresh("FRESH") is False
    # a fund with no listing at all has everything to fetch
    assert c._needs_refresh("NEVER_SEEN") is True


def test_a_reopened_crawl_stops_at_what_we_already_hold(tmp_path):
    """Otherwise a refresh re-walks to 2001 and refetches the whole archive."""
    import inspect

    src = inspect.getsource(InvestegateCrawler.crawl_company)
    assert 'if st.get("reopened")' in src
    assert "known = self._known_ids(ticker)" in src
    assert 'if not fresh:' in src, "an all-known page must end the listing pass"


def test_known_ids_reads_the_existing_listing(tmp_path):
    c = _crawler(tmp_path)
    (c.listings / "AAA.csv").write_text("ann_id,date\nabc,2026-08-01\ndef,2026-08-02\n")
    assert c._known_ids("AAA") == {"abc", "def"}
    assert c._known_ids("MISSING") == set()


def test_shards_take_disjoint_slices_and_cover_everything(tmp_path):
    """Parallel runs must not duplicate work or drop a fund.

    Hashing the ticker (rather than slicing a sorted list) keeps each shard's
    membership stable as the universe grows, so a fund does not migrate
    between shards and get crawled twice.
    """
    import zlib

    tickers = [f"T{i:03d}" for i in range(300)]
    shards = 6
    seen = []
    for sh in range(shards):
        seen += [t for t in tickers if zlib.crc32(t.encode()) % shards == sh]
    assert sorted(seen) == sorted(tickers), "every fund must land in exactly one shard"
    assert len(seen) == len(set(seen)), "no fund may appear in two shards"


def test_the_refresh_leaves_runners_for_other_work():
    """6 shards, not 12 - the archive and extraction jobs need runners too."""
    import yaml as _yaml
    from pathlib import Path as _P

    wf = _yaml.safe_load(_P(".github/workflows/uk_refresh.yml").read_text())
    strat = wf["jobs"]["refresh"]["strategy"]
    assert len(strat["matrix"]["shard"]) <= 6
    assert strat.get("max-parallel", 99) <= 6
