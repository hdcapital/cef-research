"""Invariants for the daily UK discount panel.

Each of these encodes a way the panel could be wrong while looking
completely ordinary - a discount is a ratio of two numbers, so every bug in
it produces a plausible percentage rather than an error.
"""

from __future__ import annotations

import inspect
import sys

import pandas as pd
import pytest

sys.path.insert(0, "src")

from cef_live import uk_discount as DISC
from cef_live import uk_nav_panel as NAV
from cef_live import uk_prices_history as PH


def _px(ticker, dates, close, ccy="GBp"):
    return pd.DataFrame({"ticker": ticker, "date": pd.to_datetime(dates),
                         "close_raw": close, "close_adj": close,
                         "volume": 1, "price_ccy": ccy,
                         "price_source": "test"})


def _nav(ticker, published, asat, pence):
    return pd.DataFrame({"ticker": ticker,
                         "ann_id": [f"a{i}" for i in range(len(published))],
                         "published_at": pd.to_datetime(published),
                         "nav_date": pd.to_datetime(asat),
                         "nav_pence": pence, "nav_ex_pence": None,
                         "cum_assumed": False, "nav_source": "test",
                         "quality": "parsed"})


# ------------------------------------------------------------- look-ahead
def test_discount_joins_on_publication_not_valuation_date():
    """A 30 June NAV announced on 15 July was not knowable on 1 July.

    Joining on the as-at date would hand every row a hindsight window as
    wide as the fund's own reporting lag - for the quarterly infrastructure
    cohort, a fortnight or more on every observation in the panel.
    """
    nav = _nav("AAA", ["2021-07-15"], ["2021-06-30"], [100.0])
    px = _px("AAA", ["2021-07-01", "2021-07-16"], [80.0, 80.0])
    out = DISC.build(nav, px, start="2007-01-01")
    before = out[out["date"] == pd.Timestamp("2021-07-01")].iloc[0]
    after = out[out["date"] == pd.Timestamp("2021-07-16")].iloc[0]
    assert pd.isna(before["discount"]), "a NAV was used before it was published"
    assert after["discount"] == pytest.approx(-0.20)
    # and the valuation date it describes is still carried, unaltered
    assert after["nav_date"] == pd.Timestamp("2021-06-30")


def test_panel_never_built_on_adjusted_close():
    """Yahoo's adjclose is retro-adjusted for dividends paid AFTER the date.

    It is not a price anyone could have traded at, and it CHANGES whenever
    the fund next pays out - so a discount built on it is contaminated by
    the future. UK trusts yield 3-5%, so on a 2007 row the two series differ
    by most of a decade of income. This is look-ahead arriving through a
    column name, which is why it would survive every date check we have.
    """
    src = inspect.getsource(DISC.build)
    assert "close_raw" in src
    assert "close_adj" not in src


def test_zscore_window_is_trailing_not_full_sample():
    """A z-score computed over the whole sample knows the future."""
    src = inspect.getsource(DISC.with_zscores)
    assert ".rolling(" in src and "expanding" not in src


# ------------------------------------------------------------------ units
def test_pounds_series_labelled_gbp_is_rescaled_not_believed():
    """The BSIF trap, from this repo's own probe evidence.

    Yahoo returned BSIF.L at 1.0227 with currency "GBp" for a trust trading
    near 102p. Believing the label reads the price as 1.02p against a ~110p
    NAV: a -99% discount on a healthy fund, indistinguishable in the panel
    from terminal collapse.
    """
    dates = pd.bdate_range("2021-01-01", "2021-12-31")
    nav = _nav("AAA", dates, dates, [100.0] * len(dates))
    px = _px("AAA", dates, [0.90] * len(dates))          # pounds, wrong label
    units = PH.reconcile_units(nav, px)
    row = units.iloc[0]
    assert row["price_scale"] == 100.0
    assert row["price_unit_status"] == "rescaled_x100"
    out = DISC.build(nav, px, units=units)
    assert out["discount"].median() == pytest.approx(-0.10)


def test_a_genuinely_wide_discount_is_never_rescaled():
    """A -60% discount is a market, not a unit error, and must survive.

    The x100 correction is only ever applied to ratios that cluster at 1/100
    or 100. Anything in between - however extreme - is the market's own
    number and is left exactly as it is.
    """
    dates = pd.bdate_range("2021-01-01", "2021-12-31")
    nav = _nav("AAA", dates, dates, [100.0] * len(dates))
    px = _px("AAA", dates, [40.0] * len(dates))
    units = PH.reconcile_units(nav, px)
    assert units.iloc[0]["price_scale"] == 1.0
    out = DISC.build(nav, px, units=units)
    assert out["discount"].median() == pytest.approx(-0.60)


