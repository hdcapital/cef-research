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
