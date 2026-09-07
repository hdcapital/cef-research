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
