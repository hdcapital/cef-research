import pytest

from uk_cef.data_sources.investegate import _tokens_compatible
from uk_cef.parsers.dividends import (
    classify_headline,
    parse_dividend_announcement,
)


def test_parse_cty_style():
    # phrasing verified against the live CTY announcement (probe sample)
    body = ("The City of London Investment Trust PLC announced a fourth interim "
            "dividend of 5.70p per ordinary share, payable on 28 August 2026, "
            "bringing the total dividend for the year ended 30 June 2026 to 22.80p. "
            "The shares will be marked ex-dividend on 17 July 2026.")
    d = parse_dividend_announcement("Dividend Declaration", body)
    assert d["amount"] == pytest.approx(5.70)
    assert d["currency"] == "GBX"
    assert d["ex_date"] == "2026-07-17"
    assert d["pay_date"] == "2026-08-28"
    assert d["confidence"] == "high"
    assert d["period"].startswith("fourth")


def test_parse_pence_and_table_style():
    body = ("Interim dividend per share of 2.25 pence. Ex-dividend date: 4 May 2015. "
            "Record date: 5 May 2015. Payment date: 29 May 2015.")
    d = parse_dividend_announcement("Interim Dividend", body)
    assert d["amount"] == pytest.approx(2.25)
    assert d["ex_date"] == "2015-05-04"
    assert d["record_date"] == "2015-05-05"
    assert d["confidence"] == "high"


def test_parse_cents_not_converted_to_gbx():
    body = "declared a dividend of 1.75 cents per share, payable on 15 March 2019"
    d = parse_dividend_announcement("Dividend Declaration", body)
    assert d["amount"] == pytest.approx(1.75)
    assert d["currency"] == "c"
    assert d["amount_gbx"] is None  # never silently converted at an assumed FX rate
    assert d["confidence"] == "medium"


def test_no_amount_returns_none():
    assert parse_dividend_announcement("Dividend Declaration",
                                       "The Board will announce the dividend in due course.") is None


def test_amount_only_low_confidence():
    d = parse_dividend_announcement("Dividend Declaration",
                                    "a first interim dividend of 3.0p per share")
    assert d["confidence"] == "low"
    assert d["ex_date"] is None


def test_special_flag():
    d = parse_dividend_announcement(
        "Special Dividend",
        "a special dividend of 10.0p per share, payable on 1 June 2020")
    assert d["special"] is True


def test_classify_headlines():
    assert classify_headline("Dividend Declaration") == "dividend"
    assert classify_headline("Third Interim Dividend") == "dividend"
    assert classify_headline("Tender Offer") == "catalyst"
    assert classify_headline("Proposed merger with XYZ Trust") == "catalyst"
    assert classify_headline("Result of Strategic Review") == "catalyst"
    assert classify_headline("Net Asset Value(s)") is None
    assert classify_headline("Director/PDMR Shareholding") is None
    assert classify_headline("Transaction in Own Shares") is None  # buybacks: too many to fetch


def test_name_compatibility():
    assert _tokens_compatible("City of London Inv Trust", "City of London Investment Trust")
    # renames (abrdn vs Aberdeen) are handled by checking ALL historical
    # names from the entity registry, not by fuzzy matching here
    assert _tokens_compatible("Aberdeen Diversified Income and Growth",
                              "Aberdeen Diversified Income & Growth")
    assert not _tokens_compatible("Achilles Investment Company Limited",
                                  "Association of Investment Companies")
    assert not _tokens_compatible("Achilles Investment Company", "Henderson High Income")
