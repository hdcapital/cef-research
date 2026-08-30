"""Parse the prescribed-form 72% in Python. No model, no per-document cost.

These are the documents the discount and manager studies actually run on:
the NTA/NAV series, the distributions that turn a NAV series into a total
return, and the buyback and cancellation forms that keep per-share history
valid. All of them are prescribed ASX forms or standing report formats, so
they are parsed rather than prompted.

The NTA path reuses the parser that was already validated against real
announcements (scripts/sample_nta_pdfs.py) - candidate scoring by label
specificity, not first-rule-wins - rather than starting a second, unproven
one. What is new here is the source: PDFs come from the S3 archive, so
nothing is re-fetched from the ASX and the whole corpus can be reparsed as
often as the parsers improve.

Output is point-in-time by construction: every row carries published_at
(when the fact became public) separately from valuation_date or ex_date
(what the fact is about), and the announcement id it came from.
"""

from __future__ import annotations

import importlib.util
import io
import re
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "nta_parse", Path(__file__).resolve().parents[3] / "scripts" / "sample_nta_pdfs.py")
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)

MONEY = r"\$?\s*([0-9][0-9,]*\.?[0-9]*)"
CENTS = r"([0-9]+\.?[0-9]*)\s*(?:cents|cps|c\b)"


# The ASX "For personal use only" watermark is set VERTICALLY, and pdfplumber
# emits its letters one at a time, interleaved through the real sentences:
#   "y ASX Release l n ABSOLUTE EQUITY ... o eMonday, 14 August 2017 s"
# Those stray letters split phrases the parsers match on ("fully lfranke"),
# and on an image-only PDF they are the ONLY text extracted. This is not one
# document's quirk - it is on a large share of the corpus, so stripping it is
# the highest-leverage fix available to every parser at once.
WATERMARK = "forpersonaluseonly"


def strip_sidebar(text: str) -> str:
    """Remove the interleaved vertical-watermark letters.

    Only fires when the standalone single letters actually spell the
    watermark (in either direction), so ordinary text containing "a" or "I"
    is untouched, and a document that simply uses single letters is not
    mangled.
    """
    toks = (text or "").split()
    idx = [i for i, t in enumerate(toks) if len(t) == 1 and t.isalpha()]
    if len(idx) < 6:
        return text
    joined = "".join(toks[i] for i in idx).lower()
    rev = WATERMARK[::-1]
    # containment BOTH ways: a single page often carries only part of the
    # phrase ("ylnoesu"), and requiring the whole thing missed those - which
    # is most one-page NTA notices, the highest-volume document in the corpus
    if not (joined in rev or rev in joined
            or joined in WATERMARK or WATERMARK in joined):
        return text
    drop = set(idx)
    return " ".join(t for i, t in enumerate(toks)
                    if i not in drop or t in ("a", "A", "I"))


def has_text_layer(text: str, min_chars: int = 120) -> bool:
    """Is there any real text, or is this a scanned image?

    An image-only PDF is not a parser failure and must not be escalated to a
    text model, which would read exactly as little from it. It needs OCR, and
    it needs to be counted separately so the parse rate measures parsing.
    """
    return len(strip_sidebar(text or "").strip()) >= min_chars


def pdf_pages(data: bytes, max_pages: int = 4) -> tuple[str, list[list[str]]]:
    """(flat text, candidate table rows) from PDF bytes.

    Mirrors the extraction the validated parser was tuned against - same page
    cap, same whitespace flattening, same table-row filter - so its accuracy
    carries over instead of being re-earned on differently-shaped input.
    """
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages[:max_pages]
        text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pages))
        rows: list[list[str]] = []
        for p in pages:
            try:
                for tbl in p.extract_tables() or []:
                    for row in tbl:
                        joined = " ".join(str(c) for c in row if c)
                        if P.NTA_HEAD.search(joined):
                            rows.append([str(c) if c is not None else "" for c in row])
            except Exception:  # noqa: BLE001
                pass
    return strip_sidebar(text), rows


def _asat(text: str, headline: str) -> str | None:
    for src in (headline or "", text or ""):
        m = P.ASAT.search(src)
        if m:
            try:
                return pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
        m = re.search(r"\b(\d{1,2}[./]\d{1,2}[./]20\d\d)\b", src)
        if m:
            try:
                return pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
    return None



