import numpy as np
import pandas as pd
import pytest

from uk_cef.signals import (
    add_discount_changes,
    add_discount_zscore,
    add_overshoot_score,
    add_sector_relative,
    compute_discount,
)


def test_discount_sign_convention():
    d = compute_discount(pd.Series([80.0]), pd.Series([100.0]))
    assert d.iloc[0] == pytest.approx(-0.20)
    prem = compute_discount(pd.Series([105.0]), pd.Series([100.0]))
    assert prem.iloc[0] == pytest.approx(0.05)


def test_discount_missing_and_bad_nav():
    d = compute_discount(pd.Series([80.0, np.nan, 50.0]), pd.Series([np.nan, 100.0, -10.0]))
    assert d.isna().all()


def _panel_one_security(discounts, start="2010-01"):
    months = pd.period_range(start, periods=len(discounts), freq="M")
    return pd.DataFrame(
        {
            "date": months.to_timestamp(how="end"),
            "security_id": "X",
            "discount": discounts,
        }
    )


def test_zscore_computation():
    # 35 months at -10%, then one month at -20%
    panel = _panel_one_security([-0.10] * 35 + [-0.20])
    out = add_discount_zscore(panel, window=36, min_history=24)
    z = out["discount_z_36m"].iloc[-1]
    vals = np.array([-0.10] * 35 + [-0.20])
    expect = (vals[-1] - vals.mean()) / vals.std(ddof=1)
    assert z == pytest.approx(expect)
    assert z < -5  # dramatic dislocation vs stable history


def test_zscore_requires_min_history():
    panel = _panel_one_security([-0.10] * 20)
    out = add_discount_zscore(panel, window=36, min_history=24)
    assert out["discount_z_36m"].isna().all()


def test_zscore_no_future_data():
    """Perturbing FUTURE discounts must not change today's z-score."""
    base = [-0.10 + 0.001 * i for i in range(40)]
    p1 = _panel_one_security(base)
    p2 = _panel_one_security(base[:30] + [0.5] * 10)  # different future
    z1 = add_discount_zscore(p1)["discount_z_36m"].iloc[29]
    z2 = add_discount_zscore(p2)["discount_z_36m"].iloc[29]
    assert z1 == pytest.approx(z2)


def test_zscore_calendar_gap_alignment():
    """A 12-month reporting gap must not compress the window."""
    months = list(pd.period_range("2010-01", periods=24, freq="M")) + list(
        pd.period_range("2014-01", periods=24, freq="M")
    )
    panel = pd.DataFrame(
        {
            "date": [m.to_timestamp(how="end") for m in months],
            "security_id": "X",
            "discount": [-0.1] * 24 + [-0.2] * 24,
        }
    )
    out = add_discount_zscore(panel, window=36, min_history=24)
    # first month after the 24m gap: the trailing 36 CALENDAR months
    # (2011-02..2014-01) contain only 11 old + 1 new observations -> < 24
    # -> NaN, NOT a z computed on 24 stale observations
    assert np.isnan(out["discount_z_36m"].iloc[24])


def test_discount_changes():
    panel = _panel_one_security([-0.05, -0.06, -0.07, -0.20])
    out = add_discount_changes(panel, horizons=(1, 3))
    assert out["discount_change_1m"].iloc[-1] == pytest.approx(-0.13)
    assert out["discount_change_3m"].iloc[-1] == pytest.approx(-0.15)
    assert np.isnan(out["discount_change_3m"].iloc[2])


def test_sector_relative_and_overshoot(toy_panel):
    p = add_discount_zscore(toy_panel, window=12, min_history=6, out_col="discount_z_36m")
    p = add_discount_changes(p, horizons=(1, 3))
    p = add_sector_relative(p)
    p = add_overshoot_score(p)
    last = p[p["obs_month"] == "2016-12"]
    med = last[last["sector"] == "Global"]["sector_median_discount"].iloc[0]
    assert med == pytest.approx(last[last["sector"] == "Global"]["discount"].median())
    ok = p.dropna(subset=["overshoot_score"])
    assert not ok.empty
    assert ok["overshoot_score"].between(0, 1).all()


def test_overshoot_requires_all_components(toy_panel):
    p = add_discount_zscore(toy_panel, window=12, min_history=6, out_col="discount_z_36m")
    p = add_discount_changes(p, horizons=(1, 3))
    p.loc[p.index[5], "discount_z_36m"] = np.nan
    p = add_overshoot_score(p)
    assert np.isnan(p.loc[p.index[5], "overshoot_score"])


def test_duplicate_rows_rejected():
    panel = _panel_one_security([-0.1, -0.1])
    dup = pd.concat([panel, panel.tail(1)])
    with pytest.raises(ValueError):
        add_discount_zscore(dup)
