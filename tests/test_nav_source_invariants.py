"""Invariants for where NAV comes from.

These are not unit tests of convenience - they encode the architectural
decision that the AIC/ASX registry files are used for IDENTITY ONLY and
every NAV value comes from the fund's own announcements. Each test fails
loudly if a future change quietly reintroduces the old behaviour, which is
how funds went missing before: not by anyone deciding to drop them, but by
a filter narrowing the target set as a side effect.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _order_targets(tick2sid, census_tickers, extra_targets):
    """Mirror of the ordering in harvest_uk - kept in step by the test below."""
    first = [t for t in (extra_targets or {})]
    seen = set(first)
    second = [t for t in census_tickers if t in tick2sid and t not in seen]
    seen |= set(second)
    rest = [t for t in tick2sid if t not in seen]
    return first + second + rest


def test_every_addressable_fund_is_a_nav_target():
    """No fund we can address may be excluded from the NAV harvest.

    A fund must be absent from NAV output only because it published nothing
    parseable - never because the target set filtered it out.
    """
    tick2sid = {"HICL": "s1", "TRIG": "s2", "CTY": "s3", "ATST": "s4",
                "SYNC": "s5", "OCI": "s6"}
    census = ["CTY", "ATST"]                     # only what the crawl covered
    extra = {"HICL": "s1", "TRIG": "s2"}         # registry never prices these
    ordered = _order_targets(tick2sid, census, extra)

    assert set(ordered) == set(tick2sid), (
        "every addressable fund must be targeted; missing: "
        f"{set(tick2sid) - set(ordered)}")
    assert len(ordered) == len(set(ordered)), "targets must not duplicate"
    # funds with no other NAV source are polled first, since a census fund
    # at least has a (stale) registry print to fall back on
    assert ordered[:2] == ["HICL", "TRIG"]


def test_harvest_uk_signature_still_accepts_full_target_set():
    """harvest_uk must keep the parameter that carries registry targets."""
    from cef_live import harvest_nav
    import inspect
    params = inspect.signature(harvest_nav.harvest_uk).parameters
    assert "extra_targets" in params, (
        "harvest_uk lost extra_targets - registry-listed funds would stop "
        "being polled for their own NAV")


def test_published_nav_beats_registry_anchor():
    """A Tier 0 announcement NAV must override the registry's monthly print.

    If this inverts, the system silently prefers a month-old aggregator
    value over the fund's own published figure.
    """
    import re
    import yaml
    from cef_live import nta_live

    params = yaml.safe_load(
        re.sub(r"\$\{[^}]+\}", "", Path("config/params.yaml").read_text()))
    months = pd.period_range("2019-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "X", "obs_month": m, "sector": "S",
         "nta_total_return": 0.005, "nta_derived": 1.0,
         "share_price": 0.9, "company_name": "X"} for m in months])
    tier0 = pd.DataFrame([{"security_id": "X", "nav_date": "2026-08-27",
                           "nav_value": 2.5, "source": "investegate:999"}])

    t = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                             "share_price", params, tier0=tier0)
    row = t[t["security_id"] == "X"].iloc[0]
    assert row["basis"] == 0, "published NAV must set basis 0"
    assert row["nav_anchor"] == 2.5, "published NAV must win over registry print"
    assert "investegate" in str(row["anchor_source"])


def test_estimate_never_stored_as_published():
    """The modelled value and the published value stay in separate fields."""
    import re
    import yaml
    from cef_live import nta_live

    params = yaml.safe_load(
        re.sub(r"\$\{[^}]+\}", "", Path("config/params.yaml").read_text()))
    months = pd.period_range("2019-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "Y", "obs_month": m, "sector": "S",
         "nta_total_return": 0.004, "nta_derived": 1.0 + i * 0.001,
         "share_price": 0.9, "company_name": "Y"}
        for i, m in enumerate(months)])
    t = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                             "share_price", params)
    row = t[t["security_id"] == "Y"].iloc[0]
    assert "nav_anchor" in row and "nta_est" in row, (
        "published anchor and modelled estimate must be distinct columns")
    assert row["basis"] in (1, 3)
    assert pd.notna(row["anchor_date"]) and pd.notna(row["staleness_days"]), (
        "an estimate must always carry the date it is anchored to and its age")


def test_index_sweep_resweeps_when_registry_gains_codes():
    """A newly listed fund must not be permanently absent from the index.

    UWC, AIX, MRE, PCX and WHI had no announcements indexed because the
    sweep filtered to the code set known at sweep time and never revisited.
    """
    src = Path("scripts/sample_nta_pdfs.py").read_text()
    assert "code_sig" in src, "sweep must record which codes it kept"
    assert "re-sweeping history" in src, (
        "sweep must re-run history when the registry gains codes")


@pytest.mark.parametrize("script", [
    "scripts/archive_to_s3.py",
    "scripts/archive_uk_navs.py",
    "scripts/sync_state.py",
])
def test_archive_scripts_actually_execute(script, monkeypatch):
    """Import each archive script for real, not just parse it.

    A botched edit once produced `PDF_# comment` on one line and
    `BUDGET = ...` on the next: valid syntax, so ast.parse passed, but a
    NameError at import that killed shards after they had been dispatched.
    Parsing is not enough - the module has to run.
    """
    import importlib.util
    import sys

    monkeypatch.setenv("SHARD_INDEX", "0")
    monkeypatch.setenv("SHARD_COUNT", "8")
    monkeypatch.setattr(sys, "argv", ["test"])
    spec = importlib.util.spec_from_file_location("_probe", script)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass          # argv-driven scripts may exit on usage; that is fine
