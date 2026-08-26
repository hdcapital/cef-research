import numpy as np
import pandas as pd
import pytest

from uk_cef.deciles import bucket_summary, monthly_bucket_returns
from uk_cef.panel import _detect_splits
from uk_cef.validation import check_no_lookahead


def _g(months, prices, shares):
    return pd.DataFrame(
        {
            "security_id": "X",
            "obs_month": months,
            "price": prices,
            "shares": shares,
        }
    )


def test_plain_price_return():
    g = _g(["2020-01", "2020-02"], [100.0, 110.0], [1e6, 1e6])
    out = _detect_splits(g)
    r = out[out["obs_month"] == "2020-02"]["price_return"].iloc[0]
    assert r == pytest.approx(0.10)


def test_split_adjustment():
    # 5-for-1 split: shares x5, price /5 -> economic return ~0, not -80%
    g = _g(["2020-01", "2020-02"], [500.0, 101.0], [1e6, 5e6])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2020-02"].iloc[0]
    assert row["split_adjusted"]
    assert row["price_return"] == pytest.approx(0.01, abs=1e-6)


def test_consolidation_adjustment():
    # 1-for-10 consolidation: shares /10, price x10
    g = _g(["2020-01", "2020-02"], [50.0, 495.0], [10e6, 1e6])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2020-02"].iloc[0]
    assert row["split_adjusted"]
    assert row["price_return"] == pytest.approx(-0.01, abs=1e-6)


def test_buyback_not_treated_as_split():
    # 10% buyback with real -20% price fall must NOT be 'adjusted' away
    g = _g(["2020-01", "2020-02"], [100.0, 80.0], [1e6, 0.9e6])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2020-02"].iloc[0]
    assert not row["split_adjusted"]
    assert row["price_return"] == pytest.approx(-0.20)


def test_gap_produces_no_return():
    g = _g(["2020-01", "2020-04"], [100.0, 130.0], [1e6, 1e6])
    out = _detect_splits(g)
    # 2020-04 has no 2020-03 price -> no return observation for 04, and the
    # 30% move is never booked as a one-month return
    apr = out[out["obs_month"] == "2020-04"]
    assert apr.empty or apr["price_return"].isna().all()


def test_check_no_lookahead_flags_bad_rows(toy_panel):
    assert check_no_lookahead(toy_panel) == []
    bad = toy_panel.copy()
    bad.loc[bad.index[0], "fwd_return_month"] = bad.loc[bad.index[0], "obs_month"]
    problems = check_no_lookahead(bad)
    assert problems and "fwd_return_month" in problems[0]


def test_decile_monotone_recovery():
    """A constructed cross-section where cheap outperforms must produce
    monotone bucket averages with bucket 1 highest."""
    rng = np.random.RandomState(0)
    rows = []
    for m in pd.period_range("2015-01", "2019-12", freq="M"):
        for i in range(40):
            disc = -0.40 + i * 0.01
            rows.append(
                {"date": m.to_timestamp(how="end"), "obs_month": str(m),
                 "security_id": f"S{i}", "discount": disc,
                 "fwd_return": -disc * 0.05 + rng.normal(0, 0.001)}
            )
    panel = pd.DataFrame(rows)
    br = monthly_bucket_returns(panel, "discount", n_buckets=10, min_names=20)
    s = bucket_summary(br, 10)
    numeric = s[s["bucket"].astype(str).str.isdigit()].sort_values("bucket")
    avg = numeric["avg_fwd_return_monthly"].values
    assert avg[0] > avg[-1]
    assert (np.diff(avg) < 0).all()
    ls = s[~s["bucket"].astype(str).str.isdigit()].iloc[0]
    assert ls["avg_fwd_return_monthly"] > 0
    assert ls["t_stat"] > 10


def test_decile_missing_returns_excluded():
    rows = []
    for i in range(40):
        rows.append(
            {"date": pd.Timestamp("2015-01-31"), "obs_month": "2015-01",
             "security_id": f"S{i}", "discount": -0.4 + i * 0.01,
             "fwd_return": np.nan if i < 4 else 0.01}
        )
    br = monthly_bucket_returns(pd.DataFrame(rows), "discount", n_buckets=10, min_names=20)
    b1 = br[br["bucket"] == 1].iloc[0]
    assert b1["n_members"] == 4
    assert b1["n_with_return"] == 0
    assert np.isnan(b1["fwd_return"])  # not silently 0


def _g_nav(months, prices, shares, navs):
    df = _g(months, prices, shares)
    df["nav"] = navs
    return df


