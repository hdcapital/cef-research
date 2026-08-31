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
    assert '"nav_value": val,' in src

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

    panel = pd.DataFrame([{
        "ticker": "PNL", "ann_id": "9743001",
        "published_at": pd.Timestamp("2026-08-27"),
        "nav_date": pd.Timestamp("2026-08-26"), "nav_pence": 555.50,
        "nav_ex_pence": None, "cum_assumed": False,
        "nav_source": "archive", "quality": "ok"}])
    monkeypatch.setattr(UKP, "read_panel", lambda *a, **k: panel)
    monkeypatch.setattr(CA, "load_tickers", lambda: pd.DataFrame(
        [{"security_id": "SEDOL:PNL1", "ticker": "PNL",
          "ticker_status": "verified"}]))

    facts = CA.uk_nav_archive_facts().set_index("security_id")
    assert "SEDOL:PNL1" in facts.index
    row = facts.loc["SEDOL:PNL1"]
    assert row["last_parsed_nav_date"] == "2026-08-27", (
        "a re-parsed announcement must count as parsed")

    # and the value itself must reach the live table's NAV anchor
    src = inspect.getsource(cli._own_nav_history)
    assert "uk_nav_panel" in src and "nav_pence" in src
    assert '"nav_unit": "GBX"' in src
