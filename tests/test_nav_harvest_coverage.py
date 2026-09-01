"""The two things that decide how much NAV coverage we actually get.

Reach - do we ask the right funds, for the right documents, over the right
window? - and parse - does a value come out of the document we fetched?
Both were losing most of the universe for reasons that had nothing to do
with the funds, and both are measured here against real committed data
rather than against a synthetic example.
"""

from __future__ import annotations

import gzip
import inspect
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cef_live import harvest_nav as H

CORPUS = Path("data/uk_nav_corpus.json.gz")


def _corpus() -> list[dict]:
    if not CORPUS.exists():
        pytest.skip("UK NAV corpus not present")
    return json.loads(gzip.decompress(CORPUS.read_bytes()))


# ------------------------------------------------------------ UK parse rate

def test_the_uk_parser_reads_at_least_95_percent_of_the_real_corpus():
    """It read 136 of 175. The 39 it missed were not obscure funds.

    Fidelity, CQS, Columbia Threadneedle, Personal Assets, Law Debenture,
    Temple Bar, Templeton, Witan and Montanaro all publish layouts the rule
    list had never been shown. Their funds sat at a 2-26% parse rate and
    the coverage audit reported them, correctly, as parser failures.
    """
    docs = _corpus()
    parsed = [d for d in docs if "nav_cum_pence" in H.parse_uk_nav_text(d["text"])]
    rate = len(parsed) / len(docs)
    assert rate >= 0.95, (
        f"UK NAV parse rate fell to {rate:.1%} ({len(parsed)}/{len(docs)}); "
        "the corpus is the evidence the rule list is written against")


def test_the_families_that_used_to_fail_now_parse_to_the_published_figure():
    """Named funds, real numbers, taken from the announcements themselves."""
    docs = {d["ticker"]: d["text"] for d in _corpus()}
    expected = {
        "FCSS": 281.80,    # Fidelity daily: "was: 281.80p"
        "CHI": 121.04,     # CT table: "Cum Income Ex Income ... 121.04 119.84"
        "CMPG": 336.94,    # CT table with an empty ex column
        "CYN": 421.52,     # CQS prose: "was 421.52 pence, including ..."
        "PNL": 555.50,     # Juniper: "cum-income net asset values ... is:"
        "MTU": 118.05,     # same layout; the archive had recorded 2.0p
        "LWDB": 1232.92,   # ex column FIRST - header order is matched
        "TMPL": 417.39,    # cum column first, fair value beats par
        "WTAN": 283.40,    # header split by "Pence per share"
        "TEM": 346.36,     # "representing a NAV of 346.36 pence per share"
        "GOT": 407.52,     # "Including income: 407.52 pence per share"
        "STS": 234.33,
        "INV": 78.5,       # "Per Ordinary Share: 78.5p"
        "SDV": 155.92,     # "Per Ordinary share (Last price) - including ..."
        "GPM": 130.62,     # "NAV per share - undiluted, bid basis"
        "UKW": 136.1,      # "(136.1 pence per share)"
    }
    for ticker, want in expected.items():
        if ticker not in docs:
            continue
        got = H.parse_uk_nav_text(docs[ticker])
        assert got.get("nav_cum_pence") == pytest.approx(want), (
            f"{ticker}: expected {want}, got {got.get('nav_cum_pence')}")


def test_the_cum_income_figure_is_taken_not_the_capital_only_one():
    """Allianz Technology and Mid-Wynd were reporting their EX-income NAV.

    Both publish "capital only" first and "cum income" second; the rule
    list matched the first pence figure it saw. The discount was computed
    against the wrong basis, quietly, for every fund with that layout.
    """
    docs = {d["ticker"]: d["text"] for d in _corpus()}
    for ticker, cum, ex in (("ATT", 741.53, 742.94), ("MWY", 810.76, 810.14)):
        if ticker not in docs:
            continue
        got = H.parse_uk_nav_text(docs[ticker])
        assert got.get("nav_cum_pence") == pytest.approx(cum)
        assert got.get("nav_ex_pence") == pytest.approx(ex)


def test_a_shares_nominal_value_is_never_read_as_its_nav():
    """"per Ordinary Share of 1p" is company law, not a valuation.

    The plain fallback took it for Chelverton Growth and recorded a 1p NAV
    against a 30p share price - a 2,900% premium produced by a formality
    sitting one clause before the number the rule wanted.
    """
    text = ("CHELVERTON GROWTH TRUST PLC NET ASSET VALUE The Net Asset Value, "
            "based on bid prices, per Ordinary Share of 1p (including current "
            "period revenue to 31 October 2023) at close of business on "
            "31 October 2023 was: Per Ordinary Share 55.03p "
            "Ordinary Share price 29.50p")
    got = H.parse_uk_nav_text(text)
    assert got.get("nav_cum_pence") == pytest.approx(55.03)


def test_a_foreign_currency_nav_is_refused_rather_than_read_as_pence():
    """A euro or dollar NAV in a pence column is the unit bug all over again.

    BH Macro, CVC and Greencoat Renewables publish in USD/EUR next to a
    sterling class. Absence is the correct answer; a number is not.
    """
    text = ("Greencoat Renewables PLC reported an unaudited Net Asset Value of "
            "EUR1,055 million, or 97.2c per share, as of June 30, 2026, and "
            "announced a Q2 dividend of EUR18.5 million, or 1.70250c per share.")
    got = H.parse_uk_nav_text(text)
    assert "nav_cum_pence" not in got, (
        "a cents figure was read into the pence field")


