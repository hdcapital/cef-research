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

import collections
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

# UK NAV headline selector - ONE pattern, shared by the archiver
# (scripts/archive_uk_navs.py), the frequency census and the Tier-0
# harvest below. Three separate copies of "net asset value" is how the ASX
# side lost UWC four times over. Widened to bare NAV on probe evidence
# (reports/build/uk_nav_reach_probe.json): Chrysalis publishes "Quarterly
# NAV Announcement and Trading Update" and never the full phrase -
# \bNAVs?\b matches the abbreviation without matching "Navigator".
UK_NAV_HEAD = re.compile(r"net asset value|\bNAVs?\b", re.I)

# Update/results headlines that may carry the NAV for the cohort that
# publishes no NAV-shaped RNS at all (measured:
# reports/build/uk_nav_reach_probe.json - BSIF, HGT, ICGT, 3IN, the
# REITs). Used ONLY for funds where UK_NAV_HEAD matches nothing recent,
# never universe-wide: fetching every trust's results would multiply the
# crawl for documents whose NAV we already hold from daily RNS.
UK_FACTSHEET_HEAD = re.compile(
    r"portfolio update|trading update|business update|monthly update|"
    r"quarterly update|interim update|fact\s?sheet|"
    r"investor (?:update|report)|EPRA|periodic valuation|valuation update|"
    r"(?:half[- ]?year(?:ly)?|annual|interim|final|full[- ]?year|"
    r"quarter(?:ly)?|[1-4](?:st|nd|rd|th) quarter) "
    r"(?:results?|report|financial report)|results? for the", re.I)
# a third party's research note is never a source for a fund's NAV
UK_THIRD_PARTY = re.compile(
    r"kepler|edison|quoteddata|hardman|analysis from|research", re.I)
# Investegate announcement URLs carry the company's own slug:
#   /announcement/rns/<company-slug>--<ticker>/<headline-slug>/<id>
# A DEAD ticker's company page silently serves the MARKET-WIDE feed, so a
# listing cache for one can fill with other companies' announcements - 56
# funds the AIC delisted years ago came back "announced today" through
# exactly this, resurrecting them into the live universe. Every reader of
# ticker-keyed announcement rows must therefore check the row's own slug.
_URL_TICKER = re.compile(r"--([a-z0-9.]{2,8})/")


def uk_row_matches_ticker(url: str, ticker: str) -> bool:
    """Does this announcement URL belong to this ticker's company?

    True when the URL's company-slug ticker suffix matches, and also when
    the URL carries no recognisable slug (older cache rows) - absence of
    evidence is not evidence of contamination. False only on a positive
    mismatch, which is the market-feed fallback signature.
    """
    m = _URL_TICKER.search(str(url or "").lower())
    if m is None:
        return True
    return m.group(1) == str(ticker or "").lower()


# Investegate's model-generated summary panel is deliberately NOT stripped
# before parsing. It paraphrases THIS announcement's own figure, so a match
# inside it nearly always yields the correct number - and stripping it was
# measured to cost four corpus parses (BIPS, GRP, IAD, IGET) whose value
# sits before the first reliable body anchor. The residual risk of a
# summary mis-stating the figure is exactly what the nightly AIC
# cross-validation (nav_validation.run_uk) exists to police, per fund.

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
    r"monthly (?:report|update|investment)|investment update|"
    # Underwood Capital publishes its monthly NTA as "UWC Investment
    # Portfolio Performance July 2026", and Australian Leaders as "Monthly
    # Portfolio Performance Update". Neither headline contains any of the
    # words above, so both funds' monthly NAV was indexed, sitting in the
    # announcement index with a working PDF link, and never fetched.
    r"portfolio performance|portfolio (?:update|disclosure|valuation)", re.I)


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


# "Net Tangible Asset per share - $ 0.1047 0.1033 ... pre-tax" - the
# monthly-report layout, where the pre/post-tax qualifier follows the
# numbers instead of preceding the label, and two-column page furniture
# lands between them.
ASX_PER_SHARE_LABEL = re.compile(
    r"(?:net\s+tangible\s+assets?|\bNTA\b)\s+per\s+share", re.I)
ASX_NUM = re.compile(r"[0-9]*\.[0-9]{3,6}")
ASX_PRE_TAX = re.compile(r"\bpre[\s-]?tax\b|\bbefore\s+tax\b", re.I)
ASX_POST_TAX = re.compile(r"\bpost[\s-]?tax\b|\bafter\s+tax\b", re.I)
# "Key Metrics as at 31-Jul-26 30-Jun-26" - the column order is stated, so
# it is read rather than assumed. Law Debenture taught that lesson on the
# UK side: two funds put the same two columns in opposite orders.
ASX_ASAT_HEADER = re.compile(
    r"as at\s+(\d{1,2}-\w{3}-\d{2,4})\s+(\d{1,2}-\w{3}-\d{2,4})", re.I)


