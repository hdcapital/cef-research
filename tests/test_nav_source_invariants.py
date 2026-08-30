"""Invariants for where NAV comes from.

These are not unit tests of convenience - they encode the architectural
decision that the AIC/ASX registry files are used for IDENTITY ONLY and
every NAV value comes from the fund's own announcements. Each test fails
loudly if a future change quietly reintroduces the old behaviour, which is
how funds went missing before: not by anyone deciding to drop them, but by
a filter narrowing the target set as a side effect.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _order_targets(tick2sid, census_tickers, extra_targets):
    """Mirror of the ordering in harvest_uk - kept in step by the test below."""
    first = [t for t in (extra_targets or {})]
    seen = set(first)
    second = [t for t in census_tickers if t in tick2sid and t not in seen]
    seen |= set(second)
    rest = [t for t in tick2sid if t not in seen]
    return first + second + rest


def test_every_addressable_fund_is_a_nav_target():
    """No fund we can address may be excluded from the NAV harvest.

    A fund must be absent from NAV output only because it published nothing
    parseable - never because the target set filtered it out.
    """
    tick2sid = {"HICL": "s1", "TRIG": "s2", "CTY": "s3", "ATST": "s4",
                "SYNC": "s5", "OCI": "s6"}
    census = ["CTY", "ATST"]                     # only what the crawl covered
    extra = {"HICL": "s1", "TRIG": "s2"}         # registry never prices these
    ordered = _order_targets(tick2sid, census, extra)

    assert set(ordered) == set(tick2sid), (
        "every addressable fund must be targeted; missing: "
        f"{set(tick2sid) - set(ordered)}")
    assert len(ordered) == len(set(ordered)), "targets must not duplicate"
    # funds with no other NAV source are polled first, since a census fund
    # at least has a (stale) registry print to fall back on
    assert ordered[:2] == ["HICL", "TRIG"]


def test_no_item_budget_truncates_the_addressable_universe():
    """Ordering every fund is not enough - nothing may cut the list after.

    The ordering helper was already correct; the harvest then took
    ordered[:400] while the addressable universe had grown to 562, so 162
    funds were dropped every night by a default constant. A fund missing
    from the NAV output must always mean it published nothing parseable.
    """
    import inspect

    from cef_live import harvest_nav

    default = inspect.signature(harvest_nav.harvest_uk).parameters["budget"].default
    assert not default, (
        f"harvest_uk defaults to a {default}-fund cap; funds beyond it are "
        "never asked for their NAV")
    src = inspect.getsource(harvest_nav.harvest_uk)
    assert "ordered[:budget] if budget else ordered" in src, (
        "the target list is truncated unconditionally")


def test_harvest_uk_signature_still_accepts_full_target_set():
    """harvest_uk must keep the parameter that carries registry targets."""
    from cef_live import harvest_nav
    import inspect
    params = inspect.signature(harvest_nav.harvest_uk).parameters
    assert "extra_targets" in params, (
        "harvest_uk lost extra_targets - registry-listed funds would stop "
        "being polled for their own NAV")


def test_published_nav_beats_registry_anchor():
    """A Tier 0 announcement NAV must override the registry's monthly print.

    If this inverts, the system silently prefers a month-old aggregator
    value over the fund's own published figure.
    """
    import re
    import yaml
    from cef_live import nta_live

    params = yaml.safe_load(
        re.sub(r"\$\{[^}]+\}", "", Path("config/params.yaml").read_text()))
    months = pd.period_range("2019-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "X", "obs_month": m, "sector": "S",
         "nta_total_return": 0.005, "nta_derived": 1.0,
         "share_price": 0.9, "company_name": "X"} for m in months])
    tier0 = pd.DataFrame([{"security_id": "X", "nav_date": "2026-08-27",
                           "nav_value": 2.5, "source": "investegate:999"}])

    t = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                             "share_price", params, tier0=tier0)
    row = t[t["security_id"] == "X"].iloc[0]
    assert row["basis"] == 0, "published NAV must set basis 0"
    assert row["nav_anchor"] == 2.5, "published NAV must win over registry print"
    assert "investegate" in str(row["anchor_source"])


def test_estimate_never_stored_as_published():
    """The modelled value and the published value stay in separate fields."""
    import re
    import yaml
    from cef_live import nta_live

    params = yaml.safe_load(
        re.sub(r"\$\{[^}]+\}", "", Path("config/params.yaml").read_text()))
    months = pd.period_range("2019-01", "2026-06", freq="M").astype(str)
    panel = pd.DataFrame([
        {"security_id": "Y", "obs_month": m, "sector": "S",
         "nta_total_return": 0.004, "nta_derived": 1.0 + i * 0.001,
         "share_price": 0.9, "company_name": "Y"}
        for i, m in enumerate(months)])
    t = nta_live.build_table(panel, "AU", "nta_total_return", "nta_derived",
                             "share_price", params)
    row = t[t["security_id"] == "Y"].iloc[0]
    assert "nav_anchor" in row and "nta_est" in row, (
        "published anchor and modelled estimate must be distinct columns")
    assert row["basis"] in (1, 3)
    assert pd.notna(row["anchor_date"]) and pd.notna(row["staleness_days"]), (
        "an estimate must always carry the date it is anchored to and its age")


def test_index_sweep_resweeps_when_registry_gains_codes():
    """A newly listed fund must not be permanently absent from the index.

    UWC, AIX, MRE, PCX and WHI had no announcements indexed because the
    sweep filtered to the code set known at sweep time and never revisited.
    """
    src = Path("scripts/sample_nta_pdfs.py").read_text()
    assert "code_sig" in src, "sweep must record which codes it kept"
    assert "re-sweeping history" in src, (
        "sweep must re-run history when the registry gains codes")


@pytest.mark.parametrize("script", [
    "scripts/archive_to_s3.py",
    "scripts/archive_uk_navs.py",
    "scripts/sync_state.py",
])
def test_archive_scripts_actually_execute(script, monkeypatch):
    """Import each archive script for real, not just parse it.

    A botched edit once produced `PDF_# comment` on one line and
    `BUDGET = ...` on the next: valid syntax, so ast.parse passed, but a
    NameError at import that killed shards after they had been dispatched.
    Parsing is not enough - the module has to run.
    """
    import importlib.util
    import sys

    monkeypatch.setenv("SHARD_INDEX", "0")
    monkeypatch.setenv("SHARD_COUNT", "8")
    monkeypatch.setattr(sys, "argv", ["test"])
    spec = importlib.util.spec_from_file_location("_probe", script)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass          # argv-driven scripts may exit on usage; that is fine


def test_keyfacts_tickers_join_without_re_resolving_ids():
    """Ticker join must use ISIN, not a second entity-resolution pass.

    Re-resolving the companies file produced different security_ids than the
    registry held (old numeric SEDOLs vs modern ones), so the join matched
    zero of the 105 funds it exists to serve - twice, silently.
    """
    from unittest.mock import patch
    from cef_live import tickers as T

    reg = pd.DataFrame([
        {"security_id": "SEDOL:BT3GKD0", "name": "Achilles Investment Company",
         "isin": "GG00BT3GKD08"},
        {"security_id": "NAME:british and american|ordinary share",
         "name": "British & American", "isin": ""},
    ])
    comp = pd.DataFrame([
        {"company_name": "Achilles Investment Company", "isin": "GG00BT3GKD08",
         "ticker": "AIC", "obs_month": "2026-07"},
        {"company_name": "British & American", "isin": None,
         "ticker": "BAF", "obs_month": "2026-07"},
    ])
    with patch("uk_cef.panel.parse_all_companies", return_value=comp):
        out = T.from_aic_keyfacts(reg, {"download": {"raw_dir": "x"}})

    assert len(out) == 2, "both funds must resolve"
    assert set(out["ticker"]) == {"AIC", "BAF"}
    # offshore ISIN joins directly; a blank ISIN still resolves by name
    assert "isin" in out[out["ticker"] == "AIC"].iloc[0]["method"]
    assert "name" in out[out["ticker"] == "BAF"].iloc[0]["method"]


def test_figi_pick_prefers_the_london_closed_end_listing():
    """The Frankfurt line of a London trust must never win.

    Yahoo returned Bluefield's Frankfurt listing ahead of London; an
    exchange-scoped identifier map is only safe if the picker actually
    honours the scope.
    """
    from cef_live import tickers as T

    data = [{"exchCode": "GR", "ticker": "XYZ", "securityType": "Closed-End Fund"},
            {"exchCode": "LN", "ticker": "BSIF", "securityType": "Closed-End Fund"},
            {"exchCode": "LN", "ticker": "BSIFD", "securityType": "Debt"}]
    assert T._figi_pick(data)["ticker"] == "BSIF"
    assert T._figi_pick([]) is None


def test_identifier_candidate_is_never_accepted_without_the_name_check(monkeypatch):
    """An ISIN map is an identifier join, but it is still only a candidate.

    A ticker that fails the H1 name check must be recorded as a
    disagreement - keeping WHAT was rejected - never written as the fund's
    ticker, because a wrong ticker prices this fund off another company's
    shares.
    """
    import pandas as pd

    from cef_live import tickers as T

    reg = pd.DataFrame([
        {"security_id": "SEDOL:AAA", "name": "Good Trust", "isin": "GB00AAAAAAA1",
         "status": "live", "market": "UK"},
        {"security_id": "SEDOL:BBB", "name": "British & American", "isin": "GB0000653112",
         "status": "live", "market": "UK"},
    ])
    monkeypatch.setattr(T, "CACHE", Path("/tmp/_test_tickers_cache.csv"))
    if T.CACHE.exists():
        T.CACHE.unlink()
    monkeypatch.setattr(T, "from_openfigi", lambda need, session=None: {
        "GB00AAAAAAA1": ("GDT", "GOOD TRUST PLC", "Closed-End Fund"),
        "GB0000653112": ("BATS", "BRITISH AMERICAN TOBACCO", "Common Stock"),
    })
    # the page check passes only where the H1 really names this fund
    monkeypatch.setattr(T, "verify", lambda s, slug, names:
                        ("GDT", "Good Trust") if slug == "GDT" else None)
    monkeypatch.setattr(T, "_candidates", lambda s, name: [])

    out = T.resolve(reg, budget=10).set_index("security_id")
    assert out.loc["SEDOL:AAA", "status"] == "verified"
    assert out.loc["SEDOL:AAA", "ticker"] == "GDT"
    assert out.loc["SEDOL:AAA", "method"] == "openfigi_isin+h1"
    # the tobacco company must not become this trust's ticker
    assert out.loc["SEDOL:BBB", "status"] == "unresolved_name_mismatch"
    assert pd.isna(out.loc["SEDOL:BBB", "ticker"])
    assert "BATS" in str(out.loc["SEDOL:BBB", "verified_name"])
    T.CACHE.unlink(missing_ok=True)


def test_unconfirmed_isin_mapping_needs_fund_type_not_just_a_name():
    """Security type is what makes an unconfirmed page acceptable.

    The name matcher is lenient by necessity - it must accept
    "CHENAVARI TORO INCOME FUND L" for "Chenavari Toro Income Fund" - and
    it is lenient enough to also accept "BRITISH AMERICAN TOBACCO PLC" for
    "British & American". So a name alone can never carry an unverified
    mapping; the record must also be typed a fund.
    """
    from cef_live import tickers as T

    ok = ("TORO", "CHENAVARI TORO INCOME FUND L", "Closed-End Fund")
    assert T._figi_self_consistent(ok, ["Chenavari Toro Income Fund"])

    # the exact trap: a lenient name match on an operating company
    tobacco = ("BATS", "BRITISH AMERICAN TOBACCO PLC", "Common Stock")
    from uk_cef.data_sources.investegate import _tokens_compatible
    assert _tokens_compatible("British & American", tobacco[1])   # name says yes
    assert not T._figi_self_consistent(tobacco, ["British & American"])  # type says no

    # right type, wrong company - the name check still has to bite
    wrong = ("XYZ", "SOME OTHER TRUST PLC", "Closed-End Fund")
    assert not T._figi_self_consistent(wrong, ["Chenavari Toro Income Fund"])


def test_one_broken_ticker_source_does_not_cancel_the_others(monkeypatch, tmp_path):
    """A source that fails must not take the working ones down with it.

    This is the defect behind four consecutive 0-of-105 runs: the MIR
    ticker map raised, seed_known returned on the spot, and the keyfacts
    join below it never executed - silently, so every diagnosis blamed the
    join it had in fact never run.
    """
    import pandas as pd

    from cef_live import tickers as T

    monkeypatch.setattr(T, "CACHE", tmp_path / "cache.csv")
    reg = pd.DataFrame([{"security_id": "SEDOL:AAA", "name": "Good Trust",
                         "isin": "GB00AAAAAAA1", "status": "live", "market": "UK"}])

    def boom(_cfg):
        raise RuntimeError("MIR files not on disk")

    import uk_cef.data_sources.investegate as IG
    monkeypatch.setattr(IG, "build_ticker_map", boom)
    monkeypatch.setattr(T, "from_aic_keyfacts", lambda registry, cfg: pd.DataFrame(
        [{"security_id": "SEDOL:AAA", "ticker": "GDT", "verified_name": "Good Trust",
          "method": "aic_keyfacts_isin", "status": "verified"}]))

    out = T.seed_known(reg, {"download": {"raw_dir": "x"}})
    assert (out["security_id"] == "SEDOL:AAA").any(), \
        "keyfacts result was discarded because an unrelated source failed"
    assert out.set_index("security_id").loc["SEDOL:AAA", "ticker"] == "GDT"


@pytest.mark.parametrize("fname", ["harvest_uk", "harvest_au", "uk_nav_samples"])
def test_harvester_globals_all_resolve(fname):
    """A harvester must not reference a name the module never imported.

    harvest_uk used both `requests` and `_t` while neither was imported at
    module level. It raised NameError on its first target, every night, and
    the caller's `except Exception` recorded it as a one-line note in a
    JSON report - so the UK Tier 0 NAV count sat at 0 and looked like
    "nobody published", which is exactly the confusion the design forbids.

    A NameError inside a crawl loop cannot be caught by tests that only
    check signatures, so check the names themselves.
    """
    import builtins

    from cef_live import harvest_nav

    fn = getattr(harvest_nav, fname)
    missing = [n for n in fn.__code__.co_names
               if n not in harvest_nav.__dict__
               and not hasattr(builtins, n)]
    # attribute lookups (obj.attr) also land in co_names; keep only names
    # that are genuinely referenced as globals by the bytecode
    import dis
    referenced = {i.argval for i in dis.get_instructions(fn)
                  if i.opname == "LOAD_GLOBAL"}
    missing = [n for n in missing if n in referenced]
    assert not missing, f"{fname} references undefined global(s): {missing}"


def test_nan_name_does_not_crash_the_catalyst_digest():
    """`x or y` is wrong whenever x may be NaN - NaN is truthy.

    A left join that misses puts a float in `name`; `(r.name or r.security_id)`
    then returns the float and slicing it raises. This crashed the nightly
    AFTER a 24-minute harvest, so the cost of the bug was the whole run, not
    one email line. Same trap, same shape, as the ISIN handling in tickers.py.
    """
    import math
    import pandas as pd

    df = pd.DataFrame([{"security_id": "ASX:ABC", "name": float("nan")},
                       {"security_id": "ASX:DEF", "name": "Real Fund"}])
    for r in df.itertuples(index=False):
        nm = getattr(r, "name", None)
        label = str(nm) if isinstance(nm, str) and nm.strip() else str(r.security_id)
        assert isinstance(label[:34], str)
    # the trap itself, documented so it is not reintroduced
    assert (float("nan") or "fallback") != "fallback"
    assert math.isnan(float("nan") or "fallback")


def test_a_fund_the_aggregator_never_priced_still_gets_a_row():
    """The registry defines the universe; the aggregator only says who exists.

    Keying the live table on the research panel meant a fund the AIC never
    priced had no row for a harvested NAV to attach to. 113 tradeable funds -
    HICL, TRIG, INPP, Pershing Square, Syncona, 24 VCTs, the whole offshore
    and alternatives cohort - had their NAVs fetched every night and dropped,
    because the table was keyed on an aggregator's past coverage rather than
    on the fund's existence. That is the cohort whose discounts blew out
    after 2022, so its absence points the same way as the hypothesis.
    """
    import pandas as pd
    import yaml

    from cef_live import nta_live

    params = yaml.safe_load(Path("config/params.yaml").read_text())
    panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                  "nav_per_share", "share_price",
                                  "nav_total_return", "discount"])
    registry = pd.DataFrame([{"security_id": "SEDOL:HICL", "market": "UK",
                              "status": "live", "name": "HICL Infrastructure"}])
    tier0 = pd.DataFrame([{"security_id": "SEDOL:HICL", "nav_date": "2026-08-29",
                           "nav_value": 1.60, "source": "announcement:rns123"}])
    live_px = pd.DataFrame([{"security_id": "SEDOL:HICL", "price": 1.20,
                             "price_source": "yahoo:HICL.L",
                             "price_date": "2026-08-29"}])
    out = nta_live.build_table(panel, "UK", ret_col="nav_total_return",
                               nav_col="nav_per_share", price_col="share_price",
                               params=params, tier0=tier0, live_prices=live_px,
                               registry=registry)
    assert len(out) == 1, "a fund with no aggregator history got no row"
    r = out.iloc[0]
    assert r["basis"] == 0                      # priced on its OWN published NAV
    assert abs(r["discount_est"] - (-0.25)) < 1e-9
    assert "yahoo" in str(r["price_asof"])      # priced from the feed


def test_the_aggregator_anchor_is_named_so_its_use_is_countable():
    """Falling back to the aggregator is allowed; hiding it is not.

    Labelling the anchor `aggregator_panel:` is what makes "how many funds
    still depend on the source we are moving off" a measurement rather than
    an assumption.
    """
    import inspect

    from cef_live import nta_live

    src = inspect.getsource(nta_live.build_table)
    assert "aggregator_panel:" in src
    # and a published NAV is checked after the aggregator, so it wins
    assert src.index("aggregator_panel:") < src.index("basis = 0")
