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
    problems, feature_problems, matches = X.guard(_rec(), DOC)
    assert problems == [] and feature_problems == {}
    assert matches == {"discount_stance": "exact", "buyback_commitment": "exact"}


def test_contract_needs_a_quote_for_every_non_silent_value():
    r = _rec(features={**_rec()["features"], "nav_marking": "independent_valuation"})
    assert X.guard(r, DOC)[1] == {"nav_marking": "no_quote"}
    r["quotes"]["nav_marking"] = "An independent valuer values the portfolio each quarter."
    assert X.guard(r, DOC)[1] == {}


def test_an_unquotable_feature_lapses_to_silent_and_the_rest_survives():
    r = _rec(quotes={**_rec()["quotes"], "discount_stance": "The Board is delighted."})
    problems, feature_problems, _ = X.guard(r, DOC)
    assert problems == [] and feature_problems == {"discount_stance": "quote_not_in_document"}
    kept = X.apply_feature_problems(r, feature_problems)
    assert kept["features"]["discount_stance"] == "not_mentioned"
    assert kept["features"]["buyback_commitment"] == "specific_target"
    assert "discount_stance" not in kept["quotes"]


def test_contract_rejects_vocabulary_drift_and_computed_signals():
    assert X.guard(_rec(features={**_rec()["features"], "winddown_path": "thinking about it"}),
                   DOC)[1] == {"winddown_path": "enum:'thinking about it'"}
    assert "computed_signal_field:discount_z" in X.guard(_rec(discount_z=-2.1), DOC)[0]
    assert any(p.startswith("confidence_below_floor") for p in X.guard(_rec(confidence=0.2), DOC)[0])


def test_quote_matching_survives_pdf_layout_but_not_a_changed_number():
    pdf = ("The Board acknowledges the persistent dis-\ncount to net asset\n--- PAGE 2 ---\n"
           "value, and will buy back shares at discounts wider than 10%.")
    assert X.quote_match("The Board acknowledges the persistent discount to net asset value", pdf) == "exact"
    assert X.quote_match("The Board acknowledges the persistent discount to net asset value, and will "
                         "buy back shares at discounts wider than 10 per cent", pdf) == "fuzzy"
    assert X.quote_match("will buy back shares at discounts wider than 15%", pdf) is None
    assert X.quote_match("Board acknowledges", pdf) == "exact"
    assert X.quote_match("the persistent Board wider buy", pdf) is None


def test_rejected_documents_are_read_again_after_a_guard_change():
    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                class R:
                    stop_reason = "end_turn"
                    usage = None
                    content = [type("B", (), {"type": "text", "text": "not json"})()]
                return R()
    docs = pd.DataFrame([{"security_id": "NAME:1", "market": "UK", "ticker": "T", "ann_id": "1",
                          "date": "2020-01-01", "obs_month": "2020-01", "end_month": "2020-06",
                          "headline": "Final Results", "family": "narrative", "url": ""}])
    done: set = set()
    rows, rejects, stats = X.run(docs, budget=10, client=Client(), done=done, text_fn=lambda d: DOC * 3)
    assert stats["rejected"] == 1 and rejects[0]["reasons"] == "unparseable"
    assert done == {f"NAME:1|1|{X.prompt_version()}|{X.GUARD_VERSION}"}
    _, _, again = X.run(docs, budget=10, client=Client(), done=done, text_fn=lambda d: DOC * 3)
    assert again["skipped_done"] == 1
    other = {k.replace(X.GUARD_VERSION, "g0") for k in done}
    _, _, reread = X.run(docs, budget=10, client=Client(), done=other, text_fn=lambda d: DOC * 3)
    assert reread["read"] == 1


def test_a_bad_stated_date_is_dropped_not_the_document():
    r = _rec(stated_dates=[{"what": "AGM", "date": "2020-03"}, {"what": "EGM", "date": "2020-03-31"}])
    problems, feature_problems, _ = X.guard(r, DOC)
    assert problems == [] and r["stated_dates"] == [{"what": "EGM", "date": "2020-03-31"}]


def test_each_run_writes_its_own_file_and_the_store_is_the_done_set(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "OUT_DIR", tmp_path)
    doc = {"security_id": "NAME:a", "market": "UK", "ticker": "AAA", "ann_id": "1",
           "date": "2020-01-15", "obs_month": "2020-01", "end_month": "2020-12",
           "headline": "Final Results", "family": "narrative"}
    row = X.flatten(_rec(), doc, {"model": "m", "prompt_version": X.prompt_version()})
    a = X.write_outputs([row], [], "all", out_dir=tmp_path)
    b = X.write_outputs([dict(row, ann_id="2")], [{"security_id": "NAME:a", "ann_id": "3",
                                                    "prompt_version": X.prompt_version(),
                                                    "guard_version": X.GUARD_VERSION, "reasons": "x"}],
                        "all", out_dir=tmp_path)
    assert a["features"] != b["features"]
    assert len(X.load_features(tmp_path)) == 2
    done = X.read_manifest(None)
    assert {"NAME:a|1", "NAME:a|2", f"NAME:a|3|{X.prompt_version()}|{X.GUARD_VERSION}"} <= done


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


def _registry():
    return pd.DataFrame({
        "security_id": ["NAME:a|ordinary share", "NAME:surv|ordinary share", "NAME:young|ordinary share",
                        "NAME:index|ordinary share", "ASX:ZZZ", "ASX:LIVE"],
        "name": ["A", "Survivor", "Young", "S&P Index", "Zed", "Live Co"],
        "market": ["UK", "UK", "UK", "UK", "AU", "AU"],
        "status": ["delisted", "live", "live", "live", "delisted", "live"],
        "first_seen": ["2007-01", "2007-01", "2020-06", "2007-01", "2016-12", "2016-12"],
        "last_seen": ["2020-12", "2026-08", "2026-08", "2026-08", "2022-08", "2026-08"],
        "research_eligible": [True, True, True, True, True, True]})


