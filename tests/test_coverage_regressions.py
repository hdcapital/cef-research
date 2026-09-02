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
    # the daily panel's universe dropped every live_stale_nav fund with a
    # hand-written list (Cordiant Digital among them): it must read the
    # liveness module's own set, and a live security must own a ticker it
    # shares with a delisted predecessor (BH Macro)
    from cef_live import uk_nav_panel
    src = inspect.getsource(uk_nav_panel.live_universe)
    assert "TRACKED_STATUSES" in src
    assert '["live", "delist_candidate"]' not in src
    assert "_status_rank" in src


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

    from au_lic.extract import facts as AUF
    assert '"nav_unit": "AUD"' in inspect.getsource(AUF.nav_observations), (
        "the AU NAV unit must be stated on the frame")

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
    # "pounds", not "GBP": a GBP label means PENCE in this system's sources,
    # so only the unambiguous word may trigger a conversion
    own = pd.DataFrame([{"security_id": "S1", "nav_date": "2026-08-28",
                         "nav_value": 4.60, "nav_unit": "pounds"}])
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
    got = opportunities.evaluate(live, cats, None, _params())
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

def test_each_market_gets_its_own_panel_and_one_shared_hurdle():
    """Each market keeps its own panel; gate 3 is one absolute number.

    `universe_trailing_tr` looked only for `nav_tr_cagr_*`, which the AU
    panel does not have (it names them `nta_tr_cagr_*`), so a per-market
    trailing return silently disappeared for AU. It survives as reported
    context. The IRR hurdle itself is a fixed `min_irr_central` since
    2026-09-01 (owner instruction, config/CHANGELOG.md): the required
    return is the owner's, so identical funds in different markets are
    judged identically.
    """
    assert cli.PANEL_PATHS["UK"].startswith("data/processed")
    assert cli.PANEL_PATHS["AU"].startswith("data/au_processed")

    au = pd.DataFrame([{"security_id": f"ASX:{i}", "obs_month": "2026-06",
                        "nta_tr_cagr_5y": 0.05} for i in range(30)])
    uk = pd.DataFrame([{"security_id": f"SEDOL:{i}", "obs_month": "2026-06",
                        "nav_tr_cagr_5y": 0.09} for i in range(30)])
    assert opportunities.universe_trailing_tr(au) == pytest.approx(0.05)
    assert opportunities.universe_trailing_tr(uk) == pytest.approx(0.09)

    params = _params()
    min_irr = params["opportunity"]["min_irr_central"]
    assert min_irr == pytest.approx(0.15)
    live = pd.DataFrame([
        {"security_id": "SEDOL:1", "market": "UK", "name": "UK fund",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.1},
        {"security_id": "ASX:1", "market": "AU", "name": "AU fund",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 1,
         "basis": 0, "discount_est": -0.1}])
    irr = pd.DataFrame([{"security_id": "SEDOL:1", "irr_central": 0.16},
                        {"security_id": "ASX:1", "irr_central": 0.14}])
    got = opportunities.evaluate(live, None, irr, params).set_index("security_id")
    assert got.loc["ASX:1", "hurdle"] == pytest.approx(min_irr)
    assert got.loc["SEDOL:1", "hurdle"] == pytest.approx(min_irr)
    # 16% clears the fixed hurdle; 14% misses it - market plays no part
    assert got.loc["SEDOL:1", "gate3_return"]
    assert not got.loc["ASX:1", "gate3_return"]


def test_a_single_strong_trigger_is_a_watch_and_a_weak_one_is_not():
    """The owner's brief: anything actionable on z, IRR or catalyst shows.

    Requiring two gates hid a z -2.5 fund with no catalyst and no IRR
    coverage. A routine buyback-programme headline alone is the opposite
    problem - 91 of the last 30 days' 117 catalysts are discount_control
    noise - so a catalyst carries a verdict on its own only at weight >=
    `standalone_catalyst_weight`.
    """
    today = pd.Timestamp.utcnow().date().isoformat()
    base = {"market": "UK", "research_eligible": True, "staleness_days": 1,
            "basis": 0, "discount_est": -0.20}
    live = pd.DataFrame([
        {**base, "security_id": "Z", "name": "Dislocated only", "z_adj": -2.5},
        {**base, "security_id": "I", "name": "IRR only", "z_adj": 0.0},
        {**base, "security_id": "CW4", "name": "Tender only", "z_adj": 0.0},
        {**base, "security_id": "CW3", "name": "Buyback only", "z_adj": 0.0},
        {**base, "security_id": "STALE", "name": "IRR on a carried NAV",
         "z_adj": 0.0, "basis": 3, "staleness_days": 200}])
    irr = pd.DataFrame([{"security_id": "I", "irr_central": 0.22},
                        {"security_id": "STALE", "irr_central": 0.22}])
    cats = pd.DataFrame([
        {"security_id": "CW4", "catalyst_class": "tender_offer", "weight": 4.0,
         "date": today, "headline": "off-market tender"},
        {"security_id": "CW3", "catalyst_class": "discount_control",
         "weight": 3.0, "date": today, "headline": "buyback programme"}])
    got = opportunities.evaluate(live, cats, irr, _params())
    verdicts = dict(zip(got["security_id"], got["verdict"]))
    assert verdicts.get("Z") == "WATCH", "a lone dislocation must surface"
    assert verdicts.get("I") == "WATCH", "a lone passing IRR must surface"
    assert verdicts.get("CW4") == "WATCH", "a tender alone must surface"
    assert "CW3" not in verdicts, "a routine buyback alone is not an idea"
    assert "STALE" not in verdicts, (
        "an IRR computed off a stale carried NAV evidenced a return")


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


# ------------------------------------- alert gating and identity (P1, P7)

