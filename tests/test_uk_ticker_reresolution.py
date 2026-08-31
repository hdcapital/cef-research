"""Re-resolution must never write a ticker it has not verified.

A wrong ticker staples another company's share price onto this fund's NAV,
which is worse than the missing page it was meant to fix. These tests run
the verifier against the REAL Investegate pages saved under data/probe -
including the two that have no company page at all - rather than against
strings invented to match the parser.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "reres", ROOT / "scripts" / "resolve_missing_uk_tickers.py")
reres = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reres)


class _Resp:
    def __init__(self, content, status=200):
        self.content, self.status_code = content, status


class _Sess:
    def __init__(self, path=None, status=200):
        self.path, self.status = path, status
        self.asked = []

    def get(self, url, **_kw):
        self.asked.append(url)
        if self.path is None:
            return _Resp(b"", self.status)
        return _Resp(Path(self.path).read_bytes(), self.status)


FIX = ROOT / "data" / "probe"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(reres.time, "sleep", lambda *_: None)


def test_real_company_page_verifies():
    ok, detail = reres.verify(
        "CTY", ["City of London Investment Trust"],
        _Sess(FIX / "investegate" / "company_CTY.html"))
    assert ok, detail
    assert "City of London" in detail


def test_page_for_a_different_company_is_rejected():
    """The decisive case: FIGI hands back a ticker belonging to someone else."""
    ok, detail = reres.verify(
        "CTY", ["Volta Finance"],
        _Sess(FIX / "investegate" / "company_CTY.html"))
    assert not ok
    assert detail.startswith("identity_mismatch")


def test_ticker_not_matching_the_page_is_rejected():
    ok, detail = reres.verify(
        "WRONG", ["City of London Investment Trust"],
        _Sess(FIX / "investegate" / "company_CTY.html"))
    assert not ok
    assert detail.startswith("not_a_company_page")


def test_page_with_no_company_h1_is_rejected():
    """A real dead-fund page that falls back to the generic market feed."""
    ok, detail = reres.verify(
        "SCIN", ["Scottish Investment Trust"],
        _Sess(FIX / "investegate2" / "dead_SCIN.html"))
    assert not ok
    assert detail in ("no_h1",) or detail.startswith("not_a_company_page")


def test_http_error_is_not_a_verification():
    ok, detail = reres.verify("XXX", ["Anything"], _Sess(None, status=404))
    assert not ok and detail == "http_404"


def test_fetch_exception_is_not_a_verification():
    class Boom:
        def get(self, *_a, **_k):
            raise RuntimeError("network down")
    ok, detail = reres.verify("XXX", ["Anything"], Boom())
    assert not ok and detail.startswith("fetch_failed")


def test_verification_uses_the_company_page_url():
    s = _Sess(FIX / "investegate" / "company_CTY.html")
    reres.verify("CTY", ["City of London Investment Trust"], s)
    assert s.asked == ["https://www.investegate.co.uk/company/CTY?page=1"]