# "NTA per unit as at 26 August 2026 $2.0172" - the label, then a DATE,
# then the value. The scored parser's gap between label and number cannot
# cross digits, so the date defeats it: Gryphon, 360 Capital, Qualitas,
# Perpetual Credit and both Metrics trusts all publish this shape and all
# six lost their NAV to it. Exactly the root cause of the UK "was N pence"
# family, one market over.
#
# The literal "$" is what makes the wide gap safe. Without it the pattern
# could pick up a fragment of the date it was written to cross; with it,
# the match must land on a stated dollar amount. Two to six decimals keeps
# it off "$762m" and "$19.0m".
ASX_PER_UNIT_DOLLARS = re.compile(
    r"(?:\bNTA\b|\bNAV\b|net\s+tangible\s+assets?|net\s+asset\s+value)"
    r"\s*(?:backing\s*)?(?:value\s*)?"
    r"per\s+(?:ordinary\s+)?(?:share|unit|security)"
    r"[\s\S]{0,90}?\$\s*([0-9]+\.[0-9]{2,6})\b", re.I)


def _asx_per_unit_dollars(text: str) -> float | None:
    """A per-unit NTA stated in dollars, with a date allowed in between."""
    m = ASX_PER_UNIT_DOLLARS.search(text)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _asx_pretax_per_share(text: str) -> float | None:
    """The CURRENT PRE-TAX NTA per share from a monthly-report table.

    Two things have to be right and neither is the number itself.

    Basis: this system's ASX convention is pre-tax throughout, and the
    generic parser returned Underwood Capital's POST-tax figure (0.0966)
    because it is the later of the two identical labels. Reading the wrong
    basis silently changes every discount computed from it.

    Column: the row carries this month and last month side by side -
    "0.1047 0.1033" under "as at 31-Jul-26 30-Jun-26". Taking the wrong one
    is a month-old NAV wearing today's date, which is the failure the whole
    staleness apparatus exists to make visible.
    """
    newest_first = True
    m = ASX_ASAT_HEADER.search(text)
    if m:
        try:
            d1 = pd.to_datetime(m.group(1), dayfirst=True)
            d2 = pd.to_datetime(m.group(2), dayfirst=True)
            newest_first = d1 >= d2
        except Exception:  # noqa: BLE001
            pass
    best = None
    for lab in ASX_PER_SHARE_LABEL.finditer(text):
        window = text[lab.end():lab.end() + 200]
        nums = ASX_NUM.findall(window[:60])
        if not nums:
            continue
        pre = ASX_PRE_TAX.search(window)
        post = ASX_POST_TAX.search(window)
        # whichever qualifier is nearer describes THIS row
        if post and (not pre or post.start() < pre.start()):
            continue
        if not pre:
            continue
        try:
            vals = [float(n) for n in nums]
        except ValueError:
            continue
        best = vals[0] if newest_first else vals[-1]
        break
    return best


def _note_failure(stats: dict, code: str, headline: str, url: str,
                  status: str | None, text: str) -> None:
    """Record one unreadable document: per CODE, and always the outcome.

    The first version of this kept the first 40 failures it saw, which
    sounds neutral and is not. The loop runs newest-first across every
    fund, so the sample fills up with whoever announced in the last few
    days - and the funds this diagnostic exists for are the MONTHLY
    reporters, whose one announcement is two or three weeks old and
    therefore always arrives after the cap. Underwood Capital's failure was
    dropped that way: 52 documents failed, 24 codes were sampled, and the
    one fund the instrumentation was added for was not among them.

    So the outcome is now recorded for EVERY code (a short string, cheap),
    and the text sample is kept once per code rather than first-come.
    """
    stats.setdefault("by_code", {})[code] = status or "unknown"
    samples = stats.setdefault("fail_samples", [])
    if any(x.get("code") == code for x in samples):
        return                      # one sample per fund, not forty per week
    if len(samples) >= 60:
        return
    samples.append({"code": code, "headline": (headline or "")[:120],
                    "url": url, "status": status, "text_head": text})


