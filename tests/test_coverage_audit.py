"""The coverage audit must be able to say GREEN, AMBER, RED and mean it.

These tests fix the meaning of each verdict against synthetic funds whose
answer is known by construction, so a later change to a threshold or a rule
shows up as a failing expectation rather than as a quietly different
percentage in a report nobody re-derives.

Synthetic data is used in UNIT TESTS ONLY - never in a published number.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cef_live import coverage_audit as CA
from cef_live import units

TODAY = date(2026, 8, 31)
PARAMS = CA.load_params()


# ------------------------------------------------------------------ units

def test_pence_and_pounds_are_different_units_and_the_gap_is_detected():
    """The bug this whole module exists for: a NAV in pounds, a price in pence.

    Montanaro's live row read a 5,649% premium because a 113p share price
    was divided into a NAV of 1.13 pounds. The ratio is not a valuation,
    it is a unit, and the check has to say so without rewriting either side.
    """
    got = units.scale_diagnosis(price=113.0, nav=1.13)      # pence vs pounds
    assert got["unit_check_status"] == "suspect_scale"
    assert got["suspected_scale_factor"] == 100.0
    assert got["extreme_discount_flag"]

    # and the conversion itself, when the unit is stated UNAMBIGUOUSLY, is
    # explicit. "GBP" is NOT such a unit - Yahoo and the AIC both use it for
    # pence quotes - so only the word converts. See
    # test_a_sterling_currency_label_never_rescales_a_uk_price.
    v, unit, note = units.normalise("UK", 1.13, "pounds")
    assert v == pytest.approx(113.0) and unit == "GBX"
    assert note == "converted_x100_from_pounds"
    assert units.normalise("UK", 113.0, "GBX")[0] == 113.0
    # an unstated unit is assumed canonical and SAYS so - never silently
    assert units.normalise("UK", 113.0, None)[2] == "unit_unstated_assumed_canonical"


def test_cents_and_dollars_are_different_units_and_the_gap_is_detected():
    got = units.scale_diagnosis(price=2.50, nav=250.0)      # dollars vs cents
    assert got["unit_check_status"] == "suspect_scale"
    assert got["suspected_scale_factor"] == 0.01
    assert units.normalise("AU", 250.0, "cents")[0] == 2.5
    assert units.normalise("AU", 2.5, "AUD")[0] == 2.5


def test_a_plausible_discount_is_not_a_unit_warning():
    assert units.scale_diagnosis(90.0, 100.0)["unit_check_status"] == "ok"
    assert units.scale_diagnosis(70.0, 100.0)["unit_check_status"] == "ok"
    assert not units.scale_diagnosis(70.0, 100.0)["extreme_discount_flag"]


def test_an_80_percent_discount_is_flagged_without_being_called_a_unit_error():
    got = units.scale_diagnosis(15.0, 100.0)               # -85%
    assert got["unit_check_status"] == "extreme"
    assert got["extreme_discount_flag"]
    assert got["suspected_scale_factor"] is None


def test_a_quote_currency_that_contradicts_the_market_is_reported():
    # GBP is ambiguous rather than wrong - reported, never acted on
    assert "ambiguous" in units.unit_metadata_conflict("UK", "GBP")
    assert units.unit_metadata_conflict("UK", "GBp") is None
    assert units.unit_metadata_conflict("AU", "USD").startswith("quote_currency_USD")
    assert units.unit_metadata_conflict("AU", "AUD") is None


def test_discount_is_absent_not_zero_when_it_cannot_be_computed():
    assert units.discount(100.0, None) is None
    assert units.discount(100.0, 0.0) is None
    assert units.discount(90.0, 100.0) == pytest.approx(-0.10)


# ------------------------------------------------------------------ rows

def _row(**kw) -> dict:
    """A monitoring-eligible fund with clean data; override to degrade it."""
    base = dict(
        market="UK", market_code="UK", ticker="CTY", name="City of London",
        security_id="SEDOL:0199049", isin="GB0001990497", sedol="0199049",
        asx_code=None, sector="UK Equity Income", share_type="Ordinary Share",
        research_eligible=True, monitoring_eligible=True, live_status="live",
        live_status_reason="nav_1d_old", live_status_source="own_nav_announcement",
        aggregator_status="live", exclusion_reason="",
        price=452.0, price_unit="GBX", price_raw=452.0, price_raw_ccy="GBp",
        price_unit_note="unit_canonical", price_date="2026-08-28",
        price_source="yahoo:CTY.L", price_age_days=3, price_age_trading_days=1,
        price_is_fresh=True, price_is_fallback_panel=False,
        ticker_status="verified", price_error=None,
        nav=460.0, nav_unit="GBX", nav_stored_raw=460.0,
        nav_effective_date="2026-08-28", nav_announcement_date="2026-08-28",
        nav_source="investegate:1", nav_basis=0,
        nav_basis_label=CA.BASIS_LABEL[0], nav_age_days=3, nav_is_published=True,
        nav_kind="newly_published_nav_announcement", nav_est_error=0.01,
        nav_staleness_days=1, nav_rederived="tier0_announcement",
        nav_parser_status="parsed", nav_announcement_unparsed=False,
        nav_parse_failed_recorded=False,
        nav_parse_reason=None, nav_parse_rate=1.0, nav_announcements_held=120,
        last_parsed_nav_date="2026-08-28",
        discount=-0.0174, discount_stored=-0.0174, price_nav_ratio=0.9826,
        unit_check_status="ok", unit_check_reason="", suspected_scale_factor=None,
        extreme_discount_flag=False, price_vs_panel_ratio=None,
        price_history_check="no_panel_price_held",
        disc_mu_36m=-0.03, disc_sigma_36m=0.02, zscore_history_ok=True,
        zscore_min_months_required=24, z_adj=0.5, z_raw=0.5,
        z_status="computed", alert_eligible=True, in_live_table=True,
        identity_status="sole_claimant", identity_ok=True, identity_reason="",
        identity_incumbent_id="SEDOL:0199049", identity_incumbent_name="City of London",
        identity_claimants=1, identity_same_name=False,
        data_quality_ok=True, data_quality_reason="",
    )
    base.update(kw)
    return base


def _classify(*rows) -> pd.DataFrame:
    return CA.classify_coverage(pd.DataFrame(list(rows)), PARAMS)


def test_a_clean_fund_is_green_and_signal_ready():
    got = _classify(_row()).iloc[0]
    assert got["coverage_status"] == CA.GREEN
    assert got["signal_ready"]
    assert got["usable_price"] and got["usable_nav"] and got["valid_discount"]
    assert got["blocking_issue"] == ""


def test_a_rolled_forward_nav_is_amber_not_green():
    """Usable, with a qualification stated - which is the point of AMBER."""
    got = _classify(_row(nav_basis=1, nav_is_published=False,
                         nav_kind="rolled_forward_model",
                         nav_source="aggregator_panel:2026-07",
                         nav_age_days=20)).iloc[0]
    assert got["coverage_status"] == CA.AMBER
    assert "rolled_forward_nav" in got["coverage_reason"]
    assert not got["signal_ready"]
    assert got["usable_price"] and got["usable_nav"]   # still monitorable


def test_a_somewhat_stale_price_is_amber_and_a_very_stale_one_is_red():
    amber = _classify(_row(price_age_days=9, price_is_fresh=False)).iloc[0]
    assert amber["coverage_status"] == CA.AMBER
    assert "stale_price" in amber["coverage_reason"]
    red = _classify(_row(price_age_days=120, price_is_fresh=False)).iloc[0]
    assert red["coverage_status"] == CA.RED
    assert red["blocking_issue"] == "stale_price"
    assert not red["usable_price"]


def test_a_fresh_price_inside_the_window_is_fresh():
    thr = CA.thresholds(PARAMS)["price"]["fresh_days"]
    got = _classify(_row(price_age_days=thr, price_is_fresh=True)).iloc[0]
    assert got["coverage_status"] == CA.GREEN


def test_a_historical_panel_price_is_never_treated_as_todays_price():
    """The fallback is RED however recent the month it came from.

    A month-end aggregator print is not a market price, and presenting it
    as one is the failure the whole audit is built to make impossible.
    """
    got = _classify(_row(price_is_fallback_panel=True, price_is_fresh=False,
                         price_source="aggregator_panel", price_date="2026-08",
                         price_age_days=1)).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "stale_panel_price_only"
    assert not got["usable_price"]


def test_an_unresolved_ticker_is_red_and_named_as_such():
    got = _classify(_row(ticker=None, ticker_status="unresolved", price=None,
                         price_is_fresh=False,
                         price_error="no_ticker_to_price_with")).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "ticker_unresolved"
    assert "resolve the TIDM" in got["recommended_fix"]


def test_no_usable_nav_is_red():
    got = _classify(_row(nav=None, nav_age_days=None, nav_basis=None,
                         nav_source=None, discount=None)).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "no_nav"
    assert not got["usable_nav"] and not got["valid_discount"]


def test_a_nav_announcement_that_exists_but_did_not_parse_is_its_own_failure():
    """"The fund went quiet" and "our regex missed" must never be one label."""
    got = _classify(_row(nav=None, nav_age_days=None, nav_basis=None,
                         nav_source=None, discount=None,
                         nav_announcement_unparsed=True,
                         nav_announcement_date="2026-08-28",
                         nav_parser_status="parse_failed_recorded",
                         nav_parse_failed_recorded=True,
                         last_parsed_nav_date=None)).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "nav_announcement_unparsed"
    assert "parser rule" in got["recommended_fix"] or "add a" in got["recommended_fix"]


def test_a_suspected_unit_mismatch_is_red_and_nothing_is_rescaled():
    got = _classify(_row(nav=4.60, price=452.0, price_nav_ratio=98.26,
                         unit_check_status="suspect_scale",
                         suspected_scale_factor=100.0,
                         unit_check_reason="~100x")).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "suspected_unit_mismatch"
    # the numbers are reported exactly as found
    assert got["nav"] == 4.60 and got["price"] == 452.0
    assert "do NOT rescale" in got["recommended_fix"]


def test_insufficient_zscore_history_is_amber_not_green():
    got = _classify(_row(zscore_history_ok=False, disc_sigma_36m=None,
                         z_adj=None)).iloc[0]
    assert got["coverage_status"] == CA.AMBER
    assert got["blocking_issue"] == "insufficient_zscore_history"
    assert not got["signal_ready"]
    assert got["valid_discount"]          # it can still price a discount


def test_an_excluded_fund_can_never_be_signal_ready():
    """Even with perfect data. Exclusion is a policy fact, not a data fact."""
    got = _classify(_row(monitoring_eligible=False, research_eligible=False,
                         exclusion_reason="vct_excluded_by_research_policy")).iloc[0]
    assert got["coverage_status"] == CA.EXCLUDED
    assert not got["signal_ready"]
    assert not got["usable_price"] and not got["valid_discount"]
    assert got["coverage_reason"] == "vct_excluded_by_research_policy"


def test_nav_basis_labels_cover_the_three_bases_the_system_produces():
    for basis, frag in ((0, "published NAV"), (1, "roll-forward"), (3, "stale")):
        assert frag in CA.BASIS_LABEL[basis]
    rows = _classify(_row(nav_basis=0),
                     _row(nav_basis=1, nav_is_published=False, nav_age_days=20,
                          nav_source="aggregator_panel:2026-08"),
                     _row(nav_basis=3, nav_is_published=True, nav_age_days=60))
    assert rows.iloc[0]["coverage_status"] == CA.GREEN            # basis 0
    assert rows.iloc[1]["coverage_status"] == CA.AMBER            # basis 1
    assert rows.iloc[2]["coverage_status"] == CA.AMBER            # stale anchor
    assert "stale_nav" in rows.iloc[2]["coverage_reason"]


# ------------------------------------------------------------ reconciliation

def test_summary_counts_reconcile_to_the_row_level_data():
    rows = _classify(
        _row(security_id="A"),
        _row(security_id="B", nav_basis=1, nav_is_published=False,
             nav_age_days=20, nav_source="aggregator_panel:2026-08"),
        _row(security_id="C", price=None, price_is_fresh=False, discount=None),
        _row(security_id="D", market="ASX", market_code="AU", ticker="ARG",
             asx_code="ARG", security_id_="x"),
        _row(security_id="E", monitoring_eligible=False,
             exclusion_reason="vct_excluded_by_research_policy"),
    )
    s = CA.summarise(rows, PARAMS)
    for market in ("UK", "ASX", "COMBINED"):
        g = rows if market == "COMBINED" else rows[rows["market"] == market]
        mon = g[g["monitoring_eligible"]]
        u = s[market]["universe"]
        assert u["registry_total"] == len(g)
        assert u["monitoring_eligible"] == len(mon)
        assert u["excluded"] == len(g) - len(mon)
        assert s[market]["signal"]["signal_ready"] == int(mon["signal_ready"].sum())
        assert sum(s[market]["status"].values()) == len(mon), (
            "every monitored fund must land in exactly one of GREEN/AMBER/RED")
        assert s[market]["prices"]["fresh"] == int(mon["price_is_fresh"].sum())
    assert (s["COMBINED"]["universe"]["monitoring_eligible"]
            == s["UK"]["universe"]["monitoring_eligible"]
            + s["ASX"]["universe"]["monitoring_eligible"])


def test_green_is_exactly_the_signal_ready_set():
    rows = _classify(_row(security_id="A"),
                     _row(security_id="B", zscore_history_ok=False),
                     _row(security_id="C", price=None, price_is_fresh=False))
    assert list(rows["signal_ready"]) == list(rows["coverage_status"] == CA.GREEN)


def test_every_failing_fund_appears_in_the_failure_table():
    rows = _classify(_row(security_id="A"),
                     _row(security_id="B", zscore_history_ok=False),
                     _row(security_id="C", price=None, price_is_fresh=False,
                          discount=None))
    f = CA.failure_table(rows)
    assert set(f["security_id"]) == {"B", "C"}, "GREEN funds must not appear"
    rank = CA.failure_ranking(f)
    assert rank["Total"].sum() == len(f)
    assert set(rank.columns) == {"issue", "UK", "ASX", "Total"}


def test_excluded_funds_stay_in_the_output_with_their_reason():
    """Hiding a fund from the denominator is how a coverage number lies."""
    rows = _classify(_row(security_id="A"),
                     _row(security_id="X", monitoring_eligible=False,
                          research_eligible=False,
                          exclusion_reason="vct_excluded_by_research_policy"))
    assert "X" in set(rows["security_id"])
    s = CA.summarise(rows, PARAMS)
    assert s["UK"]["excluded_by_reason"]["vct_excluded_by_research_policy"] == 1


# ------------------------------------------------------------- market split

def test_uk_and_asx_are_never_joined_to_the_wrong_historical_panel():
    """Each market's history belongs to its own funds and nothing else."""
    from cef_live import cli, forward_irr

    assert cli.PANEL_PATHS["UK"] != cli.PANEL_PATHS["AU"]
    assert forward_irr.CAGR_COLS["UK"] != forward_irr.CAGR_COLS["AU"]

    uk_panel = pd.DataFrame([
        {"security_id": "SEDOL:AAA", "obs_month": m, "sector": "S",
         "discount": -0.1, "nav_tr_cagr_5y": 0.08, "share_price": 100.0,
         "nav_per_share": 110.0}
        for m in pd.period_range("2021-01", "2026-06", freq="M").astype(str)])
    au_panel = pd.DataFrame([
        {"security_id": "ASX:BBB", "obs_month": m, "sector": "T",
         "discount": -0.2, "nta_tr_cagr_5y": 0.05, "share_price": 1.0,
         "nta_derived": 1.25}
        for m in pd.period_range("2021-01", "2026-06", freq="M").astype(str)])
    live = pd.DataFrame([
        {"security_id": "SEDOL:AAA", "market": "UK", "price": 100.0,
         "nta_est": 110.0, "discount_est": -0.09},
        {"security_id": "ASX:BBB", "market": "AU", "price": 1.0,
         "nta_est": 1.25, "discount_est": -0.2}])

    irr = forward_irr.build_by_market(
        live, {"UK": uk_panel, "AU": au_panel}, CA.load_params())
    assert set(irr["security_id"]) == {"SEDOL:AAA", "ASX:BBB"}
    got = irr.set_index("security_id")
    # each fund's growth input comes from ITS OWN panel's CAGR column
    assert got.loc["SEDOL:AAA", "g_used"] == pytest.approx(0.08)
    assert got.loc["ASX:BBB", "g_used"] == pytest.approx(0.05)
    assert got.loc["ASX:BBB", "terminal_discount_own"] == pytest.approx(-0.2)