def test_a_unit_error_can_never_become_an_opportunity():
    """All three of these were alert_eligible on the last real run.

    Lindsell Train reached z = -19.3, Benjamin Hornigold -6.5, Thorney Tech
    -6.0 - the three most extreme "dislocations" in the table, every one of
    them a price and a NAV in different units. Ranking by z would have put
    them at the top. So the data-quality verdict is computed on the row and
    gates the alert BEFORE any z, catalyst or IRR logic looks at it.
    """
    live = pd.DataFrame([
        {"security_id": "GOOD", "market": "UK", "name": "Clean fund",
         "research_eligible": True, "data_quality_ok": True, "identity_ok": True,
         "z_adj": -3.0, "staleness_days": 1, "basis": 0, "discount_est": -0.30},
        {"security_id": "LTI", "market": "UK", "name": "Unit error",
         "research_eligible": True, "data_quality_ok": False, "identity_ok": True,
         "z_adj": -19.3, "staleness_days": 1, "basis": 0, "discount_est": -0.99},
        {"security_id": "REUSED", "market": "UK", "name": "Reused ticker",
         "research_eligible": True, "data_quality_ok": True, "identity_ok": False,
         "z_adj": -8.0, "staleness_days": 1, "basis": 0, "discount_est": -0.80}])
    cats = pd.DataFrame([
        {"security_id": s, "catalyst_class": "tender", "weight": 1.0,
         "date": pd.Timestamp.utcnow().date().isoformat(), "headline": "h"}
        for s in ("GOOD", "LTI", "REUSED")])
    got = opportunities.evaluate(live, cats, None, _params())
    assert set(got["security_id"]) == {"GOOD"}, (
        f"a gated row was scored: {sorted(set(got['security_id']))}")


def test_the_gates_run_before_the_scoring_loop():
    """Order is the whole point - a filter after ranking is not a gate."""
    src = inspect.getsource(opportunities.evaluate)
    assert src.index('gates = [') < src.index("for r in df.itertuples")
    for col in ("research_eligible", "data_quality_ok", "identity_ok"):
        assert f'"{col}"' in src


def test_the_live_table_refuses_to_alert_on_a_unit_mismatch():
    """The gate belongs on the row, not only in the consumer."""
    params = _params()
    months = pd.period_range("2019-01", "2026-08", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "X", "obs_month": m, "sector": "S",
         "nav_total_return": 0.004, "nav_per_share": 100.0,
         "share_price": 90.0 + (i % 5), "company_name": "X",
         "discount": -0.10 + (i % 5) / 100.0} for i, m in enumerate(months)])
    registry = pd.DataFrame([{"security_id": "X", "market": "UK",
                              "status": "live", "name": "X",
                              "research_eligible": True, "identity_ok": True}])
    # a price 100x out against the same NAV history
    px = pd.DataFrame([{"security_id": "X", "price": 0.9,
                        "price_source": "yahoo:X.L", "price_date": "2026-08-28",
                        "price_ccy": "GBp"}])
    out = nta_live.build_table(panel, "UK", "nav_total_return", "nav_per_share",
                               "share_price", params, live_prices=px,
                               registry=registry, today=TODAY)
    r = out.iloc[0]
    assert r["unit_check_status"] == "suspect_scale"
    assert not r["data_quality_ok"]
    assert not r["alert_eligible"], "a 100x scale gap alerted"


def test_a_superseded_ticker_holder_is_never_priced_or_alerted():
    """CIP was CIP Merchant Capital until 2022 and is Channel Islands
    Property now; VEIL was a Ventus VCT class and is Vietnam Enterprise
    Investments now. A delisted fund holding the old lease would receive
    the new company's quote, compute a discount against its own final NAV,
    and produce a spectacular false dislocation that nothing downstream
    could detect - the price is real, the NAV is real, the join is clean.
    """
    from cef_live import identity as I

    reg = pd.DataFrame([
        {"security_id": "OLD", "market": "UK", "name": "CIP Merchant Capital",
         "status": "delisted", "first_seen": "2018-05", "last_seen": "2022-04"},
        {"security_id": "NEW", "market": "UK", "name": "Channel Islands Property",
         "status": "live", "first_seen": "2018-07", "last_seen": "2026-07"}])
    tk = pd.DataFrame([{"security_id": "OLD", "ticker": "CIP"},
                       {"security_id": "NEW", "ticker": "CIP"}])
    got = I.resolve(reg, tk).set_index("security_id")
    assert got.loc["NEW", "identity_status"] == I.STATUS_INCUMBENT
    assert got.loc["OLD", "identity_status"] == I.STATUS_SUPERSEDED
    assert got.loc["NEW", "identity_ok"] and not got.loc["OLD", "identity_ok"]

    # and the superseded security is never even ASKED for a price
    syms = I.priceable_symbols(got.reset_index(), ".L")
    assert syms == {"NEW": "CIP.L"}, "a superseded holder was sent to the feed"

    q = I.conflict_queue(got.reset_index())
    assert "OLD" in set(q["security_id"]), "the conflict must be queued for review"


def test_two_live_claimants_are_a_conflict_that_is_never_guessed():
    """JPMorgan Income & Growth and JPMorgan India Growth & Income both
    answer to JIGI and both read as live. The registry cannot say whose
    quote it is, so NEITHER carries a live signal until a human settles it.
    """
    from cef_live import identity as I
    reg = pd.DataFrame([
        {"security_id": "A", "market": "UK", "name": "JPMorgan Income & Growth",
         "status": "live", "first_seen": "2007-01", "last_seen": "2016-10"},
        {"security_id": "B", "market": "UK", "name": "JPMorgan India Growth & Income",
         "status": "live", "first_seen": "2007-01", "last_seen": "2026-07"}])
    tk = pd.DataFrame([{"security_id": s, "ticker": "JIGI"} for s in ("A", "B")])
    got = I.resolve(reg, tk).set_index("security_id")
    assert set(got["identity_status"]) == {I.STATUS_CONFLICT}
    assert not got["identity_ok"].any()
    assert not got.loc["A", "identity_same_name"]
    assert I.priceable_symbols(got.reset_index(), ".L") == {}