def _nta_from_document(doc: dict, headline: str) -> dict | None:
    """The stated per-share NTA from one parsed PDF, or None.

    Routed through au_lic.extract.deterministic.extract_nta - the SAME
    extractor the archive's deterministic pass uses - rather than calling
    the scored parser directly. That pass reads about 72% of the documents
    it is given; this harvest was reading far fewer of the same documents,
    because it skipped the two things the extractor does first:
    normalise_nta_text (which repairs the run-together text pdfplumber
    produces for these layouts) and a retry on the raw text when the
    normalised form does not match. It also derives the as-at date from the
    document rather than only from the headline.

    Two parsers for one corpus is two things to keep in step, and the one
    with the lower hit rate was the one feeding the live table.
    """
    try:
        from au_lic.extract import deterministic as D
    except Exception:  # noqa: BLE001
        D = None

    # the monthly-report pre-tax row first: it is the only reader that knows
    # which of two identically-labelled rows is the pre-tax one, and which
    # of two side-by-side columns is this month
    text = doc.get("text") or ""
    asat = (D._asat(text, headline or "") if D is not None else None)

    # ORDER MATTERS, and it is basis-aware first. The pre-tax reader knows
    # which of two identically-labelled rows is the pre-tax one and which
    # of two side-by-side columns is this month; the plain reader below
    # knows neither and takes the first dollar amount after the label.
    #
    # Run the other way round, UWC still returned 0.1047 - but only because
    # its pre-tax row happens to come first. The basis fell to "unknown",
    # the valuation date was lost, and a fund that printed post-tax first
    # would have been read silently wrong. A right answer for the wrong
    # reason is not a right answer.
    pre = _asx_pretax_per_share(text)
    if pre is not None:
        return {"nav_per_share": pre, "unit_source": "dollars",
                "nav_basis": "pre_tax", "valuation_date": asat,
                "extractor": "asx_monthly_pretax_v1"}
    if D is not None:
        try:
            got = D.extract_nta(doc.get("text") or "", doc.get("rows") or [],
                                headline or "")
        except Exception:  # noqa: BLE001
            got = []
        if got:
            g = got[0]
            if g.get("nav_per_share") is not None:
                return {"nav_per_share": g["nav_per_share"],
                        "unit_source": g.get("unit"),
                        "nav_basis": g.get("nav_basis"),
                        "valuation_date": g.get("valuation_date"),
                        "extractor": g.get("extractor", "nta_scored_v1")}
    # Only once the table-aware extractor has declined. It reads the row
    # structure these documents are built from, so letting a text pattern
    # pre-empt it would trade a considered answer for a nearby one; the
    # six funds this rule exists for are precisely the ones the extractor
    # returns nothing for.
    plain = _asx_per_unit_dollars(text)
    if plain is not None:
        return {"nav_per_share": plain, "unit_source": "dollars",
                "nav_basis": "unknown", "valuation_date": asat,
                "extractor": "asx_per_unit_dollars_v1"}

    # last resort: the scored parser on the raw text, so a failure here is
    # still a parser failure rather than an import problem
    res = P.derive_stated(doc)
    if res.get("status") != "parsed" or res.get("stated_raw") is None:
        return None
    unit = res.get("unit")
    val = res["stated_raw"]
    return {"nav_per_share": val / 100.0 if unit == "cents" else val,
            "unit_source": unit, "nav_basis": res.get("basis"),
            "valuation_date": None, "extractor": "derive_stated"}


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
             "parse_failed": 0, "ambiguous_unit": 0,
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
            # No as-at date in the headline. That is not a reason to skip
            # the document: this row is already an AU_NAV_HEAD candidate,
            # the extractor derives a valuation date from the document
            # itself where there is one, and the release date is an honest
            # labelled fallback where there is not.
            #
            # There used to be a SECOND headline pattern here deciding
            # whether the release date could be used - a fourth copy of
            # "headlines that carry an NTA", and the one I did not unify.
            # "UWC Investment Portfolio Performance July 2026" matches the
            # candidate filter and matches none of daily / weekly / monthly
            # / NTA / net tangible / NAV / fund update / investment update,
            # so Underwood Capital was counted as `no_date` and never
            # fetched at all. The document parses correctly; it was simply
            # never asked for. Same drift, fourth copy, third time it hid
            # the same fund.
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
        doc = P.parse_pdf(s, str(r.id), r.url, counters)
        if doc.get("status") != "extracted":
            # an image-only scan is a different fact from a failed fetch:
            # no text parser will ever read the first, and the second is
            # worth retrying
            key = ("image_only" if doc.get("status") == "no_text_layer"
                   else "fetch_failed")
            stats[key] = stats.get(key, 0) + 1
            _note_failure(stats, code, head, r.url, doc.get("status"), "")
            continue
        got = _nta_from_document(doc, head)
        if got is None:
            stats["parse_failed"] += 1
            # keep WHAT could not be read, as the UK harvester does. A count
            # says how many failed; a sample says why, and is the difference
            # between guessing at a layout and reading one.
            _note_failure(stats, code, head, r.url, "no_nta_parsed",
                          # 1,200 characters reached only the cover text on
                          # five of the twenty-eight failures - Metrics,
                          # Ophir, Cadence and the Future Generation funds
                          # front-load commentary and put the NTA table
                          # after it, so the sample showed prose and no
                          # number. A sample that cannot reach the value is
                          # not a sample of the problem.
                          (doc.get("text") or "")[:4000])
            continue
        if got.get("unit_source") == "ambiguous":
            stats["ambiguous_unit"] += 1
            continue                      # flagged, never guessed
        stats["parsed"] += 1
        stats.setdefault("by_code", {})[code] = "parsed"
        done.add(code)
        if got.get("valuation_date"):
            asat = pd.Timestamp(got["valuation_date"])
            date_src = "as_at_document"
        rows.append({"security_id": f"ASX:{code}",
                     "nav_date": asat.date().isoformat(),
                     # already reduced to AUD by the extractor; the unit it
                     # was STATED in is kept beside it so one conversion is
                     # the only conversion
                     "nav_value": got["nav_per_share"],
                     "unit": "dollars",
                     "basis_note": (got.get("nav_basis") or "pre_tax")
                                   + f"|{date_src}|unit_source={got.get('unit_source')}"
                                   + f"|extractor={got.get('extractor')}",
                     "source": f"asx_ann:{r.id}", "headline": head[:120]})

    stats["codes_parsed"] = len(done)
    seen_codes = {str(r.code) for r in cands}
    for c in sorted(str(x) for x in codes):
        stats.setdefault("by_code", {}).setdefault(
            c, "no_candidate_in_window" if c not in seen_codes else "not_attempted")
    stats["by_outcome"] = dict(sorted(collections.Counter(
        stats.get("by_code", {}).values()).items()))
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
    pat = UK_NAV_HEAD
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
# A number sitting next to a DIVIDEND is a dividend. The looser rules added
# for the "was N pence" family reached these before the guard existed:
# Gore Street came back at 1.9p and NextEnergy at 1.79p, both of which are
# the quarterly distribution, against real NAVs near 100p.
UK_DIVIDEND = re.compile(r"\bdividend\b|\bdistribution\b", re.I)
# ...and a number sitting next to a FOREIGN unit is not pence. Schiehallion
# publishes "Cum NAV* 161.54cents" in US cents; reading that as pence is the
# unit bug in its most direct form.
UK_FOREIGN = re.compile(r"\bcents?\b|US\s*\$|\bUSD\b|\bEUR\b|€|\bCAD\b", re.I)

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
    # ---- factsheet / results-route shapes, written against the committed
    # probe of real update bodies (reports/build/uk_factsheet_probe.json).
    # Direction matters: the plain fallback read RECI's PRIOR month (140.6p
    # from "decreased from 140.6p in June to 138.2p in July") and read
    # LBOW's "£2.42 million" as a NAV. These sit before the loose family so
    # the directional reading wins.
    # RECI: "NAV per share decreased from 140.6p in June to 138.2p in July"
    ("cum_assumed", 4, _R(r"(?:net asset value|\bNAV\b)[^.]{0,120}?\bfrom\s+"
                          r"[0-9][0-9,]*(?:\.[0-9]+)?\s*p(?:ence)?\b"
                          r"[^.]{0,80}?\bto\s+" + UK_PENCE, re.I)),
    # LBOW: "net asset value per share falling to 17.15 pence from 27.15"
    ("cum_assumed", 4, _R(r"(?:net asset value|\bNAV\b)[^.]{0,100}?"
                          r"(?:\b(?:fell|falling|rose|rising|increas\w*|"
                          r"decreas\w*|declin\w*|down|up)\b[^.]{0,30}?)?"
                          r"\bto\s+" + UK_PENCE + r"[^.]{0,80}?\bfrom\b",
                          re.I)),
    # AEET results table: 'Net asset value ("NAV") per share (pence)
    # 50.15 85.55' - current period first, per the "At <date> At <date>"
    # header convention; the validator polices the funds that invert it
    ("cum_assumed", 4, _R(r"net asset value[^0-9]{0,80}?\(pence\)\s*"
                          + UK_PNUM, re.I)),
    # THRL / the REIT cohort: "EPRA Net Tangible Assets per share to
    # 120.6 pence" - EPRA NTA is that cohort's published NAV basis
    ("cum_assumed", 4, _R(r"EPRA\s+(?:NTA|net tangible assets?)"
                          r"[^0-9]{0,80}?" + UK_PENCE, re.I)),

    # TwentyFour Income (Northern Trust table): "FUND NAME NAV ISIN NAV DATE
    # Twenty Four Income Fund Limited 106.38 GG00B90J5Z95 28th Aug 2026" -
    # the pence figure sits between the fund name and the ISIN with no
    # unit at all; 806 NAV announcements never parsed for want of a 'p'.
    ("cum_assumed", 3, _R(r"FUND NAME\s+NAV\s+ISIN[\s\S]{0,160}?\s"
                          r"([0-9]{1,5}\.[0-9]{2,4})\s+"
                          r"(?:GB|GG|JE|IE|LU)[0-9A-Z]{10}\b", re.I)),
    # Diverse Income: "Including current period revenue to 25th June 2026
    # 119.51 per ordinary share" - no pence marker after the number
    ("cum", 2, _R(r"Including current (?:period|year) revenue[^0-9]{0,80}?"
                  r"(?:[0-9]{1,2}(?:st|nd|rd|th)?\s+\w{3,9}\s+20[0-9]{2}\s+)?"
                  r"([0-9]{1,5}\.[0-9]{2})\s+per ordinary share", re.I)),
    ("ex", 2, _R(r"Excluding current (?:period|year) revenue[^0-9]{0,40}?"
                 r"([0-9]{1,5}\.[0-9]{2})\s+per ordinary share", re.I)),
    # Hydrogen Capital Growth: "quarterly NAV per share of the Company
    # (the "31 December NAV") was 30.54 pence"
    ("cum_assumed", 5, _R(r"NAV per share[\s\S]{0,140}?" + UK_PENCE, re.I)),

    # "capital only" is the ex-income basis stated in words: Allianz
    # Technology writes "the capital only net asset value per ordinary share
    # was 742.94p", Mid-Wynd writes "Capital only: 810.14p"
    ("ex", 3, _R(r"capital[- ]only net asset value[\s\S]{0,140}?" + UK_PENCE, re.I)),
    ("ex", 3, _R(r"\bCapital only:?\s*" + UK_PENCE, re.I)),

    # THE "was N pence" FAMILY - the largest remaining cluster by a wide
    # margin. Patria, Crystal Amber, Sequoia, Target Healthcare, Foresight
    # Solar, NextEnergy, Schroder REIT, Pantheon and India Capital Growth
    # all write some variant of
    #     "...net asset value ... at <date> was 864.9 pence per share"
    # and every one of them defeated the plain fallback for the same
    # reason: `net asset value[^0-9]{0,220}?` cannot cross the DATE that
    # sits between the label and the number. Anchoring on the word "was"
    # instead lets the gap contain digits while keeping the match tied to a
    # NAV statement, and the number must still be followed by "pence"/"p".
    ("cum_assumed", 4, _R(r"(?:net asset value|\bNAV\b)[\s\S]{0,220}?\bwas\s+"
                          r"(?:approximately\s+|estimated\s+(?:to\s+be|at)\s+)?"
                          + UK_PENCE, re.I)),

    # "NAV per Ordinary Share of 111.0 pence" (Foresight Solar) and
    # "net asset value per share at 30th September 2013 of 1,283.3p"
    # (Pantheon). Anchored on "of" the way the family above is anchored on
    # "was", and for the same reason: a date sits in the gap.
    ("cum_assumed", 4, _R(r"NAV per (?:Ordinary |Redeemable )?Share\s+of\s+"
                          + UK_PENCE, re.I)),
    # "increased to 101.66p from the prior month's NAV of 101.10p" - the
    # movement verb has to win, or the rule below takes LAST month's number
    ("cum_assumed", 4, _R(r"\b(?:increased|decreased|rose|fell|moved|changed)"
                          r"\s+to\s+" + UK_PENCE, re.I)),
    # "£252.9 million or 97.1 pence per share" (JLEN) - the sterling total
    # comes first and the per-share figure follows it
    ("cum_assumed", 5, _R(r"\bor\s+" + UK_PENCE
                          + r"\s*per\s+(?:ordinary\s+)?share", re.I)),
    # "FUND NAME NAV SEDOL NAV DATE  BACIT Limited 101.45p" - the Northern
    # Trust administrator table used by BACIT/Syncona and Castelnau
    ("cum_assumed", 5, _R(r"FUND NAME[\s\S]{0,140}?NAV[\s\S]{0,200}?"
                          + UK_PENCE, re.I)),
    # "net asset value ("NAV") per share at 30th September 2013 of 1,283.3p"
    # (Pantheon). Anchored on "per share" with a SHORT gap: the same rule
    # written loosely - NAV within 160 characters of any "of N pence" -
    # read "Interest income net of expenses of 0.42p" as Sequoia's NAV and
    # "an uplift of 1.9 pence per share" as Gore Street's. Both were
    # arithmetic about the NAV sitting one clause away from it.
    ("cum_assumed", 6, _R(r"net asset value[^.]{0,40}?per share[\s\S]{0,70}?"
                          r"\bof\s+" + UK_PENCE, re.I)),
    # "Net Asset Value (pence): 261.09" - Ecofin states the unit in the
    # label and omits the suffix, so UK_PENCE alone never matches
    ("cum", 2, _R(r"Net Asset Value\s*\(pence\):?\s*"
                  r"([0-9][0-9,]*\.[0-9]+)", re.I)),

    # Greencoat: "unaudited Net Asset Value as of 31 December 2025 is
    # GBP2,939.1 million (136.1 pence per share)"
    ("cum_assumed", 4, _R(r"Net Asset Value[\s\S]{0,140}?\(" + UK_PENCE
                          + r"\s+per share\)", re.I)),

    # ---------------------------------------------------------------- the
    # non-pence cohort. Written against reports/build/uk_nav_parse_failures*,
    # the announcements the archiver kept because nothing could read them:
    # 0 of 483 samples parsed before these, across 37 funds.
    #
    # What they have in common is that they do not quote pence. They quote
    # GBP per share, US cents, Canadian dollars, US dollars - and several
    # quote a TOTAL alongside the per-share figure ("USD 91.7 million or USD
    # 4.970 per share"). So every rule here is anchored on explicit
    # per-share language and none of them will match a bare currency and a
    # number: a rule loose enough to take "USD 91.7 million" is exactly how
    # the twelve unreliable series were made.
    #
    # Each carries its unit. A GBP figure becomes pence; US cents become
    # dollars; a foreign currency stays in its own currency and is recorded,
    # because a USD NAV divided into a pence price is an FX rate wearing a
    # discount's clothing.
    #
    # "Net Asset Value per ordinary share as at 11 August 2026 was estimated
    # to be 197.39 pence" (IGC). The plain fallback cannot reach this: it
    # forbids digits between the label and the number, and an as-at date is
    # always digits.
    ("cum", 6, _R(r"net asset value per ordinary share[^.]{0,120}?"
                  r"([0-9][0-9,]*\.[0-9]+)\s*pence", re.I)),
    # plain fallback: "net asset value ... 123.45p"
    ("cum_assumed", 9, _R(r"net asset value[^0-9]{0,220}?" + UK_PENCE, re.I)),
]

