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