def test_a_cross_market_ticker_collision_is_not_a_conflict():
    """ASX:ALF and the UK's Alternative Liquidity Fund both answer to ALF,
    and ASX:SEC and Strategic Equity Capital both to SEC - but the price
    layer asks for ALF.AX and ALF.L, which are different instruments on
    different exchanges. Grouping on the bare ticker reported four
    conflicts that cannot happen and would have suppressed four live funds.
    """
    from cef_live import identity as I
    reg = pd.DataFrame([
        {"security_id": "ASX:ALF", "market": "AU", "name": "Australian Leaders Fund",
         "status": "live", "first_seen": "2016-12", "last_seen": "2021-01"},
        {"security_id": "SEDOL:BYRGPD6", "market": "UK",
         "name": "Alternative Liquidity Fund", "status": "live",
         "first_seen": "2018-01", "last_seen": "2026-07"}])
    tk = pd.DataFrame([{"security_id": "SEDOL:BYRGPD6", "ticker": "ALF"}])
    got = I.resolve(reg, tk).set_index("security_id")
    assert got["identity_ok"].all()
    assert set(got["identity_status"]) == {I.STATUS_SOLE}


def test_an_asx_security_is_its_own_code_and_needs_no_mapping():
    from cef_live import identity as I
    reg = pd.DataFrame([{"security_id": "ASX:ARG", "market": "AU",
                         "name": "Argo", "status": "live",
                         "first_seen": "2016-12", "last_seen": "2026-06"}])
    got = I.resolve(reg, pd.DataFrame(columns=["security_id", "ticker"]))
    assert got.iloc[0]["ticker"] == "ARG"
    assert got.iloc[0]["identity_ok"]


def test_the_nightly_restores_the_uk_daily_nav_panel_it_reads():
    """46 UK funds reported "no NAV" while their NAV sat in S3.

    `_own_nav_history("UK")` reads data/uk/nav - the re-parsed daily NAV
    panel, state group `uk_daily`. The nightly restored raw_aic, raw_asx,
    uk_announcements, asx_index and tickers, and not that one, so the panel
    was empty on every runner and the live table fell back to the legacy
    shards. Baker Steel, CT Private Equity, Digital 9 and Gresham House
    Energy Storage all had NAVs in the panel and none in the table.
    """
    import yaml as _yaml
    for wf in (".github/workflows/cef_live.yml", ".github/workflows/ideas.yml"):
        doc = _yaml.safe_load(Path(wf).read_text())
        job = next(iter(doc["jobs"].values()))
        pulls = [str(s.get("run", "")) for s in job["steps"]
                 if "sync_state.py pull" in str(s.get("run", ""))]
        assert pulls, f"{wf} restores no state at all"
        assert any("uk_daily" in p for p in pulls), (
            f"{wf} reads data/uk/nav but never restores it")


def test_an_amber_row_may_be_watched_but_never_called_an_opportunity():
    """Three gates on a rolled-forward NAV is a real observation, not
    something to act on. OPPORTUNITY is reserved for a row whose data is
    fully sound - which is what alert_eligible already means on the row.
    """
    base = {"market": "UK", "research_eligible": True, "data_quality_ok": True,
            "identity_ok": True, "z_adj": -3.0, "staleness_days": 1,
            "basis": 0, "discount_est": -0.30}
    live = pd.DataFrame([
        {**base, "security_id": "CLEAN", "name": "Clean", "alert_eligible": True},
        {**base, "security_id": "AMBER", "name": "Rolled forward",
         "basis": 1, "alert_eligible": False}])
    irr = pd.DataFrame([{"security_id": s, "irr_central": 0.40}
                        for s in ("CLEAN", "AMBER")])
    cats = pd.DataFrame([
        {"security_id": s, "catalyst_class": "tender", "weight": 1.0,
         "date": pd.Timestamp.utcnow().date().isoformat(), "headline": "h"}
        for s in ("CLEAN", "AMBER")])
    got = opportunities.evaluate(live, cats, irr,
                                 _params()).set_index("security_id")
    assert got.loc["CLEAN", "verdict"] == "OPPORTUNITY"
    assert got.loc["AMBER", "gates_passed"] == 3, "it really does clear all three"
    assert got.loc["AMBER", "verdict"] == "WATCH", (
        "a degraded row was promoted to OPPORTUNITY")
    assert not got.loc["AMBER", "data_fully_sound"]


def test_the_asx_monthly_report_is_a_fallback_not_a_default():
    """A fund-specific NTA announcement must win on date, and the monthly
    investment-products report must be used only where nothing newer
    exists. All 44 funds anchored on the June report had a newer indexed
    NTA announcement, several from the day of the audit.
    """
    params = _params()
    months = pd.period_range("2024-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "ASX:X", "obs_month": m, "sector": "S",
         "nta_total_return": 0.004, "nta_derived": 1.00,
         "share_price": 0.95, "company_name": "X", "discount": -0.05}
        for m in months])
    registry = pd.DataFrame([{"security_id": "ASX:X", "market": "AU",
                              "status": "live", "name": "X",
                              "research_eligible": True, "identity_ok": True}])

    # no announcement: the monthly report legitimately carries the NTA
    out = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                               "share_price", params, registry=registry,
                               today=TODAY)
    r = out[out["security_id"] == "ASX:X"].iloc[0]
    assert str(r["anchor_source"]).startswith("aggregator_panel"), (
        "with nothing newer, the monthly report is the right source")

    # a newer fund-specific NTA wins outright, and says so
    tier0 = pd.DataFrame([{"security_id": "ASX:X", "nav_date": "2026-08-28",
                           "nav_value": 1.20, "unit": "dollars",
                           "source": "asx_ann:03133390"}])
    out2 = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                                "share_price", params, tier0=tier0,
                                registry=registry, today=TODAY)
    r2 = out2[out2["security_id"] == "ASX:X"].iloc[0]
    assert r2["basis"] == 0 and r2["nav_anchor"] == pytest.approx(1.20)
    assert "asx_ann" in str(r2["anchor_source"])
    assert r2["anchor_date"] == "2026-08-28"