def test_unresolvable_units_yield_no_discount_rather_than_a_wrong_one():
    """A ratio that fits no scale must produce absence, never a guess."""
    dates = pd.bdate_range("2021-01-01", "2021-12-31")
    nav = _nav("AAA", dates, dates, [100.0] * len(dates))
    px = _px("AAA", dates, [1500.0] * len(dates), ccy="XYZ")   # ratio 15
    units = PH.reconcile_units(nav, px)
    assert units.iloc[0]["price_scale"] is None or pd.isna(units.iloc[0]["price_scale"])
    out = DISC.build(nav, px, units=units)
    assert out["discount"].isna().all()
    assert (out["quality"] == "price_unit_unresolved").all()


# ------------------------------------------------------------- staleness
def test_staleness_limit_is_relative_to_each_funds_own_cadence():
    """One fixed threshold cannot serve a daily and a quarterly publisher.

    At 45 days it blanks most of the property and infrastructure cohort's
    history; at 200 it lets a suspended daily publisher look tradable.
    """
    assert DISC.staleness_limit(1.0) == DISC.STALE_FLOOR_DAYS
    assert DISC.staleness_limit(91.0) == pytest.approx(273.0)
    assert DISC.staleness_limit(None) == DISC.STALE_FLOOR_DAYS


def test_quarterly_publisher_keeps_a_discount_a_daily_one_would_lose():
    """The same 40-day-old NAV is normal for one fund and stale for another."""
    # a real quarterly publisher, so its measured cadence is quarterly
    q_pub = pd.to_datetime(["2019-04-15", "2019-07-15", "2019-10-15",
                            "2020-01-15", "2020-04-15", "2020-07-15",
                            "2020-10-15", "2021-01-15"])
    q_nav = _nav("Q", q_pub, q_pub - pd.Timedelta(days=15), [100.0] * len(q_pub))
    d_nav = _nav("D", pd.bdate_range("2020-12-01", "2021-01-15"),
                 pd.bdate_range("2020-12-01", "2021-01-15"),
                 [100.0] * len(pd.bdate_range("2020-12-01", "2021-01-15")))
    nav = pd.concat([q_nav, d_nav], ignore_index=True)
    px = pd.concat([_px("Q", ["2021-02-20"], [80.0]),
                    _px("D", ["2021-02-20"], [80.0])], ignore_index=True)
    freq = NAV.publication_frequency(nav)
    out = DISC.build(nav, px, frequency=freq)
    q = out[out["ticker"] == "Q"].iloc[0]
    d = out[out["ticker"] == "D"].iloc[0]
    assert not bool(q["nav_stale"]) and q["discount_fresh"] == pytest.approx(-0.20)
    assert bool(d["nav_stale"]) and pd.isna(d["discount_fresh"])
    # the market's own convention is still available for both
    assert d["discount"] == pytest.approx(-0.20)


# ------------------------------------------------------------- normalising
def test_unparsed_announcements_are_excluded_not_guessed():
    rows = pd.DataFrame([
        {"ticker": "AAA", "ann_id": "1", "published_at": "2021-01-04",
         "nav_date": "2021-01-04", "nav_pence": None, "nav_ex_pence": None,
         "cum_assumed": False, "nav_source": "s3_archive",
         "quality": "no_nav_parsed"}])
    assert NAV.normalise(rows).empty


def test_impossible_asat_date_is_flagged_and_clamped_not_trusted():
    """An as-at date after the publication date is a parse artefact.

    The observation still stands - the join is on the publication date - but
    the valuation date must not be trusted for staleness, or a mis-read year
    would make a decade-old NAV look published this morning.
    """
    rows = pd.DataFrame([
        {"ticker": "AAA", "ann_id": "1", "published_at": "2021-01-04",
         "nav_date": "2031-01-04", "nav_pence": 100.0, "nav_ex_pence": None,
         "cum_assumed": False, "nav_source": "s3_archive", "quality": "parsed"}])
    out = NAV.normalise(rows)
    assert len(out) == 1
    assert out.iloc[0]["quality"] == "asat_after_publication"
    assert out.iloc[0]["nav_date"] == pd.Timestamp("2021-01-04")


