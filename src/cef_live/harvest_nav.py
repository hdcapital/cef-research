"""Tier 0 harvester: issuer-published high-frequency NAV/NTA announcements.

AU: the open market-wide announcement index (validation work) already
carries every fund's announcements with direct PDF links. The research
validation deliberately EXCLUDED weekly/daily NTA statements; Tier 0 is
exactly those plus any-day month-end statements. This module selects them,
fetches + parses the PDFs through the same battle-tested extraction and
scored label parser as the validation (scripts/sample_nta_pdfs.py), and
returns published NAV observations - real numbers with real dates,
never estimates.

UK: Investegate "Net Asset Value(s)" RNS announcements; the frequency
census runs off the existing crawler cache, value harvesting lands with
the incremental crawler extension (see docs/RUNBOOK.md).
"""

from __future__ import annotations

import importlib.util
import re
import time as _t
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

_SPEC = importlib.util.spec_from_file_location(
    "nta_parse", Path(__file__).resolve().parents[2] / "scripts" / "sample_nta_pdfs.py")
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)

# Tier 0 headline: an NTA/NAV statement with an as-at date at ANY day of
# month, or an explicit daily/weekly update. Amendments still excluded.
BAD = re.compile(r"amendment|amended|correction|withdraw", re.I)

# Headlines that may carry a published NTA. Deliberately WIDER than the
# archive sweep's NTA_HEAD: several of the largest LIC families never use
# the words "NTA" in a headline. Metrics publishes its NTA inside a "Daily
# Fund Update"; the WAM and Future Generation funds publish theirs inside a
# "Monthly Report" or "Investment Update". Measured against the live index
# over 45 days: NTA_HEAD reaches 79 of the 108 monitored ASX funds, this
# pattern reaches 91 - the twelve it adds are WAM, WAX, WAR, WAA, WMI, WMA,
# WGB, FGX, FGG, MOT, MRE and MXT. A document that turns out to carry no
# NTA simply fails to parse and is recorded, which is the cheap direction
# for this error to run in.
AU_NAV_HEAD = re.compile(
    r"\bNTA\b|net tangible|net asset|\bNAV\b|fund update|"
    r"monthly (?:report|update|investment)|investment update", re.I)


DOTTED_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b")


def _asat_date(head: str) -> pd.Timestamp | None:
    m = P.ASAT.search(head or "")
    if m:
        try:
            return pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            return None
    # "NTA at 21.08.2026", "Daily Estimate NTA for 26.08.2026"
    m = DOTTED_DATE.search(head or "")
    if m:
        try:
            return pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


AU_COLS = ["security_id", "nav_date", "nav_value", "unit", "basis_note",
           "source", "headline"]