def test_a_month_end_panel_price_can_never_alert():
    """European Opportunities Trust cleared every gate on a JULY panel
    price and reached z = +2.15, alert_eligible.

    Every other check was about the number rather than about what the
    number IS: the price was positive, the NAV positive, the units
    consistent, the discount computed. None of that makes a month-end
    aggregator print a current market price, and a discount against one is
    not a current discount. The audit's usable_price already drew this
    line; the row-level gate now draws it in the same place.
    """
    params = _params()
    months = pd.period_range("2019-01", "2026-07", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "EOT", "obs_month": m, "sector": "S",
         "nav_total_return": 0.004, "nav_per_share": 991.4,
         "share_price": 937.0 + (i % 9), "company_name": "EOT",
         "discount": -0.05 + (i % 9) / 100.0} for i, m in enumerate(months)])
    registry = pd.DataFrame([{"security_id": "EOT", "market": "UK",
                              "status": "live", "name": "EOT",
                              "research_eligible": True, "identity_ok": True}])
    out = nta_live.build_table(panel, "UK", "nav_total_return", "nav_per_share",
                               "share_price", params, live_prices=None,
                               registry=registry, today=TODAY)
    r = out.iloc[0]
    assert r["price_is_fallback"], "the fixture must exercise the fallback path"
    assert not r["data_quality_ok"]
    assert r["data_quality_reason"] == "stale_panel_price_only"
    assert not r["alert_eligible"], "a month-end panel price alerted"


def test_a_within_error_z_is_kept_flagged_not_voided():
    """76 priced funds vanished because their z was set to NaN.

    A dislocation smaller than the NAV estimate's own error band must not
    ALERT - but the z is a real computed number the universe pricing
    needs. The old code voided it to NaN, making a fully priced fund
    indistinguishable from one with no history at all. Now the number
    stays, `z_within_error` travels with it, and both alert_eligible and
    the dislocation gate refuse it.
    """
    params = _params()
    months = pd.period_range("2018-01", "2026-08", freq="M").astype(str)
    # constant discount for X: today's discount equals its own history
    # mean, so the anomaly (zero) sits inside any non-missing error band.
    # Three funds in one sector with varying returns give the factor model
    # a leave-one-out sector factor and a real sigma - a single fund has
    # neither, and est_error would be NaN.
    panel = pd.DataFrame([
        {"security_id": s, "obs_month": m, "sector": "S",
         "nav_total_return": 0.004 + 0.003 * ((i + j) % 5 - 2) / 2,
         "nav_per_share": 100.0, "share_price": 90.0,
         "company_name": s, "discount": -0.10}
        for j, s in enumerate(("X", "Y", "Z2")) for i, m in enumerate(months)])
    registry = pd.DataFrame([{"security_id": "X", "market": "UK",
                              "status": "live", "name": "X",
                              "research_eligible": True, "identity_ok": True}])
    px = pd.DataFrame([{"security_id": "X", "price": 90.0,
                        "price_source": "yahoo:X.L",
                        "price_date": "2026-08-28", "price_ccy": "GBp"}])
    out = nta_live.build_table(panel, "UK", "nav_total_return",
                               "nav_per_share", "share_price", params,
                               live_prices=px, registry=registry, today=TODAY)
    r = out[out["security_id"] == "X"].iloc[0]
    assert r["z_status"] == "within_error_band", r["z_status"]
    assert pd.notna(r["z_adj"]), "the z must survive the error-band flag"
    assert bool(r["z_within_error"])
    assert not r["alert_eligible"], "a within-error z must not alert"

    # and the dislocation gate refuses it even at an extreme z
    live = pd.DataFrame([{"security_id": "X", "market": "UK", "name": "X",
                          "research_eligible": True, "z_adj": -3.0,
                          "z_within_error": True, "staleness_days": 1,
                          "basis": 0, "discount_est": -0.30}])
    got = opportunities.evaluate(live, None, None, params)
    assert not len(got), "a within-error dislocation produced an idea"


def test_a_registry_only_fund_gets_its_z_from_the_daily_panel():
    """The announcements-only cohort had a NAV, a price, and no z.

    The aggregator's monthly panel never priced these funds, so the z
    block had no history however good the fund's own NAV series was. The
    daily panel resampled monthly is that history; the panel stays
    primary and the source is recorded.
    """
    params = _params()
    empty_panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                        "nav_total_return", "nav_per_share",
                                        "share_price", "discount"])
    registry = pd.DataFrame([{"security_id": "SEDOL:AUX1", "market": "UK",
                              "status": "live", "name": "Aux fund",
                              "research_eligible": True, "identity_ok": True}])
    tier0 = pd.DataFrame([{"security_id": "SEDOL:AUX1",
                           "nav_date": "2026-08-28", "nav_value": 100.0,
                           "unit": "GBX", "source": "investegate:1"}])
    px = pd.DataFrame([{"security_id": "SEDOL:AUX1", "price": 80.0,
                        "price_source": "yahoo:AUX1.L",
                        "price_date": "2026-08-29", "price_ccy": "GBp"}])
    months = pd.period_range("2023-01", "2026-08", freq="M").astype(str)
    aux = pd.DataFrame([{"security_id": "SEDOL:AUX1", "obs_month": m,
                         "discount": -0.10 + 0.02 * (i % 4)}
                        for i, m in enumerate(months)])
    out = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                               "nav_per_share", "share_price", params,
                               tier0=tier0, live_prices=px,
                               registry=registry,
                               aux_discount_history=aux, today=TODAY)
    r = out[out["security_id"] == "SEDOL:AUX1"].iloc[0]
    assert pd.notna(r["z_adj"]), r["z_status"]
    assert r["z_source"] == "own_daily_panel"
    assert r["z_status"] in ("computed", "within_error_band")