def test_the_audit_labels_australian_rows_ASX_and_keeps_the_two_apart():
    rows = _classify(_row(security_id="A", market="UK"),
                     _row(security_id="B", market="ASX", market_code="AU"))
    s = CA.summarise(rows, PARAMS)
    assert s["UK"]["universe"]["registry_total"] == 1
    assert s["ASX"]["universe"]["registry_total"] == 1
    assert s["COMBINED"]["universe"]["registry_total"] == 2


# --------------------------------------------------------------- no lookahead

def test_no_look_ahead_every_dated_fact_is_at_or_before_the_as_of_date():
    """An age is never negative, and a date after `as_of` is not evidence."""
    assert CA._age_days("2026-08-28", TODAY) == 3
    assert CA._age_days(None, TODAY) is None
    # a future-dated observation yields a negative age, which the freshness
    # rule must not silently accept as "very fresh"
    assert CA._age_days("2026-09-30", TODAY) < 0
    got = _classify(_row(price_date="2026-09-30", price_age_days=-30,
                         price_is_fresh=False)).iloc[0]
    assert got["coverage_status"] != CA.GREEN, (
        "a price dated after the audit date is not a fresh price")


def test_the_audit_is_deterministic_for_the_same_inputs():
    a = _classify(_row(security_id="A"), _row(security_id="B", nav=None,
                                              nav_age_days=None, discount=None))
    b = _classify(_row(security_id="A"), _row(security_id="B", nav=None,
                                              nav_age_days=None, discount=None))
    pd.testing.assert_frame_equal(a, b)


