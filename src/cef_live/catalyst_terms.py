"""Phase 3: read the BODY of a catalyst announcement for its terms.

The headline says a tender is coming; the body says how big, at what price
and by when. Those terms - and their DATES - are what make a catalyst
actionable and what a forward calendar is built from. A model reads only
the bodies of events the signed taxonomy already rated |weight| >= 3, a
bounded number per night, and every record it returns is guarded the
house way: the supporting quote must be verbatim in the document, the
confidence above the floor, and nothing computed (no discount, no
expected return) is accepted from it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROMPT_F = Path("config/prompts/catalyst_terms_v1.md")
MIN_CONFIDENCE = 0.6
MAX_DOC_CHARS = 60_000
FORBIDDEN = ("discount_z", "z_score", "expected_return", "irr", "attractiveness", "score")
EVENT_CLASSES = {
    "tender_offer", "continuation_vote", "liquidation_wind_down", "scheme_merger_rollover",
    "strategic_review", "takeover_offer", "manager_change", "discount_control",
    "distribution_policy", "dividend_cut", "nav_writedown", "covenant_gearing", "suspension",
    "failed_continuation", "offer_withdrawn", "legal_sanctions", "going_concern_delay",
    "manager_exit", "other"}


def model_name() -> str:
    # the repo's cheap bulk-document model (repo variable CHEAP_MODEL,
    # config/params.yaml pulse.models.cheap); Opus 5 when none is set
    return (os.environ.get("CATALYST_MODEL") or os.environ.get("CHEAP_MODEL")
            or "claude-opus-5")


def prompt_text() -> str:
    return PROMPT_F.read_text()


def prompt_version() -> str:
    return "v1:" + hashlib.sha256(PROMPT_F.read_bytes()).hexdigest()[:12]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def request_params(headline: str, security_id: str, date: str, text: str) -> dict:
    """The instruction block is identical every call and sits in `system`
    with a cache breakpoint; only the document varies."""
    return {
        "model": model_name(),
        "max_tokens": 4096,
        "system": [{"type": "text", "text": prompt_text(),
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content":
                      f"security_id: {security_id}\npublished: {date}\n"
                      f"headline: {headline}\n\ndocument_text:\n{text[:MAX_DOC_CHARS]}"}],
    }


def parse_response(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        obj = json.loads(t)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
    return obj if isinstance(obj, dict) else None


def guard(rec: dict, doc_text: str) -> list[str]:
    """Reasons the record must not be stored. Empty = accepted."""
    problems = []
    if not isinstance(rec, dict):
        return ["not_an_object"]
    if rec.get("event_class") not in EVENT_CLASSES:
        problems.append(f"enum:event_class={rec.get('event_class')!r}")
    conf = rec.get("confidence")
    if not isinstance(conf, (int, float)):
        problems.append("confidence_missing")
    elif not 0.0 <= float(conf) <= 1.0:
        problems.append("confidence_out_of_range")
    elif float(conf) < MIN_CONFIDENCE:
        problems.append(f"confidence_below_floor:{conf}")
    q = str(rec.get("quote") or "").strip()
    if not q:
        problems.append("no_source_quote")
    elif _norm(q) not in _norm(doc_text):
        problems.append("quote_not_in_document")
    terms = rec.get("terms") or {}
    if not isinstance(terms, dict):
        problems.append("terms_not_an_object")
    else:
        for k in terms:
            if any(b in str(k).lower() for b in FORBIDDEN):
                problems.append(f"computed_signal_field:{k}")
    for d in rec.get("dates") or []:
        if not isinstance(d, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d.get("date") or "")):
            problems.append(f"bad_date:{d!r}")
    return problems


def extract_one(client, headline: str, security_id: str, date: str, text: str) -> tuple[dict | None, dict]:
    """One model call -> (accepted record or None, audit dict)."""
    params = request_params(headline, security_id, date, text)
    resp = client.messages.create(**params)
    audit = {"model": params["model"], "prompt_version": prompt_version(),
             "stop_reason": getattr(resp, "stop_reason", None),
             "input_tokens": getattr(getattr(resp, "usage", None), "input_tokens", None),
             "output_tokens": getattr(getattr(resp, "usage", None), "output_tokens", None),
             "cache_read": getattr(getattr(resp, "usage", None), "cache_read_input_tokens", None)}
    if getattr(resp, "stop_reason", None) == "refusal":
        audit["rejected"] = ["refusal"]
        return None, audit
    out_text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", [])
                       if getattr(b, "type", "") == "text")
    rec = parse_response(out_text)
    if rec is None:
        audit["rejected"] = ["unparseable"]
        return None, audit
    problems = guard(rec, text)
    if problems:
        audit["rejected"] = problems
        return None, audit
    keep = {"event_class": rec["event_class"], "stage": rec.get("stage"),
            "terms": {k: v for k, v in (rec.get("terms") or {}).items() if v not in (None, "")},
            "dates": rec.get("dates") or [], "quote": rec["quote"][:400],
            "confidence": float(rec["confidence"]), **audit}
    return keep, audit


# ------------------------------------------------------------ bodies
def fetch_body(url: str, session, throttle: float = 1.5) -> str:
    """Investegate page text or an ASX PDF's text; '' when unreadable."""
    if not url:
        return ""
    r = session.get(url, timeout=60)
    time.sleep(throttle)
    if r.status_code != 200:
        return ""
    if r.content[:4] == b"%PDF":
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                return " ".join((p.extract_text() or "") for p in pdf.pages[:12])
        except Exception:  # noqa: BLE001
            return ""
    from bs4 import BeautifulSoup
    return " ".join(BeautifulSoup(r.text, "html.parser").get_text(" ").split())


