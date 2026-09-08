"""Phase 3: signed taxonomy, the events store, TR-1 terms, new-event alerts,
fund files."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from cef_live import catalysts, events as EV


def _today(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


@pytest.mark.parametrize("headline,cls,sign", [
    ("Suspension - Ceiba Investments Limited", "suspension", -1),
    ("No Intention to Bid Statement", "offer_withdrawn", -1),
    ("Termination of potential Reverse Takeover", "offer_withdrawn", -1),
    ("Dispute with Smith Square Partners LLP", "legal_sanctions", -1),
    ("Statement re U.S. Sanctions Designation", "legal_sanctions", -1),
    ("Delay in Accounts Publication", "going_concern_delay", -1),
    ("Update: Board & Management Resignations", "manager_exit", -1),
    ("Reduction in quarterly dividend", "dividend_cut", -1),
    ("Continuation vote not passed", "failed_continuation", -1),
    ("Recommended Cash Acquisition of Augmentum Fintech plc", "takeover_offer", 1),
    ("Tender Offer", "tender_offer", 1),
    ("Result of Continuation Vote", "continuation_vote", 1),
    ("Holding(s) in Company", "holdings_notification", 0),
    ("Issue of Equity", "issuance", 0),
])
def test_signed_taxonomy_on_real_headlines(headline, cls, sign):
    got = catalysts.classify_signed(headline)
    assert got is not None and got["class"] == cls
    assert (got["weight"] > 0) - (got["weight"] < 0) == sign
    # the positive-only view is unchanged for its callers
    pos = catalysts.classify(headline)
    assert (pos is not None) == (sign > 0)


def test_noise_and_unknown_headlines_are_not_events():
    assert catalysts.classify_signed("Total Voting Rights") is None
    assert catalysts.classify_signed("Net Asset Value(s)") is None
    assert catalysts.classify_signed("Transaction in Own Shares") is None


def test_scan_rows_carries_direction_and_drops_neutral():
    rows = [{"security_id": "S1", "date": _today(1), "headline": "Tender Offer", "url": "u1"},
            {"security_id": "S1", "date": _today(1), "headline": "Suspension - X plc", "url": "u2"},
            {"security_id": "S1", "date": _today(1), "headline": "Holding(s) in Company", "url": "u3"}]
    df = catalysts.scan_rows(rows)
    assert set(df["direction"]) == {"positive", "negative"}
    assert len(df) == 2


def test_events_store_remembers_first_seen_and_alerted(tmp_path):
    path = tmp_path / "events.parquet"
    rows = [{"security_id": "SEDOL:1", "date": _today(2), "headline": "Tender Offer", "url": "u1"},
            {"security_id": "SEDOL:1", "date": _today(1), "headline": "Holding(s) in Company", "url": "u2"}]
    ev = EV.merge_events(EV.build_events(rows, None), path)
    assert len(ev) == 2 and ev["alerted_at"].isna().all()
    first = ev.set_index("event_id")["first_seen"].to_dict()
    new = EV.new_events(ev, live_sids={"SEDOL:1"})
    assert list(new["event_class"]) == ["tender_offer"]        # holdings are weight 0
    ev = EV.mark_alerted(ev, new["event_id"], when="2026-09-07T06:20:00+00:00", path=path)
    # a re-scan of the same announcements changes nothing it remembers
    again = EV.merge_events(EV.build_events(rows, None), path)
    assert len(again) == 2
    assert again.set_index("event_id")["first_seen"].to_dict() == first
    assert again.set_index("event_id").loc[new["event_id"].iloc[0], "alerted_at"] == "2026-09-07T06:20:00+00:00"
    assert len(EV.new_events(again, live_sids={"SEDOL:1"})) == 0
    # a dead fund's event is never alerted
    assert len(EV.new_events(ev.assign(alerted_at=None), live_sids={"SEDOL:2"})) == 0


TR1 = ("TR-1: Standard form for notification of major holdings 1. Identity of the issuer "
       "Regional REIT Limited 2. Reason for the notification An acquisition or disposal of "
       "voting rights 3. Details of person subject to the notification obligation Name: "
       "Lombard Odier Asset Management (Europe) Limited City and country of registered office "
       "London, United Kingdom 4. Full name of shareholder(s) 5. Date on which the threshold "
       "was crossed or reached: 02/09/2026 6. Date on which issuer notified: 03/09/2026 7. Total "
       "positions of person(s) subject to the notification obligation % of voting rights "
       "attached to shares % of voting rights through financial instruments Total of both in % "
       "Total number of voting rights held in issuer Resulting situation on the date on which "
       "threshold was crossed or reached 6.12% 0% 6.12% 9,876,543 Position of previous "
       "notification (if applicable) 7.25% 0% 7.25%")


def test_tr1_terms_read_previous_and_resulting_stake():
    got = EV.parse_tr1(TR1)
    assert got["new_pct"] == 6.12 and got["prev_pct"] == 7.25
    assert got["direction"] == "selling"
    assert got.get("holder", "").startswith("Lombard Odier")
    assert EV.parse_tr1("nothing here") == {}


def test_holder_overhang_from_enriched_holdings(tmp_path):
    rows = [{"security_id": "SEDOL:R", "date": _today(i * 5), "headline": "Holding(s) in Company",
             "url": f"https://www.investegate.co.uk/announcement/rns/regional-reit--rgl/holding-s-in-company/{i}"}
            for i in range(4)]
    ev = EV.build_events(rows, None)
    fetched = []

    def fetch(url):
        fetched.append(url)
        return TR1
    ev, stats = EV.enrich_holdings(ev, None, budget=3, fetch=fetch)
    assert stats["fetched"] == 3 and stats["parsed"] == 3
    # budget respected, and a fetched page is never fetched twice
    ev, stats2 = EV.enrich_holdings(ev, None, budget=10, fetch=fetch)
    assert stats2["fetched"] == 1 and len(fetched) == 4
    churn = EV.holder_churn(ev)
    assert len(churn) == 1
    row = churn.iloc[0]
    assert row["filings"] == 4 and row["direction"] == "overhang" and row["largest_move"] == -1.13


def test_fund_file_is_one_record_per_fund(tmp_path):
    live = pd.DataFrame([{"security_id": "SEDOL:1", "name": "Fund One", "market": "UK",
                          "sector": "Property", "nav_anchor": 100.0, "anchor_date": "2026-08-31",
                          "basis": 0, "staleness_days": 7, "nav_current": True, "price": 80.0,
                          "price_ccy": "GBp", "price_date": "2026-09-05", "discount_est": -0.2,
                          "z_adj": -1.9, "z_status": "computed", "alert_eligible": True}])
    rows = [{"security_id": "SEDOL:1", "date": _today(3), "headline": "Tender Offer", "url": "u1"}]
    ev = EV.build_events(rows, None)
    reg = pd.DataFrame([{"security_id": "SEDOL:1", "status": "live", "ticker": "ONE",
                         "liveness_reason": "nav_7d_old"}])
    irr = pd.DataFrame([{"security_id": "SEDOL:1", "irr_central": 0.18, "g_used": 0.04, "g_source": "panel_tr_cagr"}])
    got = EV.write_fund_files(live, ev, reg, irr, out_dir=tmp_path)
    assert got["fund_files"] == 1
    rec = json.loads((tmp_path / "SEDOL_1.json").read_text())
    assert rec["ticker"] == "ONE" and rec["discount"]["z"] == -1.9
    assert rec["forward_irr"]["central"] == 0.18
    assert rec["events_90d"][0]["class"] == "tender_offer"
    idx = json.loads((tmp_path / "index.json").read_text())
    assert idx["funds"] == 1


@pytest.mark.parametrize("headline,cls,direction", [
    ("Update on Offer for Subscription", "issuance", "neutral"),
    ("Intention to Launch an Offer for Subscription", "issuance", "neutral"),
    ("New combined offer for subscription", "issuance", "neutral"),
    ("Change in substantial holding for AOV", "portfolio_holding", "neutral"),
    ("Becoming a substantial holder for VYS", "portfolio_holding", "neutral"),
    ("Change in substantial holding from WAR", "substantial_holder", "positive"),
    ("Recommended Cash Offer for Augmentum Fintech plc", "takeover_offer", "positive"),
])
def test_issuance_and_portfolio_holdings_are_not_catalysts(headline, cls, direction):
    got = catalysts.classify_signed(headline)
    assert got["class"] == cls and got["direction"] == direction


def test_merge_reclassifies_held_rows(tmp_path):
    path = tmp_path / "EV.parquet"
    row = {c: None for c in EV.COLUMNS}
    row.update(security_id="SEDOL:263193", date="2026-09-04",
               headline="Update on Offer for Subscription", url="u",
               event_class="takeover_offer", weight=5, direction="positive",
               first_seen="2026-09-05T00:00:00+00:00")
    row["event_id"] = EV.event_id(row["security_id"], row["date"], row["headline"])
    EV.save(pd.DataFrame([row]), path)
    merged = EV.merge_events(pd.DataFrame(columns=EV.COLUMNS), path)
    assert merged.loc[0, "event_class"] == "issuance" and int(merged.loc[0, "weight"]) == 0
    assert merged.loc[0, "first_seen"] == "2026-09-05T00:00:00+00:00"


# ------------------------------------------------ realisations (Phase 3a layer 3)
@pytest.mark.parametrize("text,pct,basis", [
    ("The disposal was completed at a premium of 12.5% to the 31 March 2026 carrying value.", 12.5, "carrying value"),
    ("Proceeds represent a 7% premium to the last published valuation of the asset.", 7.0, "valuation"),
    ("The sale price represents a discount of approximately 15% to book value.", -15.0, "book value"),
    ("The investment was sold in line with its carrying value.", 0.0, "carrying value"),
    ("an uplift of 22% to the holding value at 30 June 2026", 22.0, "holding value"),
])
def test_realisation_terms_read_the_stated_premium_or_discount(text, pct, basis):
    got = EV.parse_realisation(text)
    assert got["vs_carrying_pct"] == pct and got["basis"] == basis


def test_a_body_without_a_carrying_value_comparison_yields_nothing():
    assert EV.parse_realisation("The Company has sold its stake in XYZ for £45 million.") == {}
    assert EV.parse_realisation("") == {}


@pytest.mark.parametrize("headline,cls", [
    ("Disposal of investment in Alpha Holdings", "realisation"),
    ("Completion of the sale of the Retirement Portfolio", "realisation"),
    ("Exit from Beta Ventures", "realisation"),
    ("Transaction in Own Shares", None),
])
def test_disposal_headlines_class_as_realisation(headline, cls):
    got = catalysts.classify_signed(headline)
    assert (got["class"] if got else None) == cls


def test_realisation_evidence_and_line():
    rows = []
    for i, (d, pct) in enumerate([("2026-03-01", 10.0), ("2026-06-01", 15.0), ("2026-08-20", -4.0)]):
        r = {c: None for c in EV.COLUMNS}
        r.update(security_id="F", date=d, headline=f"Disposal {i}", url=f"u{i}",
                 event_class="realisation", weight=1, direction="positive",
                 event_id=f"e{i}", terms=json.dumps({"realisation": {"vs_carrying_pct": pct, "basis": "carrying value"}}))
        rows.append(r)
    ev = pd.DataFrame(rows)
    got = EV.realisation_evidence(ev, "F")
    assert got["n"] == 3 and got["avg_vs_carrying_pct"] == 7.0 and got["last"]["vs_carrying_pct"] == -4.0
    line = EV.realisation_line(got)
    assert line.startswith("3 realisations in 12m at +7.0% to carrying value")
    assert EV.realisation_evidence(ev, "G") is None and EV.realisation_line(None) == ""


def test_enrich_realisations_reads_bodies_once():
    r = {c: None for c in EV.COLUMNS}
    r.update(security_id="F", date=datetime.now(timezone.utc).date().isoformat(),
             headline="Disposal of investment", url="u", event_class="realisation",
             weight=1, direction="positive", event_id="e")
    ev = pd.DataFrame([r])
    out, st = EV.enrich_realisations(ev, None, fetch=lambda u: "sold at a premium of 9% to carrying value")
    assert st == {"candidates": 1, "fetched": 1, "parsed": 1, "failed": 0}
    assert json.loads(out.loc[0, "terms"])["realisation"]["vs_carrying_pct"] == 9.0
    out2, st2 = EV.enrich_realisations(out, None, fetch=lambda u: "x")
    assert st2["candidates"] == 0


@pytest.mark.parametrize("text, pct, sign", [
    # VH Global Energy, 2026-09-02: a share of NAV
    ("The total consideration for the Assets will be at least R$38.4 million, representing 92% "
     "of the Assets' NAV as of 31 March 2026.", -8.0, -1),
    ("The sale, representing 92% of their net asset value as of March 31, 2026, completes the "
     "Brazilian exit.", -8.0, -1),
    # VH Global Energy, 2026-09-01: above NAV
    ("This transaction, part of the company's asset realization strategy, is expected to deliver "
     "approximately 105% of the asset's net asset value as of December 31, 2024.", 5.0, 1),
    # Gore Street, 2026-08-20: a floor, no figure
    ("The Company can confirm that it achieved no less than the values ascribed for these assets "
     "in the most recently published NAV following the sale.", 0.0, 0),
    ("The disposals, with independent valuation, achieved prices no less than the assets' recent "
     "Net Asset Value.", 0.0, 0),
    # INPP, 2026-08-19: a premium, no figure
    ("International Public Partnerships Limited has agreed to sell nine UK PPP projects for gross "
     "proceeds exceeding £58 million, representing a premium to its last published valuation.",
     None, 1),
])
def test_realisation_terms_read_the_wording_the_first_nightly_missed(text, pct, sign):
    got = EV.parse_realisation(text)
    assert got, text
    assert got["vs_carrying_pct"] == pct and got["sign"] == sign


def test_realisation_share_of_portfolio_is_not_a_share_of_value():
    assert EV.parse_realisation("INPP has realised over £440 million, approximately 17% of its "
                                "portfolio, since 2022.") == {}


def test_unparsed_rows_are_read_again_when_the_parser_changes():
    ev = pd.DataFrame([{"security_id": "S", "market": "UK", "date": "2026-09-01",
                        "headline": "Disposal of assets", "url": "u", "event_class": "realisation",
                        "terms": json.dumps({"unparsed": True, "parser": "r1"})},
                       {"security_id": "S", "market": "UK", "date": "2026-09-02",
                        "headline": "Disposal of more assets", "url": "u2", "event_class": "realisation",
                        "terms": json.dumps({"unparsed": True, "parser": EV.REALISATION_PARSER})}])
    calls = []
    out, stats = EV.enrich_realisations(ev, None, fetch=lambda u: calls.append(u) or
                                        "sold at a premium of 12% to carrying value")
    assert calls == ["u"] and stats["parsed"] == 1
    assert json.loads(out.iloc[0]["terms"])["realisation"]["vs_carrying_pct"] == 12.0


def test_unquantified_premiums_reach_the_evidence_and_the_line():
    ev = pd.DataFrame([{"security_id": "S", "market": "UK", "date": "2026-08-19",
                        "headline": "Disposal at a premium", "url": "u", "event_class": "realisation",
                        "terms": json.dumps({"realisation": {"vs_carrying_pct": None, "sign": 1,
                                                             "basis": "valuation"}})}])
    e = EV.realisation_evidence(ev, "S")
    assert e["n"] == 1 and e["n_quantified"] == 0 and e["premiums"] == 1
    line = EV.realisation_line(e)
    assert "1 at a premium" in line and "unquantified" in line and "premium)" in line
