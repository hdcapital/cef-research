"""A NAV that jumps implausibly from the fund's own last NAV must not alert.

Argo Global Listed Infrastructure anchored at $5.00 against a $2.55 price -
a -49% discount and z = -10.2, the single most attractive signal in the
table - while its real NTA series runs $2.31 to $2.75 and our own extractor
had read June 2026 as $2.75 exactly. The unit check could not catch it:
$5.00 against $2.55 is 1.8x, not the 100x a cents/dollars error produces.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cef_live.nta_live import NAV_JUMP_ALERT_LIMIT, nav_continuity  # noqa: E402

T = pd.Timestamp


def test_the_argo_case_is_rejected():
    """The real numbers that produced the false top signal."""
    prior = [(T("2026-06-30"), 2.75), (T("2026-05-31"), 2.71)]
    out = nav_continuity(5.00, T("2026-08-24"), prior)
    assert out["ok"] is False
    assert out["prev"] == 2.75
    assert out["jump"] == pytest.approx(0.818, abs=0.01)
    assert "nav_jump" in out["reason"]


def test_an_ordinary_month_passes():
    """Median consecutive NAV change is 0.54%; nothing normal may be blocked."""
    prior = [(T("2026-06-30"), 2.75)]
    assert nav_continuity(2.77, T("2026-07-31"), prior)["ok"] is True


def test_a_large_but_credible_move_still_passes():
    """A 30% drawdown is a market, not a parse error."""
    prior = [(T("2026-06-30"), 2.75)]
    assert nav_continuity(1.95, T("2026-07-31"), prior)["ok"] is True


def test_no_prior_history_is_not_evidence_of_a_bad_parse():
    out = nav_continuity(5.00, T("2026-08-24"), [])
    assert out["ok"] is True and out["reason"] == "no_prior_nav"


def test_only_observations_strictly_before_the_anchor_count():
    """The anchor must not be compared against itself."""
    prior = [(T("2026-08-24"), 5.00), (T("2026-06-30"), 2.75)]
    out = nav_continuity(5.00, T("2026-08-24"), prior)
    assert out["ok"] is False and out["prev"] == 2.75


def test_the_most_recent_prior_is_the_comparison():
    prior = [(T("2020-01-31"), 0.50), (T("2026-06-30"), 2.75)]
    assert nav_continuity(2.80, T("2026-07-31"), prior)["prev"] == 2.75


def test_bad_values_are_ignored_rather_than_compared():
    prior = [(T("2026-06-30"), 0.0), (T("2026-05-31"), None),
             (T("2026-04-30"), float("nan"))]
    assert nav_continuity(5.00, T("2026-08-24"), prior)["reason"] == "no_prior_nav"


def test_a_missing_anchor_is_not_a_continuity_failure():
    assert nav_continuity(None, T("2026-08-24"), [(T("2026-06-30"), 2.75)])["ok"]


@pytest.mark.parametrize("jump,expect_ok", [
    (NAV_JUMP_ALERT_LIMIT - 0.01, True),
    (NAV_JUMP_ALERT_LIMIT + 0.01, False),
])
def test_the_threshold_is_where_it_is_documented(jump, expect_ok):
    prev = 2.00
    assert nav_continuity(prev * (1 + jump), T("2026-07-31"),
                          [(T("2026-06-30"), prev)])["ok"] is expect_ok


def test_the_comparator_must_not_mix_units():
    """A pounds-vs-pence pair is a unit bug, not a discontinuity.

    The first version of this guard compared the anchor against our own
    extracted history, where normalise() passes an unstated unit through
    untouched while labelling it canonical - so a NAV held in pounds and one
    held in pence both come back tagged GBX. It quarantined 32 funds on that
    artefact (RIT at 3106 against 39, HICL at 158.20 against 1.20) while
    missing ALI entirely. The comparator is now the aggregator panel, which
    is canonical by construction; this pins the arithmetic that made those
    false alarms so obvious in hindsight.
    """
    prior_pounds = [(T("2026-06-30"), 39.00)]     # pounds
    anchor_pence = 3106.0                          # pence: the SAME value
    out = nav_continuity(anchor_pence, T("2026-08-24"), prior_pounds)
    assert out["ok"] is False, "sanity: mixed units do look like a huge jump"
    # ...which is exactly why the caller must only pass canonical-unit priors.


def test_a_stale_two_month_old_comparator_is_still_usable():
    """The panel lags, and that is fine against a 35% threshold."""
    prior = [(T("2026-06-30"), 2.75)]
    assert nav_continuity(5.00, T("2026-08-24"), prior)["ok"] is False
    assert nav_continuity(2.80, T("2026-08-24"), prior)["ok"] is True