def test_nav_outside_the_plausible_pence_band_is_dropped():
    """A mis-parsed order of magnitude reads exactly like a dislocation."""
    rows = pd.DataFrame([
        {"ticker": "AAA", "ann_id": "1", "published_at": "2021-01-04",
         "nav_date": "2021-01-04", "nav_pence": 0.0, "nav_ex_pence": None,
         "cum_assumed": False, "nav_source": "s3_archive", "quality": "parsed"},
        {"ticker": "AAA", "ann_id": "2", "published_at": "2021-01-05",
         "nav_date": "2021-01-05", "nav_pence": 1e9, "nav_ex_pence": None,
         "cum_assumed": False, "nav_source": "s3_archive", "quality": "parsed"}])
    assert NAV.normalise(rows).empty


def test_published_archive_row_outranks_a_snapshot_of_the_same_day():
    """Two stores can hold the same day; the fund's own text is the source."""
    rows = pd.DataFrame([
        {"ticker": "AAA", "ann_id": "snap:1", "published_at": "2021-01-04",
         "nav_date": "2021-01-04", "nav_pence": 99.0, "nav_ex_pence": None,
         "cum_assumed": True, "nav_source": "s3_snapshot", "quality": "parsed"},
        {"ticker": "AAA", "ann_id": "1", "published_at": "2021-01-04",
         "nav_date": "2021-01-04", "nav_pence": 100.0, "nav_ex_pence": None,
         "cum_assumed": False, "nav_source": "s3_archive", "quality": "parsed"}])
    out = NAV.normalise(rows)
    assert len(out) == 1 and out.iloc[0]["nav_pence"] == 100.0


# ---------------------------------------------------------------- universe
def test_a_zdp_line_never_inherits_its_ordinarys_nav():
    """The parser excludes ZDP entitlements, so the NAV harvested under a
    shared ticker is the ordinary share's. Giving it to both lines would
    state the same number about two different claims on the same company."""
    src = inspect.getsource(NAV.live_universe)
    assert "zdp|zero dividend|preference" in src
    assert "drop_duplicates" in src


def test_price_top_up_never_discards_history_it_did_not_refetch():
    """A daily run fetches a short tail; the decade before it must survive."""
    old = _px("AAA", pd.bdate_range("2010-01-01", "2010-12-31"), 100.0)
    new = _px("AAA", pd.bdate_range("2026-08-01", "2026-08-28"), 120.0)
    merged = PH.merge_prices(old, new)
    assert merged["date"].min() == old["date"].min()
    assert len(merged) == len(old) + len(new)


def test_refetched_bar_supersedes_the_held_one():
    """Yahoo revises the latest bar once it settles."""
    old = _px("AAA", ["2026-08-28"], [100.0])
    new = _px("AAA", ["2026-08-28"], [101.5])
    merged = PH.merge_prices(old, new)
    assert len(merged) == 1 and merged.iloc[0]["close_raw"] == 101.5


def test_a_fund_that_trades_without_a_nav_keeps_its_rows():
    """Coverage is measured. A fund with no readable NAV is a gap we report,
    not a fund we pretend was never listed."""
    nav = _nav("AAA", ["2021-01-04"], ["2021-01-04"], [100.0])
    px = pd.concat([_px("AAA", ["2021-01-05"], [90.0]),
                    _px("BBB", ["2021-01-05"], [90.0])], ignore_index=True)
    out = DISC.build(nav, px)
    b = out[out["ticker"] == "BBB"]
    assert len(b) == 1
    assert pd.isna(b.iloc[0]["discount"])
    assert b.iloc[0]["quality"] == "no_nav_published_yet"


# ------------------------------------------------- unit invariant, UK table
def test_uk_own_nav_history_is_pence_like_every_other_uk_figure():
    """NAV and the price it is divided by must share a unit.

    Every UK figure in the live table is pence - the AIC panel's NAV column,
    the tier 0 harvest, and the price. `_own_nav_history` divided its UK NAV
    by 100, putting one anchor source in pounds. The 20 funds anchored that
    way carried discount_est between +79 and +5649 in the committed table:
    premiums of 7,990% to 564,900%, obvious in isolation and easy to miss in
    a 641-row file. AU is dollars on both sides and must not be touched.
    """
    from cef_live import cli
    src = inspect.getsource(cli._own_nav_history)
    uk_half = src.split("for f in sorted(Path(\"data/asx_extract\")")[0]
    assert "nav_cum_pence" in uk_half
    assert "/ 100.0" not in uk_half, \
        "UK NAV must stay in pence - the price it is compared against is"


def test_snapshot_navs_are_read_as_pence_not_converted():
    """The mirror-image mistake, in the reader rather than the writer.

    The nightly snapshots are the ONLY NAV history the announcements_only
    cohort has, so a x100 there would be wrong in exactly the place where no
    other source exists to contradict it.
    """
    src = inspect.getsource(NAV.extract_from_snapshots)
    assert "* 100.0" not in src