def test_the_parser_is_deterministic():
    docs = _corpus()[:40]
    a = [H.parse_uk_nav_text(d["text"]) for d in docs]
    b = [H.parse_uk_nav_text(d["text"]) for d in docs]
    assert a == b


# ------------------------------------------------- a parser fix must land

def test_a_parser_fix_can_reach_announcements_already_recorded_as_failures():
    """The crawl skips anything in the manifest - right for fetching, wrong
    for parsing.

    An announcement recorded `no_nav_parsed` would never be looked at
    again, so improving the rule list could not reach the funds it was
    improved for. The archived payload keeps the announcement TEXT, so a
    re-parse needs no crawling: it reads the objects back and re-runs the
    current parser.
    """
    src = Path("scripts/archive_uk_navs.py").read_text()
    assert "def reparse(" in src
    assert 'UK_NAV_MODE' in src and '--reparse' in src
    # it must read the archive, never re-fetch pages
    body = src[src.index("def reparse("):src.index("if __name__")]
    assert "get_object" in body and "uk/nav_announcements/" in body
    assert "requests" not in body and "sess.get" not in body, (
        "re-parse must not fetch anything - the text is already archived")
    assert "parse_uk_nav_text(text)" in body

    import yaml
    wf = yaml.safe_load(Path(".github/workflows/uk_archive.yml").read_text())
    trig = wf[True] if True in wf else wf["on"]
    assert "reparse" in str(trig["workflow_dispatch"]["inputs"]["mode"])


# ------------------------------------------------------------- ASX reach

def test_the_asx_harvest_looks_back_far_enough_for_a_monthly_publisher():
    """A 14-day window saw an NTA for 40 of 108 funds; 30 days sees 79.

    Many LICs publish an NTA monthly. Measured on the live announcement
    index, 45 days adds no funds beyond 30 but tolerates a late filing.
    """
    assert inspect.signature(H.harvest_au).parameters["lookback_days"].default >= 30


def test_the_asx_harvest_reaches_the_families_that_never_say_NTA():
    """Metrics publishes its NTA in a "Daily Fund Update"; the WAM and
    Future Generation funds publish theirs in a "Monthly Report" or
    "Investment Update". The narrow NTA headline pattern reached 79 of the
    108 monitored funds; these twelve were the difference.
    """
    for head in ("Net Tangible Asset Backing", "Daily Fund Update",
                 "July 2026 Monthly Report", "July 2026 Investment Update",
                 "Weekly NTA Update", "Daily Net Tangible Asset Statement"):
        assert H.AU_NAV_HEAD.search(head), f"{head!r} would not be fetched"
    for head in ("Update - Notification of buy-back - AFI", "Appendix 4G",
                 "Change of Director's Interest Notice"):
        assert not H.AU_NAV_HEAD.search(head), f"{head!r} is not an NTA document"


def test_the_asx_harvest_spends_its_budget_on_funds_not_on_repeats():
    """It re-parsed the same fund's daily statement for every day in the
    window, so a 200-document budget bought 7 funds' back-catalogue.

    Only the NEWEST value per fund is wanted, so a fund leaves the queue
    as soon as one document parses.
    """
    src = inspect.getsource(H.harvest_au)
    assert "if code in done:" in src and "done.add(code)" in src
    assert not inspect.signature(H.harvest_au).parameters["pdf_budget"].default, (
        "a default item cap truncates the addressable universe")
    assert "deadline_min" in inspect.signature(H.harvest_au).parameters


def test_the_asx_harvest_states_the_unit_and_does_not_convert_twice():
    """It divided cents by 100 AND labelled the row "cents".

    units.normalise then divides a cents-labelled value by 100 again, so
    the fix is to return the value AS STATED and let the one normaliser do
    the one conversion.
    """
    src = inspect.getsource(H.harvest_au)
    assert "val / 100.0 if unit ==" not in src, (
        "the harvester converts, and the normaliser will convert again")
    assert '"unit": "dollars"' in src, (
        "the emitted unit must describe the value as emitted, so the next "
        "normaliser is a no-op rather than a second conversion")

    from cef_live import units as U
    # the contract the harvester now relies on
    assert U.normalise("AU", 250.0, "cents")[0] == pytest.approx(2.5)
    assert U.normalise("AU", 2.5, "dollars")[0] == pytest.approx(2.5)


def test_the_extracted_asx_facts_are_restored_from_s3_when_the_cache_is_empty():
    """26,274 extracted NAV observations sat in S3 while the live table
    said the funds had no NAV route of their own.

    `_own_nav_history("AU")` reads data/asx_extract, the nightly restored
    every other state group but not that one, and a fresh runner therefore
    always had an empty directory - so every ASX fund fell back to the
    aggregator's monthly print.
    """
    from au_lic.extract import facts as AUF
    from cef_live import cli

    assert AUF.S3_PREFIX == "asx/extract/facts_det_"
    assert "fetch_from_s3" in inspect.getsource(AUF.load)
    assert "facts" in inspect.getsource(cli._own_nav_history), (
        "the AU branch must go through the shared loader")
    assert '"nav_unit": "AUD"' in inspect.getsource(AUF.nav_observations)

    # a missing bucket is a reported gap, never an exception
    assert AUF.fetch_from_s3(Path("/tmp/_no_such_dir_facts"), bucket="") == 0
    assert not len(AUF.nav_observations(Path("/tmp/_no_such_dir_facts"),
                                        allow_s3=False))


def test_one_loader_decides_where_the_extracted_facts_live():
    """Two copies of "download the shards from S3" is two places to be wrong."""
    from au_lic.extract import runner
    src = inspect.getsource(runner.run_validate)
    assert "AUF.load()" in src
    assert "asx/extract/facts_det_" not in src, (
        "run_validate keeps its own copy of the S3 path")


