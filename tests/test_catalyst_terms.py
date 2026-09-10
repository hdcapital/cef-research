"""Phase 3: the catalyst term extractor - guards, parsing, budget, calendar."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from cef_live import catalyst_terms as CT, events as EV

DOC = ("Tender Offer. The Board announces a tender offer for up to 15 per cent. of the "
       "Company's issued share capital at a price equal to the NAV per share less 2 per cent. "
       "The Record Date is 26 September 2026 and the Tender Offer will close at 1.00 p.m. on "
       "30 September 2026. Settlement is expected on 7 October 2026.")

GOOD = {"event_class": "tender_offer", "stage": "announced",
        "terms": {"size_pct_of_shares": 15, "price_basis": "NAV per share less 2 per cent."},
        "dates": [{"what": "record date", "date": "2026-09-26"},
                  {"what": "tender close", "date": "2026-09-30"},
                  {"what": "settlement", "date": "2026-10-07"}],
        "quote": "The Record Date is 26 September 2026 and the Tender Offer will close at 1.00 p.m. on 30 September 2026.",
        "confidence": 0.92}


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

        class _M:
            def __init__(s, outer):
                s.outer = outer

            def create(s, **params):
                s.outer.calls.append(params)
                p = s.outer.payloads.pop(0)
                text = p if isinstance(p, str) else json.dumps(p)
                return SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text=text)],
                                       usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                                             cache_read_input_tokens=900))
        self.messages = _M(self)


def test_guard_accepts_a_verbatim_quote_and_rejects_the_rest():
    assert CT.guard(GOOD, DOC) == []
    bad = dict(GOOD, quote="The Board expects the discount to narrow to 5%.")
    assert "quote_not_in_document" in CT.guard(bad, DOC)
    assert any(p.startswith("confidence_below_floor") for p in CT.guard(dict(GOOD, confidence=0.3), DOC))
    assert any(p.startswith("computed_signal_field") for p in
               CT.guard(dict(GOOD, terms={"expected_return_pct": 12}), DOC))
    assert any(p.startswith("bad_date") for p in
               CT.guard(dict(GOOD, dates=[{"what": "x", "date": "September 2026"}]), DOC))
    assert any(p.startswith("enum") for p in CT.guard(dict(GOOD, event_class="rumour"), DOC))


def test_parse_tolerates_a_code_fence_and_prose():
    assert CT.parse_response("```json\n" + json.dumps(GOOD) + "\n```")["event_class"] == "tender_offer"
    assert CT.parse_response("Here it is: " + json.dumps(GOOD))["stage"] == "announced"
    assert CT.parse_response("no json at all") is None


def test_request_puts_the_instruction_in_a_cached_system_block():
    p = CT.request_params("Tender Offer", "SEDOL:1", "2026-09-05", DOC)
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "document_text" in p["messages"][0]["content"] and "max_tokens" in p


def _events():
    today = datetime.now(timezone.utc).date()
    rows = [{"security_id": "SEDOL:1", "date": (today - timedelta(days=2)).isoformat(),
             "headline": "Tender Offer", "url": "https://x/tender"},
            {"security_id": "SEDOL:2", "date": (today - timedelta(days=3)).isoformat(),
             "headline": "Suspension - Two plc", "url": "https://x/susp"},
            {"security_id": "SEDOL:3", "date": (today - timedelta(days=1)).isoformat(),
             "headline": "Holding(s) in Company", "url": "https://x/tr1"}]
    return EV.build_events(rows, None)


def test_run_reads_within_budget_stores_verdicts_and_never_rereads():
    ev = _events()
    client = FakeClient([GOOD, {"event_class": "suspension", "confidence": 0.9,
                                "quote": "not in the document", "terms": {}, "dates": []}])
    texts = {"https://x/tender": DOC, "https://x/susp": "Trading in the shares is suspended pending clarification. " * 6}
    out, stats = CT.run(ev, None, budget_docs=5, client=client, fetch=lambda u: texts.get(u, ""))
    assert stats["candidates"] == 2          # the holdings row is weight 0
    assert stats["accepted"] == 1 and stats["rejected"] == 1 and len(client.calls) == 2
    t = json.loads(out.set_index("security_id").loc["SEDOL:1", "terms"])
    assert t["llm"] == "accepted" and t["terms"]["size_pct_of_shares"] == 15
    r = json.loads(out.set_index("security_id").loc["SEDOL:2", "terms"])
    assert r["llm"] == "rejected" and "quote_not_in_document" in r["rejected"]
    # nothing is read twice
    out2, stats2 = CT.run(out, None, budget_docs=5, client=client, fetch=lambda u: texts.get(u, ""))
    assert stats2["candidates"] == 0 and len(client.calls) == 2
    # budget respected
    ev3 = _events()
    c3 = FakeClient([GOOD, GOOD])
    _, s3 = CT.run(ev3, None, budget_docs=1, client=c3, fetch=lambda u: texts.get(u, DOC))
    assert s3["read"] == 1 and len(c3.calls) == 1


def test_calendar_and_summary_come_from_accepted_terms_only():
    ev = _events()
    client = FakeClient([GOOD, {"event_class": "suspension", "confidence": 0.9,
                                "quote": "nope", "terms": {}, "dates": [{"what": "x", "date": "2099-01-01"}]}])
    texts = {"https://x/tender": DOC, "https://x/susp": "Trading suspended. " * 20}
    out, _ = CT.run(ev, None, budget_docs=5, client=client, fetch=lambda u: texts.get(u, ""))
    cal = CT.calendar(out, horizon_days=365 * 5)
    assert list(cal["what"]) == ["record date", "tender close", "settlement"]
    assert set(cal["security_id"]) == {"SEDOL:1"}
    line = CT.terms_summary(out.set_index("security_id").loc["SEDOL:1", "terms"])
    assert "15% of shares" in line and "record date 2026-09-26" in line
    assert CT.terms_summary(out.set_index("security_id").loc["SEDOL:2", "terms"]) == ""


def test_call_errors_are_retried_and_abort_after_three(monkeypatch):
    """An API error is not a verdict on the document: the row stays a
    candidate, and three in a row stop the run before the budget burns."""
    import pandas as pd
    from cef_live import catalyst_terms as CT, events as EV

    class Boom:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("Error code: 400 - workspace header required")

    rows = []
    for i in range(6):
        r = {c: None for c in EV.COLUMNS}
        r.update(security_id=f"S{i}", date="2026-09-01", headline="Tender Offer",
                 url=f"http://x/{i}", event_class="tender_offer", weight=4,
                 direction="positive", event_id=f"e{i}")
        rows.append(r)
    ev = pd.DataFrame(rows)
    ev.loc[0, "terms"] = '{"llm": "error", "error": "old"}'      # retried
    ev.loc[1, "terms"] = '{"llm": "rejected"}'                    # not retried
    assert set(CT.candidates(ev)["security_id"]) == {"S0", "S2", "S3", "S4", "S5"}
    out, stats = CT.run(ev, None, budget_docs=40, client=Boom(),
                        fetch=lambda u: "x" * 500)
    assert stats["errors"] == 3 and "aborted" in stats
    assert stats["rejected"] == 0
    assert sum(CT._is_error_terms(t) for t in out["terms"]) == 3


def test_every_term_the_prompt_asks_for_passes_the_guard():
    """The first keyed nightly rejected all 20 documents it read: the prompt
    asked for expected_return_pct_of_nav and the guard forbids any key with
    'expected_return' in it. The prompt's own vocabulary must be storable."""
    text = CT.prompt_text()
    block = text[text.index('"terms": {'):text.index('"dates"')]
    keys = re.findall(r'"([a-z_]+)":', block)
    assert len(keys) >= 8
    rec = dict(GOOD, terms={k: 1 for k in keys if k != "terms"})
    assert [p for p in CT.guard(rec, DOC) if p.startswith("computed_signal_field")] == []


def test_a_rejection_under_an_older_prompt_is_read_again():
    old = json.dumps({"llm": "rejected", "prompt_version": "v1:0000000000000000",
                      "rejected": ["computed_signal_field:x"]})
    cur = json.dumps({"llm": "rejected", "prompt_version": CT.prompt_version(),
                      "rejected": ["quote_not_in_document"]})
    err = json.dumps({"llm": "error", "error": "boom"})
    assert CT._is_error_terms(old) and CT._is_error_terms(err) and not CT._is_error_terms(cur)