# --------------------------------------------------------------- on demand

def test_the_audit_is_manual_only():
    """No schedule, anywhere: not in the workflow, not in the module."""
    import yaml as _yaml

    wf = _yaml.safe_load(Path(".github/workflows/live-coverage-audit.yml").read_text())
    on = wf[True] if True in wf else wf["on"]
    assert "workflow_dispatch" in on
    assert "schedule" not in on, "the coverage audit must never be scheduled"
    assert "push" not in on

    # and it must not have been bolted onto an existing scheduled job
    for f in Path(".github/workflows").glob("*.yml"):
        text = f.read_text()
        if "coverage_audit" in text and f.name != "live-coverage-audit.yml":
            doc = _yaml.safe_load(text)
            trig = doc[True] if True in doc else doc.get("on", {})
            assert "schedule" not in (trig or {}), (
                f"{f.name} would run the coverage audit on a schedule")


def test_a_sterling_currency_label_never_rescales_a_uk_price():
    """GBp and GBP differ only by case, and this module lowercased both.

    Yahoo labels London lines "GBp" - pence. A case-insensitive lookup
    collapsed that into "gbp" and multiplied by 100, so the first audit run
    with real currency metadata put AVI Global in at 265.5p and reported it
    at 26,550 against a 280.51p NAV: a 9,265% premium, on 271 UK funds at
    once, produced by the module built to prevent exactly this.

    uk_cef/panel.py records the same hazard from the other side - some AIC
    vintages label pence prices "GBP" - so no sterling label can be trusted
    to mean pounds. A value that really is in pounds is caught by
    scale_diagnosis as a ~100x gap and reported, not silently rescaled.
    """
    for label in ("GBp", "GBP", "GBX", "gbx", "STG", "pence", "p", None, ""):
        value, unit, _ = units.normalise("UK", 265.5, label)
        assert value == pytest.approx(265.5), (
            f"a {label!r} label rescaled a pence price to {value}")
        assert unit == "GBX"

    # the real pairs from the live table, which must read as ordinary discounts
    for price, nav, lo, hi in ((265.5, 280.51, -0.10, 0.0),   # AVI Global
                               (395.5, 419.0, -0.10, 0.0),    # 3i Infrastructure
                               (453.0, 501.6, -0.15, 0.0)):   # Aberdeen Asia Focus
        px, _, _ = units.normalise("UK", price, "GBp")
        d = units.discount(px, nav)
        assert lo <= d <= hi, f"{price}/{nav} gave {d:+.2%}"
        assert units.scale_diagnosis(px, nav)["unit_check_status"] == "ok"

    # an explicit WORD still converts - it is a description, not a code two
    # conventions disagree about
    assert units.normalise("UK", 2.655, "pounds")[0] == pytest.approx(265.5)

    # and a genuinely foreign quote is never read as pence
    assert units.unit_metadata_conflict("UK", "USD").startswith("quote_currency_USD")
    assert units.unit_metadata_conflict("UK", "GBp") is None
    assert "ambiguous" in units.unit_metadata_conflict("UK", "GBP")


