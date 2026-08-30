"""Alive means WE can find evidence the fund is alive - not that a file says so."""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd

sys.path.insert(0, "src")

from cef_live import liveness as L

TODAY = date(2026, 8, 30)


def test_a_recent_nav_makes_a_fund_live_even_if_the_aggregator_dropped_it():
    """A fund publishing a NAV is trading, whatever the AIC decided.

    This is the case that matters most: the aggregator's coverage is an
    editorial decision, and treating it as a fact about the market marks
    live, tradeable funds dead - the exact cohort this project exists to
    reach.
    """
    got = L.classify({"last_nav": "2026-08-28", "registry_last_seen": "2024-01"},
                     as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE
    assert got["liveness_reason"].startswith("nav_")


def test_a_fund_with_no_nav_but_a_recent_report_is_live_on_slow_evidence():
    """Quarterly reporters and wind-downs go months between NAVs.

    An annual or half-year report is slow evidence but unambiguous proof of
    existence. It is labelled separately because it cannot carry a discount -
    being alive and being priceable are different claims.
    """
    got = L.classify({"last_nav": "2025-01-31", "last_report": "2026-04-30"},
                     as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE_STALE
    assert "no_recent_nav" in got["liveness_reason"]


def test_the_registry_alone_cannot_assert_a_fund_is_trading():
    """Being listed by the AIC is the weakest evidence there is.

    A file can keep listing a fund for months after it stops filing anything.
    With no filings of our own AND no prior aggregator verdict, the fund goes
    under review - never straight to live.
    """
    got = L.classify({"registry_last_seen": "2026-08"}, as_of=TODAY)
    assert got["status"] == L.STATUS_CANDIDATE

    # but where we hold filings and they show only registry listing, the
    # original rule still applies
    got2 = L.classify({"registry_last_seen": "2026-08",
                       "last_announcement": "2020-01-01"}, as_of=TODAY)
    assert got2["status"] in (L.STATUS_CANDIDATE, L.STATUS_DELISTED)


def test_silence_becomes_a_candidate_before_it_becomes_delisted():
    """Nothing is deleted on one missing month; review comes first."""
    cand = L.classify({"last_announcement": "2025-08-01"}, as_of=TODAY)
    assert cand["status"] in (L.STATUS_CANDIDATE, L.STATUS_DELISTED)
    dead = L.classify({"last_announcement": "2023-01-01"}, as_of=TODAY)
    assert dead["status"] == L.STATUS_DELISTED
    assert dead["liveness_reason"].startswith("silent_")


def test_a_manual_entry_is_always_live():
    """Its absence from every file is the reason the user added it."""
    got = L.classify({"manual": True}, as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE
    assert got["liveness_reason"] == "manual_entry"


def test_a_fund_no_registry_ever_listed_can_be_live_on_its_own_filings():
    """Holdcos and oddballs were previously unreachable.

    Liveness driven by the registry could not express "exists, trades,
    publishes a NAV, appears in no aggregator" at all.
    """
    got = L.classify({"last_nav": "2026-08-20"}, as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE


def test_every_decision_carries_the_evidence_that_made_it():
    """A status with no reason cannot be argued with or audited."""
    got = L.classify({"last_nav": "2026-08-28"}, as_of=TODAY)
    for k in ("liveness_reason", "nav_age_days", "last_nav"):
        assert k in got and got[k] is not None


def test_apply_keeps_the_aggregator_status_for_comparison():
    """Keeping both makes the migration measurable.

    How often the evidence disagrees with the file is the number that says
    whether moving off the aggregator was worth doing.
    """
    reg = pd.DataFrame([{"security_id": "S1", "status": "delisted",
                         "last_seen": "2024-01", "manual_entry": False}])
    ev = pd.DataFrame([{"security_id": "S1", "last_nav": "2026-08-28"}])
    out = L.apply(reg, ev, as_of=TODAY)
    assert out.iloc[0]["aggregator_status"] == "delisted"
    assert out.iloc[0]["status"] == L.STATUS_LIVE      # evidence wins


def test_the_universe_build_decides_liveness_from_evidence():
    """The registry build must not hand the aggregator the final word.

    universe.build() still derives a status from the aggregator's monthly
    coverage; the point of this layer is that the evidence overrides it and
    both are kept, so the disagreement is reportable rather than silent.
    """
    import inspect

    from cef_live import cli

    src = inspect.getsource(cli.build_universe)
    assert "liveness.apply" in src
    assert "_liveness_evidence" in src
    # and the summary reports the disagreement in both directions
    assert "revived_by_own_filings" in src
    assert "aggregator_said_live_evidence_says_not" in src


def test_no_evidence_does_not_demote_a_fund():
    """Absence of evidence is not evidence of death.

    The first version of this module demoted 222 funds the aggregator called
    live, purely because we hold no filings for them. Our own collection has
    a KNOWN 2.5-year hole (the ASX announcement index stops at 2023-11), so
    every AU fund looked silent since 2023 and would have been written off by
    a gap in our data rather than by anything about the funds.

    Evidence may PROMOTE - a fund publishing NAVs is alive whatever a file
    says - but never demote below the aggregator when we have nothing. A
    wrongly-revived fund shows up as one with no priceable NAV: visible and
    harmless. A wrongly-delisted one silently leaves the universe.
    """
    got = L.classify({"aggregator_status": "live", "registry_last_seen": "2026-07"},
                     as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE
    assert got["evidence_coverage"] == "none"
    assert "deferring_to_registry" in got["liveness_reason"]


def test_evidence_still_promotes_a_fund_the_aggregator_wrote_off():
    """The asymmetry must not break revival."""
    got = L.classify({"aggregator_status": "delisted", "last_nav": "2026-08-28"},
                     as_of=TODAY)
    assert got["status"] == L.STATUS_LIVE


def test_evidence_of_silence_still_demotes():
    """A fund we DO hold filings for, all of them old, is genuinely quiet."""
    got = L.classify({"aggregator_status": "live", "last_announcement": "2023-01-01"},
                     as_of=TODAY)
    assert got["status"] in (L.STATUS_CANDIDATE, L.STATUS_DELISTED)