# The validated parser handles "pre-tax NTA per share as at 28 October 2016
# was $0.8623". Real announcements add three things it was never shown, and
# each one alone defeats it:
#   "... Backing per share (NTA) as at Friday, 28 October 2016 was: $0.8623"
#   "... backing per Bisan Limited share as at 31 August 2016 is 0.0384c"
# So the TEXT is normalised into the shape the parser was validated on,
# rather than the parser being rewritten. Its accuracy was earned against a
# real corpus; re-tuning it here would put that at risk to fix an input
# problem.
WEEKDAY = re.compile(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+", re.I)
PARENTHETICAL = re.compile(r"\s*\((?:[^()]{1,30})\)")
PER_ENTITY_SHARE = re.compile(
    r"\bper\s+(?:[A-Z][\w&.'\-]*\s+){1,5}(share|unit|security|stapled security)\b")
CENTS_SUFFIX = re.compile(r"\b([0-9]+\.[0-9]+)\s*c\b(?!\w)")


def normalise_nta_text(text: str) -> str:
    t = PARENTHETICAL.sub(" ", text or "")          # "(NTA)", "(ASX: WDE)"
    t = WEEKDAY.sub("", t)                           # "Friday, 28 October 2016"
    t = PER_ENTITY_SHARE.sub(r"per \1", t)           # "per Bisan Limited share"
    t = re.sub(r"\b(was|is|of)\s*:\s*", r"\1 ", t)   # "was: $0.8623"
    t = CENTS_SUFFIX.sub(r"\1 cents", t)             # "0.0384c"
    # The scored label rules key on the ABBREVIATIONS. Announcements that
    # spell the term out in full - "Net Tangible Asset Backing per share" -
    # carry the identical meaning and scored zero, which is a large share of
    # the highest-volume document type in the corpus.
    t = re.sub(r"\bnet tangible asset(?:s)?(?:\s+backing)?\b", "NTA", t, flags=re.I)
    t = re.sub(r"\bnet asset value\b", "NAV", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ NTA / NAV
def extract_nta(text: str, rows: list[list[str]], headline: str) -> list[dict]:
    """Stated per-share NTA via the already-validated parser."""
    clean = normalise_nta_text(text)
    got = P.derive_stated({"status": "extracted", "text": clean, "rows": rows})
    if got.get("status") != "parsed" and clean != text:
        got = P.derive_stated({"status": "extracted", "text": text, "rows": rows})
    if got.get("status") != "parsed" or got.get("stated_raw") is None:
        return []
    unit = got.get("unit")
    if unit == "ambiguous":
        return []                        # flagged upstream, never guessed
    val = got["stated_raw"]
    return [{"section": "nav_observations",
             "valuation_date": _asat(normalise_nta_text(text), headline),
             "nav_per_share": val / 100.0 if unit == "cents" else val,
             "unit": unit,
             "nav_basis": got.get("basis") or "unknown",
             "raw_nav_label": got.get("label"),
             "extractor": "nta_scored_v1"}]


# ------------------------------------------------- Appendix 3E (daily buyback)
BB_BOUGHT = re.compile(
    r"(?:total number of|number of).{0,60}?(?:bought back|purchased)[^0-9]{0,40}"
    r"([0-9][0-9,]*)", re.I | re.S)
BB_PRICE = re.compile(
    r"(?:highest|lowest|average)\s+price[^0-9$]{0,40}" + MONEY, re.I)
BB_REMAIN = re.compile(
    r"remain(?:ing|s)?\s+to be bought back[^0-9]{0,40}([0-9][0-9,]*)", re.I)


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def extract_buyback(text: str, headline: str) -> list[dict]:
    """Shares actually bought back, from the daily notice.

    This is the execution series - what the buyback did, day by day - which
    is what a discount-convergence test needs; the announcement of a buyback
    is a separate, narrative fact.
    """
    bought = _num(m.group(1)) if (m := BB_BOUGHT.search(text)) else None
    if bought is None:
        return []
    return [{"section": "capital_structure_events",
             "event_type": "buyback_execution",
             "shares_bought_back": bought,
             "price": _num(m2.group(1)) if (m2 := BB_PRICE.search(text)) else None,
             "remaining_to_buy_back": _num(m3.group(1)) if (m3 := BB_REMAIN.search(text)) else None,
             "extractor": "appendix_3e_v1"}]


# ------------------------------------------------------ Appendix 3A dividends
# The modern ASX online form, which is most of this family:
#   "Distribution Amount AUD 0.07000000 Ex Date Wednesday February 28, 2018"
# and the older narrative style:
#   "2.5 cents per share fully franked final dividend"
# The original pattern required a label like "amount per security" before the
# number and matched NEITHER - 1 of 13 sampled documents parsed.
DIV_AMT_AUD = re.compile(
    r"(?:distribution|dividend)\s+amount[^0-9A-Z]{0,20}(?:AUD|A\$|\$)?\s*"
    r"([0-9]+\.[0-9]+)", re.I)
DIV_AMT_LABELLED = re.compile(
    r"(?:amount per|dividend/?distribution amount|amount of[^.]{0,30}per)"
    r"[^0-9$]{0,60}(?:" + CENTS + r"|\$\s*([0-9]+\.[0-9]+))", re.I)
DIV_AMT_BARE = re.compile(
    CENTS + r"\s*per\s+(?:share|security|unit)", re.I)
FRANK = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%\s*frank|frank(?:ed|ing)"
                   r"[^0-9%]{0,40}([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I)
FULLY_FRANKED = re.compile(r"fully\s+franked", re.I)
UNFRANKED = re.compile(r"\bunfranked\b|nil\s+franking|0%\s*franked", re.I)
# "Ex Date Wednesday February 28, 2018" as well as 28/02/2018 and 28 Feb 2018
DATE_ANY = (r"((?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?"
            r"(?:[0-9]{1,2}[/ -][0-9]{1,2}[/ -][0-9]{2,4}"
            r"|[0-9]{1,2}\s+[A-Za-z]{3,9},?\s+[0-9]{4}"
            r"|[A-Za-z]{3,9}\s+[0-9]{1,2},?\s+[0-9]{4}))")
DATE_LABEL = {
    "ex_date": re.compile(r"ex[\s-]?date[^0-9A-Za-z]{0,10}" + DATE_ANY, re.I),
    "record_date": re.compile(r"record date[^0-9A-Za-z]{0,10}" + DATE_ANY, re.I),
    "payment_date": re.compile(r"(?:payment|payable) date[^0-9A-Za-z]{0,10}"
                               + DATE_ANY, re.I),
}


def _parse_any_date(v: str) -> str | None:
    v = re.sub(r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+", "", v or "",
               flags=re.I)
    for dayfirst in (True, False):
        try:
            return pd.to_datetime(v, dayfirst=dayfirst).date().isoformat()
        except Exception:  # noqa: BLE001
            continue
    return None


def extract_dividend(text: str, headline: str) -> list[dict]:
    """Distribution amount, franking and the three dates.

    Without these a NAV series is a price return, not a total return, and a
    manager judged on price return alone is judged on the wrong number.
    """
    cents = None
    m = DIV_AMT_AUD.search(text)
    if m:                                  # "Distribution Amount AUD 0.07"
        v = _num(m.group(1))
        cents = v * 100 if v is not None else None
    if cents is None and (m := DIV_AMT_LABELLED.search(text)):
        cents = _num(m.group(1))
        if cents is None and m.group(2):
            d = _num(m.group(2))
            cents = d * 100 if d is not None else None
    if cents is None and (m := DIV_AMT_BARE.search(text)):
        cents = _num(m.group(1))
    if cents is None:
        return []

    franking = None
    if (fm := FRANK.search(text)):
        franking = _num(fm.group(1) or fm.group(2))
    elif FULLY_FRANKED.search(text):
        franking = 100.0
    elif UNFRANKED.search(text):
        franking = 0.0

    rec = {"section": "distribution_events",
           "event_type": ("special_dividend"
                          if re.search(r"special", headline or "", re.I)
                          else "ordinary_dividend"),
           "amount_per_share_cents": cents,
           "franking_pct": franking,
           "extractor": "dividend_v2"}
    for field, pat in DATE_LABEL.items():
        mm = pat.search(text)
        rec[field] = _parse_any_date(mm.group(1)) if mm else None
    return [rec]


# ------------------------------------------------------ Form 484 cancellations
CANCEL = re.compile(
    r"(?:cancell?(?:ed|ation) of|number of (?:shares|securities|units)[^0-9]{0,40})"
    r"[^0-9]{0,40}([0-9][0-9,]{2,})", re.I)


def extract_cancellation(text: str, headline: str) -> list[dict]:
    m = CANCEL.search(text)
    n = _num(m.group(1)) if m else None
    if n is None:
        return []
    return [{"section": "capital_structure_events",
             "event_type": "share_cancellation", "shares_cancelled": n,
             "extractor": "form_484_v1"}]


def extract_terminal_value(text: str, headline: str) -> list[dict]:
    """Scheme consideration, offer price or liquidator distribution.

    Routed as a corporate action (so it also reaches the model for the
    narrative facts), but the NUMBER is deterministic and is taken here: a
    stated dollar amount per share does not need a model, and a terminal
    value is too consequential to leave to one.
    """
    from au_lic import terminal as TV

    got = TV.extract_terminal(text, headline)
    if not got:
        return []
    return [{"section": "terminal_value", **got, "extractor": "terminal_v1"}]


# ------------------------------------------- Appendix 3B / 2A: securities issued
ISSUED_N = re.compile(
    r"(?:number of\s+\+?securities?|securities? to be issued|number of\s+\+?shares?"
    r"|total number of\s+\+?securities)[^0-9]{0,60}([0-9][0-9,]{2,})", re.I)
ISSUE_PRICE = re.compile(
    r"(?:issue price|price per\s+\+?security|offer price|application price)"
    r"[^0-9$]{0,50}(?:\$\s*([0-9]+(?:\.[0-9]+)?)|([0-9]+(?:\.[0-9]+)?)\s*cents)", re.I)


def extract_issue(text: str, headline: str) -> list[dict]:
    """Securities issued and at what price.

    Share count changes the denominator of every per-share number, so an
    unrecorded issue silently corrupts NAV per share and the discount built
    on it. DRP issues in particular happen every distribution.
    """
    m = ISSUED_N.search(text)
    n = _num(m.group(1)) if m else None
    if n is None:
        return []
    pm = ISSUE_PRICE.search(text)
    price = None
    if pm:
        price = _num(pm.group(1))
        if price is None and pm.group(2):
            c = _num(pm.group(2))
            price = c / 100.0 if c is not None else None
    kind = "share_purchase_plan" if re.search(r"\bSPP\b|share purchase plan",
                                              text, re.I) else (
        "rights_issue" if re.search(r"rights issue|entitlement", text, re.I) else
        "placement" if re.search(r"placement", text, re.I) else "other")
    return [{"section": "capital_structure_events", "event_type": kind,
             "shares_issued": n, "issue_price": price,
             "drp": bool(re.search(r"\bDRP\b|dividend reinvestment", text, re.I)),
             "extractor": "appendix_3b_2a_v1"}]


# ------------------------------------- Form 603/604/605: substantial holdings
# Form 604 is a TABLE: "Person's votes | Voting power" are column headers and
# the numbers sit in the rows below, far past any small window. So the label
# is located, then the percentages are read positionally - preferring the
# "Present notice" row, which is the holding NOW rather than the previous one.
PCT_ANY = re.compile(r"([0-9]{1,2}(?:\.[0-9]+)?)\s*%")


def _voting_power(text: str) -> tuple[float | None, float | None]:
    """(previous, present) voting power from a Form 603/604/605.

    Form 604 states BOTH, in one row: "Previous notice | Present notice", so
    the first percentage after the label is the OLD holding. Taking it would
    report a holder as smaller than they are and hide an accumulation - which
    is the catalyst this extractor exists to see. The last percentage in the
    row is the current holding, and keeping both makes the CHANGE available,
    which is the signal itself rather than a level.
    """
    t = text or ""
    low = t.lower()
    for anchor in ("voting power", "present notice", "substantial holder"):
        i = low.find(anchor)
        if i == -1:
            continue
        vals = [v for v in (_num(m.group(1)) for m in PCT_ANY.finditer(t, i))
                if v is not None and 0 <= v <= 100]
        if not vals:
            continue
        return (vals[0], vals[-1]) if len(vals) >= 2 else (None, vals[0])
    return None, None


HOLDER_NAME = re.compile(
    r"name of substantial holder\s*(?:\([^)]{0,30}\))?\s*[:\-]\s*"
    r"([A-Z][^\n.;:]{2,59})", re.I)


def extract_substantial_holder(text: str, headline: str) -> list[dict]:
    """Who holds how much, and which way it moved.

    An activist or a wind-up campaigner accumulating stock is a catalyst in
    the taxonomy, and the direction is in the headline: becoming, changing,
    or ceasing.
    """
    prev_pct, pct = _voting_power(text)
    h = headline or ""
    if re.search(r"ceasing", h, re.I):
        direction = "ceased"
    elif re.search(r"becoming|initial", h, re.I):
        direction = "became"
    else:
        direction = "changed"
    if pct is None and direction != "ceased":
        return []
    nm = HOLDER_NAME.search(text)
    return [{"section": "other_material_events", "event_type": "substantial_holder",
             "direction": direction, "voting_power_pct": pct,
             "voting_power_prev_pct": prev_pct,
             "holder": nm.group(1).strip() if nm else None,
             "extractor": "form_60x_v1"}]


FAMILY_EXTRACTORS = {
    "nta": lambda text, rows, head: extract_nta(text, rows, head),
    "corporate_action": lambda text, rows, head: extract_terminal_value(text, head),
    "buyback_daily": lambda text, rows, head: extract_buyback(text, head),
    "dividend": lambda text, rows, head: extract_dividend(text, head),
    "share_cancellation": lambda text, rows, head: extract_cancellation(text, head),
    "issue": lambda text, rows, head: extract_issue(text, head),
    "substantial_holder": lambda text, rows, head: extract_substantial_holder(text, head),
}


def extract(family: str, text: str, rows: list[list[str]], headline: str) -> list[dict]:
    """Facts from one document, or [] - which means escalate, not discard.

    An empty result is the signal the router's intent was not met: the
    document goes to the model rather than being written off as containing
    nothing.
    """
    fn = FAMILY_EXTRACTORS.get(family)
    return fn(text, rows, headline) if fn else []