def test_staleness_limit_follows_the_funds_own_cadence():
    """A quarterly publisher's 60-day-old NAV is current by its cadence.

    The flat 45-day cap wrote off the infrastructure/property cohort as
    stale. The limit is now 3x the fund's own median publication gap,
    floored at the flat cap and capped at max_days_cadence_cap.
    """
    params = _params()
    empty_panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                        "nav_total_return", "nav_per_share",
                                        "share_price", "discount"])
    registry = pd.DataFrame([
        {"security_id": "Q", "market": "UK", "status": "live", "name": "Q",
         "research_eligible": True, "identity_ok": True},
        {"security_id": "D", "market": "UK", "status": "live", "name": "D",
         "research_eligible": True, "identity_ok": True}])
    hist_rows = []
    for i in range(12):     # quarterly publisher, three years of NAVs
        hist_rows.append({"security_id": "Q",
                          "nav_date": pd.Timestamp("2026-08-01")
                          - pd.Timedelta(days=91 * i), "nav_value": 100.0})
    for i in range(24):     # daily publisher (recent business days)
        hist_rows.append({"security_id": "D",
                          "nav_date": pd.Timestamp("2026-08-28")
                          - pd.Timedelta(days=i), "nav_value": 100.0})
    own = pd.DataFrame(hist_rows)
    out = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                               "nav_per_share", "share_price", params,
                               registry=registry, own_nav_history=own,
                               today=TODAY)
    q = out[out["security_id"] == "Q"].iloc[0]
    d = out[out["security_id"] == "D"].iloc[0]
    cap = params["live_nta"]["staleness"]["max_days_cadence_cap"]
    floor = params["live_nta"]["staleness"]["max_days_for_alerting"]
    assert q["staleness_limit_days"] == cap, (
        "3 x 91d exceeds the cap, so the cap must bind")
    assert d["staleness_limit_days"] == floor, (
        "a daily publisher keeps the flat floor")


def test_forward_irr_growth_falls_back_to_the_funds_own_nav_history():
    """~64 funds got no IRR because the panel had no CAGR for them.

    The fallback reads the fund's own NAV-per-share history - median
    monthly log-change annualised, with a 10-for-1 split excluded by the
    outlier filter rather than poisoning the number.
    """
    dates, vals = [], []
    v = 100.0
    for i in range(60):
        d_ = pd.Timestamp("2021-01-31") + pd.DateOffset(months=i)
        v *= 1.005
        if i == 30:
            v /= 10.0        # subdivision: NAV/share drops 10x overnight
        dates.append(d_)
        vals.append(v)
    own = pd.DataFrame({"security_id": "X", "nav_date": dates,
                        "nav_value": vals})
    g = forward_irr.own_history_cagr(own)
    assert "X" in g.index
    assert g["X"] == pytest.approx((1.005 ** 12) - 1, rel=0.05), (
        "the split month must be excluded, not compounded")

    # and build() uses it for a fund the panel never covered
    live = pd.DataFrame([{"security_id": "X", "market": "UK", "name": "X",
                          "sector": "Infra", "price": 90.0, "nta_est": 100.0,
                          "discount_est": -0.10}])
    aux = pd.DataFrame([{"security_id": "X", "obs_month": m,
                         "discount": -0.08}
                        for m in pd.period_range("2022-01", "2026-08",
                                                 freq="M").astype(str)])
    empty_panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                        "discount", "nav_tr_cagr_5y"])
    got = forward_irr.build(live, empty_panel, _params(), own_hist=own,
                            aux_discount_hist=aux)
    r = got.iloc[0]
    assert r["g_source"] == "own_nav_history"
    assert r["irr_central"] is not None, (
        "growth + aux terminal discount must be enough for an IRR")


def test_a_fresh_published_nav_is_current_even_without_a_factor_model():
    """EJF failed the freshness gate on a 22-day-old published NAV.

    Basis states PROVENANCE and stays untouched (0 = the fund's own
    announcement - the nav-source invariant); freshness is the separate
    `nav_current` fact, judged by the fund's own cadence. A modelless
    fund's fresh NAV is basis 3 AND current; a genuinely stale one is
    basis 3 and NOT current, and the gates read the freshness fact.
    """
    params = _params()
    empty_panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                        "nav_total_return", "nav_per_share",
                                        "share_price", "discount"])
    registry = pd.DataFrame([{"security_id": "F", "market": "UK",
                              "status": "live", "name": "F",
                              "research_eligible": True, "identity_ok": True}])
    own = pd.DataFrame([{"security_id": "F",
                         "nav_date": pd.Timestamp("2026-08-15"),
                         "nav_value": 100.0}])
    out = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                               "nav_per_share", "share_price", params,
                               registry=registry, own_nav_history=own,
                               today=TODAY)
    r = out[out["security_id"] == "F"].iloc[0]
    assert r["basis"] == 3, "provenance must not be relabelled by freshness"
    assert bool(r["nav_current"]), (r["staleness_days"],
                                    r["staleness_limit_days"])
    # and a genuinely stale carry is not current
    old_nav = pd.DataFrame([{"security_id": "F",
                             "nav_date": pd.Timestamp("2024-01-15"),
                             "nav_value": 100.0}])
    out2 = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                                "nav_per_share", "share_price", params,
                                registry=registry, own_nav_history=old_nav,
                                today=TODAY)
    assert out2.iloc[0]["basis"] == 3
    assert not bool(out2.iloc[0]["nav_current"])

    # the dislocation gate accepts a fresh basis-3 NAV and refuses a stale one
    live = pd.DataFrame([
        {"security_id": "FRESH", "market": "UK", "name": "Fresh",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 22,
         "basis": 3, "nav_current": True, "discount_est": -0.30},
        {"security_id": "STALE3", "market": "UK", "name": "Stale",
         "research_eligible": True, "z_adj": -3.0, "staleness_days": 300,
         "basis": 3, "nav_current": False, "discount_est": -0.30}])
    got = opportunities.evaluate(live, None, None, params)
    assert set(got["security_id"]) == {"FRESH"}



