"""One test per coverage bug found while building the audit.

Each of these was live in the repository and each one made the monitoring
universe, or a number derived from it, describe something other than
reality. They are grouped here rather than scattered so the list of things
that have already gone wrong stays readable.
"""

from __future__ import annotations

import inspect
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cef_live import cli, eligibility, forward_irr, liveness, nta_live, opportunities

TODAY = date(2026, 8, 31)


def _params() -> dict:
    return yaml.safe_load(
        re.sub(r"\$\{[^}]+\}", "", Path("config/params.yaml").read_text()))


# ------------------------------------------------- 1. liveness persistence

def test_the_improved_liveness_is_written_back_to_the_registry():
    """It was computed nightly and thrown away.

    universe.build() wrote the registry with the AGGREGATOR's status;
    build_universe then recomputed a better status from the funds' own
    filings and wrote only a summary JSON. Every downstream reader - the
    NAV harvest target set, the price target set, the idea scan's universe
    filter - kept reading the aggregator's answer, so the whole
    evidence-based liveness layer had no effect on anything.
    """
    src = inspect.getsource(cli.build_universe)
    apply_at = src.index("liveness.apply")
    write_at = src.index('to_parquet(out / "registry.parquet"')
    assert apply_at < write_at, (
        "the evidence-based status must be persisted AFTER it is computed")
    assert 'reg.to_csv(out / "registry.csv"' in src


def test_reapplying_liveness_does_not_overwrite_the_aggregator_column():
    """Persisting creates a second risk: comparing the model with itself."""
    reg = pd.DataFrame([{"security_id": "X", "status": "live",
                         "last_seen": "2026-07", "manual_entry": False}])
    once = liveness.apply(reg, pd.DataFrame(columns=["security_id"]),
                          as_of=TODAY, params=_params())
    twice = liveness.apply(once, pd.DataFrame(columns=["security_id"]),
                           as_of=TODAY, params=_params())
    assert once["aggregator_status"].iloc[0] == "live"
    assert twice["aggregator_status"].iloc[0] == "live", (
        "the aggregator's verdict must survive a second pass")


def test_live_stale_nav_counts_as_alive_everywhere_it_is_read():
    """`live_stale_nav` means "trading, NAV not fresh" - a coverage fact.

    Once liveness is persisted, any reader still filtering on
    `status == "live"` silently drops those funds from the priced universe
    for a data-coverage reason. That is the target-set narrowing the NAV
    invariants exist to forbid, arriving through a string comparison.
    """
    assert liveness.STATUS_LIVE_STALE in liveness.LIVE_STATUSES
    assert liveness.STATUS_CANDIDATE in liveness.TRACKED_STATUSES

    for fn in (cli._registry_for, cli.resolve_tickers, cli.ideas):
        src = inspect.getsource(fn)
        assert '== "live"' not in src, (
            f"{fn.__name__} filters on the bare 'live' string and would drop "
            "live_stale_nav funds")
    from cef_live import tickers
    assert "LIVE_STATUSES" in inspect.getsource(tickers.resolve)


def test_a_nav_older_than_the_fresh_window_still_means_alive():
    """The ladder had inverted: an old ANNUAL REPORT outranked an old NAV.

    An 18-month-old annual report made a fund live_stale_nav, while a
    7-month-old NAV made it a delisting candidate - so quarterly and
    semi-annual NAV publishers (Greencoat UK Wind among them) were queued
    for review while trading and reporting normally.
    """
    got = liveness.classify({"last_nav": "2026-01-28"}, as_of=TODAY)
    assert got["status"] == liveness.STATUS_LIVE_STALE
    assert got["live_status_source"] == "own_nav_announcement_stale"
    # and a NAV beyond even the report window is still not "live"
    old = liveness.classify({"last_nav": "2018-01-01"}, as_of=TODAY)
    assert old["status"] in (liveness.STATUS_CANDIDATE, liveness.STATUS_DELISTED)