# (regex, kind, currency, multiplier) - matched only after UK_RULES fails,
# so no announcement that already parses can change meaning.
UK_CCY_RULES = [
    # Ruffer (Apex table): "FUND NAME NAV SEDOL NAV DATE Ruffer Investment Co
    # Ltd £3.0976 B018CS4" - pounds, then the SEDOL; 1,283 NAV announcements
    # never parsed. Pounds become pence here, exactly once.
    (_R(r"FUND NAME\s+NAV\s+SEDOL[\s\S]{0,160}?£\s*([0-9]{1,3}\.[0-9]{2,4})\s+[A-Z0-9]{7}\b", re.I),
     "cum", "GBX", 100.0),
    # "Ordinary Share GBP 2.8157" (River UK Micro Cap)
    (_R(r"\bOrdinary Share\s+(?:GBP|£)\s*([0-9][0-9,]*\.[0-9]+)", re.I), "cum", "GBX", 100.0),
    # "GBP 3.645 per share" (VietNam Holding, VinaCapital) - the sterling
    # figure is preferred over the USD one the same sentence carries, so a
    # fund's series stays in one unit rather than alternating.
    (_R(r"\bGBP\s*([0-9][0-9,]*\.[0-9]+)\s*per\s+share", re.I), "cum", "GBX", 100.0),
    # "Estimated NAV per share $54.62 (£42.28)" (HarbourVest) - again the
    # sterling figure, which is the one its London line trades against.
    (_R(r"Estimated NAV per share[^(]{0,40}\(£\s*([0-9][0-9,]*\.[0-9]+)\)", re.I), "cum", "GBX", 100.0),
    # "Cum NAV* 191.47cents / Ex NAV 191.74cents" (Schiehallion), US cents
    (_R(r"\bCum\s*NAV\*?\s*([0-9][0-9,]*\.[0-9]+)\s*cents?", re.I), "cum", "USD", 0.01),
    (_R(r"\bEx\s*NAV\*?\s*([0-9][0-9,]*\.[0-9]+)\s*cents?", re.I), "ex", "USD", 0.01),
    # "Net asset value (unaudited) per common share: $ 90.79" - Canadian
    # General states Canadian dollar values; its London line is secondary,
    # so this will usually end as a currency mismatch and no discount. That
    # is the correct outcome, and it is now a stated one rather than a gap.
    (_R(r"net asset value[^.]{0,40}per common share:?\s*\$?\s*([0-9][0-9,]*\.[0-9]+)", re.I), "cum", "CAD", 1.0),
]