# ------------------------------------------- GREEN is a conjunction (P5, P6)

def test_green_requires_every_named_input_and_not_merely_no_complaints():
    """Ironbark Capital was GREEN and signal-ready with a NAV of 0.0.

    GREEN used to mean "no RED issue fired and no AMBER qualification
    fired", which is a weaker statement than "every required input is
    valid": a zero NAV tripped no rule in either list, so it fell through.
    valid_discount was already False on the same row - the audit knew and
    said GREEN anyway.
    """
    for label, override, expect in (
            ("nav is zero", dict(nav=0.0, discount=None, unit_check_status="no_data"), CA.RED),
            ("nav is negative", dict(nav=-1.0, discount=None,
                                     unit_check_status="no_data"), CA.RED),
            ("no discount", dict(discount=None), CA.RED),
            ("no current z-score", dict(z_adj=None, z_status="voided_within_error_band"),
             CA.AMBER),
            ("no history", dict(zscore_history_ok=False, disc_sigma_36m=None,
                                z_adj=None), CA.AMBER),
            ("identity unresolved", dict(identity_ok=False,
                                         identity_status="conflict"), CA.RED),
            ("suspect units", dict(unit_check_status="suspect_scale",
                                   suspected_scale_factor=100.0), CA.RED),
            ("price is a panel fallback", dict(price_is_fallback_panel=True,
                                               price_is_fresh=False), CA.RED)):
        got = _classify(_row(**override)).iloc[0]
        assert got["coverage_status"] == expect, (
            f"{label}: expected {expect}, got {got['coverage_status']} "
            f"({got['coverage_reason']})")
        assert not got["signal_ready"], f"{label} was signal-ready"