def test_a_published_nav_announcement_is_liveness_evidence_even_if_unparsed():
    """"The fund went quiet" and "our regex missed" are different facts.

    Counting only PARSED NAVs put Personal Assets, Law Debenture, Temple
    Bar and Fidelity China - all of which had filed a NAV RNS within the
    week - into the delisting review queue.
    """
    src = inspect.getsource(cli._liveness_evidence)
    assert "NAV_HEAD" in src, "ASX NTA headlines must count as NAV evidence"
    assert 'note(sid, "last_nav", g["d"].max())' in src, (
        "a UK NAV RNS date must count as a published NAV, parsed or not")
    assert cli.NAV_HEAD.search("Net Tangible Asset Backing")
    assert cli.NAV_HEAD.search("Daily Net Tangible Asset Statement")
    assert not cli.NAV_HEAD.search("Notification of buy-back - AFI")


# ------------------------------------------------------------ 2. UK NAV units

def test_uk_nav_history_is_read_in_pence_not_pounds():
    """The 5,649% premium.

    `nav_cum_pence / 100` put pounds into a column the rest of the system
    reads as pence, so every fund anchored on our own UK NAV archive was
    compared with a pence share price and showed an 80x-5,000x premium.
    The unit is fixed at the canonical reader, not patched per output.
    """
    src = inspect.getsource(cli._own_nav_history)
    assert 'pd.to_numeric(h["nav_cum_pence"], errors="coerce") / 100.0' not in src
    assert '"nav_unit": "GBX"' in src, "the UK NAV unit must be stated on the frame"
    assert '"nav_unit": "AUD"' in src, "the AU NAV unit must be stated on the frame"

    from cef_live import units
    assert units.CANONICAL_UNIT["UK"] == "GBX"
    assert units.CANONICAL_UNIT["AU"] == "AUD"


def test_the_live_table_normalises_a_nav_through_the_stated_unit():
    """A source that states pounds is converted; one that states pence is not."""
    params = _params()
    registry = pd.DataFrame([{"security_id": "S1", "market": "UK",
                              "status": "live", "name": "Test Trust"}])
    px = pd.DataFrame([{"security_id": "S1", "price": 452.0,
                        "price_source": "yahoo:T.L", "price_date": "2026-08-28",
                        "price_ccy": "GBp"}])
    own = pd.DataFrame([{"security_id": "S1", "nav_date": "2026-08-28",
                         "nav_value": 4.60, "nav_unit": "GBP"}])
    out = nta_live.build_table(pd.DataFrame(columns=["security_id", "obs_month",
                                                     "sector", "nav_total_return",
                                                     "nav_per_share", "share_price"]),
                               "UK", "nav_total_return", "nav_per_share",
                               "share_price", params, live_prices=px,
                               registry=registry, own_nav_history=own,
                               today=TODAY)
    r = out.iloc[0]
    assert r["nav_anchor"] == pytest.approx(460.0), "pounds must become pence"
    assert r["nav_unit"] == "GBX"
    assert r["discount_est"] == pytest.approx(452.0 / 460.0 - 1.0, abs=1e-6)


# --------------------------------------------------------- 3. eligibility

def test_a_research_excluded_vehicle_cannot_become_alert_eligible():
    """15 rows were alert-eligible with research_eligible=False, 4 of them VCTs.

    A registry-only fund defaulted to eligible=True because it had no panel
    row, so exactly the funds the rebuild reached - the ones with no
    aggregator history - bypassed the research universe's exclusions.
    """
    params = _params()
    months = pd.period_range("2019-01", "2026-08", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "VCT1", "obs_month": m, "sector": "VCT Generalist",
         "nav_total_return": 0.004, "nav_per_share": 100.0,
         "share_price": 60.0 + (i % 7), "company_name": "A VCT",
         "discount": -0.40 + (i % 7) / 100.0}
        for i, m in enumerate(months)])
    registry = pd.DataFrame([{"security_id": "VCT1", "market": "UK",
                              "status": "live", "name": "A VCT",
                              "sector": "VCT Generalist", "is_vct": True,
                              "research_eligible": False}])
    out = nta_live.build_table(panel, "UK", "nav_total_return", "nav_per_share",
                               "share_price", params, registry=registry,
                               today=TODAY)
    r = out[out["security_id"] == "VCT1"].iloc[0]
    assert not r["research_eligible"]
    assert not r["alert_eligible"], "a research-excluded vehicle must not alert"
    # ...and it keeps its row, price and NAV: excluded from SIGNALS, not hidden
    assert pd.notna(r["nav_anchor"])