# A document that DECLARES its per-share unit and declares a foreign one is
# not reporting pence, whatever else it contains. Schiehallion heads its
# table "(US cents per ordinary share)" and then prints "Cum NAV* 161.54
# cents" - while separately carrying a legend that explains what a pence
# NAV would mean. Reading anything out of that as pence is the unit bug in
# its most direct form, so the whole document is refused.
UK_FOREIGN_DECL = re.compile(
    r"\((?:in\s+)?(?:US\s*)?cents?\s+per\s+(?:ordinary\s+)?share\)|"
    r"\bin\s+US\s*cents\b|\(US\$?\s*per\s+(?:ordinary\s+)?share\)|"
    r"\(€\s*per\s+(?:ordinary\s+)?share\)", re.I)

UK_HDR_NAME = re.compile(r"Net Asset Value\(s\)\s+(.{5,90}?)\s+\d{1,2}\s+\w{3,9}\s+20\d\d")


def parse_uk_nav_text(text: str) -> dict:
    """Cum/ex-income NAV per share (pence) from RNS text - rule-list parser
    written against the committed corpus of real announcement pages.

    ZDP / preference-share entitlements are excluded per candidate match;
    fair-value-debt figures outrank par (panel basis FCCWETScum); the plain
    fallback is flagged cum_assumed. Ambiguity yields absence, never a guess.
    """
    # A document that DECLARES a foreign per-share unit may still be read -
    # by the CURRENCY rules below, which record what unit the number is in.
    # What it must never do is hand a value to the PENCE rules: Schiehallion
    # prints "Cum NAV* 161.54cents" under a "(US cents per ordinary share)"
    # heading while separately carrying a legend explaining what a pence NAV
    # would mean, and a loose rule read that legend as a pence figure.
    foreign_declared = bool(UK_FOREIGN_DECL.search(text))

    def hits(pat, loose: bool = False):
        """Candidate values for one rule.

        `loose` marks the rules that are anchored on prose rather than on an
        explicit NAV label - the "was N pence" family and friends. Those are
        the ones that can wander into a neighbouring number, so only they
        pay for the extra guards. Applying the guards to the precise rules
        as well cost 37 correctly-parsed funds, because "ex-dividend" and
        "cents" appear in the ordinary prose of a perfectly good sterling
        NAV announcement.
        """
        for m in pat.finditer(text):
            before = text[max(0, m.start() - 90):m.start()]
            if UK_ZDP.search(before):
                continue
            if UK_NOMINAL.search(before[-30:]):
                continue          # a nominal/par value, not a NAV
            if loose:
                near = text[max(0, m.start() - 40):m.end() + 25]
                if UK_DIVIDEND.search(near):
                    continue      # a distribution, not a valuation
                if UK_FOREIGN.search(near):
                    continue      # not sterling pence; absence beats a guess
            yield float(m.group(1).replace(",", ""))

    asat = None
    m = UK_ASAT.search(text)
    if m:
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
        except Exception:  # noqa: BLE001
            pass

    best: dict = {}
    for kind, prio, pat in (() if foreign_declared else UK_RULES):
        if kind in ("cum", "cum_assumed") and best.get("_cum_prio", 99) <= prio:
            continue
        if kind == "ex" and best.get("_ex_prio", 99) <= prio:
            continue
        v = next(hits(pat, loose=prio >= 4), None)
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

    # Only when no labelled pence rule matched: the non-pence cohort. These
    # never override an announcement that already parses, so adding them
    # cannot change a single existing observation.
    if "nav_cum_pence" not in best:
        for pat, kind, ccy, mult in UK_CCY_RULES:
            m = pat.search(text)
            if m is None:
                continue
            if UK_ZDP.search(text[max(0, m.start() - 90):m.start()]):
                continue
            try:
                v = float(m.group(1).replace(",", "")) * mult
            except ValueError:
                continue
            key = "nav_cum_pence" if kind == "cum" else "nav_ex_pence"
            if key in best:
                continue
            best[key] = v
            best["nav_ccy"] = ccy

    out = {k: v for k, v in best.items() if not k.startswith("_")}
    if foreign_declared:
        out["unit_declared_foreign"] = True
    out.setdefault("nav_ccy", "GBX")
    if asat:
        out["asat"] = asat
    return out


