"""Widening attribution (Phase 3a, layer 1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cef_live import attribution as AT


def _hist(prices, navs, start="2026-08-01"):
    idx = pd.bdate_range(start, periods=len(prices)).strftime("%Y-%m-%d")
    p, n = np.array(prices, float), np.array(navs, float)
    return pd.DataFrame({"price": p, "nta_est": n, "discount_est": p / n - 1.0}, index=idx)


def test_legs_sum_exactly_to_the_discount_change():
    h = _hist([100, 99, 97, 95, 94, 92, 90, 88], [110, 110, 111, 112, 112, 113, 113, 114])
    d = AT.decompose(h, lookback=21)
    assert d["window_days"] == 7
    assert d["price_leg"] + d["nav_leg"] == pytest.approx(d["delta_d"], abs=1e-12)
    assert d["price_ret"] == pytest.approx(-0.12)
    assert d["driver"] == "price-led"


def test_nav_led_widening_when_the_price_stands_still():
    h = _hist([100] * 8, [110, 111, 112, 114, 116, 118, 120, 122])
    d = AT.decompose(h)
    assert d["driver"] == "NAV-led" and d["delta_d"] < 0


def test_series_break_is_named_not_attributed():
    h = _hist([100, 100, 100, 100, 100, 100], [1.1, 1.1, 1.1, 110, 110, 110])
    d = AT.decompose(h)
    assert d["driver"] == "series break - verify units"


def test_too_short_a_history_gives_no_decomposition():
    assert AT.decompose(_hist([100, 99, 98], [110, 110, 110])) is None


def test_sector_scope_from_peer_medians():
    fund = _hist([100, 98, 96, 94, 92, 90], [100] * 6)
    peers = {f"P{i}": _hist([100, 99, 98, 97, 96, 95], [100] * 6) for i in range(5)}
    hist = {"F": fund, **peers}
    a = AT.attribute("F", "UK", "Growth", hist, list(peers))
    assert a["sector_n"] == 5
    assert a["sector_delta"] == pytest.approx(-0.05)
    assert a["scope"] == "partly sector"   # -5pt of the fund's -10pt
    assert "sector median -5.0pt (n=5)" in a["why"]
    idio = AT.attribute("F", "UK", "Growth",
                        {"F": fund, **{k: _hist([100] * 6, [100] * 6) for k in peers}},
                        list(peers))
    assert idio["scope"] == "idiosyncratic"


def test_too_few_peers_is_said_not_guessed():
    fund = _hist([100, 98, 96, 94, 92, 90], [100] * 6)
    a = AT.attribute("F", "UK", "Growth", {"F": fund, "P1": fund}, ["P1"])
    assert a["sector_delta"] is None and a["scope"] == "unknown"
    assert "too few peers" in a["why"]


def test_volume_ratio_needs_a_base():
    bars = pd.DataFrame({"volume": [1000.0] * 60 + [3000.0] * 5})
    assert AT.volume_ratio(bars) == pytest.approx(3.0)
    assert AT.volume_ratio(pd.DataFrame({"volume": [1.0] * 10})) is None
    assert AT.volume_ratio(None) is None


def test_no_history_line_names_the_gap():
    a = AT.attribute("F", "AU", "Equity", {}, [])
    assert a["window_days"] is None
    assert a["why"].startswith("no attribution: 0 day(s)")


def test_snapshot_is_one_row_per_fund_per_day(tmp_path):
    p = tmp_path / "live.parquet"
    live = pd.DataFrame({"security_id": ["A", "B"], "market": ["UK", "AU"],
                         "sector": ["x", "y"], "price": [100.0, 2.0],
                         "nta_est": [110.0, 2.5], "nav_anchor": [109.0, 2.4],
                         "discount_est": [-0.09, -0.2], "z_adj": [-1.0, -2.5],
                         "nav_current": [True, True]})
    AT.snapshot(live, p, asof="2026-09-01")
    live2 = live.assign(price=[101.0, 2.1])
    AT.snapshot(live2, p, asof="2026-09-01")     # re-run replaces the day
    AT.snapshot(live2, p, asof="2026-09-02")
    st = AT.load_store(p)
    assert len(st) == 4 and st["date"].nunique() == 2
    assert float(st[(st.date == "2026-09-01") & (st.security_id == "A")]["price"].iloc[0]) == 101.0
    h = AT.fund_history(st, "A")
    assert list(h.index) == ["2026-09-01", "2026-09-02"]


def test_attribute_all_merges_store_and_deeper_uk_panel():
    days = 8
    store = pd.concat([
        pd.DataFrame({"date": pd.bdate_range("2026-08-20", periods=days).strftime("%Y-%m-%d"),
                      "security_id": sid, "market": "UK", "sector": "Growth",
                      "price": np.linspace(100, 90, days), "nta_est": 100.0,
                      "nav_anchor": 100.0, "discount_est": np.linspace(100, 90, days) / 100 - 1,
                      "z_adj": -2.0, "nav_current": True})
        for sid in ("F", "P1", "P2", "P3", "P4")], ignore_index=True)
    live = pd.DataFrame({"security_id": ["F", "P1", "P2", "P3", "P4"], "market": "UK",
                         "sector": "Growth"})
    verdicts = pd.DataFrame({"security_id": ["F"], "verdict": ["WATCH"]})
    deep = _hist(list(np.linspace(100, 80, 30)), [100] * 30, start="2026-08-10")
    att = AT.attribute_all(verdicts, live, store, None, None, {"F": deep}, lookback=21)
    assert att.loc[0, "window_days"] == 21          # the deeper panel history won
    assert att.loc[0, "scope"] in ("sector-wide", "partly sector")
    assert att.loc[0, "why"].startswith("Discount widened")