def test_the_idea_scan_never_scores_a_research_excluded_vehicle():
    live = pd.DataFrame([
        {"security_id": "OK", "market": "UK", "name": "Eligible",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.30},
        {"security_id": "NO", "market": "UK", "name": "A VCT",
         "research_eligible": False, "z_adj": -9.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.60}])
    # both funds clear dislocation and both have a catalyst, so on the data
    # alone both would be WATCH; only the policy separates them
    cats = pd.DataFrame([
        {"security_id": s, "catalyst_class": "tender", "weight": 1.0,
         "date": pd.Timestamp.utcnow().date().isoformat(), "headline": "h"}
        for s in ("OK", "NO")])
    got = opportunities.evaluate(live, cats, None, _params(), hurdle_base=None)
    assert len(got), "the eligible fund should still produce a verdict"
    assert set(got["security_id"]) == {"OK"}, (
        "an excluded vehicle produced an idea")


def test_the_research_policy_exclusions_come_from_the_research_config():
    pol = eligibility.research_policy("config/default.yaml")
    assert pol["exclude_vcts"] is True
    assert "zero dividend preference" in pol["exclude_security_types"]

    reg = pd.DataFrame([
        {"security_id": "A", "market": "UK", "name": "Ordinary trust",
         "sector": "Global", "share_type": "Ordinary Share", "currency": "GBX",
         "is_vct": False, "is_split": False, "status": "live"},
        {"security_id": "B", "market": "UK", "name": "A VCT",
         "sector": "VCT Generalist", "share_type": "Ordinary Share",
         "currency": "GBX", "is_vct": True, "is_split": False, "status": "live"},
        {"security_id": "C", "market": "UK", "name": "A ZDP",
         "sector": "Global", "share_type": "Zero Dividend Preference share",
         "currency": "GBX", "is_vct": False, "is_split": False, "status": "live"},
        {"security_id": "D", "market": "UK", "name": "Dollar line",
         "sector": "Global", "share_type": "Ordinary Share", "currency": "USD",
         "is_vct": False, "is_split": False, "status": "live"},
        {"security_id": "E", "market": "AU", "name": "S&P/ASX 200 Accumulation",
         "sector": "Australian Indices", "share_type": "Ordinary",
         "currency": "AUD", "is_vct": False, "is_split": False, "status": "live"},
        {"security_id": "F", "market": "UK", "name": "Dead trust",
         "sector": "Global", "share_type": "Ordinary Share", "currency": "GBX",
         "is_vct": False, "is_split": False, "status": "delisted"},
    ])
    got = eligibility.classify(reg, liveness.LIVE_STATUSES).set_index("security_id")
    assert got.loc["A", "monitoring_eligible"]
    assert not got.loc["B", "monitoring_eligible"]
    assert "vct" in got.loc["B", "exclusion_reason"]
    assert not got.loc["C", "monitoring_eligible"]
    assert not got.loc["D", "monitoring_eligible"]
    assert not got.loc["E", "monitoring_eligible"]
    assert got.loc["E", "exclusion_reason"] == "benchmark_index_not_a_fund"
    assert got.loc["F", "exclusion_reason"] == "not_live:delisted"
    # nothing was deleted - every input row is still present with a reason
    assert len(got) == len(reg)