def test_controls_are_survivors_of_the_same_window_and_never_endings_or_benchmarks():
    ep = pd.DataFrame([{"security_id": "NAME:a|ordinary share", "market": "UK", "ticker": "AAA",
                        "end_month": "2020-12"},
                       {"security_id": "ASX:ZZZ", "market": "AU", "ticker": "ZZZ", "end_month": "2022-08"}])
    ctrl = W.select_controls(ep, _registry(), tickers={"NAME:surv|ordinary share": "svr",
                                                       "NAME:young|ordinary share": "yng"})
    by = ctrl.set_index("case_id")
    assert by.loc["NAME:a|ordinary share", "security_id"] == "NAME:surv|ordinary share"   # listed through 2019-06..2021-12
    assert by.loc["NAME:a|ordinary share", "ticker"] == "SVR"
    assert by.loc["ASX:ZZZ", "security_id"] == "ASX:LIVE" and by.loc["ASX:ZZZ", "ticker"] == "LIVE"
    assert "NAME:index|ordinary share" not in set(ctrl["security_id"])


def test_universe_headline_features_cover_every_listed_month(tmp_path):
    reg = _registry().iloc[[1]].assign(first_seen="2020-01", last_seen="2020-12")
    hf = W.headline_features_universe(reg, listings_dir=_listing(tmp_path), au=pd.DataFrame(),
                                      tickers={"NAME:surv|ordinary share": "AAA"}).set_index("obs_month")
    assert len(hf) == 12 and hf.loc["2020-03", "holder_filings_3m"] == 3
    assert hf.loc["2020-05", "buyback_execs_3m"] == 1 and hf.loc["2020-09", "buyback_execs_3m"] == 0
    assert hf.loc["2020-08", "months_since_strategic_review"] == 2
    assert hf.loc["2020-10", "windup_headline_seen"] == 0 and hf.loc["2020-12", "windup_headline_seen"] == 1


def test_documented_marks_only_the_months_after_a_read_document():
    rows = pd.DataFrame([{"security_id": "NAME:a", "ann_id": "1", "date": "2020-01-15"}])
    months = pd.DataFrame({"security_id": ["NAME:a"] * 3 + ["NAME:b"],
                           "obs_month": ["2019-12", "2020-04", "2020-09", "2020-02"]})
    assert V.documented(rows, months, persist_months=6).tolist() == [False, True, False, False]


def test_listing_targets_are_the_endings_the_crawl_never_reached(tmp_path):
    ep = pd.DataFrame([
        {"security_id": "NAME:a|ordinary share", "market": "UK", "ticker": "AAA", "end_month": "2020-12", "name": "A"},
        {"security_id": "NAME:old|ordinary share", "market": "UK", "ticker": "AAA", "end_month": "2010-06", "name": "Old"},
        {"security_id": "NAME:none|ordinary share", "market": "UK", "ticker": "NOPE", "end_month": "2020-12", "name": "None"},
        {"security_id": "ASX:ZZZ", "market": "AU", "ticker": "ZZZ", "end_month": "2022-08", "name": "Zed"}])
    t = W.listing_targets(ep, listings_dir=_listing(tmp_path))
    assert set(t["security_id"]) == {"NAME:old|ordinary share", "NAME:none|ordinary share"}
    assert t.set_index("security_id").loc["NAME:none|ordinary share", "name"] == "None"


def test_aic_activity_features_are_point_in_time_by_effective_month():
    ca = pd.DataFrame([
        {"event_month": "2019-03", "category": "buyback", "company_name": "Alpha Trust"},
        {"event_month": "2019-09", "category": "tender", "company_name": "Alpha Trust"},
        {"event_month": "2020-02", "category": "buyback", "company_name": "Alpha Trust"},
        {"event_month": "2019-06", "category": "manager_change", "company_name": "Beta Trust"}])
    months = pd.DataFrame({"security_id": ["SEDOL:A"] * 4 + ["SEDOL:C"],
                           "company_name": ["Alpha Trust"] * 4 + ["Gamma Trust"],
                           "obs_month": ["2019-01", "2019-06", "2019-10", "2020-06", "2019-06"]})
    af = W.aic_activity_features(ca, months).set_index("obs_month")
    assert pd.isna(af.loc["2019-01", "aic_buyback_since"]) and af.loc["2019-01", "aic_buyback_12m"] == 0
    assert af.loc["2019-06", "aic_buyback_since"] == 3 and af.loc["2019-06", "aic_buyback_12m"] == 1
    assert pd.isna(af.loc["2019-06", "aic_tender_since"])            # not yet recorded
    assert af.loc["2019-10", "aic_tender_since"] == 1 and af.loc["2019-10", "aic_tender_12m"] == 1
    assert af.loc["2020-06", "aic_buyback_12m"] == 1                  # 2019-03 has rolled off, 2020-02 stays
    assert "SEDOL:C" not in set(af["security_id"])                    # no record: no row, never a false silent
    flags = V.aic_feature_flags(af.reset_index()).set_index("obs_month")
    assert flags.loc["2019-10", "aic_tender_seen"] == "seen" and flags.loc["2019-01", "aic_tender_seen"] == "none"
    assert flags.loc["2020-06", "aic_buyback_recent"] == "recent"
