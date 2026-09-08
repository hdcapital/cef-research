"""Phase 3b, the learning layer: episodes, labels, windows, the extraction
contract and the anticipation test, each on synthetic frames."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "src")

from learning import episodes as E  # noqa: E402
from learning import evaluate as V  # noqa: E402
from learning import extract as X  # noqa: E402
from learning import labels as L  # noqa: E402
from learning import schema as S  # noqa: E402
from learning import windows as W  # noqa: E402


# ------------------------------------------------------------------ episodes
def _cov():
    return pd.DataFrame({
        "security_id": ["NAME:a|ordinary share", "NAME:b|ordinary share",
                        "NAME:c|ordinary share", "NAME:d|ordinary share"],
        "company_name": ["A", "B", "C", "D"],
        "first_month": ["2007-01"] * 4, "last_month": ["2019-06", "2021-10", "2026-07", "2015-02"],
        "months_expected": [150, 178, 235, 98],
        "terminal_status": ["terminal_liquidated", "terminal_merged", "missing_next_price",
                            "terminal_unresolved"]})


def _reg():
    return pd.DataFrame({
        "security_id": ["ASX:ZZZ", "NAME:a|ordinary share", "ASX:LIVE"],
        "name": ["Zed Ltd", "A", "Live Co"], "market": ["AU", "UK", "AU"],
        "status": ["delisted", "delisted", "live"],
        "first_seen": ["2016-12", "2007-01", "2016-12"],
        "last_seen": ["2022-08", "2019-06", "2026-08"], "months_listed": [69, 150, 117],
        "delisting_notice": ["2022-10-04", "2019-07-15", None]})


def test_episodes_take_the_panel_outcome_and_the_registry_notice():
    ep = E.build(_cov(), _reg(), tickers={"NAME:b|ordinary share": "bbb"})
    assert set(ep["security_id"]) == {"NAME:a|ordinary share", "NAME:b|ordinary share",
                                      "NAME:d|ordinary share", "ASX:ZZZ"}
    a = ep.set_index("security_id").loc["NAME:a|ordinary share"]
    assert a["outcome"] == "liquidated" and a["value_realising"] is True
    assert a["delisting_notice"] == "2019-07-15"       # registry notice joined
    z = ep.set_index("security_id").loc["ASX:ZZZ"]
    assert z["outcome"] == "delisted" and z["ticker"] == "ZZZ" and z["value_realising"] is None
    assert ep.set_index("security_id").loc["NAME:b|ordinary share", "ticker"] == "BBB"
    assert "NAME:c|ordinary share" not in set(ep["security_id"])   # still trading


def test_window_months_run_up_to_the_end():
    assert E.window_months("2021-10", 3) == ["2021-07", "2021-08", "2021-09", "2021-10"]


# ------------------------------------------------------------------ labels
def test_labels_count_endings_ahead_and_never_behind():
    ep = E.build(_cov(), _reg())
    panel = pd.DataFrame({"security_id": ["NAME:b|ordinary share"] * 3 + ["ASX:ZZZ", "X"],
                          "obs_month": ["2020-04", "2021-04", "2021-11", "2022-01", "2020-01"]})
    lab = L.attach(panel, ep)
    b = lab[lab["security_id"] == "NAME:b|ordinary share"].set_index("obs_month")
    assert b.loc["2020-04", "resolved_within_18m"] == 1 and b.loc["2020-04", "resolved_within_12m"] == 0
    assert b.loc["2021-04", "resolved_within_6m"] == 1
    assert b.loc["2021-11", "resolved_within_6m"] == 0          # after the end
    z = lab[lab["security_id"] == "ASX:ZZZ"].iloc[0]
    assert z["resolved_within_12m"] == 1 and pd.isna(z["value_realising_within_12m"])
    assert lab[lab["security_id"] == "X"]["resolved_within_18m"].iloc[0] == 0
    assert 0 < L.base_rates(lab)["resolved_within_12m"] < 1


# ------------------------------------------------------------------ windows
def _listing(tmp_path: Path) -> Path:
    d = tmp_path / "listings"
    d.mkdir()
    rows = [
        ("1", "2020-01-15", "Final Results", "/a/1"),
        ("2", "2020-03-02", "Holding(s) in Company", "/a/2"),
        ("3", "2020-03-03", "Holding(s) in Company", "/a/3"),
        ("4", "2020-03-04", "Holding(s) in Company", "/a/4"),
        ("5", "2020-05-11", "Transaction in Own Shares", "/a/5"),
        ("6", "2020-06-30", "Strategic Review", "/a/6"),
        ("7", "2020-09-01", "Net Asset Value(s)", "/a/7"),
        ("8", "2020-11-20", "Proposals for the Reconstruction and Winding-up", "/a/8"),
        ("9", "2018-01-01", "Final Results", "/a/9"),          # outside the window
    ]
    pd.DataFrame(rows, columns=["ann_id", "date", "headline", "url"]).to_csv(
        d / "AAA.csv", index=False)
    return d


def test_uk_window_reads_narrative_and_catalyst_headlines_only(tmp_path):
    ep = pd.DataFrame([{"security_id": "NAME:a|ordinary share", "market": "UK",
                        "ticker": "AAA", "end_month": "2020-12"}])
    docs = W.select_documents(ep, before=18, after=0, listings_dir=_listing(tmp_path),
                              au=pd.DataFrame())
    heads = set(docs["headline"])
    assert "Final Results" in heads and "Strategic Review" in heads
    assert "Proposals for the Reconstruction and Winding-up" in heads
    assert "Net Asset Value(s)" not in heads and "Holding(s) in Company" not in heads
    assert docs["ann_id"].tolist()[0] == "8"                     # newest first
    assert "9" not in set(docs["ann_id"])


def test_headline_features_are_point_in_time(tmp_path):
    ep = pd.DataFrame([{"security_id": "NAME:a|ordinary share", "market": "UK",
                        "ticker": "AAA", "end_month": "2020-12"}])
    hf = W.headline_features(ep, before=12, listings_dir=_listing(tmp_path),
                             au=pd.DataFrame()).set_index("obs_month")
    assert hf.loc["2020-03", "holder_filings_3m"] == 3
    assert hf.loc["2020-05", "buyback_execs_3m"] == 1 and hf.loc["2020-09", "buyback_execs_3m"] == 0
    assert pd.isna(hf.loc["2020-05", "months_since_strategic_review"])
    assert hf.loc["2020-08", "months_since_strategic_review"] == 2
    assert hf.loc["2020-10", "windup_headline_seen"] == 0 and hf.loc["2020-11", "windup_headline_seen"] == 1


def test_au_window_uses_the_router_families():
    au = pd.DataFrame({"ann_id": ["1", "2", "3"], "ticker": ["ZZZ"] * 3,
                       "date": ["2022-06-01", "2022-07-01", "2022-08-01"],
                       "headline": ["NTA Backing", "Notice of General Meeting/Proxy Form",
                                    "Scheme of Arrangement - Court Approval"],
                       "url": ["u1", "u2", "u3"]})
    ep = pd.DataFrame([{"security_id": "ASX:ZZZ", "market": "AU", "ticker": "ZZZ",
                        "end_month": "2022-08"}])
    docs = W.select_documents(ep, au=au)
    assert set(docs["family"]) == {"meeting", "corporate_action"}


# ------------------------------------------------------------------ contract
DOC = ("The Board acknowledges the persistent discount to net asset value. "
       "The Company will buy back shares at discounts wider than 10%. "
       "An independent valuer values the portfolio each quarter.")


def _rec(**over):
    rec = {"document_kind": "results",
           "features": {**S.SILENT, "discount_stance": "action_promised",
                        "buyback_commitment": "specific_target"},
           "quotes": {"discount_stance": "The Board acknowledges the persistent discount to net asset value.",
                      "buyback_commitment": "The Company will buy back shares at discounts wider than 10%."},
           "stated_dates": [], "confidence": 0.9}
    rec.update(over)
    return rec


def test_contract_accepts_a_quoted_record():
    assert X.guard(_rec(), DOC) == []


def test_contract_needs_a_quote_for_every_non_silent_value():
    r = _rec(features={**_rec()["features"], "nav_marking": "independent_valuation"})
    assert X.guard(r, DOC) == ["no_quote:nav_marking"]
    r["quotes"]["nav_marking"] = "An independent valuer values the portfolio each quarter."
    assert X.guard(r, DOC) == []


def test_contract_rejects_a_quote_not_in_the_document():
    r = _rec(quotes={**_rec()["quotes"], "discount_stance": "The Board is delighted."})
    assert X.guard(r, DOC) == ["quote_not_in_document:discount_stance"]


def test_contract_rejects_vocabulary_drift_and_computed_signals():
    assert "enum:winddown_path='thinking about it'" in X.guard(
        _rec(features={**_rec()["features"], "winddown_path": "thinking about it"}), DOC)
    assert "computed_signal_field:discount_z" in X.guard(_rec(discount_z=-2.1), DOC)
    assert any(p.startswith("confidence_below_floor") for p in X.guard(_rec(confidence=0.2), DOC))


def test_run_records_rejections_and_aborts_on_repeated_errors():
    class Client:
        calls = 0

        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                raise RuntimeError("boom")
    docs = pd.DataFrame([{"security_id": f"NAME:{i}", "market": "UK", "ticker": "T",
                          "ann_id": str(i), "date": "2020-01-01", "obs_month": "2020-01",
                          "end_month": "2020-06", "headline": "Final Results", "family": "narrative",
                          "url": ""} for i in range(6)])
    rows, rejects, stats = X.run(docs, budget=10, client=Client(), done=set(),
                                 text_fn=lambda d: DOC * 3)
    assert stats["aborted"] and stats["errors"] == X.MAX_CONSECUTIVE_ERRORS
    assert rows == [] and rejects == []


def test_flatten_carries_every_feature_and_provenance():
    doc = {"security_id": "NAME:a", "market": "UK", "ticker": "AAA", "ann_id": "1",
           "date": "2020-01-15", "obs_month": "2020-01", "end_month": "2020-12",
           "headline": "Final Results", "family": "narrative"}
    row = X.flatten(_rec(), doc, {"model": "m", "prompt_version": "v1:x"})
    assert row["discount_stance"] == "action_promised" and row["nav_marking"] == "not_stated"
    assert set(X.FEATURE_COLUMNS) <= set(row) and row["prompt_version"] == "v1:x"


# ------------------------------------------------------------------ evaluation
def test_features_persist_then_lapse_to_silent():
    rows = pd.DataFrame([{"security_id": "NAME:a", "ann_id": "1", "date": "2020-01-15",
                          **S.SILENT, "winddown_path": "strategic_review"}])
    months = pd.DataFrame({"security_id": ["NAME:a"] * 3 + ["NAME:b"],
                           "obs_month": ["2019-12", "2020-03", "2020-09", "2020-03"]})
    f = V.features_by_month(rows, months, persist_months=6).set_index(["security_id", "obs_month"])
    assert f.loc[("NAME:a", "2019-12"), "winddown_path"] == "none"      # not yet known
    assert f.loc[("NAME:a", "2020-03"), "winddown_path"] == "strategic_review"
    assert f.loc[("NAME:a", "2020-09"), "winddown_path"] == "none"      # lapsed
    assert f.loc[("NAME:b", "2020-03"), "winddown_path"] == "none"


def test_anticipation_reports_lift_against_the_base_rate():
    n = 200
    lab = pd.DataFrame({
        "security_id": [f"s{i}" for i in range(n)],
        "obs_month": ["2015-01"] * 100 + ["2023-01"] * 100,
        "winddown_path": ["strategic_review" if i % 4 == 0 else "none" for i in range(n)],
        "resolved_within_12m": [1 if (i % 4 == 0 or i % 10 == 0) else 0 for i in range(n)]})
    ant = V.anticipation(lab, ["winddown_path"], horizons=(12,))
    dev = ant[(ant["period"] == "development") & (ant["value"] == "strategic_review")].iloc[0]
    assert dev["rate"] == 1.0 and dev["lift"] > 1 and dev["z_vs_rest"] > 3
    assert {"development", "holdout_2022+", "all"} == set(ant["period"])


def test_listing_coverage_explains_an_empty_window(tmp_path):
    ep = pd.DataFrame([
        {"security_id": "NAME:a|ordinary share", "market": "UK", "ticker": "AAA", "end_month": "2020-12"},
        {"security_id": "NAME:old|ordinary share", "market": "UK", "ticker": "AAA", "end_month": "2010-06"},
        {"security_id": "NAME:none|ordinary share", "market": "UK", "ticker": "NOPE", "end_month": "2020-12"}])
    cov = W.listing_coverage(ep, before=18, listings_dir=_listing(tmp_path),
                             au=pd.DataFrame()).set_index("security_id")
    assert cov.loc["NAME:a|ordinary share", "reaches_window"] and cov.loc["NAME:a|ordinary share", "window_rows"] == 8
    assert not cov.loc["NAME:old|ordinary share", "reaches_window"]     # listing starts after the ending
    assert cov.loc["NAME:none|ordinary share", "listing_rows"] == 0