def harvest_uk(ticker_map: pd.DataFrame, census: pd.DataFrame,
               lookback_days: int = 45, budget: int = 0,
               extra_targets: dict[str, str] | None = None,
               deadline_min: float = 60.0,
               write_listing_cache: bool = True) -> pd.DataFrame:
    """Published NAV for EVERY fund we can address, from its own RNS page.

    The registry (AIC/ASX files) is used for identity only. Every live fund
    with a resolved ticker is polled here - not just the ones the research
    crawl happened to cover, and not just the ones the registry declines to
    price. A fund is absent from this output only because it published no
    parseable NAV, never because we did not ask.

    Returns (navs, announcement rows); the announcement rows feed the
    catalyst scan from the same fetched pages.

    lookback_days - 45, not 7. A week-long window can only ever see a fund
    that publishes at least weekly. 215 of 368 targets came back
    `no_recent_nav` on the last run, and the reason for most of them was
    their publication cadence, not their silence: Achilles Investment
    Company published a NAV on 7 August and was invisible to a harvest run
    on the 30th. Monthly and quarterly publishers are most of the offshore,
    property and infrastructure cohort this module exists to reach.

    write_listing_cache - every listing page fetched here is written back to
    data/investegate_cache/listings/{ticker}.csv. The NAV archive job builds
    its queue from that cache, so a fund the historical dividends crawl
    never paged - anything that listed recently, Achilles among them - had
    no listing index, therefore no archived announcements, therefore no NAV
    history, for want of a file we were already downloading the contents of.
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
    pat = UK_NAV_HEAD
    rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    # every listing page is already being fetched for NAV; the catalyst rows
    # are on the same page, so collecting them costs no extra requests
    cat_cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    ann_rows: list[dict] = []
    # everything the listing page shows, keyed by ticker, for the cache the
    # archive job reads
    listing_rows: dict[str, list[dict]] = {}
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
        # An unknown or dead ticker's page silently serves the MARKET-WIDE
        # feed - the crawler has guarded its H1 since day one, and this
        # harvest not doing the same resurrected 56 delisted funds with
        # other companies' announcement dates and polluted their listing
        # caches. The page must name THIS ticker or nothing here is used.
        h1 = soup.find("h1")
        h1_m = re.search(r"\(([A-Z0-9.]{2,8})\)\s+RNS",
                         h1.get_text(" ", strip=True)) if h1 else None
        if h1_m is None or h1_m.group(1).upper() != tk.upper():
            stats["identity_mismatch"] = stats.get("identity_mismatch", 0) + 1
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
            url_ = ("https://www.investegate.co.uk" + href) if href.startswith("/") else href
            # page footers leak other companies' announcements; only rows
            # whose URL slug names THIS ticker are this fund's
            if not uk_row_matches_ticker(url_, tk):
                continue
            listing_rows.setdefault(tk, []).append({
                "ann_id": url_.rsplit("/", 1)[-1], "date": d,
                "headline": head, "url": url_})
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
            # Factsheet-route fallback, for the cohort that publishes no
            # NAV-shaped headline at all (measured: the factsheet probe -
            # BSIF, HGT, ICGT, 3IN, the REITs state NAV inside results and
            # updates). Only fires when UK_NAV_HEAD matched nothing, so a
            # daily NAV publisher never costs an extra fetch. The window is
            # wider because these shapes are quarterly/semiannual. A third
            # party's research note is never a source.
            fs_cutoff = (datetime.now(timezone.utc)
                         - timedelta(days=200)).date().isoformat()
            for row_ in listing_rows.get(tk, []):   # newest-first
                head_ = row_.get("headline") or ""
                if (row_.get("date") or "") < fs_cutoff:
                    continue
                if not UK_FACTSHEET_HEAD.search(head_):
                    continue
                if UK_THIRD_PARTY.search(head_) or BAD.search(head_):
                    continue
                best = {"date": row_["date"], "headline": head_,
                        "url": row_["url"], "route": "factsheet"}
                stats["factsheet_route"] = stats.get("factsheet_route", 0) + 1
                break
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
                     # a NAV the currency rules read in dollars or euros
                     # must say so: without this a $1.91 NAV travelled as
                     # 1.91 PENCE against a 200p price
                     "unit": got.get("nav_ccy", "GBX"),
                     "nav_ex": got.get("nav_ex_pence"),
                     "cum_assumed": got.get("cum_assumed", False),
                     "source": (f"investegate{'_fs' if best.get('route') else ''}"
                                f":{best['url'].rsplit('/', 1)[-1]}"),
                     "headline": best["headline"][:120]})
    if write_listing_cache and listing_rows:
        stats["listing_cache_written"] = _write_listing_cache(listing_rows)
    try:
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        import json as _json
        stats["announcements_seen"] = len(ann_rows)
        Path("reports/build/uk_tier0_debug.json").write_text(
            _json.dumps(stats, indent=1))
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame(rows), ann_rows


def _write_listing_cache(by_ticker: dict[str, list[dict]],
                         cache_dir: Path | None = None) -> int:
    """Merge freshly-seen listing rows into the Investegate listing cache.

    The archive job's queue IS this cache, so a fund missing from it can
    never have its announcements archived however well the rest of the
    pipeline works. Existing rows are kept - this only ever ADDS, so the
    deep history a full crawl built is never truncated by a 45-day view.
    """
    cache = cache_dir or Path("data/investegate_cache/listings")
    cache.mkdir(parents=True, exist_ok=True)
    written = 0
    for tk, rows_ in by_ticker.items():
        if not rows_:
            continue
        new = pd.DataFrame(rows_)
        f = cache / f"{tk}.csv"
        if f.exists():
            try:
                old = pd.read_csv(f, dtype=str)
                new = pd.concat([old, new.astype(str)], ignore_index=True)
            except Exception:  # noqa: BLE001
                pass
        new = new.drop_duplicates(subset=["ann_id"], keep="first")
        new.to_csv(f, index=False)
        written += 1
    return written


def uk_nav_samples(cache_dir: Path, n: int = 5) -> list[dict]:
    """Fetch a handful of recent UK NAV announcement pages (throttled) and
    return their text heads - parser-design evidence, committed to
    reports/build so the value parser is written against real RNS text,
    never guessed."""
    import requests

    pat = UK_NAV_HEAD
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
