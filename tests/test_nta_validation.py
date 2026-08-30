"""Cross-check against the exchange's own NTA - the only check that can catch
a value that is WRONG rather than missing."""

from __future__ import annotations

import sys

import pandas as pd

sys.path.insert(0, "src")

from au_lic import validate_nta as V


def _ex(rows):
    return pd.DataFrame([{"ticker": t, "valuation_date": d, "nav_per_share": v}
                         for t, d, v in rows])


def _panel(rows):
    return pd.DataFrame([{"security_id": f"ASX:{t}", "obs_month": m, "nta_price": v}
                         for t, m, v in rows])


def test_matching_values_agree():
    c = V.classify(V.compare(_ex([("AFI", "2021-03-31", 6.1234)]),
                             _panel([("AFI", "2021-03", 6.1200)])))
    assert c.iloc[0]["verdict"] == "match"


def test_cents_read_as_dollars_is_caught():
    """A 100x unit error looks entirely plausible in isolation.

    $612.34 against a $6.10 share price is a -99% discount - it would sail to
    the top of every "cheap" screen and be the single most attractive
    opportunity in the dataset. Nothing except an independent source can
    catch it, because the number itself is well-formed.
    """
    c = V.classify(V.compare(_ex([("AFI", "2021-03-31", 612.34)]),
                             _panel([("AFI", "2021-03", 6.12)])))
    assert c.iloc[0]["verdict"] == "unit_error"
    # and the reverse direction
    c2 = V.classify(V.compare(_ex([("AFI", "2021-03-31", 0.0612)]),
                              _panel([("AFI", "2021-03", 6.12)])))
    assert c2.iloc[0]["verdict"] == "unit_error"


def test_a_consistent_offset_is_a_basis_difference_not_an_error():
    """The exchange may publish post-tax where the fund announces pre-tax.

    A stable per-fund ratio across many months is two measures of different
    things, not a parse failure - and "correcting" one to match the other
    would destroy the evidence that they differ.
    """
    ex = _ex([("XYZ", "2021-01-31", 1.10), ("XYZ", "2021-02-28", 1.12),
              ("XYZ", "2021-03-31", 1.14), ("XYZ", "2021-04-30", 1.16)])
    pan = _panel([("XYZ", "2021-01", 1.034), ("XYZ", "2021-02", 1.053),
                  ("XYZ", "2021-03", 1.072), ("XYZ", "2021-04", 1.090)])
    c = V.classify(V.compare(ex, pan))
    assert (c["verdict"] == "basis_gap").all()


def test_a_one_off_disagreement_stays_a_mismatch():
    """One odd month is not a basis difference; it needs looking at."""
    c = V.classify(V.compare(_ex([("XYZ", "2021-03-31", 2.50)]),
                             _panel([("XYZ", "2021-03", 1.07)])))
    assert c.iloc[0]["verdict"] == "mismatch"


def test_comparison_joins_on_valuation_month_not_publication():
    """A 31 March NTA announced on 8 April is the exchange's March figure.

    Joining on publication month would compare March against April and
    manufacture disagreement everywhere - the check would fail loudly while
    the extractor was correct.
    """
    ex = pd.DataFrame([{"ticker": "AFI", "valuation_date": "2021-03-31",
                        "published_at": "2021-04-08", "nav_per_share": 6.12}])
    c = V.compare(ex, _panel([("AFI", "2021-03", 6.12)]))
    assert len(c) == 1 and c.iloc[0]["month"] == "2021-03"


def test_summary_reports_coverage_not_just_agreement():
    """Agreement without coverage overstates what has been verified.

    The exchange file starts in 2017 and lists only the funds it covers, so
    a 99% agreement rate on 5% of the extraction is not a validated dataset.
    """
    c = V.classify(V.compare(_ex([("AFI", "2021-03-31", 6.12)]),
                             _panel([("AFI", "2021-03", 6.12)])))
    s = V.summarise(c, extracted_total=100)
    assert s["agreement_rate"] == 1.0
    assert s["check_coverage"] == 0.01
    assert "subset" in s["note"]


def test_nothing_is_overwritten_by_the_check():
    """The extractor's value is what the fund published; the check reports."""
    ex = _ex([("AFI", "2021-03-31", 612.34)])
    before = ex["nav_per_share"].tolist()
    V.classify(V.compare(ex, _panel([("AFI", "2021-03", 6.12)])))
    assert ex["nav_per_share"].tolist() == before


def test_validation_reads_the_built_panel_not_a_rebuild():
    """build_panel(cfg) takes a config and rebuilds from raw; load_panel reads.

    Calling build_panel() with no argument raises TypeError - and it would
    have done so AFTER the full extraction had already run, which is an hour
    to discover a one-line mistake.
    """
    import inspect

    from au_lic import panel as AUP
    from au_lic.extract import runner as R

    assert "cfg" in inspect.signature(AUP.build_panel).parameters
    src = inspect.getsource(R.run_validate)
    assert "build_panel()" not in src
    assert "load_panel" in src
    # and it fails with a clear instruction rather than a stack trace
    assert "build-panel" in src
