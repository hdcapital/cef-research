import pandas as pd

from uk_cef.entities import EntityRegistry, normalize_name, sedol_from_code


def test_sedol_from_isin():
    assert sedol_from_code("GB00B0P6J834") == "B0P6J83"
    assert sedol_from_code("B0P6J83") == "B0P6J83"
    assert sedol_from_code("392075") == "392075"  # 6-char legacy code kept
    assert sedol_from_code(None) is None
    assert sedol_from_code(float("nan")) is None


def test_isin_sedol_era_transition_links():
    reg = EntityRegistry()
    a = reg.resolve("Aberdeen Asian Income", "B0P6J83", "Ordinary Share")
    b = reg.resolve("Aberdeen Asian Income Fund", "GB00B0P6J834", "Ordinary Share")
    assert a == b


def test_rename_same_sedol_single_entity():
    reg = EntityRegistry()
    a = reg.resolve("Perpetual Income & Growth", "0682754", "Ordinary Share")
    b = reg.resolve("Invesco Select Trust", "0682754", "Ordinary Share")
    assert a == b
    rec = reg.to_frame()
    assert len(rec) == 1
    assert "Perpetual Income & Growth" in rec.iloc[0]["all_names"]


def test_name_change_alias_without_codes():
    reg = EntityRegistry()
    ca = pd.DataFrame(
        [
            {"category": "name_change", "event": "Name Change",
             "company_name": "Henderson Strata", "detail": "to Henderson Opportunities"},
        ]
    )
    reg.load_name_changes(ca)
    a = reg.resolve("Henderson Strata", None, "Ordinary Share")
    b = reg.resolve("Henderson Opportunities", None, "Ordinary Share")
    assert a == b


def test_same_ticker_different_company_not_merged():
    reg = EntityRegistry()
    a = reg.resolve("Alpha Trust", "1111111", "Ordinary Share")
    b = reg.resolve("Beta Trust", "2222222", "Ordinary Share")
    assert a != b


def test_share_classes_stay_separate():
    reg = EntityRegistry()
    a = reg.resolve("Some Split Trust", None, "Ordinary Share")
    b = reg.resolve("Some Split Trust", None, "Zero Dividend Preference share")
    assert a != b


def test_normalize_name():
    assert normalize_name("The Scottish Investment Trust PLC") == normalize_name(
        "Scottish Investment Trust"
    )
    assert normalize_name("JPMorgan Smaller Cos.") == normalize_name("JPMorgan Smaller Cos")