def test_unit_switch_adjusted_not_dropped():
    # pence -> pounds reporting switch: price and NAV both /100; true
    # return recovered (~ -0.4%), not -99% and not discarded
    g = _g_nav(["2014-09", "2014-10"], [37400.0, 372.5], [2e5, 2e5], [33569.06, 334.62])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2014-10"].iloc[0]
    assert row["split_adjusted"]
    assert row["price_return"] == pytest.approx(372.5 * 100 / 37400 - 1, abs=1e-9)


def test_price_nav_inconsistent_invalidated():
    # x10 decimal error in one month's price: +878% "return" with stable NAV
    g = _g_nav(["2007-03", "2007-04"], [23.5, 230.0], [1e6, 1e6], [276.55, 296.70])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2007-04"].iloc[0]
    assert pd.isna(row["price_return"])
    assert row["return_invalid_reason"] == "price_nav_inconsistent"


def test_real_capital_distribution_kept():
    # genuine -75% price with matching -72% NAV (capital returned): kept
    g = _g_nav(["2024-05", "2024-06"], [34.5, 8.5], [4.5e7, 4.5e7], [40.02, 11.36])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2024-06"].iloc[0]
    assert row["price_return"] == pytest.approx(8.5 / 34.5 - 1)
    assert pd.isna(row["return_invalid_reason"])


def test_extreme_unverified_no_nav():
    g = _g_nav(["2009-04", "2009-05"], [0.38, 1.76], [5e7, 5e7], [np.nan, np.nan])
    out = _detect_splits(g)
    row = out[out["obs_month"] == "2009-05"].iloc[0]
    assert pd.isna(row["price_return"])
    assert row["return_invalid_reason"] == "extreme_unverified"


def test_bitemporal_coalesce_field_level():
    """A later errata row that only carries the corrected field must not
    wipe the original price/NAV (regression for the 2012-05 universe hole)."""
    from uk_cef.panel import _dedupe_bitemporal

    rows = pd.DataFrame(
        [
            {"security_id": "X", "obs_month": "2012-05", "release_month": "2012-05",
             "file_kind": "main", "price": 100.0, "nav": 120.0, "shares": 1e6,
             "dividend_yield": 3.0},
            # errata republishes the row with ONLY gearing-style fields
            {"security_id": "X", "obs_month": "2012-05", "release_month": "2012-06",
             "file_kind": "post_errata", "price": np.nan, "nav": np.nan, "shares": np.nan,
             "dividend_yield": 3.5},
        ]
    )
    out = _dedupe_bitemporal(rows)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["price"] == 100.0 and r["nav"] == 120.0
    assert r["dividend_yield"] == 3.5           # corrected field DOES update
    assert r["first_release_month"] == "2012-05"


def test_bitemporal_correction_overrides():
    from uk_cef.panel import _dedupe_bitemporal

    rows = pd.DataFrame(
        [
            {"security_id": "X", "obs_month": "2020-01", "release_month": "2020-01",
             "file_kind": "main", "price": 100.0, "nav": 120.0},
            {"security_id": "X", "obs_month": "2020-01", "release_month": "2020-01",
             "file_kind": "errata", "price": 105.0, "nav": np.nan},
        ]
    )
    out = _dedupe_bitemporal(rows)
    assert out.iloc[0]["price"] == 105.0
    assert out.iloc[0]["nav"] == 120.0


def test_attach_total_returns(tmp_path):
    from uk_cef.panel import _attach_total_returns

    months = ["2020-01", "2020-02", "2020-03"]
    panel = pd.DataFrame(
        {
            "security_id": ["X"] * 3,
            "obs_month": months,
            "share_price": [100.0, 102.0, 99.0],
            "price_return": [np.nan, 0.02, -0.0294117647],
            "dividend_yield": [4.0, 4.0, 4.0],
        }
    )
    div = pd.DataFrame(
        [{"security_id": "X", "confidence": "high", "amount_gbx": 2.0,
          "ex_date": "2020-02-14", "pay_date": "2020-03-01"}]
    )
    div.to_parquet(tmp_path / "dividends.parquet")
    cfg = {"paths": {"outputs_dir": str(tmp_path)}}
    out = _attach_total_returns(panel, tmp_path, cfg)
    feb = out[out.obs_month == "2020-02"].iloc[0]
    # total return Feb = price return + 2p / 100p
    assert feb["total_return"] == pytest.approx(0.02 + 0.02)
    jan = out[out.obs_month == "2020-01"].iloc[0]
    assert jan["fwd_total_return"] == pytest.approx(0.04)
    # coverage: parsed 2p on ~100p = 2% vs published 4% -> exactly 50% -> ok
    assert bool(feb["dividend_coverage_ok"])


