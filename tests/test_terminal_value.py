"""A delisting is not a -100% return, and it is not a 0% return either."""

from __future__ import annotations

import sys

import pandas as pd
import pytest

sys.path.insert(0, "src")

from au_lic import terminal as T


@pytest.mark.parametrize("text,expected", [
    ("Shareholders will receive cash consideration of $1.45 per share under the Scheme.", 1.45),
    ("The liquidator has declared an initial distribution of 62.5 cents per share.", 0.625),
    ("Offer price of 87.5 cents cash per share, declared unconditional.", 0.875),
    ("A final distribution of $0.0325 per unit will be paid.", 0.0325),
    ("Return of capital of 12 cents per share.", 0.12),
])
def test_decimal_amounts_survive_the_label_gap(text, expected):
    """Both ways of writing an amount must parse to the same number.

    Two bugs lived here, and both understated the value - the direction that
    makes a wound-up fund look like a disaster and the cheap cohort look
    worse than it was. A [^.$] gap blocked decimal points as well as sentence
    ends, and a greedy gap ate "62." leaving "5 cents". "62.5 cents" became
    $0.05, a 12x understatement that would still look like a plausible price.
    """
    got = T.extract_terminal(text)
    assert got is not None
    assert abs(got["value_per_share"] - expected) < 1e-9


def test_scrip_is_flagged_rather_than_valued_from_a_later_price():
    """Valuing scrip needs the acquirer's price - a LATER observation.

    Looking that up would import the future into a point-in-time record. So
    scrip is valued only when the document itself states an implied value,
    and otherwise carries a flag saying what is still needed.
    """
    got = T.extract_terminal("Shareholders will receive 1.2 ABC shares for each share held.")
    assert got["exit_type"] == "scheme_scrip"
    assert got["value_per_share"] is None
    assert got["requires_acquirer_price"] is True

    stated = T.extract_terminal(
        "Shareholders will receive 1.2 ABC shares for each share held, "
        "with an implied value of $2.10")
    assert stated["value_per_share"] == 2.10
    assert stated["basis"] == "stated_implied_value"


def test_wind_up_instalments_keep_their_dates():
    """Three payments over 18 months is not one lump on the delisting date.

    Only the dated form can be discounted properly, and wind-ups almost
    always pay in instalments.
    """
    ev = pd.DataFrame([
        {"ticker": "DEAD", "published_at": "2019-06-30", "exit_type": "wind_up",
         "value_per_share": 0.60, "basis": "stated_distribution"},
        {"ticker": "DEAD", "published_at": "2020-03-31", "exit_type": "wind_up",
         "value_per_share": 0.15, "basis": "stated_distribution"},
        {"ticker": "DEAD", "published_at": "2020-11-30", "exit_type": "wind_up",
         "value_per_share": 0.04, "basis": "stated_distribution"},
    ])
    out = T.build_terminal_values(ev).iloc[0]
    assert abs(out["terminal_value_per_share"] - 0.79) < 1e-9
    assert len(out["payments"]) == 3
    assert out["payments"][0]["date"] == "2019-06-30"
    assert out["payments"][-1]["amount"] == 0.04


def test_an_unrecoverable_terminal_value_is_excluded_never_zeroed():
    """The rule this whole module exists for.

    -100% punishes the wide-discount cohort hardest, because those are the
    funds that get wound up or bid for - so the error points the same way as
    the hypothesis and hides behind a plausible result. 0% silently deletes
    the payoff a discount-narrowing thesis predicts. Missing stays missing.
    """
    ev = pd.DataFrame([{"ticker": "GHOST", "published_at": "2018-01-01",
                        "exit_type": "none", "value_per_share": None,
                        "basis": None}])
    out = T.apply_to_returns(T.build_terminal_values(ev)).iloc[0]
    assert out["status"] == "unrecoverable"
    assert out["terminal_value_per_share"] is None or pd.isna(out["terminal_value_per_share"])
    assert out["usable_for_returns"] is False or out["usable_for_returns"] == False  # noqa: E712
    assert out["exclusion_reason"] == "unrecoverable"


def test_scheme_cash_beats_an_earlier_distribution():
    """The decisive consideration is the terminal value, not an interim payment."""
    ev = pd.DataFrame([
        {"ticker": "AAA", "published_at": "2020-01-01", "exit_type": "wind_up",
         "value_per_share": 0.10, "basis": "stated_distribution"},
        {"ticker": "AAA", "published_at": "2020-06-01", "exit_type": "scheme_cash",
         "value_per_share": 1.30, "basis": "stated_scheme_consideration"},
    ])
    out = T.build_terminal_values(ev).iloc[0]
    assert out["exit_type"] == "scheme_cash"
    assert out["terminal_value_per_share"] == 1.30


def test_final_nta_proxy_is_labelled_so_results_can_be_re_run_without_it():
    """A proxy must never be indistinguishable from a stated fact."""
    ev = pd.DataFrame([{"ticker": "GHOST", "published_at": "2018-01-01",
                        "exit_type": "none", "value_per_share": None, "basis": None}])
    nta = pd.DataFrame([{"ticker": "GHOST", "nav_per_share": 0.91}])
    out = T.apply_to_returns(T.build_terminal_values(ev, final_nta=nta)).iloc[0]
    assert out["status"] == "proxy_final_nta"
    assert out["basis"] == "final_published_nta_proxy"
    assert out["terminal_value_per_share"] == 0.91
    assert bool(out["usable_for_returns"]) is True
