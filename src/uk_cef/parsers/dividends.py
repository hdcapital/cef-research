"""Parser for dividend-declaration RNS announcements (Investegate pages).

Extracts: amount per share (+ unit/currency), ex-dividend date, payment
date, record date, share class and period from announcement title + body.
Anything not stated stays None; a confidence grade records how complete the
extraction is:

    high   - amount + ex-date
    medium - amount + payment or record date (ex-date inferable ~2 business
             days before record date is NOT inferred - we keep the pay date
             and match dividends to months by pay month with a flag)
    low    - amount only

Sign conventions: amounts are per share in the announcement's stated unit
(pence 'p'/'pence' -> GBX; pounds -> GBP; cents -> USc/EUc depending on
currency wording).
"""

from __future__ import annotations

import re
from datetime import datetime

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_DATE_RE = rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+(\d{{4}})"

_AMOUNT_PATTERNS = [
    # "dividend of 5.70p per ordinary share", "dividend of 5.25 pence per share"
    re.compile(
        r"dividend[^.\n]{0,120}?\bof\b[^.\n]{0,40}?(\d+(?:\.\d+)?)\s*(p\b|pence|pounds?|£|cents?|¢|us\s*cents?|euro\s*cents?)",
        re.I),
    # "dividend per share of 5.70p" / "distribution of 1.25p per share"
    re.compile(
        r"(?:dividend|distribution)\s+per\s+(?:ordinary\s+)?share\s+of\s+(\d+(?:\.\d+)?)\s*(p\b|pence|pounds?|£|cents?|¢)",
        re.I),
    # table style: "Dividend: 5.70p" / "Amount per share 5.70 pence"
    re.compile(
        r"(?:amount\s+per\s+share|dividend)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(p\b|pence|pounds?|£|cents?|¢)",
        re.I),
]

_UNIT_MAP = {
    "p": ("GBX", 1.0), "pence": ("GBX", 1.0),
    "pound": ("GBX", 100.0), "pounds": ("GBX", 100.0), "£": ("GBX", 100.0),
    "cent": ("c", 1.0), "cents": ("c", 1.0), "¢": ("c", 1.0),
    "us cent": ("USc", 1.0), "us cents": ("USc", 1.0),
    "euro cent": ("EUc", 1.0), "euro cents": ("EUc", 1.0),
}


def _parse_date(m: re.Match | None) -> str | None:
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2), m.group(3)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(f"{day} {mon[:3] if fmt == '%d %b %Y' else mon} {year}", fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _find_date(text: str, *cues: str) -> str | None:
    """Find a date within ~90 chars after any of the cue phrases."""
    for cue in cues:
        for m in re.finditer(cue, text, re.I):
            window = text[m.end(): m.end() + 90]
            dm = re.search(_DATE_RE, window, re.I)
            if dm:
                return _parse_date(dm)
    return None


def parse_dividend_announcement(title: str, body: str) -> dict | None:
    """Return extraction dict or None if no per-share amount is found."""
    text = re.sub(r"\s+", " ", f"{title}\n{body}")

    amount = unit = None
    for pat in _AMOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            amount = float(m.group(1))
            unit = m.group(2).lower().strip().rstrip(".")
            break
    if amount is None:
        return None
    currency, mult = _UNIT_MAP.get(unit, _UNIT_MAP.get(unit.rstrip("s"), ("GBX", 1.0)))
    amount_gbx = amount * mult if currency == "GBX" else None

    ex_date = _find_date(
        text, r"ex[\s\-]?dividend(?:\s+date)?", r"marked\s+ex[\s\-]?dividend", r"\bXD\s+date",
        r"shares\s+(?:will\s+)?go(?:es)?\s+ex[\s\-]?dividend",
    )
    pay_date = _find_date(text, r"pay(?:ment|able)\s*(?:date|on)?", r"paid\s+on", r"will\s+be\s+paid")
    record_date = _find_date(text, r"record\s+date", r"holders?\s+on\s+the\s+register")

    special = bool(re.search(r"\bspecial\s+dividend\b", text, re.I))
    period = None
    pm = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+interim\b|\binterim\b|\bfinal\b|\bquarterly\b",
                   text, re.I)
    if pm:
        period = pm.group(0).lower()

    if ex_date:
        confidence = "high"
    elif pay_date or record_date:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "amount": amount,
        "unit": unit,
        "currency": currency,
        "amount_gbx": amount_gbx,
        "ex_date": ex_date,
        "pay_date": pay_date,
        "record_date": record_date,
        "special": special,
        "period": period,
        "confidence": confidence,
    }


DIVIDEND_HEADLINES = re.compile(
    r"dividend\s+declaration|dividend\s+announcement|^dividend\b|interim\s+dividend|final\s+dividend"
    r"|special\s+dividend|first\s+interim|second\s+interim|third\s+interim|fourth\s+interim"
    r"|quarterly\s+dividend|distribution\s+declaration",
    re.I,
)

CATALYST_HEADLINES = re.compile(
    r"tender\s+offer|winding[\s\-]up|wind[\s\-]down|liquidat|reconstruction|scheme\s+of\s+arrangement"
    r"|proposed\s+merger|recommended\s+(?:cash\s+)?(?:offer|merger)|combination\s+with"
    r"|return\s+of\s+capital|capital\s+return|strategic\s+review|continuation\s+vote"
    r"|realisation\s+opportunity|exit\s+opportunity|managed\s+wind",
    re.I,
)


def classify_headline(headline: str) -> str | None:
    """dividend | catalyst | None (everything else is skipped)."""
    if DIVIDEND_HEADLINES.search(headline or ""):
        return "dividend"
    if CATALYST_HEADLINES.search(headline or ""):
        return "catalyst"
    return None