def test_total_return_missing_dividends_flagged_not_zeroed(tmp_path):
    from uk_cef.panel import _attach_total_returns

    panel = pd.DataFrame(
        {
            "security_id": ["Y"] * 2,
            "obs_month": ["2020-01", "2020-02"],
            "share_price": [100.0, 101.0],
            "price_return": [np.nan, 0.01],
            "dividend_yield": [5.0, 5.0],  # pays 5% but we parsed nothing
        }
    )
    div = pd.DataFrame(
        [{"security_id": "OTHER", "confidence": "high", "amount_gbx": 1.0,
          "ex_date": "2020-02-14", "pay_date": None}]
    )
    div.to_parquet(tmp_path / "dividends.parquet")
    cfg = {"paths": {"outputs_dir": str(tmp_path)}}
    out = _attach_total_returns(panel, tmp_path, cfg)
    # TR mechanically equals price return, but coverage flags it unusable
    assert not out["dividend_coverage_ok"].any()


def test_total_return_rejects_impossible_dates(tmp_path):
    from uk_cef.panel import _attach_total_returns

    panel = pd.DataFrame(
        {
            "security_id": ["Z"] * 2,
            "obs_month": ["2015-01", "2015-02"],
            "share_price": [100.0, 100.0],
            "price_return": [np.nan, 0.0],
            "dividend_yield": [np.nan, np.nan],
        }
    )
    # pay date BEFORE the announcement (mis-grabbed "year ended" text):
    # must be discarded, leaving ex-date attach only
    div = pd.DataFrame(
        [{"security_id": "Z", "confidence": "high", "amount_gbx": 2.6,
          "date": "2015-01-15", "ex_date": "2015-01-22", "pay_date": "2014-12-31"}]
    )
    div.to_parquet(tmp_path / "dividends.parquet")
    cfg = {"paths": {"outputs_dir": str(tmp_path)}}
    out = _attach_total_returns(panel, tmp_path, cfg)
    jan = out[out.obs_month == "2015-01"].iloc[0]
    assert jan["dividend_gbx_month"] == pytest.approx(2.6)  # ex-date month kept
    feb = out[out.obs_month == "2015-02"].iloc[0]
    assert pd.isna(feb["dividend_gbx_month"]) or feb["dividend_gbx_month"] == 0


def test_nav_total_return_and_rolling_cagr(tmp_path):
    """1% NAV growth + 2p dividend every month on NAV 100 base: NAV TR
    ~1.02%/mo... build 70 months and check the 5y CAGR math."""
    from uk_cef.panel import _attach_nav_returns

    months = [str(p) for p in pd.period_range("2015-01", periods=70, freq="M")]
    nav = [100.0 * (1.01 ** i) for i in range(70)]
    rows = []
    for i, m in enumerate(months):
        rows.append({
            "security_id": "Q", "obs_month": m, "nav": nav[i],
            "share_price": nav[i] * 0.9, "shares": 1e6,
            "dividend_gbx_month": 0.5 if i > 0 else np.nan,
            "dividend_coverage_ok": True, "eligible": True,
            "company_name": "Q Trust",
        })
    panel = pd.DataFrame(rows)
    out = _attach_nav_returns(panel, tmp_path)
    last = out.iloc[-1]
    # monthly nav_tr = 1.01 + 0.5/prev_nav - 1; for a rough check use the
    # first full month: 0.01 + 0.5/100
    second = out.iloc[1]
    assert second["nav_total_return"] == pytest.approx(0.01 + 0.5 / 100.0, rel=1e-6)
    assert not np.isnan(last["nav_tr_cagr_5y"])
    # 5y CAGR must exceed the ex-div 1%/mo compounding (12.68%) due to divs
    assert last["nav_tr_cagr_5y"] > 1.01**12 - 1
    assert np.isnan(last["nav_tr_cagr_10y"])  # only 70 months of history
    assert (tmp_path / "nav_cagr_rolling.csv").exists()


def test_nav_cagr_requires_dividend_coverage(tmp_path):
    from uk_cef.panel import _attach_nav_returns

    months = [str(p) for p in pd.period_range("2015-01", periods=70, freq="M")]
    rows = []
    for i, m in enumerate(months):
        rows.append({
            "security_id": "R", "obs_month": m, "nav": 100.0 + i,
            "share_price": 90.0, "shares": 1e6,
            "dividend_gbx_month": np.nan,
            "dividend_coverage_ok": False,  # a payer with no parsed divs
            "eligible": True, "company_name": "R Trust",
        })
    out = _attach_nav_returns(pd.DataFrame(rows), tmp_path)
    # CAGR masked rather than silently computed ex-dividend
    assert out["nav_tr_cagr_5y"].isna().all()