def test_the_registry_carries_the_research_policy_verdict():
    """VCTs were counting toward the coverage target.

    eligibility.classify only ever ran inside the coverage audit, so the
    registry carried no research_eligible column and the nightly's
    research gate defaulted registry-only funds - VCTs included - to True.
    The verdict is now computed and persisted when the registry is built.
    """
    src = inspect.getsource(cli.build_universe)
    assert "eligibility.classify" in src
    assert src.index("eligibility.classify") < src.index(
        'to_parquet(out / "registry.parquet"'), (
        "eligibility must be written INTO the persisted registry")


def test_one_uk_nav_headline_pattern_shared_everywhere():
    """Three copies of 'net asset value' is how the ASX side lost UWC.

    The archiver, the frequency census and the Tier-0 harvest each carried
    their own copy of the selector, so widening one silently left the
    others narrow. One pattern, in harvest_nav, imported by all three -
    widened to the bare NAV abbreviation on probe evidence (Chrysalis:
    "Quarterly NAV Announcement and Trading Update").
    """
    from cef_live import harvest_nav
    assert harvest_nav.UK_NAV_HEAD.search("Net Asset Value(s)")
    assert harvest_nav.UK_NAV_HEAD.search(
        "Quarterly NAV Announcement and Trading Update")
    assert harvest_nav.UK_NAV_HEAD.search("Monthly Net Asset Value")
    assert not harvest_nav.UK_NAV_HEAD.search("Navigator Global Update")
    assert not harvest_nav.UK_NAV_HEAD.search("Total Voting Rights")
    # no stray private copies of the phrase-only pattern
    src = Path("src/cef_live/harvest_nav.py").read_text()
    assert 're.compile(r"net asset value", re.I)' not in src
    arch = Path("scripts/archive_uk_navs.py").read_text()
    assert "UK_NAV_HEAD" in arch


def test_nav_validation_verdicts_and_tolerances():
    """A plausible wrong number is invisible to every other check.

    The validator compares our extracted NAV against the AIC's
    independently collected figure: 2% is rounding, 6% is a cum/ex or
    debt-basis definition gap (agreement, not error), x100 is the unit
    trap, anything else is the parser reading the wrong number.
    """
    from cef_live import nav_validation as NV
    own = pd.DataFrame([
        # agree
        {"security_id": "A", "nav_date": "2026-06-30", "nav_value": 100.5},
        {"security_id": "A", "nav_date": "2026-07-31", "nav_value": 101.0},
        {"security_id": "A", "nav_date": "2026-08-28", "nav_value": 101.5},
        # stable basis gap (~4%)
        {"security_id": "B", "nav_date": "2026-06-30", "nav_value": 104.0},
        {"security_id": "B", "nav_date": "2026-07-31", "nav_value": 104.0},
        {"security_id": "B", "nav_date": "2026-08-31", "nav_value": 104.0},
        # unit error: pounds in a pence column
        {"security_id": "C", "nav_date": "2026-08-31", "nav_value": 1.0},
        # wrong number entirely
        {"security_id": "D", "nav_date": "2026-06-30", "nav_value": 55.0},
        {"security_id": "D", "nav_date": "2026-07-31", "nav_value": 61.0},
        {"security_id": "D", "nav_date": "2026-08-31", "nav_value": 42.0},
        # mid-month observation must be superseded by month-end
        {"security_id": "A", "nav_date": "2026-08-14", "nav_value": 55.0},
    ])
    panel = pd.DataFrame([
        {"security_id": s, "obs_month": m, "nav_per_share": 100.0}
        for s in ("A", "B", "C", "D")
        for m in ("2026-06", "2026-07", "2026-08")])
    pairs = NV.compare(own, panel, "nav_per_share")
    got = NV.per_fund(pairs).set_index("security_id")
    assert got.loc["A", "verdict"] == "validated"
    assert got.loc["A", "agree"] == 3, "month-end must win over mid-month"
    assert got.loc["B", "verdict"] == "validated", (
        "a stable basis gap is a definition difference, not a parse error")
    assert got.loc["C", "verdict"] == "unit_suspect"
    assert got.loc["D", "verdict"] == "suspect"


def test_nav_validation_runs_inside_the_nightly():
    src = inspect.getsource(cli.nightly)
    assert "nav_validation.run_uk" in src, (
        "the UK validation must run where the panel and our history meet")


# ------------------------------------------- factsheet-route extraction

def test_directional_nav_phrases_read_the_current_value():
    """The plain fallback read RECI's PRIOR month and LBOW's £2.42m.

    Real phrasings from the committed factsheet probe. Direction matters:
    'decreased from OLD to NEW' and 'falling to NEW from OLD' place the
    current value at opposite ends of the sentence.
    """
    from cef_live.harvest_nav import parse_uk_nav_text
    reci = ("The Net Asset Value per share decreased from 140.6p in June "
            "to 138.2p in July, primarily due to a dividend payment")
    assert parse_uk_nav_text(reci).get("nav_cum_pence") == 138.2
    lbow = ("a decrease from the previous year's loss of £3.30 million, "
            "with net asset value per share falling to 17.15 pence from "
            "27.15 pence")
    assert parse_uk_nav_text(lbow).get("nav_cum_pence") == 17.15
    aeet = ('Financial information At 30 June 2025 At 31 December 2024 '
            'Net asset value ("NAV") per share (pence) 50.15 85.55 '
            'Share price (pence) 33.70 52.00')
    assert parse_uk_nav_text(aeet).get("nav_cum_pence") == 50.15
    thrl = ("a 1.0% increase in EPRA Net Tangible Assets per share to "
            "120.6 pence as of March 31, 2026")
    assert parse_uk_nav_text(thrl).get("nav_cum_pence") == 120.6


def test_the_corpus_still_parses_after_the_factsheet_rules():
    """New loose rules must never cost a previously-read announcement."""
    import gzip
    import json as _json

    from cef_live.harvest_nav import parse_uk_nav_text
    corp = _json.loads(gzip.open("data/uk_nav_corpus.json.gz").read())
    anns = corp if isinstance(corp, list) else corp.get("announcements", [])
    ok = sum(1 for a in anns
             if parse_uk_nav_text(a.get("text") or a.get("body") or "")
             .get("nav_cum_pence"))
    assert ok >= 174, f"corpus regression: {ok}/{len(anns)} parsed"