def candidates(events: pd.DataFrame, days: int = 45, min_abs_weight: int = 3) -> pd.DataFrame:
    if not len(events):
        return events
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    ev = events[(events["date"] >= cutoff) & (events["weight"].abs() >= min_abs_weight)
                & events["url"].notna() & events["terms"].isna()]
    return ev.sort_values(["date", "weight"], key=lambda s: s.abs() if s.name == "weight" else s,
                          ascending=[False, False])


def run(events: pd.DataFrame, session, budget_docs: int = 40, client=None,
        fetch=None) -> tuple[pd.DataFrame, dict]:
    """Read up to budget_docs catalyst bodies; store accepted terms (or the
    rejection) in events.terms so nothing is read twice."""
    stats = {"candidates": 0, "read": 0, "accepted": 0, "rejected": 0, "fetch_failed": 0,
             "input_tokens": 0, "output_tokens": 0, "model": model_name()}
    if not len(events):
        return events, stats
    cand = candidates(events)
    stats["candidates"] = int(len(cand))
    if not len(cand):
        return events, stats
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    ev = events.copy()
    for i, (idx, r) in enumerate(cand.iterrows()):
        if i >= budget_docs:
            break
        try:
            text = fetch(r["url"]) if fetch is not None else fetch_body(r["url"], session)
        except Exception:  # noqa: BLE001
            text = ""
        if not text or len(text) < 200:
            stats["fetch_failed"] += 1
            ev.at[idx, "terms"] = json.dumps({"llm": "unreadable_body"})
            continue
        stats["read"] += 1
        try:
            rec, audit = extract_one(client, str(r["headline"]), str(r["security_id"]),
                                     str(r["date"]), text)
        except Exception as exc:  # noqa: BLE001
            stats["rejected"] += 1
            ev.at[idx, "terms"] = json.dumps({"llm": "error", "error": str(exc)[:200]})
            continue
        stats["input_tokens"] += int(audit.get("input_tokens") or 0)
        stats["output_tokens"] += int(audit.get("output_tokens") or 0)
        if rec is None:
            stats["rejected"] += 1
            ev.at[idx, "terms"] = json.dumps({"llm": "rejected", **audit})
        else:
            stats["accepted"] += 1
            ev.at[idx, "terms"] = json.dumps({"llm": "accepted", **rec})
    return ev, stats


# ----------------------------------------------------------- calendar
def calendar(events: pd.DataFrame, horizon_days: int = 120) -> pd.DataFrame:
    """Every dated term in the store that falls within the horizon: the
    forward calendar of votes, tender closes, settlements and effective
    dates."""
    cols = ["date", "security_id", "event_class", "what", "announced", "headline", "url"]
    if not len(events):
        return pd.DataFrame(columns=cols)
    today = datetime.now(timezone.utc).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).date().isoformat()
    rows = []
    for r in events[events["terms"].notna()].itertuples(index=False):
        try:
            t = json.loads(r.terms) if isinstance(r.terms, str) else dict(r.terms)
        except Exception:  # noqa: BLE001
            continue
        if t.get("llm") != "accepted":
            continue
        for d in t.get("dates") or []:
            dd = str(d.get("date") or "")
            if today <= dd <= end:
                rows.append({"date": dd, "security_id": r.security_id,
                             "event_class": t.get("event_class") or r.event_class,
                             "what": d.get("what"), "announced": r.date,
                             "headline": r.headline, "url": r.url})
    return pd.DataFrame(rows, columns=cols).sort_values(["date", "security_id"]).reset_index(drop=True)


def terms_summary(terms_json) -> str:
    """One line for the brief: 'tender 15% at NAV less 2%; closes 2026-09-30'."""
    if not isinstance(terms_json, str):
        return ""
    try:
        t = json.loads(terms_json)
    except Exception:  # noqa: BLE001
        return ""
    if t.get("llm") != "accepted":
        return ""
    bits = []
    tm = t.get("terms") or {}
    if tm.get("size_pct_of_shares") is not None:
        bits.append(f"{tm['size_pct_of_shares']:g}% of shares")
    if tm.get("price_basis"):
        bits.append(f"at {tm['price_basis']}")
    if tm.get("offer_price"):
        bits.append(f"offer {tm['offer_price']}")
    if tm.get("expected_return_pct_of_nav") is not None:
        bits.append(f"{tm['expected_return_pct_of_nav']:g}% of NAV returned")
    if tm.get("dividend_change"):
        bits.append(tm["dividend_change"])
    if tm.get("counterparty"):
        bits.append(f"with {tm['counterparty']}")
    for d in (t.get("dates") or [])[:2]:
        bits.append(f"{d.get('what')} {d.get('date')}")
    return "; ".join(str(b) for b in bits if b)[:200]
