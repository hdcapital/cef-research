"""Parser regression tests against genuine AIC sample files committed under
data/probe/samples (small excerpts of the public archive)."""

import pytest

from uk_cef.parsers.companies import parse_companies_excel
from uk_cef.parsers.corporate_activity import categorize_event, parse_corporate_activity
from uk_cef.parsers.mir import classify_mir_file, parse_mir_csv


def test_mir_2007_format(samples_dir):
    rows = parse_mir_csv(samples_dir / "MIR0701.CSV")
    assert len(rows) > 300
    aaa = next(r for r in rows if r["company_name"] == "Aberdeen All Asia")
    assert aaa["code"] == "392075"
    assert aaa["price"] == pytest.approx(218.0)
    assert aaa["nav"] == pytest.approx(241.96)
    assert aaa["obs_month"] == "2007-01"
    assert aaa["price"] / aaa["nav"] - 1 == pytest.approx(-0.099, abs=1e-3)


def test_mir_2026_format(samples_dir):
    rows = parse_mir_csv(samples_dir / "MIR2607.csv")
    cty = next(r for r in rows if r["company_name"].startswith("City of London"))
    assert cty["code"] == "GB0001990497"
    assert cty["price"] == pytest.approx(593.0)
    assert cty["nav"] == pytest.approx(581.54)
    assert cty["currency"] == "GBX"
    assert cty["dividend_yield"] == pytest.approx(3.92)


def test_mir_repeated_headers_skipped(samples_dir):
    rows = parse_mir_csv(samples_dir / "MIR2607.csv")
    assert not any(r["company_name"] in ("Fund", "Name") for r in rows)


def test_mir_errata_classification():
    assert classify_mir_file("MIR2607ERR.csv") == "errata"
    assert classify_mir_file("MIR2607PostErr.csv") == "post_errata"
    assert classify_mir_file("PostMIRErrataAdditional0701.csv") == "post_errata"
    assert classify_mir_file("MIR0701.CSV") == "main"
    assert classify_mir_file("GEO0701.CSV") == "component"


def test_mir_errata_rows_keyed_by_own_month(samples_dir):
    rows = parse_mir_csv(samples_dir / "ErrataAdditional 0701.csv")
    months = {r["obs_month"] for r in rows}
    assert "2006-12" in months  # late-reported December rows in the Jan file


def test_companies_2007(samples_dir):
    rows = parse_companies_excel(samples_dir / "AIC Companies end Jan 07.xls")
    assert len(rows) > 300
    r = rows[0]
    assert r["company_name"] == "Aberdeen All Asia"
    assert r["market_cap_m"] == pytest.approx(36.377)
    assert not any(x["company_name"].lower().startswith("total") for x in rows)


def test_companies_2019_has_identifiers(samples_dir):
    rows = parse_companies_excel(samples_dir / "AICAllCompanies30Jun19.xlsx")
    r3i = next(r for r in rows if r["company_name"] == "3i Group")
    assert r3i["isin"] == "GB00B1YW4409"
    assert r3i["ticker"] == "III"


def test_corporate_activity_2007(samples_dir):
    rows = parse_corporate_activity(samples_dir / "AICCorporateActivity2007.xlsx")
    assert len(rows) > 300
    liq = [r for r in rows if r["category"] == "liquidation"]
    assert liq and all(r["event_month"].startswith("2007") for r in rows if not r["is_await"])


def test_corporate_activity_dedup_keys(samples_dir):
    rows = parse_corporate_activity(samples_dir / "AICCorporateActivity2017.xlsx")
    keys = [(r["event_month"], r["event"], r["company_name"], r["detail"]) for r in rows]
    # duplicates may exist in-source across sheets, but dedup key must retain
    # distinct issuance events for the same company in the same month
    assert len(set(keys)) > 0.9 * len(keys)


def test_event_categorization():
    assert categorize_event("Tender Offer") == "tender"
    assert categorize_event("Depart/Liquidate") == "liquidation"
    assert categorize_event("Merge/Depart") == "merger_departing"
    assert categorize_event("Issue (from treasury)") == "issuance"
    assert categorize_event("Capital Change") == "capital_change"
    assert categorize_event("Something Unknown") == "other"


def test_mir_2019_bom_format(samples_dir):
    """2019-era files carry a UTF-8 BOM and a blank line between the two
    header rows; regression for the header-detection fix."""
    p = samples_dir / "MIR1902_excerpt.csv"
    rows = parse_mir_csv(p)
    assert len(rows) >= 5
    aai = next(r for r in rows if r["company_name"] == "Aberdeen Asian Income")
    assert aai["price"] == pytest.approx(206.0)
    assert aai["nav"] == pytest.approx(220.3)
    assert aai["obs_month"] == "2019-02"


def test_portfolio_exposure_files_are_components():
    assert classify_mir_file("MIR Portfolio exposure endNov24.csv") == "component"
    assert classify_mir_file("MIR%20Portfolio%20exposure%20endDec23_1.xlsx") == "component"