# ---------------------------------------------- the two funds that prompted this

def test_a_monthly_uk_publisher_is_inside_the_tier_0_window():
    """Achilles published a NAV on 7 August and was invisible on the 30th.

    A 7-day window can only ever see a fund that publishes at least weekly.
    215 of 368 targets came back `no_recent_nav` on the last run, and for
    most of them the reason was their publication cadence, not silence -
    monthly and quarterly publishers are most of the offshore, property and
    infrastructure cohort this harvester exists to reach.
    """
    look = inspect.signature(H.harvest_uk).parameters["lookback_days"].default
    assert look >= 35, (
        f"a {look}-day Tier 0 window cannot see a monthly NAV publisher")


def test_the_listing_page_we_fetched_is_written_back_to_the_archive_queue(tmp_path):
    """A fund with no listing index can never have its NAV archived.

    The archive job's queue IS data/investegate_cache/listings. Achilles
    listed in 2025, after the historical dividends crawl that built that
    cache, so it had no listing file, therefore no archived announcements,
    therefore no NAV history - for want of a file whose contents the Tier 0
    harvest was already downloading every night.
    """
    assert "write_listing_cache" in inspect.signature(H.harvest_uk).parameters
    src = inspect.getsource(H.harvest_uk)
    assert "listing_rows.setdefault" in src, (
        "the listing rows must be collected as the page is parsed")

    cache = tmp_path / "listings"
    n = H._write_listing_cache({"AIC": [
        {"ann_id": "9740001", "date": "2026-08-07",
         "headline": "Net Asset Value(s)", "url": "https://x/9740001"}]}, cache)
    assert n == 1
    got = pd.read_csv(cache / "AIC.csv", dtype=str)
    assert set(got.columns) >= {"ann_id", "date", "headline", "url"}, (
        "the archive job reads exactly these columns")
    assert got["ann_id"].tolist() == ["9740001"]

    # a second pass ADDS; it never truncates the deep history a full crawl built
    H._write_listing_cache({"AIC": [
        {"ann_id": "9750002", "date": "2026-08-28",
         "headline": "Net Asset Value(s)", "url": "https://x/9750002"}]}, cache)
    got = pd.read_csv(cache / "AIC.csv", dtype=str)
    assert set(got["ann_id"]) == {"9740001", "9750002"}


def test_an_asx_nta_published_as_portfolio_performance_is_still_fetched():
    """UWC's July NTA was indexed on 12 August and never fetched.

    "UWC Investment Portfolio Performance July 2026" contains none of NTA,
    net tangible, net asset, NAV or fund update - so the fund's monthly NAV
    sat in the announcement index, with a working PDF link, entirely
    invisible. Australian Leaders publishes under the same shape.
    """
    for head in ("UWC Investment Portfolio Performance July 2026",
                 "Monthly Portfolio Performance Update - July 2026",
                 "Portfolio Update - July 2026",
                 "Quarterly Portfolio Disclosure as at 30 June 2026"):
        assert H.AU_NAV_HEAD.search(head), f"{head!r} would not be fetched"
    for head in ("Update - Notification of buy-back - UWC",
                 "FY26 UWC Appendix 4E and Annual Report",
                 "Change of Director's Interest Notice"):
        assert not H.AU_NAV_HEAD.search(head)


def test_liveness_and_the_harvester_share_one_nta_headline_pattern():
    """Two lists of "headlines that carry an NTA" would drift, and the fund
    in the gap would be live-but-unpriceable for a reason no column explains.
    """
    from cef_live import cli
    assert cli.NAV_HEAD is H.AU_NAV_HEAD


def test_a_reparsed_nav_reaches_the_live_table_and_the_audit(monkeypatch, tmp_path):
    """A NAV recovered by a re-parse must stop being a parser failure.

    `uk-daily --stages nav --reparse-unparsed` re-reads the announcements
    stored as `no_nav_parsed` and writes what today's rules extract into
    data/uk/nav. Reading only the legacy shards meant a fund could be fixed
    in one store and still RED in the audit, with a parse failure reported
    against an announcement that had just been parsed.
    """
    import pandas as pd

    from cef_live import cli, coverage_audit as CA
    from cef_live import uk_nav_panel as UKP

    # A TICKER THE REAL SHARDS CANNOT CONTAIN. uk_nav_archive_facts also
    # reads the committed data/uk_nav_history*.parquet, so a fixture keyed
    # on a real ticker silently unions with whatever the archive last
    # collected for that fund - this test used "PNL" and started failing
    # the night the archiver picked up a newer Personal Assets
    # announcement. The assertion was about the fixture; the data was not.
    panel = pd.DataFrame([{
        "ticker": "ZZFIXTURE", "ann_id": "fixture-1",
        "published_at": pd.Timestamp("2026-08-27"),
        "nav_date": pd.Timestamp("2026-08-26"), "nav_pence": 555.50,
        "nav_ex_pence": None, "cum_assumed": False,
        "nav_source": "archive", "quality": "ok"}])
    monkeypatch.setattr(UKP, "read_panel", lambda *a, **k: panel)
    monkeypatch.setattr(CA, "load_tickers", lambda: pd.DataFrame(
        [{"security_id": "SEDOL:FIX1", "ticker": "ZZFIXTURE",
          "ticker_status": "verified"}]))

    facts = CA.uk_nav_archive_facts().set_index("security_id")
    assert "SEDOL:FIX1" in facts.index
    row = facts.loc["SEDOL:FIX1"]
    assert row["last_parsed_nav_date"] == "2026-08-27", (
        "a re-parsed announcement must count as parsed")

    # and the value itself must reach the live table's NAV anchor
    src = inspect.getsource(cli._own_nav_history)
    assert "uk_nav_panel" in src and "nav_pence" in src
    assert '"nav_unit": "GBX"' in src


