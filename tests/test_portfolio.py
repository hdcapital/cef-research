import numpy as np
import pandas as pd
import pytest

from uk_cef.costs import apply_costs
from uk_cef.portfolio import benchmark_universe, run_strategy


def test_weights_sum_to_one(toy_panel):
    res = run_strategy(toy_panel, "discount", "test", top_fraction=0.5, min_names=2)
    for month, grp in res.holdings.groupby("holding_month"):
        assert grp["weight"].sum() == pytest.approx(1.0)


def test_selects_cheapest(toy_panel):
    res = run_strategy(toy_panel, "discount", "test", top_fraction=0.34, min_names=2)
    first = res.holdings[res.holdings["holding_month"] == res.holdings["holding_month"].min()]
    # S6 (-30%) and S1 (-20%) are the cheapest two of six
    assert set(first["security_id"]) == {"S6", "S1"}


def test_missing_return_not_zero_filled(toy_panel):
    p = toy_panel.copy()
    # knock out one holding's forward return in a known month
    mask = (p["security_id"] == "S6") & (p["obs_month"] == "2015-06")
    p.loc[mask, "fwd_return"] = np.nan
    p.loc[mask, "fwd_return_status"] = "missing_next_price"
    res = run_strategy(p, "discount", "test", top_fraction=0.34, min_names=2)
    row = res.monthly[res.monthly["signal_month"].astype(str) == "2015-06"].iloc[0]
    assert row["n_missing_return"] == 1
    assert row["missing_return_weight"] == pytest.approx(0.5)
    # gross return equals the OBSERVED holding's return, not an average with 0
    s1_ret = p[(p["security_id"] == "S1") & (p["obs_month"] == "2015-06")]["fwd_return"].iloc[0]
    assert row["gross_return"] == pytest.approx(s1_ret)


def test_delisted_not_assigned_minus_100(toy_panel):
    p = toy_panel[~((toy_panel["security_id"] == "S6") & (toy_panel["obs_month"] > "2015-06"))].copy()
    mask = (p["security_id"] == "S6") & (p["obs_month"] == "2015-06")
    p.loc[mask, "fwd_return"] = np.nan
    p.loc[mask, "fwd_return_status"] = "terminal_unresolved"
    res = run_strategy(p, "discount", "test", top_fraction=0.34, min_names=2)
    h = res.holdings[(res.holdings["security_id"] == "S6")]
    final = h[h["holding_month"] == "2015-07"]
    assert not final.empty
    assert final["return_status"].iloc[0] == "terminal_unresolved"
    assert final["next_month_return"].isna().all()
    # and the month's portfolio return is not dragged to -50%
    row = res.monthly[res.monthly["signal_month"].astype(str) == "2015-06"].iloc[0]
    assert row["gross_return"] > -0.5


def test_benchmark_holds_everything(toy_panel):
    res = benchmark_universe(toy_panel)
    first = res.holdings[res.holdings["holding_month"] == res.holdings["holding_month"].min()]
    assert len(first) == 6
    assert first["weight"].sum() == pytest.approx(1.0)


def test_turnover_zero_when_static():
    months = pd.period_range("2020-01", "2020-06", freq="M")
    rows = []
    for sid in ("A", "B"):
        for m in months:
            rows.append(
                {"date": m.to_timestamp(how="end"), "obs_month": str(m),
                 "security_id": sid, "company_name": sid, "sector": "G",
                 "discount": -0.1 if sid == "A" else -0.05,
                 "market_cap": 100.0, "fwd_return": 0.0, "fwd_return_status": "observed"}
            )
    p = pd.DataFrame(rows)
    res = run_strategy(p, "discount", "t", top_fraction=0.5, min_names=1)
    # same single holding every month, zero returns -> no turnover after entry
    assert res.monthly["buy_turnover"].iloc[0] == pytest.approx(1.0)
    assert res.monthly["buy_turnover"].iloc[1:].sum() == pytest.approx(0.0)
    assert res.monthly["sell_turnover"].sum() == pytest.approx(0.0)


def test_quarterly_rebalance_holds_between(toy_panel):
    res = run_strategy(toy_panel, "discount", "q", top_fraction=0.34, min_names=2,
                       rebalance="quarterly")
    rebal_flags = res.monthly.set_index(res.monthly["signal_month"].astype(str))["rebalanced"]
    assert rebal_flags.iloc[0]
    assert not rebal_flags.iloc[1]
    assert not rebal_flags.iloc[2]
    assert rebal_flags.iloc[3]


def test_transaction_costs():
    monthly = pd.DataFrame(
        {"gross_return": [0.02, 0.01], "buy_turnover": [1.0, 0.2], "sell_turnover": [0.0, 0.2]}
    )
    out = apply_costs(monthly, one_way_bps=50, stamp_duty_bps=50, duty_applicable_fraction=0.5)
    # month 1: buys 100% x (50bp + 50bp x 0.5) = 75bp
    assert out["cost"].iloc[0] == pytest.approx(0.0075)
    assert out["net_return"].iloc[0] == pytest.approx(0.02 - 0.0075)
    # month 2: buys 20% x 75bp + sells 20% x 50bp = 0.0015 + 0.001
    assert out["cost"].iloc[1] == pytest.approx(0.2 * 0.0075 + 0.2 * 0.005)


def test_market_cap_filter(toy_panel):
    p = toy_panel.copy()
    p.loc[p["security_id"] == "S6", "market_cap"] = 1.0
    res = run_strategy(p, "discount", "t", top_fraction=0.34, min_names=2, min_market_cap=50)
    assert "S6" not in set(res.holdings["security_id"])