def harvest_au(codes: set[str], lookback_days: int = 45,
               pdf_budget: int = 0, max_attempts_per_code: int = 3,
               deadline_min: float = 45.0) -> pd.DataFrame:
    """The newest published NTA per fund, from its own ASX announcements.

    Returns DataFrame: security_id, nav_date, nav_value, unit, basis_note,
    source (announcement id), headline. ``nav_value`` is the value AS
    STATED in the document and ``unit`` says what unit that is - the
    conversion to the market's canonical unit is units.normalise's job, so
    it happens exactly once. (It previously happened here as well, which
    made a cents-labelled row a dollars value wearing a cents label: the
    next normaliser would have divided it by 100 again.)

    Only successfully parsed, unambiguous values are returned; everything
    else is left absent and counted in the funnel written to
    reports/build/asx_tier0_debug.json.

    Two things decide how much of the universe this reaches:

    lookback_days - many LICs publish an NTA MONTHLY. A 14-day window saw
      an NTA for only 40 of the 108 monitored funds; 30 days sees 79 and
      45 days adds none beyond that but tolerates a late filing.

    one fetch per fund - the loop used to re-parse the same fund's daily
      statement for every day in the window, so a 200-document budget was
      spent on 7 funds' back-catalogue. Only the NEWEST value per fund is
      wanted, so a fund is dropped from the queue as soon as one parses,
      and the whole addressable universe costs about one fetch each.
    """
    import requests

    if not P.INDEX_F.exists():
        return pd.DataFrame(columns=AU_COLS)
    idx = pd.read_parquet(P.INDEX_F)
    idx = idx[idx["code"].isin(codes) & idx["url"].notna()]
    idx["release"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    idx = idx[idx["release"] >= cutoff]

    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    counters = {"pdf_calls": 0}
    started = _t.time()
    stats = {"lookback_days": lookback_days, "codes_requested": len(codes),
             "index_rows_in_window": int(len(idx)), "candidates": 0,
             "codes_with_a_candidate": 0, "attempted": 0, "parsed": 0,
             "parse_failed": 0, "ambiguous_unit": 0, "no_date": 0,
             "codes_parsed": 0, "budget_reached": False,
             "deadline_reached": False}
    done: set[str] = set()
    attempts: dict[str, int] = {}
    rows = []

    cands = [r for r in idx.sort_values("release", ascending=False)
             .itertuples(index=False)
             if AU_NAV_HEAD.search(r.headline or "")
             and not BAD.search(r.headline or "")]
    stats["candidates"] = len(cands)
    stats["codes_with_a_candidate"] = len({r.code for r in cands})

    for r in cands:
        code = str(r.code)
        if code in done:
            continue                      # newest value already in hand
        if attempts.get(code, 0) >= max_attempts_per_code:
            continue
        head = r.headline or ""
        asat = _asat_date(head)
        if asat is None:
            # daily/weekly/monthly updates often carry no as-at in the
            # headline; use the release date as the observation date,
            # labelled so the reader knows which it is
            if not re.search(r"daily|weekly|monthly|\bNTA\b|net tangible|"
                             r"\bNAV\b|fund update|investment update",
                             head, re.I):
                stats["no_date"] += 1
                continue
            asat = pd.Timestamp(r.release.date())
            date_src = "release_date"
        else:
            date_src = "as_at_headline"
        if pdf_budget and counters["pdf_calls"] >= pdf_budget:
            stats["budget_reached"] = True
            break
        if (_t.time() - started) > deadline_min * 60:
            stats["deadline_reached"] = True
            break
        attempts[code] = attempts.get(code, 0) + 1
        stats["attempted"] += 1
        res = P.derive_stated(P.parse_pdf(s, str(r.id), r.url, counters))
        if res.get("status") != "parsed":
            stats["parse_failed"] += 1
            continue
        val, unit = res["stated_raw"], res.get("unit")
        if unit == "ambiguous":
            stats["ambiguous_unit"] += 1
            continue                      # flagged, never guessed
        stats["parsed"] += 1
        done.add(code)
        rows.append({"security_id": f"ASX:{code}",
                     "nav_date": asat.date().isoformat(),
                     "nav_value": val,          # AS STATED, in `unit`
                     "unit": unit,
                     "basis_note": res.get("basis", "pre_tax") + f"|{date_src}",
                     "source": f"asx_ann:{r.id}", "headline": head[:120]})

    stats["codes_parsed"] = len(done)
    try:
        import json as _json
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        Path("reports/build/asx_tier0_debug.json").write_text(
            _json.dumps(stats, indent=1))
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame(rows, columns=AU_COLS)


def uk_frequency_census(cache_dir: Path) -> pd.DataFrame:
    """Per-fund NAV-announcement publication frequency from the existing
    Investegate crawl cache (per-ticker listing CSVs - no new fetches).

    The crawler stores listings/{ticker}.csv with ann_id, date, headline
    (and url) for every announcement it has paged over. Counts 'Net Asset
    Value' titled rows per fund and classifies nav_frequency: daily /
    weekly / monthly / adhoc. Feeds the universe config; value harvesting
    follows via the same detail-page path the dividend crawler uses.
    """
    pat = re.compile(r"net asset value", re.I)
    listings = cache_dir / "listings"
    rows = []
    files = sorted(listings.glob("*.csv")) if listings.exists() else []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if "headline" not in df.columns:
            continue
        nav = df[df["headline"].fillna("").str.contains(pat)]
        for r in nav.itertuples(index=False):
            if getattr(r, "date", None):
                rows.append({"ticker": f.stem, "date": r.date,
                             "ann_id": getattr(r, "ann_id", None)})
    if not rows:
        return pd.DataFrame(columns=["ticker", "n_navs", "first", "last",
                                     "per_month", "nav_frequency"])
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker", "ann_id"])
    g = df.groupby("ticker")["date"].agg(["count", "min", "max"]).reset_index()
    months = ((pd.to_datetime(g["max"]) - pd.to_datetime(g["min"])).dt.days / 30.4).clip(lower=1)
    g["per_month"] = g["count"] / months
    g["nav_frequency"] = pd.cut(g["per_month"], [-1, 0.5, 2.5, 12, 1e9],
                                labels=["adhoc", "monthly", "weekly", "daily"])
    return g.rename(columns={"count": "n_navs", "min": "first", "max": "last"})


# ---------------------------------------------------------------- UK Tier 0
# Patterns written against real RNS text (reports/build/uk_nav_samples.json):
#   "NAV per Share (including current financial year revenue items) 264.21p"
#   "Ordinary Share (including current year revenue) = 102.05p"
#   "unaudited net asset value ... per ordinary share ... was ... 264.21p"
# ZDP / preference-share lines are excluded; cum-income is primary and both
# variants are stored when published (brief §2 Tier 0).
UK_ASAT = re.compile(r"(?:as at|at)\s+(?:(?:the\s+)?close of business on\s+)?"
                     r"(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?"
                     r"(\d{1,2}\s+\w{3,9}\s+\d{4})", re.I)
# pence numbers may carry thousands commas: "1,941.32p"
UK_PNUM = r"(?:=\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)"
UK_PENCE = UK_PNUM + r"\s*p(?:ence)?\b"
UK_ZDP = re.compile(r"zero dividend|preference share", re.I)
# "per Ordinary Share of 1p" is the share's NOMINAL value, not its NAV. The
# plain fallback grabbed it for Chelverton Growth and recorded a 1p NAV
# against a 30p share price - a 2,900% premium that came from a company-law
# formality sitting one clause before the number we wanted.
UK_NOMINAL = re.compile(r"(?:ordinary\s+|preference\s+|\beach\s+)?shares?\s+of\s*$|"
                        r"nominal\s+value\s+of\s*$|par\s+value\s+of\s*$", re.I)

# Ordered rule list, written against the committed corpus
# (data/uk_nav_corpus.json.gz). Each entry: (kind, priority, regex).
# kind: 'cum' or 'ex'. Lower priority number wins within a kind; fair-value
# debt beats par, matching the research panel's AIC basis (FCCWETScum).
_R = re.compile
UK_RULES = [
    # Alliance Witan style: "Debt at fair value, including income: 1452.9 pence"
    ("cum", 0, _R(r"debt at fair value,?\s*(?:including|incl\.?)\s+income:?\s*" + UK_PENCE, re.I)),
    ("ex", 0, _R(r"debt at fair value,?\s*(?:excluding|excl\.?)\s+income:?\s*" + UK_PENCE, re.I)),
    # abrdn family: "with Debt at Fair Value Including Income 495.40p"
    ("cum", 0, _R(r"with Debt at Fair Value\s+Including Income\s+" + UK_PENCE, re.I)),
    ("ex", 0, _R(r"with Debt at Fair Value\s+Excluding Income\s+" + UK_PENCE, re.I)),
    ("cum", 1, _R(r"debt at par,?\s*(?:including|incl\.?)\s+income:?\s*" + UK_PENCE, re.I)),
    ("ex", 1, _R(r"debt at par,?\s*(?:excluding|excl\.?)\s+income:?\s*" + UK_PENCE, re.I)),
    # parenthesised revenue/income variants:
    # "(including current financial year revenue items) 264.21p"
    ("cum", 2, _R(r"\((?:including|incl\.?|cum)[^)]{0,60}(?:revenue|income)[^)]{0,20}\)[^0-9]{0,30}" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"\((?:excluding|excl\.?|ex)[^)]{0,60}(?:revenue|income)[^)]{0,20}\)[^0-9]{0,30}" + UK_PENCE, re.I)),
    # abrdn undiluted rows: "Undiluted Including Income 341.98p"
    ("cum", 2, _R(r"(?:Undiluted|Diluted)?\s*Including Income\s+" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"(?:Undiluted|Diluted)?\s*Excluding Income\s+" + UK_PENCE, re.I)),
    # Aberforth style bare labels: "Including ALL Revenue = 1,973.49p"
    # the "to <date>" clause is optional and its spacing varies: "20 th May
    # 2025" (Miton) and "25 th Jun 2026" (DIVI) both appear
    ("cum", 2, _R(r"\bincluding\s+(?:all\s+|current\s+(?:year|period)\s+)?revenue"
                  r"(?:\s+to\s+\d{1,2}\s*(?:st|nd|rd|th)?\s+\w{3,9}\s+\d{4})?[^0-9]{0,20}" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"\bexcluding\s+(?:all\s+|current\s+(?:year|period)\s+)?revenue"
                 r"(?:\s+to\s+\d{1,2}\s*(?:st|nd|rd|th)?\s+\w{3,9}\s+\d{4})?[^0-9]{0,20}" + UK_PENCE, re.I)),
    # bare income labels: "Cum Income 443.57p" / "EX Income 439.95p"
    ("cum", 2, _R(r"\b(?:cum|incl?\.?)[- ]income\b[^0-9]{0,25}" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"\bex[- ]income\b[^0-9]{0,25}" + UK_PENCE, re.I)),
    # Baillie Gifford: "Cum Fair NAV 137.05p ... Ex Par NAV 130.30p" - fair
    # value beats par, consistent with the panel basis
    ("cum", 1, _R(r"\bCum\s+Fair\s+NAV\s+" + UK_PENCE, re.I)),
    ("ex", 1, _R(r"\bEx\s+Fair\s+NAV\s+" + UK_PENCE, re.I)),
    ("cum", 2, _R(r"\bCum\s+Par\s+NAV\s+" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"\b(?:XD\s+)?Ex\s+Par\s+NAV\s+" + UK_PENCE, re.I)),
    # JPMorgan all-caps: "THE NAV PER SHARE IN PENCE, INCLUDING INCOME WITH
    # DEBT AT FAIR VALUE: 1,247.58" (no p suffix; decimal required)
    ("cum", 0, _R(r"INCLUDING INCOME[, ]+WITH DEBT AT FAIR VALUE:?[^0-9]{0,120}?([0-9][0-9,]*\.[0-9]+)", re.I)),
    ("ex", 0, _R(r"EXCLUDING INCOME[, ]+WITH DEBT AT FAIR VALUE:?[^0-9]{0,120}?([0-9][0-9,]*\.[0-9]+)", re.I)),
    ("cum", 3, _R(r"INCLUDING INCOME\s+WITH DEBT AT PAR(?:\s+VALUE)?:?\s*([0-9][0-9,]*\.[0-9]+)", re.I)),
    # Brunner prose: "based on the market value of ... debt ..., the
    # cum-income net asset value per ordinary share was 1745.2p"
    ("cum", 0, _R(r"market value of[^.]{0,90}?the cum-income net asset value per ordinary share was\s+" + UK_PENCE, re.I)),
    ("ex", 0, _R(r"market value of[^.]{0,90}?the capital net asset value per ordinary share was\s+" + UK_PENCE, re.I)),
    ("cum", 2, _R(r"par value of[^.]{0,90}?the cum-income net asset value per ordinary share was\s+" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"par value of[^.]{0,90}?the capital net asset value per ordinary share was\s+" + UK_PENCE, re.I)),
    # FCIT table: "Cum Income Ex Income ... Financial liabilities at fair
    # value 374.45 373.02" (cum column first)
    ("cum", 0, _R(r"Cum Income\s+Ex Income.{0,120}?at fair value\s+([0-9][0-9,]*\.[0-9]+)\s+[0-9]", re.I | re.S)),
    ("ex", 0, _R(r"Cum Income\s+Ex Income.{0,120}?at fair value\s+[0-9][0-9,]*\.[0-9]+\s+([0-9][0-9,]*\.[0-9]+)", re.I | re.S)),
    # Hansa: "Cum Income NAV per Ordinary and 'A' Ordinary Share* 545.63p"
    ("cum", 2, _R(r"Cum Income NAV per[^0-9]{0,60}" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"Ex Income NAV per[^0-9]{0,60}" + UK_PENCE, re.I)),
    # DIVI: "Including current period revenue to 25 th Jun 2026 119.51 per
    # ordinary share" (spaced ordinal, no p suffix)
    ("cum", 3, _R(r"including current (?:period|year) revenue(?:\s+to\s+\d{1,2}\s*(?:st|nd|rd|th)?\s+\w{3,9}\s+\d{4})?\s+([0-9][0-9,]*\.[0-9]+)\s+per ordinary share", re.I)),
    ("ex", 3, _R(r"excluding current (?:period|year) revenue\s+([0-9][0-9,]*\.[0-9]+)(?:\s+per ordinary share)?", re.I)),
    # Schroder table (no pence suffix): "Cum Income 729.93" - decimal required
    ("cum", 3, _R(r"\bCum Income\s+([0-9][0-9,]*\.[0-9]+)\b", re.I)),
    ("ex", 3, _R(r"\bEx Income\s+([0-9][0-9,]*\.[0-9]+)\b", re.I)),
    # value-first (Aurora / BlackRock): "290.41p per ordinary share (cum-income)"
    # / "195.37p Including current year income" / "194.65p Capital only"
    ("cum", 3, _R(UK_PENCE + r"(?:\s+per\s+ordinary\s+share)?\s*\(?cum[- ]income\)?", re.I)),
    ("ex", 3, _R(UK_PENCE + r"(?:\s+per\s+ordinary\s+share)?\s*\(?ex[- ]income\)?", re.I)),
    ("cum", 3, _R(UK_PENCE + r"(?:\s+per\s+share)?(?:\s*\(pence sterling\))?\s*-?"
                  r"\s+including\s+current\s+(?:year|period)\s+(?:income|revenue)", re.I)),
    ("ex", 3, _R(UK_PENCE + r"(?:\s+per\s+share)?(?:\s*\(pence sterling\))?\s*-?"
                 r"\s+capital\s+only", re.I)),
    # BEMO: "Including current period revenue to 26 August 2026 991.15 pence"
    # (covered by the Aberforth-style rule via the optional date group)

    # ---------------------------------------------------------------
    # Layouts added 2026-08-31 after measuring the parser against the
    # committed corpus (data/uk_nav_corpus.json.gz): 39 of 175 real
    # announcements produced no value, and the funds behind them were not
    # obscure - Fidelity, CQS, Columbia Threadneedle, Personal Assets,
    # Law Debenture, Temple Bar, Templeton, Witan. Every rule below is
    # anchored on wording taken from one of those documents.
    #
    # The generic fallback is NOT relaxed to reach them. It used to be
    # `net asset value[^0-9]{0,220}?`, which cannot cross the date in
    # "as at 28 August 2026 was 199.66 pence" - but widening it to allow
    # digits would let it wander into a multi-class table and staple the
    # sterling class's NAV onto the dollar class's ticker (BH Macro and
    # CVC publish exactly that layout). A wrong NAV is worse than none,
    # so the reach comes from anchored rules instead.

    # "Cum Income ... Ex Income" tables. Column order is NOT fixed: Witan
    # and Temple Bar put cum first, Law Debenture puts ex first, so the
    # header order is matched, never assumed.
    ("cum", 1, _R(r"Cum Income[^0-9]{0,40}?Ex[- ]?(?:Income|dividend)[\s\S]{0,300}?"
                  r"at fair value\s+([0-9][0-9,]*\.[0-9]+)\s+[0-9]", re.I)),
    ("ex", 1, _R(r"Cum Income[^0-9]{0,40}?Ex[- ]?(?:Income|dividend)[\s\S]{0,300}?"
                 r"at fair value\s+[0-9][0-9,]*\.[0-9]+\s+([0-9][0-9,]*\.[0-9]+)", re.I)),
    ("cum", 2, _R(r"Excluding Income \(pence\)\s*Including Income \(pence\)"
                  r"[\s\S]{0,300}?debt at fair value\s+[0-9][0-9,]*\.[0-9]+\s+"
                  r"([0-9][0-9,]*\.[0-9]+)", re.I)),
    ("ex", 2, _R(r"Excluding Income \(pence\)\s*Including Income \(pence\)"
                 r"[\s\S]{0,300}?debt at fair value\s+([0-9][0-9,]*\.[0-9]+)", re.I)),
    # Columbia Threadneedle / European Assets: "Cum Income Ex Income <name>
    # LEI: <alphanumeric> 121.04 119.84" (an LEI carries digits but never a
    # decimal point, so requiring one keeps the match on the values)
    ("cum", 3, _R(r"Cum Income\s+Ex[- ]?Income[\s\S]{0,260}?"
                  r"([0-9][0-9,]*\.[0-9]{2})(?:\s+(?:[0-9][0-9,]*\.[0-9]{2}|-))", re.I)),
    ("ex", 3, _R(r"Cum Income\s+Ex[- ]?Income[\s\S]{0,260}?"
                 r"[0-9][0-9,]*\.[0-9]{2}\s+([0-9][0-9,]*\.[0-9]{2})", re.I)),
    # Momentum: the header words interleave - "Cum Ex Income Income"
    ("cum", 3, _R(r"\bCum\s+Ex\s+Income\s+Income\s+([0-9][0-9,]*\.[0-9]{2})", re.I)),
    ("ex", 3, _R(r"\bCum\s+Ex\s+Income\s+Income\s+[0-9][0-9,]*\.[0-9]{2}\s+"
                 r"([0-9][0-9,]*\.[0-9]{2})", re.I)),

    # Juniper / PR Newswire prose: "The unaudited cum-income net asset
    # values ... were: 555.50 pence per share" (Personal Assets, Montanaro,
    # Strategic Equity, Templeton)
    ("cum", 2, _R(r"cum[- ]income net asset value[\s\S]{0,220}?" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"ex[- ]income (?:net asset value|NAV)[\s\S]{0,220}?" + UK_PENCE, re.I)),

    # "Including income: 407.52 pence per share" (Global Opportunities, STS)
    ("cum", 2, _R(r"\bIncluding income:?\s*" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"\bExcluding income:?\s*" + UK_PENCE, re.I)),
    # value-first variants: "350.07p per ordinary share including income"
    ("cum", 3, _R(UK_PENCE + r"\s+per\s+(?:ordinary\s+)?share[, ]{0,3}"
                  r"(?:\()?including\s+income", re.I)),
    ("ex", 3, _R(UK_PENCE + r"\s+per\s+(?:ordinary\s+)?share[, ]{0,3}"
                 r"(?:\()?excluding\s+income", re.I)),
    # Fidelity daily: "net asset value (unaudited) ... was: 281.80p"
    ("cum", 3, _R(r"net asset value\s*\(unaudited\)[\s\S]{0,200}?" + UK_PENCE, re.I)),
    # Geiger / Golden Prospect: "NAV per share - undiluted, bid basis 130.62 pence"
    ("cum", 2, _R(r"NAV per share\s*[-\u2013]\s*undiluted[^0-9]{0,40}" + UK_PENCE, re.I)),
    ("ex", 4, _R(r"NAV per share\s*[-\u2013]\s*(?:fully\s+)?diluted[^0-9]{0,40}" + UK_PENCE, re.I)),
    # Chelverton / CQS New City / Downing: "Per Ordinary share (bid price) -
    # including unaudited current period revenue* 155.92p"
    ("cum", 2, _R(r"Per Ordinary share\s*\([^)]{0,25}\)\s*[-\u2013]\s*including"
                  r"[^0-9]{0,70}" + UK_PENCE, re.I)),
    ("ex", 2, _R(r"Per Ordinary share\s*\([^)]{0,25}\)\s*[-\u2013]\s*excluding"
                 r"[^0-9]{0,70}" + UK_PENCE, re.I)),
    # The Investment Company: "Per Ordinary Share: 78.5p"; Chelverton Growth
    # writes the same row without the colon ("Per Ordinary Share 55.03p")
    ("cum", 3, _R(r"Per Ordinary Share:?\s*" + UK_PENCE, re.I)),
    # CQS Natural Resources: "was 421.52 pence, including unaudited current
    # period revenue"
    ("cum", 2, _R(UK_PENCE + r"[, ]{0,3}including unaudited current period revenue", re.I)),
    # Lindsell Train: "Net Asset Value (inclusive of accumulated income) ...
    # 21 August 2026 698.83p per Ordinary share"
    ("cum", 3, _R(r"Net Asset Value \(inclusive of accumulated income\)"
                  r"[\s\S]{0,300}?" + UK_PENCE, re.I)),
    # Majedie mid-month estimate
    ("cum", 3, _R(r"net asset value estimate per share[\s\S]{0,180}?" + UK_PENCE, re.I)),
    # SVM: "net asset value per share of the following Investment Trust ...
    # 96.29p"
    ("cum", 3, _R(r"net asset value per share of the following[\s\S]{0,220}?"
                  + UK_PENCE, re.I)),
    # Scottish Oriental: "3xx.xx pence per share (including income)"
    ("cum", 3, _R(UK_PENCE + r"\s+per share\s*\(including income", re.I)),
    ("ex", 3, _R(UK_PENCE + r"\s+per share\s*\(excluding income", re.I)),
    # Hydrogen Capital Growth: "quarterly NAV per share of the Company
    # (the "31 December NAV") was 30.54 pence"
    ("cum_assumed", 5, _R(r"NAV per share[\s\S]{0,140}?" + UK_PENCE, re.I)),

    # "capital only" is the ex-income basis stated in words: Allianz
    # Technology writes "the capital only net asset value per ordinary share
    # was 742.94p", Mid-Wynd writes "Capital only: 810.14p"
    ("ex", 3, _R(r"capital[- ]only net asset value[\s\S]{0,140}?" + UK_PENCE, re.I)),
    ("ex", 3, _R(r"\bCapital only:?\s*" + UK_PENCE, re.I)),

    # Greencoat: "unaudited Net Asset Value as of 31 December 2025 is
    # GBP2,939.1 million (136.1 pence per share)"
    ("cum_assumed", 4, _R(r"Net Asset Value[\s\S]{0,140}?\(" + UK_PENCE
                          + r"\s+per share\)", re.I)),

    # plain fallback: "net asset value ... 123.45p"
    ("cum_assumed", 9, _R(r"net asset value[^0-9]{0,220}?" + UK_PENCE, re.I)),
]

UK_HDR_NAME = re.compile(r"Net Asset Value\(s\)\s+(.{5,90}?)\s+\d{1,2}\s+\w{3,9}\s+20\d\d")


def parse_uk_nav_text(text: str) -> dict:
    """Cum/ex-income NAV per share (pence) from RNS text - rule-list parser
    written against the committed corpus of real announcement pages.

    ZDP / preference-share entitlements are excluded per candidate match;
    fair-value-debt figures outrank par (panel basis FCCWETScum); the plain
    fallback is flagged cum_assumed. Ambiguity yields absence, never a guess.
    """
    def hits(pat):
        for m in pat.finditer(text):
            before = text[max(0, m.start() - 90):m.start()]
            if UK_ZDP.search(before):
                continue
            if UK_NOMINAL.search(before[-30:]):
                continue          # a nominal/par value, not a NAV
            yield float(m.group(1).replace(",", ""))

    asat = None
    m = UK_ASAT.search(text)
    if m:
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
        except Exception:  # noqa: BLE001
            pass

    best: dict = {}
    for kind, prio, pat in UK_RULES:
        if kind in ("cum", "cum_assumed") and best.get("_cum_prio", 99) <= prio:
            continue
        if kind == "ex" and best.get("_ex_prio", 99) <= prio:
            continue
        v = next(hits(pat), None)
        if v is None:
            continue
        if kind == "ex":
            best["nav_ex_pence"] = v
            best["_ex_prio"] = prio
        else:
            best["nav_cum_pence"] = v
            best["_cum_prio"] = prio
            if kind == "cum_assumed":
                best["cum_assumed"] = True

    out = {k: v for k, v in best.items() if not k.startswith("_")}
    if asat:
        out["asat"] = asat
    return out


def harvest_uk(ticker_map: pd.DataFrame, census: pd.DataFrame,
               lookback_days: int = 7, budget: int = 0,
               extra_targets: dict[str, str] | None = None,
               deadline_min: float = 60.0) -> pd.DataFrame:
    """Published NAV for EVERY fund we can address, from its own RNS page.

    The registry (AIC/ASX files) is used for identity only. Every live fund
    with a resolved ticker is polled here - not just the ones the research
    crawl happened to cover, and not just the ones the registry declines to
    price. A fund is absent from this output only because it published no
    parseable NAV, never because we did not ask.

    Returns (navs, announcement rows); the announcement rows feed the
    catalyst scan from the same fetched pages.
    """
    tick2sid = dict(zip(ticker_map["ticker"], ticker_map["security_id"]))
    if extra_targets:
        tick2sid.update({t: sid for t, sid in extra_targets.items() if t})
    census_tickers = [t for t in census["ticker"]] if len(census) else []
    unmapped = [t for t in census_tickers if t not in tick2sid]
    # order: funds with no other NAV source first, then known publishers,
    # then everything else addressable - but ALL of them are targets
    first = [t for t in (extra_targets or {})]
    seen = set(first)
    second = [t for t in census_tickers if t in tick2sid and t not in seen]
    seen |= set(second)
    rest = [t for t in tick2sid if t not in seen]
    ordered = first + second + rest
    # No item cap by default. A budget of 400 silently dropped 162 funds
    # once ticker resolution took the addressable universe to 562 - the
    # precise failure this module's docstring rules out, arriving through
    # a constant rather than a filter. At 1.5s per fund the whole universe
    # is ~15 minutes, so wall-clock is the only control that is needed;
    # politeness is the throttle's job.
    targets = ordered[:budget] if budget else ordered
    started = _t.time()
    stats = {"targets": len(targets), "unmapped_tickers": len(unmapped),
             "registry_only_targets": len(extra_targets or {}),
             "listing_fail": 0, "no_recent_nav": 0, "detail_fail": 0,
             "parse_fail": 0, "parsed": 0, "fail_samples": []}
    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    pat = re.compile(r"net asset value", re.I)
    rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    # every listing page is already being fetched for NAV; the catalyst rows
    # are on the same page, so collecting them costs no extra requests
    cat_cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    ann_rows: list[dict] = []
    for tk in targets:
        if (_t.time() - started) > deadline_min * 60:
            stats["deadline_reached_after"] = len(rows)
            break
        _t.sleep(1.5)
        try:
            r = s.get(f"https://www.investegate.co.uk/company/{tk}", timeout=45)
            if r.status_code != 200:
                stats["listing_fail"] += 1
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:  # noqa: BLE001
            stats["listing_fail"] += 1
            continue
        best = None
        for tr in soup.select("table.table-investegate tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tds[3].find("a", href=True)
            if a is None or "/announcement/" not in a.get("href", ""):
                continue
            head = a.get_text(" ", strip=True)
            try:
                d = pd.to_datetime(tds[0].get_text(" ", strip=True),
                                   dayfirst=True).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
            href = a["href"]
            if d >= cat_cutoff:
                ann_rows.append({
                    "security_id": tick2sid[tk], "date": d, "headline": head,
                    "url": ("https://www.investegate.co.uk" + href)
                           if href.startswith("/") else href})
            if not pat.search(head):
                continue
            if d >= cutoff:
                best = best or {"date": d,
                                "url": ("https://www.investegate.co.uk" + href)
                                       if href.startswith("/") else href,
                                "headline": head}
                # keep scanning the page so catalysts below the NAV row are
                # still collected (rows are newest-first)
        if best is None:
            stats["no_recent_nav"] += 1
            continue
        _t.sleep(1.5)
        try:
            r = s.get(best["url"], timeout=45)
            text = re.sub(r"\s+", " ",
                          BeautifulSoup(r.text, "html.parser").get_text(" "))
        except Exception:  # noqa: BLE001
            stats["detail_fail"] += 1
            continue
        got = parse_uk_nav_text(text)
        if "nav_cum_pence" not in got:
            stats["parse_fail"] += 1
            if len(stats["fail_samples"]) < 8:
                stats["fail_samples"].append({"ticker": tk, "url": best["url"],
                                              "text_head": text[200:6500]})
            continue
        stats["parsed"] += 1
        rows.append({"security_id": tick2sid[tk],
                     "nav_date": got.get("asat", best["date"]),
                     "nav_value": got["nav_cum_pence"],
                     "nav_ex": got.get("nav_ex_pence"),
                     "cum_assumed": got.get("cum_assumed", False),
                     "source": f"investegate:{best['url'].rsplit('/', 1)[-1]}",
                     "headline": best["headline"][:120]})
    try:
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        import json as _json
        stats["announcements_seen"] = len(ann_rows)
        Path("reports/build/uk_tier0_debug.json").write_text(
            _json.dumps(stats, indent=1))
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame(rows), ann_rows


def uk_nav_samples(cache_dir: Path, n: int = 5) -> list[dict]:
    """Fetch a handful of recent UK NAV announcement pages (throttled) and
    return their text heads - parser-design evidence, committed to
    reports/build so the value parser is written against real RNS text,
    never guessed."""
    import requests

    pat = re.compile(r"net asset value", re.I)
    listings = cache_dir / "listings"
    cands = []
    for f in sorted(listings.glob("*.csv")) if listings.exists() else []:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if not {"headline", "url", "date"} <= set(df.columns):
            continue
        nav = df[df["headline"].fillna("").str.contains(pat) & df["url"].notna()]
        for r in nav.itertuples(index=False):
            cands.append({"ticker": f.stem, "date": r.date, "url": r.url})
    cands.sort(key=lambda c: str(c["date"]), reverse=True)
    # one per ticker, most recent first, for layout diversity
    seen, picked = set(), []
    for c in cands:
        if c["ticker"] in seen:
            continue
        seen.add(c["ticker"])
        picked.append(c)
        if len(picked) >= n:
            break
    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    out = []
    for c in picked:
        _t.sleep(1.5)
        url = c["url"]
        if url.startswith("/"):
            url = "https://www.investegate.co.uk" + url
        try:
            r = s.get(url, timeout=45)
            text = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" "))
            out.append({**c, "status": r.status_code, "text_head": text[:2500]})
        except Exception as exc:  # noqa: BLE001
            out.append({**c, "status": f"error:{exc}"})
    return out