def test_an_asx_nav_the_extractor_read_is_not_reported_as_a_parser_failure():
    """46 ASX funds read "announcement held, never parsed" while the
    deterministic extractor had in fact read them.

    The audit's ASX branch took `last_parsed` from Tier 0 alone, so a fund
    whose NAV came from data/asx_extract counted as unparsed. That is a
    parser failure claimed against a parser that worked, and it would have
    sent someone to fix it.
    """
    from cef_live import coverage_audit as CA
    src = inspect.getsource(CA.build_rows)
    i = src.index('last_ann = a.get("last_nta_announcement")')
    tail = src[i:i + 1400]
    assert 'o.get("own_nav_date")' in tail, (
        "the extracted-facts date must count as a parsed NAV for ASX")
    assert 'last_parsed = max(' in tail


# ------------------------------------------ ASX: one extractor, one conversion

def test_the_asx_harvest_uses_the_same_extractor_as_the_archive():
    """Two parsers for one corpus is two things to keep in step, and the
    one with the lower hit rate was feeding the live table.

    The archive's deterministic pass reads ~72% of the documents it is
    given; this harvest read far fewer of the SAME documents, because it
    called the scored parser directly and skipped what the extractor does
    first: normalise_nta_text, which repairs the run-together text
    pdfplumber produces for these layouts, and a retry on the raw text.
    """
    src = inspect.getsource(H._nta_from_document)
    assert "extract_nta" in src and "deterministic" in src
    assert "_nta_from_document(" in inspect.getsource(H.harvest_au), (
        "the harvest must delegate to the shared extractor, not parse inline")
    assert "P.derive_stated(P.parse_pdf(" not in inspect.getsource(H.harvest_au)

    doc = {"status": "extracted", "rows": [],
           "text": "Net Tangible Asset Backing The pre-tax NTA per share as "
                   "at 31 July 2026 was $2.0590 per share."}
    got = H._nta_from_document(doc, "Net Tangible Asset Backing")
    assert got["nav_per_share"] == pytest.approx(2.059)
    assert got["unit_source"] == "dollars"
    assert got["valuation_date"] == "2026-07-31", "the as-at date comes from the document"
    assert got["extractor"]


def test_a_cents_nta_is_reduced_once_and_the_source_unit_is_kept():
    """It used to be converted here AND labelled "cents", so the next
    normaliser divided by 100 a second time."""
    doc = {"status": "extracted", "rows": [],
           "text": "NTA per unit 125.10 cents as at 21 August 2026"}
    got = H._nta_from_document(doc, "Monthly NTA")
    assert got["nav_per_share"] == pytest.approx(1.251), "cents -> dollars, once"
    assert got["unit_source"] == "cents", "the unit it was STATED in is kept"

    src = inspect.getsource(H.harvest_au)
    assert '"unit": "dollars"' in src, "the emitted unit must describe the value"
    assert "unit_source=" in src, "provenance of the source unit is recorded"


def test_a_document_that_yields_nothing_yields_nothing():
    assert H._nta_from_document(
        {"status": "extracted", "text": "no valuation here", "rows": []}, "x") is None


# ----------------------------------- UK: template families, and their guards

def test_the_was_n_pence_family_parses_across_issuers():
    """The largest remaining cluster, and all of it defeated the fallback
    for one reason: `net asset value[^0-9]{0,220}?` cannot cross the DATE
    between the label and the number.
    """
    cases = [
        ("Estimated NAV at 31 March 2026 was 864.9 pence per share", 864.9),
        ("its unaudited net asset value (\"NAV\") per share at 28 February "
         "2026 was 177.47 pence (31 January 2026: 175.35 pence per share)", 177.47),
        ("The Company announces its Net Asset Value per ordinary share as at "
         "28 August 2026 was estimated to be 199.66 pence.", 199.66),
        ("This results in a NAV per Ordinary Share of 111.0 pence", 111.0),
        ("The NAV for SEQI increased to 101.66p from the prior month's NAV "
         "of 101.10p per share", 101.66),
        ("unaudited NAV as at 30 June 2016 was GBP252.9 million or 97.1 pence "
         "per share", 97.1),
        ("Net Asset Value (pence): 261.09", 261.09),
    ]
    for text, want in cases:
        got = H.parse_uk_nav_text(text)
        assert got.get("nav_cum_pence") == pytest.approx(want), (
            f"{text[:60]!r} -> {got.get('nav_cum_pence')}, expected {want}")


def test_a_number_beside_a_dividend_is_not_a_nav():
    """Gore Street came back at 1.9p and NextEnergy at 1.79p - both the
    quarterly distribution, against real NAVs near 100p. The looser rules
    reached them because the arithmetic ABOUT a NAV sits one clause away
    from the NAV.
    """
    text = ("As at 30 September 2019, the estimated NAV increased to 95.5 "
            "pence per share, representing an uplift of 1.9 pence per share")
    assert H.parse_uk_nav_text(text).get("nav_cum_pence") == pytest.approx(95.5)

    text2 = ("SEQI's NAV increased to 100.81 per share from 100.11 per share "
             "which arose primarily through: Interest income net of expenses "
             "of 0.42p; an increase of 0.08p in ...")
    got = H.parse_uk_nav_text(text2).get("nav_cum_pence")
    assert got is None or got > 20, f"read an expense line as a NAV: {got}"


