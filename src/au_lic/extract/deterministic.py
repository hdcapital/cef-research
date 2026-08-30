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
    return text, rows


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


# ------------------------------------------------------------------ NTA / NAV
def extract_nta(text: str, rows: list[list[str]], headline: str) -> list[dict]:
    """Stated per-share NTA via the already-validated parser."""
    got = P.derive_stated({"status": "extracted", "text": text, "rows": rows})
    if got.get("status") != "parsed" or got.get("stated_raw") is None:
        return []
    unit = got.get("unit")
    if unit == "ambiguous":
        return []                        # flagged upstream, never guessed
    val = got["stated_raw"]
    return [{"section": "nav_observations",
             "valuation_date": _asat(text, headline),
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
DIV_AMT_CENTS = re.compile(
    r"(?:amount per|dividend/?distribution amount|amount of[^.]{0,30}per)"
    r"[^0-9$]{0,60}(?:" + CENTS + r"|\$\s*([0-9]+\.[0-9]+))", re.I)
FRANK = re.compile(r"frank(?:ed|ing)[^0-9]{0,40}([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I)
DATE_LABEL = {
    "ex_date": re.compile(r"ex[- ]date[^0-9]{0,30}(\d{1,2}[/ ]\w+[/ ]\d{2,4}|"
                          r"\d{1,2}/\d{1,2}/\d{2,4})", re.I),
    "record_date": re.compile(r"record date[^0-9]{0,30}(\d{1,2}[/ ]\w+[/ ]\d{2,4}|"
                              r"\d{1,2}/\d{1,2}/\d{2,4})", re.I),
    "payment_date": re.compile(r"payment date[^0-9]{0,30}(\d{1,2}[/ ]\w+[/ ]\d{2,4}|"
                               r"\d{1,2}/\d{1,2}/\d{2,4})", re.I),
}


def extract_dividend(text: str, headline: str) -> list[dict]:
    """Distribution amount, franking and the three dates.

    Without these a NAV series is a price return, not a total return, and a
    manager judged on price return alone is judged on the wrong number.
    """
    m = DIV_AMT_CENTS.search(text)
    if not m:
        return []
    cents = _num(m.group(1))
    if cents is None and m.group(2):
        d = _num(m.group(2))
        cents = d * 100 if d is not None else None
    if cents is None:
        return []
    rec = {"section": "distribution_events",
           "event_type": ("special_dividend" if re.search(r"special", headline or "", re.I)
                          else "ordinary_dividend"),
           "amount_per_share_cents": cents,
           "franking_pct": _num(m2.group(1)) if (m2 := FRANK.search(text)) else None,
           "extractor": "appendix_3a_v1"}
    for field, pat in DATE_LABEL.items():
        mm = pat.search(text)
        rec[field] = None
        if mm:
            try:
                rec[field] = pd.to_datetime(mm.group(1), dayfirst=True).date().isoformat()
            except Exception:  # noqa: BLE001
                pass
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


FAMILY_EXTRACTORS = {
    "nta": lambda text, rows, head: extract_nta(text, rows, head),
    "buyback_daily": lambda text, rows, head: extract_buyback(text, head),
    "dividend": lambda text, rows, head: extract_dividend(text, head),
    "share_cancellation": lambda text, rows, head: extract_cancellation(text, head),
}


def extract(family: str, text: str, rows: list[list[str]], headline: str) -> list[dict]:
    """Facts from one document, or [] - which means escalate, not discard.

    An empty result is the signal the router's intent was not met: the
    document goes to the model rather than being written off as containing
    nothing.
    """
    fn = FAMILY_EXTRACTORS.get(family)
    return fn(text, rows, headline) if fn else []
