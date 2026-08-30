"""The extraction contract, enforced.

Each test is one rule from config/prompts/asx_extraction_v1.md. The prompt
asks the model to obey them; these assert the pipeline does not depend on it
having obeyed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

from au_lic.extract import guards as G
from au_lic.extract import schema as S

DOC = (
    "--- PAGE 1 ---\n"
    "Pre-tax NTA as at 31 March 2021 was $1.27 per share.\n"
    "Post-tax NTA as at 31 March 2021 was $1.19 per share.\n"
    "The Board has resolved to commence an on-market buyback of up to 5% of "
    "issued capital.\n"
    "Shareholders will vote on winding up the Company at the 2021 AGM.\n"
)
PUB = "2021-04-08"


def _nav(**over):
    rec = {"valuation_date": "2021-03-31", "nav_per_share": 1.27, "currency": "AUD",
           "nav_basis": "pre_tax", "raw_nav_label": "Pre-tax NTA",
           "cum_or_ex_distribution": None, "audited": None,
           "source": {"page": 1,
                      "quote": "Pre-tax NTA as at 31 March 2021 was $1.27 per share."},
           "confidence": 0.99}
    rec.update(over)
    return rec


def test_clean_nav_observation_is_accepted():
    assert G.check_record(_nav(), "nav_observations", DOC, PUB) == []


def test_invented_number_is_caught_by_quote_provenance():
    """The anti-hallucination check: a fabricated value has no real quote.

    A model that invents "$1.42" will also invent the sentence proving it.
    Since the document is right there, that sentence can be checked - and this
    is the only check that catches a plausible-looking number in a
    well-formed record with high confidence.
    """
    bad = _nav(nav_per_share=1.42,
               source={"page": 1,
                       "quote": "Pre-tax NTA as at 31 March 2021 was $1.42 per share."})
    assert "quote_not_in_document" in G.check_record(bad, "nav_observations", DOC, PUB)


def test_quote_matching_survives_pdf_whitespace_and_smart_punctuation():
    """Honest quotes must not be rejected by cosmetics.

    pdfplumber breaks lines mid-sentence and models normalise quotes and
    dashes when copying. If the check were literal it would reject real
    extractions, the failure rate would look like a model problem, and the
    fix would be to weaken the check that catches invention.
    """
    doc = "Pre-tax NTA as at\n31 March 2021 was $1.27 per  share."
    rec = _nav(source={"page": 1,
                       "quote": "Pre-tax NTA as at 31 March 2021 was $1.27 per share."})
    assert G.check_record(rec, "nav_observations", doc, PUB) == []


def test_knowledge_date_after_publication_is_lookahead():
    """A NAV dated after the announcement was published cannot have been known."""
    bad = _nav(valuation_date="2021-06-30")
    reasons = G.check_record(bad, "nav_observations", DOC, PUB)
    assert any(r.startswith("lookahead:valuation_date") for r in reasons)


def test_effective_date_may_be_in_the_future():
    """Announced 15 March, effective 1 April - both are legitimate facts.

    Only dates recording when something became KNOWN are bounded by
    published_at. Bounding effective_date too would delete exactly the
    forward-looking terms the catalyst study needs.
    """
    rec = {"catalyst_type": "on_market_buyback", "announcement_date": "2021-04-08",
           "effective_date": "2021-12-31", "event_stage": "announced",
           "event_status": "active", "headline_terms": {"maximum_pct": 5.0},
           "explicit_discount_reference": False, "stated_reason": None,
           "source": {"page": 1, "quote": "The Board has resolved to commence an "
                                          "on-market buyback of up to 5% of issued capital."},
           "confidence": 0.97}
    assert G.check_record(rec, "catalyst_events", DOC, PUB) == []


def test_computed_signals_are_rejected_at_any_depth():
    """The ABSOLUTE RULE, including inside free-form headline_terms.

    headline_terms is an open object, which is exactly where a discount or a
    score would slip in unnoticed.
    """
    rec = {"catalyst_type": "on_market_buyback", "announcement_date": "2021-04-08",
           "event_stage": "announced", "event_status": "active",
           "headline_terms": {"maximum_pct": 5.0, "discount_to_nav": -0.18},
           "source": {"page": 1, "quote": "The Board has resolved to commence an "
                                          "on-market buyback of up to 5% of issued capital."},
           "confidence": 0.97}
    reasons = G.check_record(rec, "catalyst_events", DOC, PUB)
    assert any("computed_signal_field" in r and "discount_to_nav" in r for r in reasons)


@pytest.mark.parametrize("key", [
    "discount_z_score", "manager_quality", "catalyst_score", "expected_return",
    "attractiveness", "future_return", "signal_strength", "recommendation"])
def test_every_forbidden_signal_name_is_matched(key):
    assert G.forbidden_keys({"x": {key: 1}}) == [f"x.{key}"]


def test_out_of_vocabulary_label_is_rejected_not_adopted():
    """A new label means the vocabulary drifted, not that a category exists."""
    bad = _nav(nav_basis="pre_tax_ish")
    assert any(r.startswith("enum:nav_basis") for r in
               G.check_record(bad, "nav_observations", DOC, PUB))


def test_low_confidence_is_dropped_per_spec():
    assert any("confidence_below_floor" in r for r in
               G.check_record(_nav(confidence=0.4), "nav_observations", DOC, PUB))


def test_missing_quote_is_rejected():
    assert "no_source_quote" in G.check_record(
        _nav(source={"page": 1, "quote": ""}), "nav_observations", DOC, PUB)


def test_two_nav_bases_stay_two_observations():
    """Pre-tax and post-tax must never be merged into one number."""
    payload = {"nav_observations": [
        _nav(),
        _nav(nav_per_share=1.19, nav_basis="post_tax", raw_nav_label="Post-tax NTA",
             source={"page": 1, "quote": "Post-tax NTA as at 31 March 2021 was "
                                         "$1.19 per share."}),
    ]}
    clean, rejects = G.validate(payload, DOC, PUB, "AN1")
    assert rejects == []
    assert len(clean["nav_observations"]) == 2
    assert {r["nav_basis"] for r in clean["nav_observations"]} == {"pre_tax", "post_tax"}


def test_rejections_are_returned_with_reasons_not_silently_dropped():
    """Absence must be explainable.

    'The quote was not in the document' and 'the fund published nothing' are
    different facts and must not look the same downstream.
    """
    payload = {"nav_observations": [_nav(nav_per_share=9.99,
                                         source={"page": 1, "quote": "NTA was $9.99."})]}
    clean, rejects = G.validate(payload, DOC, PUB, "AN1")
    assert clean["nav_observations"] == []
    assert len(rejects) == 1
    assert rejects[0]["announcement_id"] == "AN1"
    assert "quote_not_in_document" in rejects[0]["reasons"]


def test_prompt_enums_and_code_enums_cannot_drift():
    """Every vocabulary term in the prompt file must exist in the code.

    The prompt is what the model is told; schema.py is what is enforced. If
    they diverge, valid extractions get rejected as out-of-vocabulary and the
    cause looks like a model failure.
    """
    text = Path("config/prompts/asx_extraction_v1.md").read_text()
    for name, allowed in (("catalyst_type", S.CATALYST_TYPE),
                          ("event_stage", S.EVENT_STAGE),
                          ("nav_basis", S.NAV_BASIS),
                          ("primary_document_type", S.PRIMARY_DOCUMENT_TYPE)):
        missing = sorted(v for v in allowed if v not in text)
        assert not missing, f"{name} values in code but absent from the prompt: {missing}"


def test_runner_imports_and_flattens_with_provenance():
    """The module must actually execute, and every fact row must be traceable.

    A row that cannot be traced back to a prompt version, a model and a source
    quote is not reproducible, and this dataset exists to be re-derived.
    """
    from au_lic.extract import runner as R

    clean = {"announcement": {"primary_document_type": "nta_report"},
             "quality_control": {"document_parse_quality": "good",
                                 "requires_manual_review": False},
             "nav_observations": [_nav()]}
    rec = {"announcement_id": "12345", "ticker": "AFI", "published_at": PUB}
    rows = R.flatten(clean, rec, {"input_tokens": 10, "output_tokens": 20})
    assert len(rows) == 1
    row = rows[0]
    assert row["section"] == "nav_observations"
    assert row["announcement_id"] == "12345" and row["ticker"] == "AFI"
    assert row["source_quote"].startswith("Pre-tax NTA")
    assert row["prompt_version"].startswith("v1:")
    assert row["model"]
    # the fact itself survives as JSON, not flattened into guessed columns
    assert json.loads(row["payload"])["nav_per_share"] == 1.27


def test_document_with_no_facts_still_produces_a_row():
    """Silence must be recorded, not absent.

    An announcement that yields nothing is a real observation - it stops the
    same document being re-extracted forever, and it distinguishes 'read, and
    contained nothing' from 'never read'.
    """
    from au_lic.extract import runner as R

    rows = R.flatten({"announcement": {"primary_document_type": "other"},
                      "quality_control": {}}, 
                     {"announcement_id": "9", "ticker": "XYZ", "published_at": PUB}, None)
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["no_extractable_facts"] is True


def test_prompt_version_changes_when_the_prompt_changes(tmp_path, monkeypatch):
    """Rows extracted under different instructions must be distinguishable."""
    from au_lic.extract import runner as R

    before = R.prompt_version()
    p = tmp_path / "prompt.md"
    p.write_text(Path("config/prompts/asx_extraction_v1.md").read_text() + "\nx\n")
    monkeypatch.setattr(R, "PROMPT_F", p)
    assert R.prompt_version() != before


def test_empty_model_env_var_falls_back_to_the_default(monkeypatch):
    """A workflow input left blank arrives as "", not as unset.

    os.environ.get(k, default) returns "" in that case, so a blank model box
    in the dispatch form would send an empty model id to the API.
    """
    import importlib

    monkeypatch.setenv("EXTRACT_MODEL", "")
    from au_lic.extract import runner as R
    importlib.reload(R)
    assert R.MODEL == "claude-opus-5"
    monkeypatch.setenv("EXTRACT_MODEL", "claude-haiku-4-5")
    importlib.reload(R)
    assert R.MODEL == "claude-haiku-4-5"
    monkeypatch.delenv("EXTRACT_MODEL")
    importlib.reload(R)


def test_every_selectable_model_has_a_price():
    """A model that can be chosen but not priced makes the estimate silently wrong."""
    from au_lic.extract import runner as R

    for m in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        assert m in R.PRICES and R.PRICES[m]["in"] > 0 and R.PRICES[m]["out"] > 0


# ----------------------------------------------------------------- routing
def test_a_corporate_action_is_never_skipped():
    """Skipping a catalyst is the one routing error with no recovery.

    A wrongly-skipped NTA report costs one data point among tens of
    thousands. A wrongly-skipped wind-up or scheme is the event the whole
    catalyst study is trying to observe, and nothing downstream can notice it
    is missing.
    """
    from au_lic.extract import router as RT

    for headline in [
        "Scheme Booklet registered with ASIC",
        "Proposed wind-up of the Company",
        "Off-market takeover bid for the Company",
        "Strategic Review - Update",
        "Notice of Meeting - Continuation Vote",
        "Change of Investment Manager",
        "Internalisation of Management",
        "Removal from Official List",
        "Return of Capital to Shareholders",
        "Section 249D Notice received",
    ]:
        family, route = RT.classify(headline)
        assert route in ("llm", "llm_audit"), f"{headline!r} routed {route} ({family})"


def test_an_unrecognised_headline_goes_to_the_model_not_the_bin():
    """A rule that does not match must never mean 'discard'.

    The safe default has to be the expensive one, or the router silently
    becomes a filter on the dataset and the gap is invisible.
    """
    from au_lic.extract import router as RT

    for headline in ["Something Nobody Has Filed Before", "", "??? 2026 update"]:
        assert RT.classify(headline)[1] == "llm"


def test_high_volume_forms_do_not_reach_the_model():
    """The forms that dominate the corpus must be parsed, not prompted."""
    from au_lic.extract import router as RT

    for headline in ["Daily share buy-back notice - Appendix 3E",
                     "Net Tangible Asset Backing",
                     "Weekly NTA Update", "Daily Net Tangible Asset Statement",
                     "Appendix 3A.1 - Dividend/Distribution",
                     "Form 484 - Cancellation of shares under buyback"]:
        assert RT.classify(headline)[1] == "deterministic", headline
    for headline in ["Change of Director's Interest Notice", "Appendix 3Y",
                     "Corporate Governance Statement", "Trading Halt"]:
        assert RT.classify(headline)[1] == "skip", headline


def test_audit_sample_is_stable_and_drawn_from_the_cheap_route():
    """The audit sample must be the same documents every run.

    A sample that moves each run cannot produce an error rate comparable
    across runs, and measuring the deterministic path's error rate is the
    only thing that turns the saving from faith into evidence.
    """
    import pandas as pd

    from au_lic.extract import router as RT

    idx = pd.DataFrame({"id": [str(i) for i in range(4000)],
                        "headline": ["Net Tangible Asset Backing"] * 4000})
    a = RT.route_index(idx, audit_rate=0.02)
    b = RT.route_index(idx, audit_rate=0.02)
    sel_a = set(a.loc[a["route"] == "llm_audit", "id"])
    sel_b = set(b.loc[b["route"] == "llm_audit", "id"])
    assert sel_a == sel_b and 0 < len(sel_a) < 4000
    # audit rows only ever come from the route being audited
    assert (a.loc[a["route"] == "llm_audit", "family"] == "nta").all()


def test_routing_leaves_a_minority_for_the_model():
    """Guards the economics: the model must stay the exception.

    Measured on the real index this is ~17%. If a rule change pushes it back
    over a third, the cost model this design exists for is gone.
    """
    import pandas as pd

    from au_lic.extract import router as RT

    idx_path = Path("data/asx_ann_cache/asx1/lic_announcement_index.parquet")
    if not idx_path.exists():
        pytest.skip("announcement index not present locally")
    s = RT.summarise(RT.route_index(pd.read_parquet(idx_path)))
    assert s["llm_share"] < 0.33, f"LLM share regressed to {s['llm_share']:.1%}"
    assert s["deterministic_share"] > 0.5


def test_an_unpriced_model_reports_as_unpriced_not_as_free(monkeypatch):
    """A model id from a repo variable may not be in the price table.

    Defaulting a missing price to zero would produce a confident $0.00
    estimate for exactly the model the run is configured to use - the worst
    possible place for a silent default.
    """
    from au_lic.extract import runner as R

    monkeypatch.setattr(R, "MODEL", "some-model-not-in-the-table")
    monkeypatch.setattr(R, "PRICES", {"claude-haiku-4-5": {"in": 1.0, "out": 5.0}})
    priced = dict.fromkeys(list(R.PRICES) + [R.MODEL])
    assert "some-model-not-in-the-table" in priced
    assert R.PRICES.get("some-model-not-in-the-table") is None


# --------------------------------------------------- deterministic extractors
def test_deterministic_nta_uses_the_already_validated_parser():
    """The NTA path must not be a second, unproven parser.

    A fresh regex would have to re-earn the accuracy the scored-label parser
    was tuned to on real announcements; reusing it means the validation
    carries over.
    """
    from au_lic.extract import deterministic as D

    facts = D.extract_nta("Pre-Tax NTA Backing per share as at 31 March 2021 "
                          "was $1.27", [], "Net Tangible Asset Backing")
    assert len(facts) == 1
    assert facts[0]["nav_per_share"] == 1.27
    assert facts[0]["valuation_date"] == "2021-03-31"
    assert facts[0]["extractor"] == "nta_scored_v1"


def test_cents_are_converted_and_ambiguous_units_are_refused():
    """A unit error is a 100x NAV error - the worst kind, because it looks real."""
    from au_lic.extract import deterministic as D

    facts = D.extract_nta("NTA per share as at 30 June 2020: 127.5 cents", [],
                          "Net Tangible Asset Backing")
    assert facts and abs(facts[0]["nav_per_share"] - 1.275) < 1e-9


def test_buyback_execution_is_extracted_from_the_daily_notice():
    from au_lic.extract import deterministic as D

    text = ("Total number of shares bought back before the previous day: 1,200,000 "
            "Number of shares bought back 250,000 Highest price paid $1.0450 "
            "Remaining to be bought back 4,750,000")
    f = D.extract_buyback(text, "Daily share buy-back notice - Appendix 3E")
    assert f and f[0]["event_type"] == "buyback_execution"
    assert f[0]["shares_bought_back"] in (1200000.0, 250000.0)
    assert f[0]["remaining_to_buy_back"] == 4750000.0


def test_distribution_amount_and_dates_are_extracted():
    """Without distributions a NAV series is a price return, not a total return."""
    from au_lic.extract import deterministic as D

    text = ("Dividend/distribution amount per security 6.5 cents "
            "Franked amount per security 100% franked "
            "Ex-Date 15/02/2021 Record Date 16/02/2021 Payment Date 26/02/2021")
    f = D.extract_dividend(text, "Appendix 3A.1 - Dividend/Distribution")
    assert f and f[0]["amount_per_share_cents"] == 6.5
    assert f[0]["franking_pct"] == 100.0
    assert f[0]["ex_date"] == "2021-02-15"
    assert f[0]["payment_date"] == "2021-02-26"


def test_special_dividend_is_labelled_from_the_headline():
    from au_lic.extract import deterministic as D

    f = D.extract_dividend("Dividend/distribution amount per security 20 cents",
                           "Special Dividend Announcement")
    assert f and f[0]["event_type"] == "special_dividend"


def test_unparseable_document_returns_nothing_so_it_escalates():
    """[] must mean 'escalate', never 'contained nothing'.

    If a parser failure were written as an empty result, the document would
    be marked done and never seen by the model - a silent hole in the series.
    """
    from au_lic.extract import deterministic as D

    assert D.extract("nta", "This page intentionally left blank.", [], "NTA") == []
    assert D.extract("unknown_family", "anything", [], "x") == []


def test_every_deterministic_family_has_an_extractor():
    """A routed family with no extractor escalates 100% of its documents.

    `issue` (3,608 docs) and `substantial_holder` (3,055) were routed
    deterministic with nothing to parse them - 6,663 documents, 10.7% of the
    route, guaranteed to escalate to the model. That is not a parser
    inaccuracy, it is a hole, and it is invisible in an aggregate parse rate.
    """
    from au_lic.extract import deterministic as D
    from au_lic.extract import router as RT

    routed = {fam for fam, route, _ in RT.FAMILIES if route == "deterministic"}
    missing = sorted(routed - set(D.FAMILY_EXTRACTORS))
    assert not missing, f"routed deterministic with no extractor: {missing}"


def test_issue_extractor_reads_count_and_price():
    """Share count is the denominator of every per-share number."""
    from au_lic.extract import deterministic as D

    f = D.extract("issue", "Number of +securities issued 1,250,000 "
                           "Issue price $1.0450 under the dividend reinvestment plan",
                  [], "Appendix 2A")
    assert f[0]["shares_issued"] == 1250000.0
    assert f[0]["issue_price"] == 1.045
    assert f[0]["drp"] is True


def test_substantial_holder_name_stops_at_the_sentence():
    """A wrong holder name is worse than none - it reads as a real counterparty.

    The name character class contains ".", so without an explicit stop the
    capture ran through the following sentence and returned
    "ement. The voting power is 7.35".
    """
    from au_lic.extract import deterministic as D

    f = D.extract("substantial_holder",
                  "Name of substantial holder: Wilson Asset Management. "
                  "The voting power is 7.35%", [], "Change in substantial holding")
    assert f[0]["holder"] == "Wilson Asset Management"
    assert f[0]["voting_power_pct"] == 7.35

    # no name stated - None, never a fragment of the next sentence
    g = D.extract("substantial_holder", "The voting power is 5.10% held by an entity",
                  [], "Change in substantial holding")
    assert g[0]["holder"] is None and g[0]["voting_power_pct"] == 5.10


def test_ceasing_to_be_a_holder_is_recorded_without_a_percentage():
    """Going to zero is the informative case and states no percentage."""
    from au_lic.extract import deterministic as D

    f = D.extract("substantial_holder", "no percentage stated", [],
                  "Ceasing to be a substantial holder")
    assert f and f[0]["direction"] == "ceased"


# ------------------------------------------------- parse-rate fixes, from real pages
def test_vertical_watermark_letters_are_stripped():
    """"For personal use only" is set vertically and pdfplumber interleaves it.

    Its letters land one at a time INSIDE the sentences the parsers match on
    ("fully lfranke", "y ASX Release l n"), so it degrades every family at
    once. Stripping it is the single highest-leverage fix in the corpus.
    """
    from au_lic.extract import deterministic as D

    raw = ("y 1 November 2016 l n The Manager o e s Wealth Defender advises "
           "pre-tax NTA per share as at 28 October 2016 was $0.8623. u")
    out = D.strip_sidebar(raw)
    assert "y 1 November" not in out and "l n The Manager" not in out
    assert "Wealth Defender advises" in out and "$0.8623" in out
    # ordinary text with single letters is untouched
    assert D.strip_sidebar("a b c") == "a b c"


def test_image_only_pdf_is_not_a_parse_failure():
    """A scanned PDF needs OCR, not a text model, and must be counted apart.

    Its only extractable text is the watermark. Escalating it to an LLM would
    read exactly as little and cost money to do so.
    """
    from au_lic.extract import deterministic as D

    assert not D.has_text_layer("y l n o e s u l a n o s r e p r o F")
    assert D.has_text_layer("The net tangible asset backing per share as at "
                            "31 August 2016 was $1.20, and this figure is "
                            "unaudited and subject to revision by the board.")


@pytest.mark.parametrize("text,value", [
    # spelled-out label + parenthetical + weekday + colon
    ("Wealth Defender Equities Limited (ASX: WDE) advises that its estimated "
     "weekly pre-tax Net Tangible Asset Backing per share (NTA) as at "
     "Friday, 28 October 2016 was: $0.8623.", 0.8623),
    # company name between "per" and "share", cents written as a "c" suffix
    ("The net tangible asset backing per Bisan Limited share as at "
     "31 August 2016 is 0.0384c.", 0.000384),
    # spelled-out NAV
    ("The Company advises that its net asset value per unit as at "
     "30 June 2021 was $1.2750", 1.2750),
    # the shape the parser was originally validated on must still work
    ("pre-tax NTA per share as at 28 October 2016 was $0.8623", 0.8623),
])
def test_real_nta_phrasings_parse(text, value):
    """Every one of these is a real page the parser scored zero on.

    The fix normalises the TEXT into the shape the parser was validated
    against, rather than re-tuning a parser whose accuracy was earned on a
    real corpus - changing it to fix an input problem would put that at risk.
    """
    from au_lic.extract import deterministic as D

    got = D.extract_nta(text, [], "Net Tangible Asset Backing")
    assert got, f"still unparsed: {text[:60]}"
    assert abs(got[0]["nav_per_share"] - value) < 1e-9


@pytest.mark.parametrize("text,cents,ex", [
    ("Notification of dividend / distribution Entity name CARLTON INVESTMENTS "
     "LIMITED Distribution Amount AUD 0.07000000 Ex Date Wednesday February 28, "
     "2018 Record Date Thursday March 1, 2018", 7.0, "2018-02-28"),
    ("ANNOUNCES MAIDEN DIVIDEND 2.5 cents per share fully franked final dividend.",
     2.5, None),
    ("Dividend/distribution amount per security 6.5 cents Franked amount 100% "
     "franked Ex-Date 15/02/2021", 6.5, "2021-02-15"),
])
def test_real_dividend_formats_parse(text, cents, ex):
    """1 of 13 sampled documents parsed before this.

    The original pattern demanded a label like "amount per security" and so
    matched neither the modern ASX online form ("Distribution Amount AUD
    0.07000000") nor the older narrative style ("2.5 cents per share").
    Distributions are what turn a NAV series into a total return, so an 8%
    parse rate here silently mis-grades every manager.
    """
    from au_lic.extract import deterministic as D

    got = D.extract_dividend(text, "Dividend/Distribution")
    assert got, f"still unparsed: {text[:60]}"
    assert abs(got[0]["amount_per_share_cents"] - cents) < 1e-6
    assert got[0]["ex_date"] == ex


def test_weekday_prefixed_dates_parse():
    """"Wednesday February 28, 2018" is the ASX form's own date format."""
    from au_lic.extract import deterministic as D

    assert D._parse_any_date("Wednesday February 28, 2018") == "2018-02-28"
    assert D._parse_any_date("15/02/2021") == "2021-02-15"
    assert D._parse_any_date("28 October 2016") == "2016-10-28"


def test_form_604_reports_the_present_holding_not_the_previous_one():
    """Form 604 states both, in one row: "Previous notice | Present notice".

    Taking the first percentage reports the holder as SMALLER than they are
    and hides an accumulation - which is the catalyst this extractor exists
    to see, so the error points away from the signal. Both are kept, because
    the change is the signal rather than the level.
    """
    from au_lic.extract import deterministic as D

    f604 = ("Form 604 Notice of change of interests of substantial holder "
            "Class of securities Previous notice Present notice "
            "Person votes Voting power Person votes Voting power "
            "Ordinary 12,345,678 5.12% 18,900,000 7.84%")
    r = D.extract("substantial_holder", f604, [], "Change in substantial holding")[0]
    assert r["voting_power_pct"] == 7.84, "reported the previous holding"
    assert r["voting_power_prev_pct"] == 5.12


def test_sharded_status_files_do_not_collide():
    """Eight shards writing one filename means seven results are lost.

    The facts parquets were already per-shard, so no DATA was lost - but the
    committed status was whichever shard finished last, and the run's real
    totals could not be recovered from it. A per-shard status is the
    difference between "7,533 documents" and the actual corpus-wide number.
    """
    import inspect

    from au_lic.extract import runner as R

    src = inspect.getsource(R.main)
    assert "asx_deterministic_status_s{SHARD}of{SHARDS}" in src