def test_a_document_that_declares_a_foreign_per_share_unit_is_refused():
    """Schiehallion heads its table "(US cents per ordinary share)" and
    prints "Cum NAV* 161.54cents", while separately carrying a legend
    explaining what a pence NAV would mean. Reading anything out of that as
    pence is the unit bug in its most direct form.
    """
    text = ("The Schiehallion Fund Limited (MNTN) Net Asset Value as at close "
            "of business on 31 October 2025 (US cents per ordinary share) "
            "Cum NAV* 161.54cents Ex NAV 162.38cents. Cum Par NAV: Net asset "
            "value per share in pence, including income, with debt at par value.")
    got = H.parse_uk_nav_text(text)
    assert got.get("unit_declared_foreign") is True
    # the value may still be READ - by the currency rules, which record the
    # unit it is in. What must never happen is a foreign figure entering the
    # PENCE field unlabelled.
    assert got.get("nav_ccy") not in (None, "GBX"), (
        f"a US-cents NAV was recorded as sterling: {got}")


def test_the_extra_guards_apply_only_to_the_loose_rules():
    """Applying them to the precise rules cost 37 correctly-parsed funds:
    "ex-dividend" and "cents" appear in the ordinary prose of a perfectly
    good sterling NAV announcement."""
    src = inspect.getsource(H.parse_uk_nav_text)
    assert "loose=prio >= 4" in src
    # a labelled row survives a nearby dividend mention
    text = ("Pence per share Cum Income Ex Income CT UK High Income Trust PLC "
            "LEI: 213800B7D5D7RVZZPV45 121.04 119.84 ex-dividend")
    assert H.parse_uk_nav_text(text).get("nav_cum_pence") == pytest.approx(121.04)


def test_the_uk_parser_recovers_most_of_the_recorded_failures_plausibly():
    """Measured against the archive's own committed failure samples.

    Coverage is only half of it: every value that survives has to be a
    plausible NAV. An earlier pass reached 61% by also reading expense
    lines, dividends and a US-cents NAV, which is worse than reading none.
    """
    import glob
    samples = []
    for f in sorted(glob.glob("reports/build/uk_nav_parse_failures_s*.json")):
        j = json.loads(Path(f).read_text())
        for k in ("samples", "fail_samples", "failures"):
            if isinstance(j, dict) and k in j:
                samples += j[k]
    if len(samples) < 100:
        pytest.skip("failure samples not present")
    got = [H.parse_uk_nav_text(s.get("text_head") or "") for s in samples]
    vals = [g["nav_cum_pence"] for g in got if "nav_cum_pence" in g]
    assert len(vals) / len(samples) >= 0.45, (
        f"recovery fell to {len(vals) / len(samples):.1%} of recorded failures")
    # plausibility applies to the STERLING ones: a foreign NAV is recorded in
    # its own currency and is not a pence figure to sanity-check
    pence = [g["nav_cum_pence"] for g in got
             if "nav_cum_pence" in g and g.get("nav_ccy", "GBX") == "GBX"]
    assert min(pence) >= 20.0, (
        f"a recovered sterling NAV of {min(pence)}p is not a UK trust's NAV")
    assert max(pence) <= 100_000.0


def test_one_asx_nta_headline_pattern_serves_every_reader():
    """The audit kept a THIRD copy, and it drifted the moment the
    harvester's was widened.

    Underwood Capital publishes its monthly NTA as "UWC Investment
    Portfolio Performance July 2026". The harvester and the liveness
    classifier both recognise it - UWC's own audit row reads
    `nav_19d_old` - while the audit's narrower copy reported "no NAV
    announcement held" for the same fund on the same day. The report
    contradicted itself and pointed away from the real problem, which is
    that the PDF behind that announcement does not parse.
    """
    from cef_live import cli, coverage_audit as CA
    assert CA.ASX_NTA is H.AU_NAV_HEAD
    assert cli.NAV_HEAD is H.AU_NAV_HEAD
    assert CA.ASX_NTA.search("UWC Investment Portfolio Performance July 2026")


def test_an_unreadable_asx_document_is_recorded_with_enough_to_fix_it():
    """A count says how many failed; a sample says why.

    That difference is what turned the UK parser from a guess into a
    measurement, and the ASX side had only counts.
    """
    src = inspect.getsource(H.harvest_au)
    assert "_note_failure(" in src
    assert '"image_only"' in src, (
        "a scan with no text layer is not a failed fetch - no text parser "
        "will ever read it, and the two need different fixes")
    stats = {}
    for i in range(80):
        H._note_failure(stats, f"C{i}", "Monthly Report", "u", "no_nta_parsed", "x" * 5000)
    assert len(stats["fail_samples"]) == 60, "the sample list must stay bounded"
    assert set(stats["fail_samples"][0]) == {"code", "headline", "url",
                                             "status", "text_head"}
    # ...but the OUTCOME is kept for every code, cap or no cap
    assert len(stats["by_code"]) == 80


# ---------------------------------------- the monthly-report NTA (UWC)

# Real text from UWC's "Investment Portfolio Performance July 2026"
# (announcement 03124447, released 2026-08-12), page 4. Two things make it
# hard, and neither is the number: the pre/post-tax qualifier FOLLOWS the
# numbers instead of preceding the label, and two-column page furniture
# lands in between.
UWC_PAGE4 = (
    "Key Metrics as at 31-Jul-26 30-Jun-26 Net Asset Value - pre tax $m 21.3 "
    "21.1 a) undervalued, well-managed growth companies, often Investee "
    "Porfolio (ex cash) $m 19.7 20.1 founder-led, that are off the radar of "
    "the broader Cash and cash equivalents $m 1.7 1.1 investment community; "
    "Net Tangible Asset per share - $ 0.1047 0.1033 b) undervalued securities "
    "where HD seeks to realise value; pre-tax (issued pursuant to LR 4.12) "
    "Net Tangible Asset per share - $ 0.0966 0.0946 and post tax (issued "
    "pursuant to LR 4.12) c) situations that are dependent on a specific "
    "corporate event")

