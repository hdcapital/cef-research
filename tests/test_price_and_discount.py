"""Price is the other half of a discount, and it has its own ways to lie."""

from __future__ import annotations

import inspect
import sys

import pandas as pd
import pytest

sys.path.insert(0, "src")

from au_lic import prices_history as PH


def test_discount_uses_raw_close_not_adjusted():
    """Adjusted close embeds dividends paid AFTER the date.

    Yahoo's adjclose is retro-adjusted for every later dividend and split, so
    it is not a price anyone could have traded at, and the value CHANGES when
    a new dividend is paid. A discount built on it is contaminated by the
    future - look-ahead arriving through a column name rather than a date
    filter, which is why it would survive every date check in the codebase.
    """
    src = inspect.getsource(PH.build_discount)
    assert "close_raw" in src
    assert "close_adj" not in src, "discount must never be built on adjusted close"


def test_discount_joins_on_publication_not_valuation_date():
    """A 31 March NAV announced 8 April was not knowable on 1 April.

    Joining on valuation_date would hand the backtest up to a week of
    hindsight on every single observation - the largest and least visible
    bias available in this dataset.
    """
    nav = pd.DataFrame([{"ticker": "AAA", "valuation_date": "2021-03-31",
                         "published_at": "2021-04-08", "nav_per_share": 1.00,
                         "nav_basis": "pre_tax", "announcement_id": "1"}])
    px = pd.DataFrame([{"ticker": "AAA", "date": d, "close_raw": 0.80}
                       for d in pd.to_datetime(["2021-04-01", "2021-04-09"])])
    out = PH.build_discount(nav, px)
    before = out[out["date"] == pd.Timestamp("2021-04-01")].iloc[0]
    after = out[out["date"] == pd.Timestamp("2021-04-09")].iloc[0]
    assert pd.isna(before["nav_per_share"]), \
        "a NAV was used on a date before it was published"
    assert after["nav_per_share"] == 1.00
    assert abs(after["discount"] - (-0.20)) < 1e-12


def test_stale_nav_yields_no_discount_rather_than_a_stale_one():
    """A months-old NAV is not evidence of a dislocation."""
    nav = pd.DataFrame([{"ticker": "AAA", "valuation_date": "2020-01-31",
                         "published_at": "2020-02-05", "nav_per_share": 1.00,
                         "nav_basis": "pre_tax", "announcement_id": "1"}])
    px = pd.DataFrame([{"ticker": "AAA", "date": pd.Timestamp("2021-06-01"),
                        "close_raw": 0.80}])
    out = PH.build_discount(nav, px, max_nav_staleness_days=45)
    assert pd.isna(out.iloc[0]["discount"])


def test_coverage_report_names_the_missing_codes():
    """Survivorship must be measured, not assumed.

    The announcement index deliberately includes delisted funds; Yahoo drops
    many of them. If coverage were silently partial, every AU result would be
    computed on survivors and look better than the truth.
    """
    fetched = {"AAA": pd.DataFrame({"date": pd.to_datetime(["2020-01-01"]),
                                    "close_raw": [1.0]}),
               "BBB": None}
    rep = PH.coverage_report(["AAA", "BBB"], fetched)
    assert rep["codes_with_history"] == 1
    assert rep["coverage"] == 0.5
    assert "BBB" in rep["codes_missing"]


def test_raw_and_adjusted_are_separate_columns():
    """Forcing a deliberate choice - there is no ambiguous 'price' column."""
    src = inspect.getsource(PH.fetch_history)
    assert '"close_raw"' in src and '"close_adj"' in src
    assert '"price":' not in src


# ------------------------------------------------------------ delisted funds
def test_recycled_asx_code_cannot_splice_two_companies():
    """ASX codes are reassigned after a delisting.

    A code freed in 2015 is routinely reissued to an unrelated company, and
    Yahoo serves ONE continuous series across both. Splicing a dead LIC to a
    mining company's later prices fabricates a return series with no gap and
    no error - nothing downstream could ever detect it. This is the single
    most dangerous way to "solve" delisted pricing.
    """
    px = pd.DataFrame({"date": pd.date_range("2014-01-01", "2020-12-31", freq="D"),
                       "close_raw": 1.0, "ticker": "XYZ"})
    kept, status = PH.guard_ticker_reuse(px, last_announcement="2015-06-30")
    assert status == "truncated_probable_reuse"
    assert kept["date"].max() <= pd.Timestamp("2015-10-28")
    assert len(kept) < len(px)


def test_a_still_listed_fund_keeps_its_history():
    px = pd.DataFrame({"date": pd.date_range("2024-01-01", "2024-03-01", freq="D"),
                       "close_raw": 1.0, "ticker": "AFI"})
    kept, status = PH.guard_ticker_reuse(px, last_announcement="2024-02-28")
    assert status in ("ok", "truncated") and len(kept) == len(px)


def test_monthly_reports_price_funds_that_no_longer_exist():
    """The point-in-time snapshots are the survivorship-free backbone.

    A fund listed in March 2019 stays in the March 2019 file forever. Cheap
    funds are disproportionately the ones that die, so a panel built only on
    survivors flatters precisely the strategy being tested.
    """
    panel = pd.DataFrame([
        {"security_id": "ASX:DEAD", "obs_month": "2019-03", "share_price": 0.72},
        {"security_id": "ASX:DEAD", "obs_month": "2019-04", "share_price": 0.70},
        {"security_id": "ASX:AFI", "obs_month": "2019-03", "share_price": 6.10},
    ])
    out = PH.prices_from_monthly_reports(panel)
    dead = out[out["ticker"] == "DEAD"]
    assert len(dead) == 2
    assert dead["date"].min() == pd.Timestamp("2019-03-31")
    assert (dead["price_source"] == "asx_monthly_report").all()


def test_buyback_notices_give_traded_prices_from_the_funds_own_filings():
    """Survivorship-free, and densest exactly where discounts are widest."""
    import json

    facts = pd.DataFrame([
        {"ticker": "DEAD", "published_at": "2019-05-02", "family": "buyback_daily",
         "payload": json.dumps({"price": 0.685, "event_type": "buyback_execution"})},
        {"ticker": "DEAD", "published_at": "2019-05-03", "family": "nta",
         "payload": json.dumps({"nav_per_share": 0.9})},
    ])
    out = PH.prices_from_buybacks(facts)
    assert len(out) == 1
    assert out.iloc[0]["close_raw"] == 0.685
    assert out.iloc[0]["price_source"] == "appendix_3e_traded"


def test_assembled_panel_prefers_exchange_close_and_labels_every_row():
    """A finding that survives only on month-end prints is a weaker claim.

    Keeping price_source on every row is what makes that re-checkable
    instead of buried.
    """
    d = pd.Timestamp("2019-03-31")
    yahoo = pd.DataFrame([{"ticker": "AAA", "date": d, "close_raw": 1.01,
                           "price_source": "yahoo:AAA.AX"}])
    monthly = pd.DataFrame([{"ticker": "AAA", "date": d, "close_raw": 1.00,
                             "price_source": "asx_monthly_report"},
                            {"ticker": "DEAD", "date": d, "close_raw": 0.72,
                             "price_source": "asx_monthly_report"}])
    out = PH.assemble_price_panel(yahoo, monthly)
    assert len(out) == 2
    aaa = out[out["ticker"] == "AAA"].iloc[0]
    assert aaa["close_raw"] == 1.01 and aaa["price_source"].startswith("yahoo")
    assert set(out["price_source"]) >= {"asx_monthly_report"}
    assert out["price_source"].notna().all()