def test_an_asx_benchmark_series_is_not_a_fund():
    """Four S&P/ASX accumulation index rows were in the monitored universe.

    They come in on the same ASX monthly-report tables as the LICs, cannot
    have a discount, and sat there as permanently unpriceable "funds"
    depressing every coverage percentage.
    """
    reg = pd.DataFrame([{"security_id": "ASX:XJOAI", "market": "AU",
                         "name": "S&P/ASX 200 Accumulation",
                         "sector": "Australian Indices", "share_type": "Ordinary",
                         "currency": "AUD", "is_vct": False, "is_split": False,
                         "status": "live"}])
    got = eligibility.classify(reg, liveness.LIVE_STATUSES).iloc[0]
    assert not got["research_eligible"] and not got["monitoring_eligible"]


# ------------------------------------------------------ 4. UK vs AU history

def test_each_market_gets_its_own_panel_and_its_own_hurdle():
    """A UK trailing return is not a hurdle for an Australian LIC.

    `universe_trailing_tr` looked only for `nav_tr_cagr_*`, which the AU
    panel does not have (it names them `nta_tr_cagr_*`), so gate 3 was
    computed from UK history and applied to every fund in both markets.
    """
    assert cli.PANEL_PATHS["UK"].startswith("data/processed")
    assert cli.PANEL_PATHS["AU"].startswith("data/au_processed")

    au = pd.DataFrame([{"security_id": f"ASX:{i}", "obs_month": "2026-06",
                        "nta_tr_cagr_5y": 0.05} for i in range(30)])
    uk = pd.DataFrame([{"security_id": f"SEDOL:{i}", "obs_month": "2026-06",
                        "nav_tr_cagr_5y": 0.09} for i in range(30)])
    assert opportunities.universe_trailing_tr(au) == pytest.approx(0.05)
    assert opportunities.universe_trailing_tr(uk) == pytest.approx(0.09)

    # identical funds in the two markets, identical 16% forward IRR: the
    # only thing that can separate them is whose hurdle they are judged on
    live = pd.DataFrame([
        {"security_id": "SEDOL:1", "market": "UK", "name": "UK fund",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.1},
        {"security_id": "ASX:1", "market": "AU", "name": "AU fund",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.1}])
    irr = pd.DataFrame([{"security_id": "SEDOL:1", "irr_central": 0.16},
                        {"security_id": "ASX:1", "irr_central": 0.16}])
    cats = pd.DataFrame([
        {"security_id": s_, "catalyst_class": "tender", "weight": 1.0,
         "date": pd.Timestamp.utcnow().date().isoformat(), "headline": "h"}
        for s_ in ("SEDOL:1", "ASX:1")])
    got = opportunities.evaluate(live, cats, irr, _params(),
                                 hurdle_base={"UK": 0.09, "AU": 0.05}
                                 ).set_index("security_id")
    assert got.loc["ASX:1", "hurdle"] == pytest.approx(0.13)
    assert got.loc["SEDOL:1", "hurdle"] == pytest.approx(0.17)
    # 16% clears the Australian hurdle and misses the UK one
    assert got.loc["ASX:1", "gate3_return"]
    assert not got.loc["SEDOL:1", "gate3_return"]


def test_forward_irr_never_scores_a_fund_against_another_markets_panel():
    uk = pd.DataFrame([{"security_id": "SEDOL:1", "obs_month": "2026-06",
                        "sector": "S", "discount": -0.1, "nav_tr_cagr_5y": 0.09}])
    live = pd.DataFrame([{"security_id": "ASX:1", "market": "AU", "price": 1.0,
                          "nta_est": 1.2, "discount_est": -0.17}])
    got = forward_irr.build_by_market(live, {"UK": uk}, _params())
    assert not len(got), "an AU fund must get no IRR from a UK-only panel"


# ---------------------------------------------- 5. rows are never dropped