def test_the_ai_summary_stays_in_the_parsed_text_by_measured_decision():
    """Stripping Investegate's AI summary cost four corpus parses.

    The summary paraphrases THIS announcement's own figure (BIPS's value
    sits partly inside it, before the first reliable body anchor), so it
    stays in the text and the nightly AIC cross-validation polices the
    residual risk of a mis-stated paraphrase. This test pins the decision:
    a page whose only in-window statement is summary-shaped still parses.
    """
    from cef_live.harvest_nav import parse_uk_nav_text
    page = ("Net Asset Value(s) Summary by AI BETA Close X Invesco Bond "
            "Income Plus Limited reported its unaudited Net Asset Value "
            "(NAV) per Ordinary share as of August 26, 2026 was 168.94 "
            "pence. LEI: 549300O3XQTC12345678")
    assert parse_uk_nav_text(page).get("nav_cum_pence") == 168.94


def test_third_party_research_is_excluded_from_the_factsheet_route():
    from cef_live.harvest_nav import UK_FACTSHEET_HEAD, UK_THIRD_PARTY
    assert UK_FACTSHEET_HEAD.search("Portfolio Update")
    assert UK_FACTSHEET_HEAD.search("Half-Yearly Results")
    assert UK_FACTSHEET_HEAD.search("Fact Sheet Announcement")
    assert UK_THIRD_PARTY.search(
        "Results analysis from Kepler Trust Intelligence")
    assert UK_THIRD_PARTY.search("Edison issues report on HgT")
    assert not UK_THIRD_PARTY.search("Half-Yearly Results")


# --------------------------------------- ticker reuse / zombie resurrection

def test_a_dead_tickers_market_feed_rows_never_count_as_its_evidence():
    """56 funds the AIC delisted years ago came back "announced today".

    A dead ticker's Investegate page silently serves the MARKET-WIDE feed,
    so its listing cache filled with other companies' announcements and
    liveness resurrected the fund into the priced universe. Announcement
    URLs carry the company's own slug (`...--<ticker>/`), so every reader
    of ticker-keyed rows can and must verify the row belongs to the fund.
    """
    from cef_live.harvest_nav import uk_row_matches_ticker
    assert uk_row_matches_ticker(
        "https://www.investegate.co.uk/announcement/rns/"
        "3i-infrastructure--3in/some-headline/9564461", "3IN")
    assert uk_row_matches_ticker(
        "https://www.investegate.co.uk/announcement/rns/"
        "amedeo-air-four-plus-limited-red-ord-npv--aa4/factsheet/9557770",
        "AA4")
    # the market-feed leak: another company's slug under a dead ticker
    assert not uk_row_matches_ticker(
        "https://www.investegate.co.uk/announcement/rns/"
        "some-other-company--xyz/holdings-in-company/9564000", "BEEP")
    # an old cache row with no recognisable slug is not condemned
    assert uk_row_matches_ticker("https://old/shape/12345", "BEEP")

    # and every reader applies it
    src = inspect.getsource(cli._liveness_evidence)
    assert src.count("uk_row_matches_ticker") >= 2, (
        "both the listing-cache and nav-history evidence paths must verify")
    arch = Path("scripts/archive_uk_navs.py").read_text()
    assert arch.count("uk_row_matches_ticker") >= 2, (
        "the archive queue and the reparse loader must verify")
    from cef_live import uk_nav_panel
    assert "uk_row_matches_ticker" in inspect.getsource(uk_nav_panel)
    from cef_live import harvest_nav as HN
    h_src = inspect.getsource(HN.harvest_uk)
    assert "identity_mismatch" in h_src, (
        "the harvest must verify the listing page H1 names the ticker")
    assert "uk_row_matches_ticker" in h_src


def test_an_empty_listing_cache_does_not_break_liveness_evidence(tmp_path,
                                                                 monkeypatch):
    """`df[[]]` is COLUMN selection: an empty cache file made the identity
    mask an empty list, which dropped every column and killed the whole
    registry build on KeyError 'date'. Two consecutive nightlies died on
    it. Masks are Series now; this pins the empty and the mixed case."""
    cache = tmp_path / "data" / "investegate_cache" / "listings"
    cache.mkdir(parents=True)
    (cache / "EMPT.csv").write_text("ann_id,date,headline,url\n")
    (cache / "GOOD.csv").write_text(
        "ann_id,date,headline,url\n"
        "1,2026-08-28,Net Asset Value(s),"
        "https://www.investegate.co.uk/announcement/rns/good-fund--good/x/1\n"
        "2,2026-08-29,Holding(s) in Company,"
        "https://www.investegate.co.uk/announcement/rns/other-co--xyz/y/2\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "resolved_tickers.csv").write_text(
        "security_id,ticker,status\nSEDOL:1,EMPT,verified\n"
        "SEDOL:2,GOOD,verified\n")
    (tmp_path / "data" / "universe").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    ev = cli._liveness_evidence()
    got = {r["security_id"]: r for _, r in ev.iterrows()} if len(ev) else {}
    assert "SEDOL:1" not in got, "an empty cache carries no evidence"
    assert got["SEDOL:2"]["last_announcement"] == "2026-08-28", (
        "the leaked other-company row must not count as GOOD's evidence")


