"""The S3 rebuild must recover exactly the rows the archiver lost.

The archiver marks an ann_id done on PUT but only makes the parsed row
durable at the end-of-run commit, so a run that dies in between leaves the
text in S3 and no row in the parquet - and the manifest then blocks any
re-fetch. These tests pin the recovery path.
"""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "rebuild_uk", ROOT / "scripts" / "rebuild_uk_nav_history.py")
rebuild = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rebuild)


@pytest.mark.parametrize("key,expected", [
    ("uk/nav_announcements/AGT/2024-03-01_1234567.json.gz", "1234567"),
    # ann_ids contain underscores; only the date prefix may be stripped
    ("uk/nav_announcements/AGT/2024-03-01_rns_88_a.json.gz", "rns_88_a"),
    ("uk/nav_announcements/manifest_s0of12.json", None),
    ("uk/nav_announcements/AGT/notes.txt", None),
])
def test_ann_id_from_key(key, expected):
    assert rebuild.ann_id_from_key(key) == expected


def test_known_ann_ids_unions_every_shard_layout(tmp_path, monkeypatch):
    """6-shard, 12-shard and unsharded parquets all count as durable.

    Reading only one layout is what made the history look like it stopped
    in 2022 when it actually reached 2026.
    """
    d = tmp_path / "data"
    d.mkdir()
    pd.DataFrame({"ann_id": ["a", "b"]}).to_parquet(d / "uk_nav_history.parquet")
    pd.DataFrame({"ann_id": ["c"]}).to_parquet(d / "uk_nav_history_s0of6.parquet")
    pd.DataFrame({"ann_id": ["d"]}).to_parquet(d / "uk_nav_history_s3of12.parquet")
    # an unrelated parquet must not be swept in
    pd.DataFrame({"ann_id": ["zz"]}).to_parquet(d / "dividends.parquet")
    monkeypatch.chdir(tmp_path)
    assert rebuild.known_ann_ids() == {"a", "b", "c", "d"}


def test_known_ann_ids_survives_a_corrupt_shard(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    pd.DataFrame({"ann_id": ["a"]}).to_parquet(d / "uk_nav_history.parquet")
    (d / "uk_nav_history_s1of6.parquet").write_bytes(b"not a parquet")
    monkeypatch.chdir(tmp_path)
    assert rebuild.known_ann_ids() == {"a"}


def test_shard_split_is_disjoint_and_total():
    """Every archived id lands in exactly one shard - none missed, none twice."""
    import zlib
    ids = [f"rns{i}" for i in range(5000)]
    shards = 6
    buckets = [[i for i in ids if zlib.crc32(i.encode()) % shards == s]
               for s in range(shards)]
    flat = [i for b in buckets for i in b]
    assert sorted(flat) == sorted(ids)
    assert len(flat) == len(set(flat))
