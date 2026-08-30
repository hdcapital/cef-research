"""A bad payload must cost one call, not a whole run.

A 30-minute gapfill crawl died on
`JSONDecodeError: Expecting ':' delimiter` from a truncated JSONP body.
json.loads sat outside the try that already made timeouts survivable, so
the exception propagated out of the sweep - and because the index was only
written after the loop, every row crawled in those 22 minutes was thrown
away. Crawled rows are expensive (a throttled endpoint, ~6 days per call),
so both halves of that are pinned here.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load(tmp_path, monkeypatch, throttle="0", budget="6"):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NTA_INDEX_THROTTLE", throttle)
    monkeypatch.setenv("NTA_SWEEP_BUDGET", budget)
    monkeypatch.setenv("NTA_SWEEP_MODE", "gapfill")
    spec = importlib.util.spec_from_file_location(
        "sweepmod", ROOT / "scripts" / "sample_nta_pdfs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(day: int, code: str = "AAA") -> str:
    ts = pd.Timestamp(f"2025-06-{day:02d}", tz="UTC").isoformat()
    return "cb(" + json.dumps({"announcement_data": [{
        "id": f"id{day}", "issuer_code": code,
        "document_release_date": ts,
        "header": "Net Tangible Assets", "url": "http://x/y.pdf"}]}) + ");"


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def test_malformed_payload_does_not_end_the_run_or_lose_rows(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    mod.INDEX_F.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    calls = {"n": 0}
    # good, good, TRUNCATED, good ... exactly the observed sequence
    def fake_get(_s, _url, **_kw):
        calls["n"] += 1
        if calls["n"] == 3:
            return _Resp('cb({"announcement_data": [{"id": "trunc"')
        return _Resp(_payload(20 - calls["n"]))
    monkeypatch.setattr(mod, "throttled_get", fake_get)

    counters = {"index_calls": 0}
    out = mod.sweep_index(object(), {"AAA"}, counters)

    # the run survived the bad body and kept crawling past it
    assert counters.get("index_bad_payloads") == 1
    assert calls["n"] > 3, "sweep stopped at the malformed payload"
    # and the rows crawled before it are still here
    assert len(out) >= 2
    assert "index_error" not in counters or \
        counters["index_error"] != "consecutive_bad_payloads"


def test_rows_are_durable_before_the_loop_ends(tmp_path, monkeypatch):
    """The index file exists mid-crawl, not only after a clean finish."""
    mod = _load(tmp_path, monkeypatch, budget="40")
    mod.INDEX_F.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_get(_s, _url, **_kw):
        calls["n"] += 1
        if calls["n"] > 12:
            # an unexpected fault of a kind the loop does NOT catch
            raise KeyboardInterrupt("runner killed")
        return _Resp(_payload(28 - calls["n"]))
    monkeypatch.setattr(mod, "throttled_get", fake_get)

    with pytest.raises(KeyboardInterrupt):
        mod.sweep_index(object(), {"AAA"}, {"index_calls": 0})

    assert mod.INDEX_F.exists(), "crawled rows were discarded by the crash"
    saved = pd.read_parquet(mod.INDEX_F)
    # 12 calls landed before the fault; the guarantee is that at most one
    # checkpoint interval of work can ever be lost, not that nothing is.
    assert len(saved) >= 12 - mod_checkpoint_interval(), \
        f"only {len(saved)} of 12 crawled rows survived"


def mod_checkpoint_interval() -> int:
    return 10


def test_consecutive_bad_payloads_still_stop_the_run(tmp_path, monkeypatch):
    """Resilience must not become an infinite retry against a broken endpoint."""
    mod = _load(tmp_path, monkeypatch, budget="200")
    mod.INDEX_F.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mod, "throttled_get",
                        lambda *_a, **_k: _Resp("cb({broken"))
    counters = {"index_calls": 0}
    mod.sweep_index(object(), {"AAA"}, counters)
    assert counters["index_error"] == "consecutive_bad_payloads"
    assert counters["index_calls"] <= mod.MAX_CONSECUTIVE_INDEX_FAILURES + 1