def test_a_short_history_gets_a_growing_z_that_prices_but_never_alerts():
    """Owner instruction 2026-09-02: use what history exists, let it grow.

    12 months of discount history now yields a computed z (status
    computed_growing_12m, window on the row) so the fund is PRICED - and
    neither alert_eligible nor the dislocation gate accepts it until the
    window reaches the validated min_months. Below the floor (6) nothing
    is computed: a two-point sigma is noise wearing a number.
    """
    params = _params()
    registry = pd.DataFrame([{"security_id": "G", "market": "UK",
                              "status": "live", "name": "G",
                              "research_eligible": True, "identity_ok": True}])
    tier0 = pd.DataFrame([{"security_id": "G", "nav_date": "2026-08-28",
                           "nav_value": 100.0, "unit": "GBX",
                           "source": "investegate:1"}])
    px = pd.DataFrame([{"security_id": "G", "price": 70.0,
                        "price_source": "yahoo:G.L",
                        "price_date": "2026-08-29", "price_ccy": "GBp"}])
    empty_panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                        "nav_total_return", "nav_per_share",
                                        "share_price", "discount"])

    def _aux(n):
        months = pd.period_range("2026-08", periods=n, freq="M")[::-1]
        return pd.DataFrame([{"security_id": "G", "obs_month": str(m),
                              "discount": -0.10 + 0.02 * (i % 5)}
                             for i, m in enumerate(sorted(months.astype(str)))])

    out = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                               "nav_per_share", "share_price", params,
                               tier0=tier0, live_prices=px, registry=registry,
                               aux_discount_history=_aux(12), today=TODAY)
    r = out.iloc[0]
    assert pd.notna(r["z_adj"]), r["z_status"]
    assert r["z_status"] == "computed_growing_12m"
    assert r["z_window_months"] == 12
    assert not r["alert_eligible"], "a growing z must not alert"

    # the dislocation gate refuses it too, however extreme the z
    live = pd.DataFrame([{"security_id": "G", "market": "UK", "name": "G",
                          "research_eligible": True, "z_adj": -4.0,
                          "z_window_months": 12, "staleness_days": 1,
                          "basis": 0, "nav_current": True,
                          "discount_est": -0.30}])
    assert not len(opportunities.evaluate(live, None, None, params))

    # below the floor: no z at all
    out2 = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                                "nav_per_share", "share_price", params,
                                tier0=tier0, live_prices=px,
                                registry=registry,
                                aux_discount_history=_aux(4), today=TODAY)
    assert pd.isna(out2.iloc[0]["z_adj"])
    assert out2.iloc[0]["z_status"] == "insufficient_history_4m"

    # and at full depth it is plainly computed and alertable
    out3 = nta_live.build_table(empty_panel, "UK", "nav_total_return",
                                "nav_per_share", "share_price", params,
                                tier0=tier0, live_prices=px,
                                registry=registry,
                                aux_discount_history=_aux(30), today=TODAY)
    assert out3.iloc[0]["z_status"] in ("computed", "within_error_band")
    assert out3.iloc[0]["z_window_months"] == 30


def test_corroborated_delisting_outranks_the_stale_nav_grace():
    """Keystone, Jupiter Green and Henderson Opportunities sat in the
    priced universe a year after their own liquidations, alive on their
    final NAVs inside the 550-day grace. When the aggregator has already
    delisted a fund AND its own filings are silent past the fresh-NAV
    window, both independent sources agree - it demotes to the review
    queue. A fund the aggregator still lists keeps the grace unchanged.
    """
    gone = liveness.classify({"last_nav": "2025-08-01",
                              "aggregator_status": "delisted"}, as_of=TODAY)
    assert gone["status"] == liveness.STATUS_CANDIDATE
    assert gone["live_status_source"] == "corroborated_delisting"
    # same silence, but the aggregator still lists it: grace holds
    listed = liveness.classify({"last_nav": "2025-08-01",
                                "aggregator_status": "live"}, as_of=TODAY)
    assert listed["status"] == liveness.STATUS_LIVE_STALE
    # fresh own NAV always wins, whatever the aggregator thinks
    fresh = liveness.classify({"last_nav": "2026-08-20",
                               "aggregator_status": "delisted"}, as_of=TODAY)
    assert fresh["status"] == liveness.STATUS_LIVE


def test_the_irr_is_decomposed_into_discount_normalisation_and_the_rest():
    """Owner instruction: show how much of the IRR is discount
    normalisation (own-history reversion, the statistically grounded leg)
    versus NAV growth and distributions (the leg that needs diligence),
    and where the growth number came from."""
    panel = pd.DataFrame([
        {"security_id": "X", "obs_month": m, "sector": "S",
         "discount": -0.10, "nav_tr_cagr_5y": 0.06}
        for m in pd.period_range("2022-01", "2026-08", freq="M").astype(str)])
    live = pd.DataFrame([{"security_id": "X", "market": "UK", "name": "X",
                          "sector": "S", "price": 70.0, "nta_est": 100.0,
                          "discount_est": -0.30}])
    got = forward_irr.build(live, panel, _params()).iloc[0]
    assert got["irr_central"] is not None
    assert got["irr_discount_only"] is not None
    # narrowing from -30% to the own median -10% alone is a real return
    assert got["irr_discount_only"] > 0.04
    # the remainder is what the growth input and distributions add
    assert got["irr_ex_discount"] == pytest.approx(
        got["irr_central"] - got["irr_discount_only"], abs=1e-4)
    assert got["g_source"] == "panel_tr_cagr"
    assert got["g_used"] is not None


def test_the_verdicts_carry_the_irr_decomposition_to_the_brief():
    live = pd.DataFrame([{"security_id": "X", "market": "UK", "name": "X",
                          "research_eligible": True, "z_adj": -3.0,
                          "staleness_days": 1, "basis": 0,
                          "discount_est": -0.30}])
    irr = pd.DataFrame([{"security_id": "X", "irr_central": 0.22,
                         "irr_discount_only": 0.09, "g_used": 0.062,
                         "g_source": "own_nav_history"}])
    got = opportunities.evaluate(live, None, irr, _params()).iloc[0]
    assert got["irr_discount_only"] == 0.09
    assert got["g_used"] == 0.062
    assert got["g_source"] == "own_nav_history"
    from cef_live import brief
    v = pd.DataFrame([got])
    html = brief.render_html("pre-LSE open", "2026-09-03", 1, v.iloc[0:0], v,
                             v.iloc[0:0], v.iloc[0:0], -1.5, 0.15, {}, None,
                             0, 1)
    assert "of which disc. norm." in html and "NAV g" in html
    assert "+9.0%" in html and "+6.2%" in html
    assert "<sup" in html and ">h</sup>" in html, "growth source marked"