# page 1 of the same document: a covering note, no numbers at all
UWC_PAGE1 = (
    "Underwood Capital Limited Level 57 25 Martin Place Sydney NSW Australia "
    "2000 www.uwcl.com.au 12 August 2026 UWC Investment Portfolio Performance "
    "- July 2026 Underwood Capital Limited (ASX: UWC) is an Australian-listed "
    "specialist investment company. UWC is pleased to provide the portfolio "
    "performance for July 2026 which includes the disclosure pursuant to "
    "Listing Rule 4.12.")


def test_the_monthly_report_nta_is_read_pre_tax_and_current_month():
    """UWC's July NTA, from the document itself.

    Three ways to get this wrong and all three were live:

      0.1033  last month's pre-tax figure - the column beside the one we
              want, and what the live table actually held
      0.0966  this month's POST-tax figure - what the generic parser
              returned, because it is the later of two identical labels
      None    what the harvest got, because it read two pages of a
              seven-page document and the table is on page four
    """
    got = H._asx_pretax_per_share(UWC_PAGE4)
    assert got == pytest.approx(0.1047), (
        f"expected the current pre-tax NTA 0.1047, got {got}")

    doc = {"status": "extracted", "text": UWC_PAGE1 + " " + UWC_PAGE4, "rows": []}
    res = H._nta_from_document(doc, "UWC Investment Portfolio Performance July 2026")
    assert res["nav_per_share"] == pytest.approx(0.1047)
    assert res["nav_basis"] == "pre_tax", "the basis must be stated, not assumed"
    assert res["extractor"] == "asx_monthly_pretax_v1"

    # and the two pages the harvest used to read contain no NTA at all
    assert H._nta_from_document(
        {"status": "extracted", "text": UWC_PAGE1, "rows": []}, "x") is None


def test_the_pdf_reader_goes_past_the_cover_letter():
    """A monthly report puts its NTA behind the cover letter and the
    disclaimer. UWC's is on page 4 of 7; the reader took two pages."""
    src = Path("scripts/sample_nta_pdfs.py").read_text()
    assert "pages = pdf.pages[:2]" not in src, "a two-page read misses page four"
    assert "PDF_PAGES" in src
    import re as _re
    m = _re.search(r'NTA_PDF_PAGES", "(\d+)"', src)
    assert m and int(m.group(1)) >= 6, "the default must reach a monthly report's table"

    from au_lic.extract import deterministic as D
    assert inspect.signature(D.pdf_pages).parameters["max_pages"].default >= 6


def test_the_column_order_is_read_from_the_header_not_assumed():
    """"as at 31-Jul-26 30-Jun-26" says newest first. A document that says
    the opposite must not yield last month's number wearing today's date -
    the same trap Law Debenture set on the UK side."""
    reversed_header = UWC_PAGE4.replace("as at 31-Jul-26 30-Jun-26",
                                        "as at 30-Jun-26 31-Jul-26")
    assert H._asx_pretax_per_share(reversed_header) == pytest.approx(0.1033), (
        "the header says oldest-first, so the LAST column is the current one")


def test_a_post_tax_only_document_is_not_read_as_pre_tax():
    """Absence beats a basis we did not verify."""
    post_only = ("Key Metrics as at 31-Jul-26 30-Jun-26 Net Tangible Asset per "
                 "share - $ 0.0966 0.0946 and post tax (issued pursuant to LR 4.12)")
    assert H._asx_pretax_per_share(post_only) is None


def test_the_text_budget_scales_with_the_page_budget():
    """Two bounds of the same shape, one layer apart.

    20,000 characters never bound anything while the reader took two pages,
    and would have started clipping the moment it took eight. UWC's
    seven-page statement is 15,371 characters with its NTA table at
    character 5,684; a longer monthly report would have lost the table to a
    bound chosen for a smaller read. Deriving one from the other is what
    stops them drifting apart again.
    """
    src = Path("scripts/sample_nta_pdfs.py").read_text()
    assert "text[:20000]" not in src, "a fixed text cap can outlive its page cap"
    assert "PDF_TEXT_CHARS" in src and "PDF_PAGES * " in src

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_nta_budget", "scripts/sample_nta_pdfs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # room for every page the reader is allowed to open
    assert mod.PDF_TEXT_CHARS >= mod.PDF_PAGES * 4000, (
        f"{mod.PDF_TEXT_CHARS} characters cannot hold {mod.PDF_PAGES} pages")
    assert mod.PDF_TEXT_CHARS >= 30_000


def test_every_pdf_reader_goes_deep_enough_for_a_monthly_report():
    """Three readers open these PDFs; the page cap was in two of them."""
    from au_lic.extract import deterministic as D
    assert inspect.signature(D.pdf_pages).parameters["max_pages"].default >= 6

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_nta_budget2", "scripts/sample_nta_pdfs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PDF_PAGES >= 6

    # the third reader (the model path) takes every page and always did
    runner = Path("src/au_lic/extract/runner.py").read_text()
    i = runner.index("def pdf_to_text(")
    assert "pdf.pages[:" not in runner[i:i + 500], (
        "the model path must keep reading whole documents")