def test_a_fund_with_a_price_but_no_nav_keeps_its_row():
    """Dropping it discarded the price we had already paid to fetch.

    build_table used to `continue` when no NAV anchor existed, so a live
    fund with a perfectly good quote vanished from the table entirely -
    and "we hold no NAV for this fund" became indistinguishable from "this
    fund does not exist".
    """
    params = _params()
    registry = pd.DataFrame([{"security_id": "NONAV", "market": "UK",
                              "status": "live", "name": "No NAV Trust"}])
    px = pd.DataFrame([{"security_id": "NONAV", "price": 100.0,
                        "price_source": "yahoo:X.L", "price_date": "2026-08-28",
                        "price_ccy": "GBp"}])
    out = nta_live.build_table(
        pd.DataFrame(columns=["security_id", "obs_month", "sector",
                              "nav_total_return", "nav_per_share", "share_price"]),
        "UK", "nav_total_return", "nav_per_share", "share_price", params,
        live_prices=px, registry=registry, today=TODAY)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["price"] == 100.0
    assert pd.isna(r["nav_anchor"]) and pd.isna(r["nta_est"])
    assert not r["has_nav"]
    assert pd.isna(r["discount_est"]), "no NAV means no discount, never zero"
    assert not r["alert_eligible"]


def test_the_panel_price_is_kept_beside_a_live_price_not_replaced_by_it():
    """Otherwise "we fell back" is inferred from a string, not recorded."""
    params = _params()
    months = pd.period_range("2024-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([{"security_id": "S1", "obs_month": m, "sector": "S",
                           "nav_total_return": 0.004, "nav_per_share": 100.0,
                           "share_price": 90.0, "company_name": "S1",
                           "discount": -0.1} for m in months])
    px = pd.DataFrame([{"security_id": "S1", "price": 95.0,
                        "price_source": "yahoo:S1.L", "price_date": "2026-08-28",
                        "price_ccy": "GBp"}])
    out = nta_live.build_table(panel, "UK", "nav_total_return", "nav_per_share",
                               "share_price", params, live_prices=px,
                               registry=None, today=TODAY)
    r = out.iloc[0]
    assert r["price"] == 95.0 and not r["price_is_fallback"]
    assert r["price_panel"] == 90.0 and r["price_panel_month"] == "2026-06"
    assert r["price_ccy"] == "GBp"

    out2 = nta_live.build_table(panel, "UK", "nav_total_return", "nav_per_share",
                                "share_price", params, live_prices=None,
                                registry=None, today=TODAY)
    r2 = out2.iloc[0]
    assert r2["price_is_fallback"], "a panel price must be labelled a fallback"
    assert r2["price_source"] == "aggregator_panel"


def test_a_single_market_refresh_does_not_delete_the_other_market(tmp_path,
                                                                  monkeypatch):
    """`nightly --markets au` emptied every UK row from the live table.

    The table is written from whatever this run built, so a single-market
    refresh - which is exactly what `coverage_audit --refresh --markets au`
    asks for - wiped half the monitoring universe until the next full run.
    """
    src = inspect.getsource(cli.nightly)
    assert "carried forward" in src, (
        "rows for markets not rebuilt this run must be carried forward")
    i_concat = src.index('out = pd.concat(tables, ignore_index=True)')
    i_write = src.index('out.to_parquet("data/nta_live/latest.parquet"')
    i_carry = src.index('prev_f = Path("data/nta_live/latest.parquet")')
    assert i_concat < i_carry < i_write, (
        "the carry-forward must happen before the table is written")
    # and the carried rows keep their own updated_at, so their age is visible
    assert "ORIGINAL updated_at" in src


def test_the_au_nightly_does_not_require_a_panel_another_job_builds():
    """It raised FileNotFoundError before attempting anything.

    Same shape as the index sweep and the idea scan before it: a job dying
    on an artefact a different workflow happens to produce. The registry is
    the universe; the panel only adds history.
    """
    src = inspect.getsource(cli.nightly)
    assert 'panel_missing_history_unavailable' in src
    i_guard = src.index('ap = Path("data/au_processed/au_monthly_panel.parquet")')
    i_read = src.index("panel = pd.read_parquet(ap)")
    assert i_guard < i_read
