"""Deterministic enforcement of the extraction contract.

The prompt states the rules; this module is what makes them true. An LLM
instructed not to guess will still occasionally guess, and a backtest built on
a silently-invented NTA is worse than one built on missing data - it looks
fine. So every rule that CAN be checked in code is checked in code, and a row
that fails is dropped or flagged with the reason recorded, never repaired.

Four checks carry the weight:

1. quote provenance - the source quote must actually appear in the document.
   This is the anti-hallucination check: a number the model invented almost
   never comes with a verbatim quote that is really in the text.
2. lookahead - no date recording when something became KNOWN may postdate
   published_at. Effective dates legitimately may, and are exempt by name.
3. no computed signals - the ABSOLUTE RULE, applied to every key at every
   depth, so a "discount_pct" cannot ride in inside headline_terms.
4. controlled vocabulary - an out-of-set label is rejected, not adopted.

Nothing here judges whether a fund is attractive, and nothing computes a
signal; that is the point.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import schema as S


def _norm(text: str) -> str:
    """Whitespace- and punctuation-normalised text for quote matching.

    PDF extraction inserts line breaks, doubled spaces and non-breaking spaces
    inside sentences, and models normalise smart quotes and dashes when they
    copy. Matching raw would reject honest quotes, so both sides are flattened
    the same way. Digits and letters are untouched - the check must still fail
    on a changed number.
    """
    t = unicodedata.normalize("NFKD", text or "")
    t = (t.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("–", "-").replace("—", "-").replace("−", "-")
          .replace("\xa0", " "))
    return re.sub(r"\s+", " ", t).strip().lower()


def _parse_date(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
        return None
    try:
        y, m, d = (int(x) for x in v.strip().split("-"))
        return date(y, m, d)
    except ValueError:
        return None


def forbidden_keys(obj, path: str = "") -> list[str]:
    """Every key at any depth whose name is a computed backtest signal."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(bad in kl for bad in S.FORBIDDEN_KEY_SUBSTRINGS):
                found.append(f"{path}{k}")
            found += forbidden_keys(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += forbidden_keys(v, f"{path}{i}.")
    return found


def check_record(rec: dict, section: str, doc_text: str, published_at: str) -> list[str]:
    """Reasons this record must not enter the dataset. Empty list = accept."""
    problems: list[str] = []
    pub = _parse_date((published_at or "")[:10])

    # 3. no computed signals, at any depth
    for key in forbidden_keys(rec):
        problems.append(f"computed_signal_field:{key}")

    # 4. controlled vocabulary
    for field, allowed in S.ENUM_FIELDS.get(section, {}).items():
        val = rec.get(field)
        if val is None or val == "":
            continue
        if val not in allowed:
            problems.append(f"enum:{field}={val!r}")

    # 2. lookahead - knowledge dates may not postdate publication
    for field in S.KNOWLEDGE_DATE_FIELDS:
        if field not in rec or rec.get(field) in (None, ""):
            continue
        d = _parse_date(rec[field])
        if d is None:
            problems.append(f"bad_date:{field}={rec[field]!r}")
        elif pub and d > pub:
            problems.append(f"lookahead:{field}={rec[field]} > published_at={pub}")

    # confidence band
    conf = rec.get("confidence")
    if not isinstance(conf, (int, float)):
        problems.append("confidence_missing")
    elif not 0.0 <= float(conf) <= 1.0:
        problems.append(f"confidence_out_of_range:{conf}")
    elif float(conf) < S.MIN_CONFIDENCE:
        problems.append(f"confidence_below_floor:{conf}")

    # 1. quote provenance - the strongest anti-hallucination check available
    src = rec.get("source") or {}
    quote = (src.get("quote") or "").strip() if isinstance(src, dict) else ""
    if not quote:
        problems.append("no_source_quote")
    elif doc_text is not None:
        if _norm(quote) not in _norm(doc_text):
            problems.append("quote_not_in_document")
    return problems


def validate(payload: dict, doc_text: str, published_at: str,
             announcement_id: str) -> tuple[dict, list[dict]]:
    """Split an extraction into accepted records and rejections.

    Returns (clean_payload, rejections). Rejected records are RETURNED, not
    discarded silently: a fund whose NAV never lands must be explainable, and
    'the quote was not in the document' is a very different fact from 'the
    fund published nothing'.
    """
    clean = {"announcement": payload.get("announcement") or {},
             "quality_control": payload.get("quality_control") or {}}
    rejects: list[dict] = []

    for key in forbidden_keys(clean["announcement"], "announcement."):
        rejects.append({"announcement_id": announcement_id, "section": "announcement",
                        "index": 0, "reasons": [f"computed_signal_field:{key}"]})

    dt = clean["announcement"].get("primary_document_type")
    if dt and dt not in S.PRIMARY_DOCUMENT_TYPE:
        clean["announcement"]["primary_document_type"] = "other"
        clean["announcement"]["primary_document_type_rejected"] = dt
    q = clean["quality_control"].get("document_parse_quality")
    if q and q not in S.PARSE_QUALITY:
        clean["quality_control"]["document_parse_quality"] = None

    for section in S.SECTIONS:
        rows = payload.get(section) or []
        kept = []
        if not isinstance(rows, list):
            rejects.append({"announcement_id": announcement_id, "section": section,
                            "index": -1, "reasons": ["section_not_a_list"]})
            clean[section] = []
            continue
        for i, rec in enumerate(rows):
            if not isinstance(rec, dict):
                rejects.append({"announcement_id": announcement_id, "section": section,
                                "index": i, "reasons": ["record_not_an_object"]})
                continue
            problems = check_record(rec, section, doc_text, published_at)
            if problems:
                rejects.append({"announcement_id": announcement_id, "section": section,
                                "index": i, "reasons": problems,
                                "quote": ((rec.get("source") or {}).get("quote")
                                          if isinstance(rec.get("source"), dict) else None)})
            else:
                kept.append(rec)
        clean[section] = kept
    return clean, rejects