def test_the_asx_failure_sample_is_not_biased_against_monthly_reporters():
    """Keeping "the first 40 failures" sounds neutral and is not.

    The loop runs newest-first across every fund, so a first-come sample
    fills with whoever announced in the last few days - and the funds this
    diagnostic exists for are the MONTHLY reporters, whose one announcement
    is two or three weeks old and therefore always arrives after the cap.
    Underwood Capital was dropped exactly that way: 52 documents failed, 24
    codes were sampled, and the one fund the instrumentation had been added
    for was not among them.
    """
    stats = {}
    # forty recent failures, then the monthly reporter's, as the loop sees them
    for i in range(40):
        H._note_failure(stats, f"DAILY{i}", "Daily NTA", "u", "no_nta_parsed", "x")
    H._note_failure(stats, "UWC", "UWC Investment Portfolio Performance July 2026",
                    "u", "no_nta_parsed", "the document text")

    assert stats["by_code"]["UWC"] == "no_nta_parsed", (
        "every code's outcome must be recorded, whatever the sample cap")
    codes = [s["code"] for s in stats["fail_samples"]]
    assert len(codes) == len(set(codes)), "one text sample per fund, not per document"

    # a repeat for the same fund does not consume another slot
    before = len(stats["fail_samples"])
    H._note_failure(stats, "UWC", "again", "u", "no_nta_parsed", "more text")
    assert len(stats["fail_samples"]) == before


def test_every_asx_code_gets_an_outcome_not_only_the_failures():
    """"We never asked" and "we asked and it failed" need different fixes,
    and a funnel of counts cannot tell them apart for a named fund."""
    src = inspect.getsource(H.harvest_au)
    assert 'stats.setdefault("by_code", {})[code] = "parsed"' in src
    assert '"no_candidate_in_window"' in src
    assert '"by_outcome"' in src


def test_a_candidate_without_an_as_at_date_is_still_fetched():
    """The fourth copy of the pattern, and the one that actually hid UWC.

    "UWC Investment Portfolio Performance July 2026" passes the candidate
    filter, carries no as-at date in its headline, and matched none of the
    SECOND inline list - daily / weekly / monthly / NTA / net tangible /
    NAV / fund update / investment update - so it was counted as `no_date`
    and never fetched. The document parses correctly the moment it is
    handed to the parser; it was simply never asked for.

    A row that reached the candidate list has already been judged an NTA
    document. Whether its headline also happens to name a date says nothing
    about that, so it cannot be a reason to skip it.
    """
    src = inspect.getsource(H.harvest_au)
    # the check is on the CODE, not on the prose explaining it
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "re.search(" not in code, (
        "a second headline pattern inside the loop is a fourth copy")
    assert 'stats["no_date"] += 1' not in code
    assert 'date_src = "release_date"' in code

    # the headline itself, through the candidate filter it must pass
    head = "UWC Investment Portfolio Performance July 2026"
    assert H.AU_NAV_HEAD.search(head)
    assert not H.BAD.search(head)
    assert H._asat_date(head) is None, (
        "the fixture must exercise the no-as-at-date path")


# ------------------------------------- ASX cluster A: a date in the way

def test_a_date_between_the_label_and_the_value_no_longer_hides_the_nta():
    """Six live funds lost their NAV to a date.

    Gryphon, 360 Capital, Qualitas, Perpetual Credit and both Metrics
    trusts publish "<label> <date> $<value>", and the scored parser's gap
    between label and number cannot cross digits. Exactly the root cause of
    the UK "was N pence" family, one market over.

    Each expected value below was checked against the exchange's own
    monthly NTA print for that fund - an independent reference that did not
    come from this parser - and every one agrees to within 0.6%.
    """
    cases = [
        ("NTA per unit as at 26 August 2026 $2.0172", 2.0172),          # GCI
        ("Value Date NAV per Unit* 31 July 2026 $5.954", 5.954),        # TCF
        ("VALUE DATE NTA PER UNIT1 24/08/2026 $1.6084", 1.6084),        # QRI
        ("NTA PER UNIT^ 26/08/2026 $1.100", 1.100),                     # PCI
        ("NTA per Unit 26/08/2026 $2.1570", 2.1570),                    # MOT
    ]
    for text, want in cases:
        got = H._asx_per_unit_dollars(text)
        assert got == pytest.approx(want), f"{text!r} -> {got}"


def test_the_dollar_sign_is_what_makes_the_wide_gap_safe():
    """Without it the pattern could match a fragment of the date it was
    written to cross, or a total in millions."""
    # no dollar amount: absence, not a piece of the date
    assert H._asx_per_unit_dollars("NTA per unit as at 26 August 2026") is None
    # totals in millions must not be mistaken for a per-unit figure
    assert H._asx_per_unit_dollars("NTA per unit Gross Assets $762m") is None
    assert H._asx_per_unit_dollars("NAV per unit net assets $19.0m") is None
    # and a cents figure with no dollar sign is left for a unit-aware reader
    assert H._asx_per_unit_dollars("Cents Per Unit Net asset value (CUM) 197.89") is None


def test_the_basis_aware_reader_runs_before_the_plain_one():
    """Order, not coverage, is what protects the basis.

    Run the other way round UWC still returned 0.1047 - but only because
    its pre-tax row happens to come first. The basis fell to "unknown", the
    valuation date was lost, and a fund printing post-tax first would have
    been read silently wrong. A right answer for the wrong reason is not a
    right answer.
    """
    src = inspect.getsource(H._nta_from_document)
    assert src.index("_asx_pretax_per_share(") < src.index("_asx_per_unit_dollars("), (
        "the plain reader must not pre-empt the basis-aware one")
    assert src.index("D.extract_nta(") < src.index("_asx_per_unit_dollars("), (
        "the table-aware extractor must not be pre-empted either")

    uwc = ("Key Metrics as at 31-Jul-26 30-Jun-26 Net Tangible Asset per share "
           "- $ 0.1047 0.1033 b) text; pre-tax (issued pursuant to LR 4.12) "
           "Net Tangible Asset per share - $ 0.0966 0.0946 and post tax")
    got = H._nta_from_document({"status": "extracted", "text": uwc, "rows": []}, "UWC")
    assert got["nav_per_share"] == pytest.approx(0.1047)
    assert got["nav_basis"] == "pre_tax"
    assert got["extractor"] == "asx_monthly_pretax_v1"


