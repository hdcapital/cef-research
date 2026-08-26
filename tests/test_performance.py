import numpy as np
import pandas as pd
import pytest

from uk_cef import performance as perf


def _series(vals, start="2015-01"):
    idx = pd.period_range(start, periods=len(vals), freq="M").to_timestamp(how="end")
    return pd.Series(vals, index=idx)


def test_cagr_known_value():
    # 1% per month for 36 months
    s = _series([0.01] * 36)
    assert perf.cagr(s) == pytest.approx(1.01**12 - 1)


def test_cagr_refuses_short_samples():
    s = _series([0.01] * 12)
    assert np.isnan(perf.cagr(s))


def test_cumulative_return():
    s = _series([0.10, -0.10])
    assert perf.cumulative_return(s) == pytest.approx(1.1 * 0.9 - 1)


def test_max_drawdown():
    s = _series([0.10, -0.30, -0.20, 0.80, 0.05])
    # trough = 1.1*0.7*0.8 = 0.616 vs peak 1.1 -> -44%
    assert perf.max_drawdown(s) == pytest.approx(0.7 * 0.8 - 1)


def test_missing_months_not_zero_filled():
    s = _series([0.01] * 36)
    s2 = s.drop(s.index[10:20])
    # dropping months must change the sample size, not inject zeros
    assert perf.cagr(s2) == pytest.approx(1.01**12 - 1)


def test_alpha_beta_recovers_construction():
    rng = np.random.RandomState(1)
    bench = _series(rng.normal(0.01, 0.03, 120))
    strat = 0.002 + 1.5 * bench + _series(rng.normal(0, 0.001, 120))
    a, b, t_a, n = perf.alpha_beta(strat, bench)
    assert b == pytest.approx(1.5, abs=0.02)
    assert a == pytest.approx(0.002 * 12, abs=0.01)
    assert n == 120


def test_annual_returns_partial_year_labelled():
    s = _series([0.01] * 18)  # 2015 full, 2016 partial (6 months)
    ann = perf.annual_returns(s)
    assert "2015" in ann.index
    assert "2016(6m)" in ann.index
    assert ann["2015"] == pytest.approx(1.01**12 - 1)


def test_sharpe_zero_vol_nan():
    s = _series([0.01] * 30)
    assert np.isnan(perf.sharpe(s))