def test_every_green_row_satisfies_every_invariant():
    """The property, asserted directly rather than case by case."""
    rows = _classify(
        _row(security_id="A"),
        _row(security_id="B", nav=0.0, discount=None, unit_check_status="no_data"),
        _row(security_id="C", z_adj=None, z_status="voided_within_error_band"),
        _row(security_id="D", identity_ok=False, identity_status="conflict"),
        _row(security_id="E", price=None, price_is_fresh=False, discount=None),
    )
    green = rows[rows["coverage_status"] == CA.GREEN]
    assert len(green) == 1 and green.iloc[0]["security_id"] == "A"
    assert green["usable_price"].all() and green["usable_nav"].all()
    assert green["valid_discount"].all()
    assert green["z_adj"].notna().all(), "GREEN without a current z-score"
    assert (green["nav"] > 0).all(), "GREEN with a non-positive NAV"
    assert green["identity_ok"].all()
    assert green["unit_check_status"].isin(["ok", "extreme"]).all()
    assert list(rows["signal_ready"]) == list(rows["coverage_status"] == CA.GREEN)


def test_a_fund_with_history_but_no_current_z_is_amber_and_says_why():
    """42 funds were GREEN on history alone. History is the INPUT; a
    populated z_adj is the output, and only the second means a signal
    exists. Where the z was voided because the discount sits inside the
    estimate's own error band, the row says so rather than reading as a
    coverage failure.
    """
    got = _classify(_row(z_adj=None, z_status="voided_within_error_band")).iloc[0]
    assert got["coverage_status"] == CA.AMBER
    assert got["blocking_issue"] == "z_within_error_band"
    assert "none needed" in got["recommended_fix"]
    assert got["valid_discount"], "the discount itself is still fine"


def test_a_row_that_passes_every_check_but_yields_no_discount_is_a_defect():
    """"Everything looked fine and no discount came out" is a bug in the
    discount path, and must not pass as a coverage gap."""
    got = _classify(_row(discount=None)).iloc[0]
    assert got["coverage_status"] == CA.RED
    assert got["blocking_issue"] == "no_valid_discount"