def test_the_layouts_that_already_parsed_still_parse_the_same_way():
    """A new rule earns its place by adding funds, not by moving existing
    ones onto itself."""
    for text, want in (
            ("Net Tangible Asset Backing The pre-tax NTA per share as at "
             "31 July 2026 was $2.0590 per share.", 2.059),
            ("NTA per unit 125.10 cents as at 21 August 2026", 1.251),
            ("Daily Net Tangible Asset Statement NTA per share $1.2480", 1.248)):
        got = H._nta_from_document({"status": "extracted", "text": text, "rows": []}, "NTA")
        assert got["nav_per_share"] == pytest.approx(want)
        assert got["extractor"] == "nta_scored_v1", (
            "an existing layout was taken over by the new rule")


# --- clusters B, C, D: values checked against each issuer's own print ---

def test_labelled_nta_reads_declared_cents_not_magnitude():
    """Excelsior heads its table "Cents" and prints 96.85 -> $0.9685."""
    t = ("Net tangible asset per share: Cents As at 31 July 2026 "
         "NTA before all taxes1 96.85 NTA after providing all taxes 2 96.85")
    val, unit, basis = H._asx_labelled_nta(t)
    assert (round(val, 4), unit, basis) == (0.9685, "cents", "pre_tax")


def test_labelled_nta_defaults_to_dollars_when_undeclared():
    t = ("Net Tangible Asset Backing Per Ordinary Share1 "
         "NTA before tax2 2.3660 NTA after tax3 2.2990")
    assert H._asx_labelled_nta(t) == (2.3660, "dollars", "pre_tax")


def test_footnote_marker_never_eats_the_value():
    """"(CUM) 197.89" once parsed as 7.89 -> $0.0789 against a real $1.9825."""
    t = ("NET ASSET VALUE (NAV) AS AT 31 JULY 2026 Cents Per Unit "
         "Net asset value (CUM) 197.89")
    val, unit, _ = H._asx_labelled_nta(t)
    assert (round(val, 4), unit) == (1.9789, "cents")


def test_before_tax_column_takes_the_stated_before_tax_figure():
    """WAM prints before, after, then the refund - order from the header."""
    t = ("NTA NTA (before tax refund) (after tax refund) Tax refund "
         "119.50c 121.17c 1.67c July 2026 122.20c June 2026")
    val, unit = H._asx_before_tax_column(t)
    assert (round(val, 4), unit) == (1.1950, "cents")


def test_before_tax_column_reads_current_month_not_prior():
    """Whitefield heads "31 Jul 26 Prior Month" then prints "$6.28 $6.11"."""
    t = ("NET TANGIBLE ASSET BACKING 31 Jul 26 Prior Month "
         "NTA (Before Deferred Tax) $6.28 $6.11")
    assert H._asx_before_tax_column(t) == (6.28, "dollars")


def test_percentage_change_is_never_read_as_a_valuation():
    """Bentley prints "-53.8% 0.456 0.9xx" - change, this month, last."""
    t = "NTA before tax -53.8% 0.456 0.912"
    got = H._asx_labelled_nta(t)
    assert got is None or got[0] != -53.8


# --- OCR fallback: only believed where the document confirms itself ---

CDM_OCR = ("CADENCE CAPITAL LIMITED NTA AND INVESTMENT UPDATE July 2026 "
           "Net Tangible Assets as at 31st July 2026 Pre Tax NTA $0.810 "
           "Post Tax NTA $1.016. Share Price (ASX Code: CDM) $0.795 "
           "Premium (Discount) to Pre Tax NTA -1.8%")


def test_ocr_value_accepted_when_the_document_confirms_it():
    """0.795 / 0.810 - 1 = -1.85%, against a stated -1.8%."""
    got = H._nta_from_document({"status": "extracted", "text": "",
                                "ocr_text": CDM_OCR}, "NTA update")
    assert got["nav_per_share"] == 0.810
    assert got["nav_basis"] == "pre_tax" and got["ocr"] is True


def test_ocr_value_refused_when_the_arithmetic_disagrees():
    """A digit misread as 0.860 implies -7.6%, not the stated -1.8%."""
    assert H._ocr_confirms(CDM_OCR, 0.860) is False


def test_ocr_value_refused_when_there_is_nothing_to_check_it_against():
    """No stated price and discount means no confirmation, so no value:
    an unverified OCR digit is worth what an invented one is worth."""
    assert H._ocr_confirms("Pre Tax NTA $0.810", 0.810) is False
    assert H._nta_from_document(
        {"status": "extracted", "text": "", "ocr_text": "Pre Tax NTA $0.810"},
        "NTA update") is None


def test_typeset_text_is_never_held_to_the_ocr_gate():
    """The strict test applies to pictures, not to documents that parse."""
    t = ("Net Tangible Asset Backing Per Ordinary Share1 "
         "NTA before tax2 2.3660 NTA after tax3 2.2990")
    got = H._nta_from_document({"status": "extracted", "text": t}, "NTA")
    assert got["nav_per_share"] == 2.3660 and "ocr" not in got
