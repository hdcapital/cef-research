"""Tests for the market-wide discount history aggregation layer.

The real panels are built from licensed source archives in CI, so these
tests drive the whole stack from synthetic panels with the same schema -
the arithmetic, the taxonomy and the thin-group guards are what can
silently go wrong, and all three are checked against hand-computed values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from discount_history import aggregate, panel as panel_mod, segments, workbook


# --------------------------------------------------------------- taxonomy
def test_renamed_sectors_collapse_to_one_canonical_sector():
    """The AIC renamed sectors mid-sample; a series must not split in two."""
    pairs = [
        ("Sector Specialist: Debt", "Debt - Loans & Bonds"),
        ("China / Greater China", "China - Greater China"),
        ("Biotechnology/Life Sciences", "Sector Specialist: Biotechnology & Healthcare"),
        ("Commodities and Natural Resources", "Commodities & Natural Resources"),
        ("Small Media, Comms & IT Cos", "Tech Media & Telecomm"),
    ]
    for old, new in pairs:
        assert segments.canonical_sector(old) == segments.canonical_sector(new), (old, new)
        assert segments.segment(old) == segments.segment(new), (old, new)


def test_segment_rules_put_labels_in_the_right_bucket():
    cases = {
        "Renewable Energy Infrastructure": "Infrastructure & Renewables",
        "Property Securities": "Property",
        "Property - UK Commercial": "Property",
        "Private Equity": "Private Equity",
        "Hedge Funds": "Hedge & Multi-Asset",
        "Flexible Investment": "Hedge & Multi-Asset",
        "UK Smaller Companies": "UK Equity",
        "Equity - Australia Small/Mid Cap": "Australia Equity",
        "Equity - Global": "Global Equity",
        "Fixed Income - Australian Dollar": "Debt & Fixed Income",
        "Japan": "Japan Equity",
        "Global Emerging Markets": "Emerging & Frontier Equity",
    }
    for label, expected in cases.items():
        assert segments.segment(label) == expected, label


def test_equity_and_bond_income_is_equity_not_credit():
    """The bare \\bbond\\b credit rule sits above geography; this hybrid
    equity mandate must not be captured by it."""
    assert segments.segment("UK Equity & Bond Income") == "UK Equity"


def test_missing_labels_are_flagged_not_dropped():
    for blank in (None, "", float("nan")):
        assert segments.segment(blank) == segments.UNCLASSIFIED
        assert segments.canonical_sector(blank) == segments.UNCLASSIFIED


def test_a_sector_the_rules_do_not_know_keeps_its_own_label():
    """A sector the AIC introduces later must still appear under its own
    name - only the coarse cross-market bucket falls back to Unclassified,
    so the new sector is visible in the by-sector table rather than being
    quietly merged into an existing one."""
    novel = "Wholly Novel Sector 9000"
    assert segments.canonical_sector(novel) == novel
    assert segments.segment(novel) == segments.UNCLASSIFIED


# ------------------------------------------------------------- aggregation
def _frame(rows):
    df = pd.DataFrame(rows)
    df["market_cap_local"] = df.get("market_cap_local", np.nan)
    return df


def test_equal_and_cap_weighted_means_match_hand_computation():
    df = _frame([
        {"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
         "security_id": "s1", "discount": -0.10, "market_cap_local": 100.0},
        {"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
         "security_id": "s2", "discount": -0.20, "market_cap_local": 300.0},
        {"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
         "security_id": "s3", "discount": 0.00, "market_cap_local": 100.0},
    ])
    out = aggregate.by_market(df, min_funds=3)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["n_funds"] == 3
    assert row["mean_discount"] == pytest.approx(-0.10)
    assert row["median_discount"] == pytest.approx(-0.10)
    # (-0.10*100 + -0.20*300 + 0*100) / 500 = -70/500
    assert row["cap_weighted_discount"] == pytest.approx(-0.14)
    assert row["mean_discount_pct"] == pytest.approx(-10.0)
    assert row["pct_at_discount"] == pytest.approx(2 / 3)
    assert row["pct_wider_than_10"] == pytest.approx(2 / 3)
    assert row["pct_at_premium"] == pytest.approx(0.0)


def test_cap_weighting_is_nan_rather_than_a_silent_equal_weight_fallback():
    """With no usable weights the cap-weighted figure must read as missing,
    not quietly repeat the equal-weighted mean."""
    df = _frame([
        {"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
         "security_id": f"s{i}", "discount": -0.10 * i, "market_cap_local": np.nan}
        for i in range(1, 4)])
    row = aggregate.by_market(df, min_funds=3).iloc[0]
    assert np.isnan(row["cap_weighted_discount"])
    assert not np.isnan(row["mean_discount"])


def test_thin_groups_are_suppressed_but_counted():
    rows = [{"month": "2020-01", "market": "UK", "segment": "Tiny",
             "sector_canon": "Tiny", "security_id": "s1", "discount": -0.40}]
    rows += [{"month": "2020-01", "market": "UK", "segment": "Big",
              "sector_canon": "Big", "security_id": f"b{i}", "discount": -0.10}
             for i in range(4)]
    out = aggregate.by_segment(_frame(rows), min_funds=3)
    tiny = out[out["segment"] == "Tiny"].iloc[0]
    big = out[out["segment"] == "Big"].iloc[0]
    assert tiny["n_funds"] == 1 and not tiny["sufficient"]
    assert np.isnan(tiny["mean_discount"])          # not reported...
    assert tiny["n_funds"] == 1                      # ...but still visible
    assert big["sufficient"] and big["mean_discount"] == pytest.approx(-0.10)


def test_market_wide_balanced_mean_gives_each_market_one_vote():
    """Pooling every fund lets the larger market dominate; the balanced
    mean must average the two market means instead."""
    rows = [{"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
             "security_id": f"u{i}", "discount": -0.20} for i in range(9)]
    rows += [{"month": "2020-01", "market": "ASX", "segment": "B", "sector_canon": "B",
              "security_id": f"a{i}", "discount": -0.10} for i in range(3)]
    mw = aggregate.market_wide(_frame(rows), min_funds=3).iloc[0]
    # pooled: (9*-0.20 + 3*-0.10)/12 = -0.175 ; balanced: (-0.20 + -0.10)/2
    assert mw["mean_discount"] == pytest.approx(-0.175)
    assert mw["mean_discount_balanced"] == pytest.approx(-0.15)
    assert mw["markets_included"] == "ASX+UK"
    assert mw["n_markets"] == 2


def test_market_wide_records_composition_changes():
    """The ASX panel starts a decade late; a pooled series must say so."""
    rows = [{"month": "2010-01", "market": "UK", "segment": "A", "sector_canon": "A",
             "security_id": f"u{i}", "discount": -0.15} for i in range(5)]
    rows += [{"month": "2020-01", "market": "UK", "segment": "A", "sector_canon": "A",
              "security_id": f"u{i}", "discount": -0.15} for i in range(5)]
    rows += [{"month": "2020-01", "market": "ASX", "segment": "B", "sector_canon": "B",
              "security_id": f"a{i}", "discount": -0.05} for i in range(5)]
    mw = aggregate.market_wide(_frame(rows), min_funds=3).set_index("month")
    assert mw.loc["2010-01", "markets_included"] == "UK"
    assert mw.loc["2010-01", "n_markets"] == 1
    assert mw.loc["2020-01", "markets_included"] == "ASX+UK"


def test_segment_summary_reports_extremes_and_latest_gap():
    rows = []
    for month, disc in [("2020-01", -0.10), ("2020-02", -0.30), ("2020-03", -0.20)]:
        rows += [{"month": month, "market": "UK", "segment": "A", "sector_canon": "A",
                  "security_id": f"s{i}", "discount": disc} for i in range(4)]
    seg = aggregate.by_segment(_frame(rows), min_funds=3)
    summary = aggregate.segment_summary(seg).iloc[0]
    assert summary["months"] == 3
    assert summary["avg_discount"] == pytest.approx(-0.20)
    assert summary["widest_month"] == "2020-02"
    assert summary["narrowest_month"] == "2020-01"
    assert summary["latest_discount"] == pytest.approx(-0.20)
    assert summary["latest_vs_own_average_pp"] == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------- panel harmonising
def _write_panels(tmp_path, with_eligible=True, au=True):
    uk = pd.DataFrame({
        "security_id": ["SEDOL:1", "SEDOL:1", "SEDOL:2"],
        "company_name": ["Alpha IT", "Alpha IT", "Beta IT"],
        "obs_month": ["2019-01", "2019-02", "2019-01"],
        "sector": ["UK Smaller Companies", "UK Smaller Companies", "Private Equity"],
        "discount": [-0.12, -0.15, -0.30],
        "share_price": [100.0, 98.0, 250.0],
        "nav_per_share": [113.6, 115.3, 357.1],
        "market_cap": [120.0, 118.0, 450.0],
    })
    if with_eligible:
        uk["eligible"] = [True, True, True]
    uk_path = tmp_path / "monthly_panel.parquet"
    uk.to_parquet(uk_path)

    au_path = tmp_path / "au_monthly_panel.parquet"
    if au:
        pd.DataFrame({
            "security_id": ["ASX:AAA", "ASX:BBB"],
            "company_name": ["Aussie LIC", "Bondy LIT"],
            "obs_month": ["2019-01", "2019-01"],
            "sector": ["Equity - Australia", "Fixed Income - Australian Dollar"],
            "discount": [-0.08, 0.02],
            "share_price": [1.5, 2.0],
            "market_cap": [300.0, 90.0],
            "eligible": [True, True],
        }).to_parquet(au_path)
    return uk_path, au_path


def test_panel_harmonises_both_markets_and_maps_segments(tmp_path):
    uk_path, au_path = _write_panels(tmp_path)
    out = panel_mod.build(uk_path, au_path)
    assert set(out["market"]) == {"UK", "ASX"}
    assert len(out) == 5
    assert set(panel_mod.COLUMNS).issubset(out.columns)
    uk_pe = out[(out["market"] == "UK") & (out["security_id"] == "SEDOL:2")].iloc[0]
    assert uk_pe["segment"] == "Private Equity"
    asx_fi = out[out["security_id"] == "ASX:BBB"].iloc[0]
    assert asx_fi["segment"] == "Debt & Fixed Income"
    assert asx_fi["currency"] == "AUD"
    # month_end must be a real month-end timestamp for the time axis
    assert str(out["month_end"].max().date()) == "2019-02-28"


def test_missing_au_panel_degrades_to_uk_only(tmp_path):
    """One market's panel absent must not sink the other market's 19 years."""
    uk_path, au_path = _write_panels(tmp_path, au=False)
    out = panel_mod.build(uk_path, au_path)
    assert set(out["market"]) == {"UK"}
    assert len(out) == 3


def test_both_panels_missing_raises_a_clear_error(tmp_path):
    with pytest.raises(panel_mod.PanelMissing):
        panel_mod.build(tmp_path / "nope.parquet", tmp_path / "also-nope.parquet")


def test_missing_eligible_column_falls_back_to_discount_present(tmp_path):
    uk_path, au_path = _write_panels(tmp_path, with_eligible=False, au=False)
    out = panel_mod.build(uk_path, au_path)
    assert out["eligible"].all()


def test_duplicate_security_months_are_dropped(tmp_path):
    """A duplicated security-month would double-weight that fund in every
    average downstream."""
    dup = pd.DataFrame({
        "security_id": ["SEDOL:1", "SEDOL:1"],
        "company_name": ["Alpha", "Alpha"],
        "obs_month": ["2019-01", "2019-01"],
        "sector": ["Global", "Global"],
        "discount": [-0.10, -0.20],
        "eligible": [True, True],
    })
    p = tmp_path / "monthly_panel.parquet"
    dup.to_parquet(p)
    out = panel_mod.build(p, tmp_path / "absent.parquet")
    assert len(out) == 1
    assert out.iloc[0]["discount"] == pytest.approx(-0.20)  # keeps the later row


def test_eligible_only_drops_flagged_and_nan_rows():
    df = pd.DataFrame({
        "eligible": [True, True, False],
        "discount": [-0.1, np.nan, -0.2],
        "security_id": ["a", "b", "c"],
        "market": ["UK", "UK", "UK"],
    })
    kept, _ = panel_mod.eligible_only(df)
    assert list(kept["security_id"]) == ["a"]


# ---------------------------------------------------------- quality bounds
def test_absurd_premiums_are_excluded_and_reported():
    """Regression: the UK panel's upstream gate enforces the discount floor
    but NOT the premium ceiling both configs declare, so a source row with a
    price in the wrong unit (a 20,000,000p price against a 160p NAV) reached
    the aggregate as a +1,250,000% premium and moved a whole month's mean by
    thousands of points. The bound must drop it AND hand it back for audit."""
    df = pd.DataFrame({
        "market": ["UK", "UK", "UK", "ASX"],
        "security_id": ["ok", "unit_error", "too_cheap", "ok_asx"],
        "month": ["2021-04"] * 4,
        "discount": [-0.12, 1250.22, -0.97, -0.08],
        "eligible": [True, True, True, True],
    })
    kept, excluded = panel_mod.eligible_only(df)
    assert set(kept["security_id"]) == {"ok", "ok_asx"}
    assert set(excluded["security_id"]) == {"unit_error", "too_cheap"}
    reasons = dict(zip(excluded["security_id"], excluded["exclusion_reason"]))
    assert "ceiling" in reasons["unit_error"]
    assert "floor" in reasons["too_cheap"]


def test_quality_bounds_come_from_the_config_files():
    """The bound the study documents and the bound it applies must not drift."""
    bounds = panel_mod.load_quality_bounds()
    assert set(bounds) == {"UK", "ASX"}
    for market, (floor, ceiling) in bounds.items():
        assert floor == pytest.approx(-0.85), market
        assert ceiling == pytest.approx(1.00), market


def test_a_genuine_extreme_premium_is_bounded_the_same_as_an_error():
    """The ceiling is applied by rule, not by judgement: a real fund whose
    NAV was written down to near zero (leaving the shares at a ~+300%
    premium) is excluded on the same declared bound as a unit error, and
    shows up in the audit file rather than vanishing."""
    df = pd.DataFrame({
        "market": ["UK", "UK"],
        "security_id": ["JEMA", "normal"],
        "month": ["2025-04", "2025-04"],
        "discount": [3.908306, -0.10],
        "eligible": [True, True],
    })
    kept, excluded = panel_mod.eligible_only(df)
    assert list(kept["security_id"]) == ["normal"]
    assert list(excluded["security_id"]) == ["JEMA"]


def test_bounds_are_applied_per_market():
    custom = {"UK": (-0.5, 0.2), "ASX": (-0.9, 0.9)}
    df = pd.DataFrame({
        "market": ["UK", "ASX"],
        "security_id": ["uk", "asx"],
        "month": ["2020-01", "2020-01"],
        "discount": [-0.6, -0.6],
        "eligible": [True, True],
    })
    kept, excluded = panel_mod.eligible_only(df, bounds=custom)
    assert list(kept["security_id"]) == ["asx"]
    assert list(excluded["security_id"]) == ["uk"]


# ------------------------------------------------------------- deliverable
def test_workbook_writes_every_sheet(tmp_path):
    rows = []
    for month in ("2019-01", "2019-02"):
        rows += [{"month": month, "market": "UK", "segment": "UK Equity",
                  "sector_canon": "UK All Companies", "security_id": f"s{i}",
                  "discount": -0.10 - 0.01 * i, "market_cap_local": 100.0 + i,
                  "company_name": f"Fund {i}", "sector_raw": "UK All Companies",
                  "share_price": 100.0, "nav_per_share": 111.0,
                  "currency": "GBP", "eligible": True} for i in range(5)]
    elig = pd.DataFrame(rows)
    seg = aggregate.by_segment(elig)
    target = workbook.write(
        tmp_path / "wb.xlsx",
        market_wide=aggregate.market_wide(elig),
        by_market=aggregate.by_market(elig),
        by_segment=seg,
        by_sector=aggregate.by_sector(elig),
        segment_summary=aggregate.segment_summary(seg),
        panel=elig)
    assert target.exists()
    book = pd.read_excel(target, sheet_name=None)
    for sheet in ("Notes", "Market-wide", "By market", "By segment",
                  "By sector", "Segment summary", "Panel"):
        assert sheet in book, sheet
    assert len(book["Panel"]) == 10


def test_latest_is_resolved_per_market_not_globally():
    """Regression: the two markets end on different months. Resolving
    "latest" against one global last month emptied every segment of the
    market that did not reach it (the UK column came out all-NaN)."""
    rows = []
    for month in ("2026-01", "2026-02"):                      # UK ends 2026-02
        rows += [{"month": month, "market": "UK", "segment": "UK Equity",
                  "sector_canon": "UK All Companies", "security_id": f"u{i}",
                  "discount": -0.20} for i in range(4)]
    for month in ("2026-01", "2026-02", "2026-03"):           # ASX runs a month later
        rows += [{"month": month, "market": "ASX", "segment": "Australia Equity",
                  "sector_canon": "Equity - Australia", "security_id": f"a{i}",
                  "discount": -0.05} for i in range(4)]
    seg = aggregate.by_segment(_frame(rows), min_funds=3)
    summary = aggregate.segment_summary(seg).set_index(["market", "segment"])

    uk = summary.loc[("UK", "UK Equity")]
    asx = summary.loc[("ASX", "Australia Equity")]
    assert uk["latest_month"] == "2026-02"
    assert uk["latest_discount"] == pytest.approx(-0.20)   # was NaN before the fix
    assert asx["latest_month"] == "2026-03"
    assert asx["latest_discount"] == pytest.approx(-0.05)
    assert bool(uk["is_current"]) and bool(asx["is_current"])


def test_a_segment_that_stopped_reporting_is_marked_stale():
    """A dead segment keeps an honest 'latest' of its own, but must not be
    presented as a current reading."""
    rows = [{"month": m, "market": "UK", "segment": "Leasing",
             "sector_canon": "Leasing", "security_id": f"d{i}", "discount": -0.30}
            for m in ("2015-01", "2015-02") for i in range(4)]
    rows += [{"month": m, "market": "UK", "segment": "UK Equity",
              "sector_canon": "UK All Companies", "security_id": f"u{i}",
              "discount": -0.10}
             for m in ("2015-01", "2015-02", "2026-01") for i in range(4)]
    seg = aggregate.by_segment(_frame(rows), min_funds=3)
    summary = aggregate.segment_summary(seg).set_index(["market", "segment"])
    dead = summary.loc[("UK", "Leasing")]
    live = summary.loc[("UK", "UK Equity")]
    assert dead["latest_month"] == "2015-02" and not bool(dead["is_current"])
    assert live["latest_month"] == "2026-01" and bool(live["is_current"])


def test_market_wide_works_with_a_single_market():
    """Before the ASX panel starts there is only one market; the pooled
    sheet must still build."""
    rows = [{"month": "2010-01", "market": "UK", "segment": "A", "sector_canon": "A",
             "security_id": f"u{i}", "discount": -0.15} for i in range(5)]
    mw = aggregate.market_wide(_frame(rows), min_funds=3)
    assert len(mw) == 1
    assert mw.iloc[0]["n_markets"] == 1
    assert mw.iloc[0]["markets_included"] == "UK"
    assert mw.iloc[0]["mean_discount_balanced"] == pytest.approx(-0.15)
