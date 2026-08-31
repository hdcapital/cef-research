"""Discovering WHERE the NTA sits, from the exchange's published figure.

The failures this exists to fix are real and named: HCF reads 4.120 in three
consecutive months while the exchange's NTA moves 0.011 to 0.012; TEK reads
exactly 2.000 across four months against a real 0.248 to 0.262; FGG reads
0.040 against 1.71, which is roughly what FGG pays as a dividend. Same fund,
same layout, same wrong number every month.

The danger is the mirror image: a monthly NTA announcement carries dozens of
numbers, so "find the one closest to the answer" always finds something. The
tests that matter here are the ones that stop a coincidence becoming a rule.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from au_lic.extract import label_discovery as LD  # noqa: E402


# --------------------------------------------------------------- candidates
def test_a_labelled_figure_is_found_with_its_label():
    c = LD.candidates_from_text("Pre-tax NTA per share $1.71 as at 30 June")
    hit = [x for x in c if abs(x["value"] - 1.71) < 1e-9]
    assert hit, "the figure itself was not offered as a candidate"
    assert "nta per share" in hit[0]["label"]


def test_a_bare_number_offers_both_dollars_and_cents():
    """LIC NTAs are quoted both ways; only the exchange can settle which."""
    vals = {c["unit"]: c["value"] for c in LD.candidates_from_text("NTA per share 171.00")}
    assert vals["dollars"] == 171.0
    assert vals["cents"] == pytest.approx(1.71)


def test_an_explicit_cents_marker_is_not_also_read_as_dollars():
    units = {c["unit"] for c in LD.candidates_from_text("NTA per share 171.0c")}
    assert units == {"cents"}


def test_table_rows_pair_the_label_cells_with_the_numeric_cells():
    rows = [["Pre-tax NTA per share", "$1.71", "$1.66"]]
    c = LD.candidates_from_rows(rows)
    assert {round(x["value"], 2) for x in c} == {1.71, 1.66}
    assert all("nta per share" in x["label"] for x in c)


def test_percentage_cells_are_never_candidates():
    """A discount percentage is not a NAV and must not be matchable."""
    rows = [["Premium/(discount) to NTA", "12.5%"]]
    assert LD.candidates_from_rows(rows) == []


def test_labels_normalise_across_issues_of_the_same_layout():
    """Month, year, case and footnotes vary; the layout does not.

    Without this a label never reaches three supporting months, because
    every issue looks like a different label.
    """
    a = LD.normalise_label("Pre-Tax NTA per share (June 2025)")
    b = LD.normalise_label("pre-tax NTA per share (July 2026)")
    assert a == b == "pre tax nta per share"


# -------------------------------------------------------------- observations
def test_the_matching_label_is_the_one_recorded():
    text = ("Dividend per share 4.0 cents. "
            "Net tangible assets per share $1.71.")
    obs = LD.observe("FGG", "2025-06", text, None, truth=1.71)
    labs = {o["label"] for o in obs}
    assert any("net tangible assets per share" in l for l in labs)
    assert not any("dividend per share" in l for l in labs), \
        "the dividend label must not be credited with the NTA"


def test_a_wrong_figure_teaches_nothing_but_is_still_recorded():
    """HCF's constant 4.120 against a real 0.011 supports no label.

    The row it leaves is a MISS, carrying the nearest candidate so the 80% of
    documents that match nothing can be split between a real disagreement
    between the two sources and a gap in the enumerator. It must never
    contribute a label to learn from.
    """
    obs = LD.observe("HCF", "2025-10", "Something per share $4.120", None, truth=0.011)
    assert len(obs) == 1
    assert obs[0]["matched"] is False
    assert obs[0]["label"] is None
    assert obs[0]["nearest_value"] == pytest.approx(4.12)
    # and it must not survive into the rules
    out = LD.discover(pd.DataFrame(obs))
    assert len(out) == 0


def test_a_crowded_document_records_how_crowded_it_was():
    text = "A $1.71 B $1.71 C $1.71 D $1.71"
    obs = LD.observe("XYZ", "2025-06", text, None, truth=1.71)
    assert obs and all(o["n_matching_labels"] >= LD.AMBIGUOUS_MATCH_COUNT for o in obs)


# ------------------------------------------------------------------ discovery
def _obs(ticker, months, label, n=1):
    return [{"ticker": ticker, "month": m, "label": label, "unit": "dollars",
             "truth": 1.0, "n_matching_labels": n} for m in months]


def test_three_clean_months_make_a_rule():
    df = pd.DataFrame(_obs("FGG", ["2025-03", "2025-04", "2025-05"], "nta per share"))
    out = LD.discover(df)
    assert bool(out.loc[0, "is_rule"]) is True
    assert out.loc[0, "months_clean"] == 3


def test_one_lucky_month_is_not_a_rule():
    df = pd.DataFrame(_obs("FGG", ["2025-03"], "some stray label"))
    assert bool(LD.discover(df).loc[0, "is_rule"]) is False


def test_repeated_coincidence_in_crowded_documents_is_not_a_rule():
    """Three matches in three documents where everything matched prove nothing."""
    df = pd.DataFrame(_obs("FGG", ["2025-03", "2025-04", "2025-05"],
                           "stray", n=LD.AMBIGUOUS_MATCH_COUNT + 2))
    out = LD.discover(df)
    assert out.loc[0, "months_supporting"] == 3
    assert out.loc[0, "months_clean"] == 0
    assert bool(out.loc[0, "is_rule"]) is False


def test_the_same_month_twice_is_still_one_month():
    df = pd.DataFrame(_obs("FGG", ["2025-03", "2025-03", "2025-03"], "nta per share"))
    assert bool(LD.discover(df).loc[0, "is_rule"]) is False


def test_rules_carry_the_window_they_were_seen_in():
    """A backtest must be able to refuse a rule discovered after its start."""
    df = pd.DataFrame(_obs("FGG", ["2025-03", "2025-09", "2025-06"], "nta per share"))
    out = LD.discover(df)
    assert out.loc[0, "first_month"] == "2025-03"
    assert out.loc[0, "last_month"] == "2025-09"


def test_empty_evidence_returns_an_empty_table_not_a_crash():
    out = LD.discover(pd.DataFrame())
    assert len(out) == 0 and "is_rule" in out.columns


# -------------------------------------------------------------------- holdout
def test_the_holdout_is_stable_and_disjoint():
    t = [f"T{i}" for i in range(400)]
    a, b = LD.holdout_split(t), LD.holdout_split(t)
    assert a == b
    held = {k for k, v in a.items() if v == "holdout"}
    assert 0.2 < len(held) / len(t) < 0.4
    assert held.isdisjoint({k for k, v in a.items() if v == "learn"})


def test_adding_funds_does_not_move_existing_ones():
    """Membership must not churn as the universe grows."""
    base = LD.holdout_split(["AAA", "BBB", "CCC"])
    grown = LD.holdout_split(["AAA", "BBB", "CCC", "DDD", "EEE"])
    assert all(grown[k] == v for k, v in base.items())


# ------------------------------------------------- mode isolation in main()
def test_labels_mode_does_not_overwrite_the_deterministic_status(tmp_path, monkeypatch):
    """A mode that writes another mode's status file destroys its record.

    The first version of the labels mode fell through into the deterministic
    block and wrote {"status": "no_panel"} over a committed status recording
    7,536 processed documents. That is precisely how a status file ends up
    lying about a run that succeeded - the failure that cost most of a night
    to trace, reintroduced in a new place.
    """
    import importlib
    import json

    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports" / "build").mkdir(parents=True)
    keep = {"documents": 7536, "parsed": 5812}
    det = tmp_path / "reports" / "build" / "asx_deterministic_status.json"
    det.write_text(json.dumps(keep))

    runner = importlib.import_module("au_lic.extract.runner")
    monkeypatch.setattr(runner, "run_label_discovery",
                        lambda **_k: {"status": "no_panel"})
    runner.main(["labels", "--limit", "1"])

    assert json.loads(det.read_text()) == keep, \
        "labels mode overwrote the deterministic status"


def test_deterministic_mode_still_writes_and_returns(tmp_path, monkeypatch):
    """The fall-through also broke deterministic: it lost its own return."""
    import importlib
    import json

    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports" / "build").mkdir(parents=True)
    runner = importlib.import_module("au_lic.extract.runner")
    monkeypatch.setattr(runner, "run_deterministic",
                        lambda **_k: {"documents": 3, "parsed": 2})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = runner.main(["deterministic", "--limit", "1"])

    assert rc == 0
    got = json.loads((tmp_path / "reports" / "build"
                      / "asx_deterministic_status.json").read_text())
    assert got["documents"] == 3, "deterministic no longer records its own run"


# ------------------------------------------------------ vocabulary clause
def test_a_stable_address_is_not_a_nav_label():
    """The real one: GCI's registered office sits before the NTA every month.

    It repeats as faithfully as the true label does, so repetition alone
    promoted it. Repetition proves the POSITION is stable; it cannot prove
    the label means anything.
    """
    df = pd.DataFrame(_obs("GCI", ["2025-03", "2025-04", "2025-05"],
                           "governor macquarie tower farrer place sydney nsw p"))
    out = LD.discover(df)
    assert bool(out.loc[0, "has_nav_vocab"]) is False
    assert bool(out.loc[0, "is_rule"]) is False
    assert out.loc[0, "months_clean"] == 3, "support is still reported for review"


@pytest.mark.parametrize("label,ok", [
    ("pre tax nta per share", True),
    ("nta before tax", True),
    ("nav per unit", True),
    ("net tangible assets per share", True),
    ("asset backing per share", True),
    ("limited level exchange centre bridge street sydney nsw", False),
    ("per unit of the dominion income trust was", False),
    ("dividend per share", False),
])
def test_the_vocabulary_clause_separates_labels_from_neighbours(label, ok):
    assert LD.looks_like_nav_label(label) is ok


def test_evidence_aggregates_across_shards_before_the_bar_is_applied():
    """Sharding fragments the very repetition the bar measures.

    Discovery shards by announcement id, so a fund's six months of evidence
    land roughly one per shard. Applying the three-month bar inside a shard
    tested it against an eighth of the fund's history: 88 funds had evidence
    and only 21 cleared it. Merged, 32 do.
    """
    per_shard = [pd.DataFrame(_obs("AFI", [m], "pre tax nta per share"))
                 for m in ("2025-03", "2025-04", "2025-05")]
    assert all(not LD.discover(s).loc[0, "is_rule"] for s in per_shard), \
        "a single month should never clear the bar on its own"
    merged = LD.discover(pd.concat(per_shard, ignore_index=True))
    assert bool(merged.loc[0, "is_rule"]) is True


def test_a_basis_difference_shows_as_a_consistent_ratio():
    """The signature that says 'not a parser problem'.

    Where the exchange publishes post-tax and the fund reports pre-tax, the
    nearest candidate misses by the same proportion every month. That is the
    two sources measuring different things, and no amount of parser work
    fixes it - which is exactly why the miss rows have to record the ratio.
    """
    rows = [LD.observe("XYZ", m, f"NTA before tax ${v}", None, truth=v * 0.9)[0]
            for m, v in (("2025-03", 2.00), ("2025-04", 2.10), ("2025-05", 2.20))]
    ratios = [r["nearest_ratio"] for r in rows]
    assert all(r["matched"] is False for r in rows)
    assert max(ratios) - min(ratios) < 0.01, "a basis gap is a STABLE ratio"
    assert abs(ratios[0] - 1 / 0.9) < 0.01


def test_misses_and_matches_can_coexist_without_corrupting_the_rules():
    ev = (_obs("AFI", ["2025-03", "2025-04", "2025-05"], "pre tax nta per share")
          + [{"ticker": "AFI", "month": "2025-06", "label": None, "unit": None,
              "truth": 1.0, "n_matching_labels": 0, "matched": False}])
    for r in ev[:3]:
        r["matched"] = True
    out = LD.discover(pd.DataFrame(ev))
    assert len(out) == 1 and bool(out.loc[0, "is_rule"]) is True
