"""Phase 3: the per-fund events file and the fund file.

Every announcement row the nightly already sees (UK listing pages, the ASX
market index) is classified against the signed taxonomy in `catalysts`
and appended to ONE store, `data/fund_events/events.parquet`, keyed by
event id (fund, date, headline). The store remembers when each event was
first seen and when a brief alerted on it, so the brief can show ONLY what
is new. Holdings notifications are kept at weight 0 and, for UK funds,
their TR-1 bodies are read (bounded, throttled) for the holder's previous
and resulting stake - a large holder grinding down over weeks is the
classic slow-to-clear widening (Phase 3a, holder overhang).

`write_fund_files` materialises one JSON per fund from the live table, the
registry, the forward-IRR table and this store: the record the tool
updates when an announcement changes something.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import catalysts

EVENTS_PATH = Path("data/fund_events/events.parquet")
FUND_FILES_DIR = Path("data/fund_files")

COLUMNS = ["event_id", "security_id", "market", "date", "headline", "url",
           "event_class", "weight", "direction", "source", "first_seen",
           "alerted_at", "terms"]

# holder churn: this many holdings notifications inside the window
CHURN_WINDOW_DAYS = 30
CHURN_MIN_FILINGS = 3


def event_id(security_id: str, date: str, headline: str) -> str:
    key = f"{security_id}|{str(date)[:10]}|{(headline or '')[:120].strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_events(ann_rows: list[dict] | None, au_index_path: str | Path | None,
                 days: int = 45, au_codes: set[str] | None = None) -> pd.DataFrame:
    """Classify recent announcement rows from both markets into event rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    rows: list[dict] = []
    for r in ann_rows or []:
        d = str(r.get("date") or "")[:10]
        if not d or d < cutoff:
            continue
        got = catalysts.classify_signed(r.get("headline", ""))
        if got is None:
            continue
        sid = str(r.get("security_id"))
        rows.append({"security_id": sid, "market": "AU" if sid.startswith("ASX:") else "UK",
                     "date": d, "headline": (r.get("headline") or "")[:200],
                     "url": r.get("url"), "event_class": got["class"],
                     "weight": int(got["weight"]), "direction": got["direction"],
                     "source": "investegate_listing"})
    if au_index_path and Path(au_index_path).exists():
        idx = pd.read_parquet(au_index_path)
        if au_codes:
            idx = idx[idx["code"].isin(au_codes)]
        idx = idx.copy()
        idx["d"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
        idx = idx[idx["d"] >= cutoff]
        for r in idx.itertuples(index=False):
            got = catalysts.classify_signed(str(r.headline or ""))
            if got is None:
                continue
            rows.append({"security_id": f"ASX:{str(r.code).upper()}", "market": "AU",
                         "date": r.d, "headline": str(r.headline or "")[:200],
                         "url": r.url, "event_class": got["class"],
                         "weight": int(got["weight"]), "direction": got["direction"],
                         "source": "asx_index"})
    df = pd.DataFrame(rows, columns=[c for c in COLUMNS if c not in
                                     ("event_id", "first_seen", "alerted_at", "terms")])
    if not len(df):
        return pd.DataFrame(columns=COLUMNS)
    df["event_id"] = [event_id(s, d, h) for s, d, h in
                      zip(df["security_id"], df["date"], df["headline"])]
    df["first_seen"] = _now()
    df["alerted_at"] = None
    df["terms"] = None
    return df.drop_duplicates("event_id")[COLUMNS]


def load(path: Path = EVENTS_PATH) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_parquet(path)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[COLUMNS]


def save(df: pd.DataFrame, path: Path = EVENTS_PATH) -> Path:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    out = df[COLUMNS].copy()
    for c in ("alerted_at", "terms", "url", "first_seen"):
        out[c] = out[c].astype(object).where(out[c].notna(), None)
    out.sort_values(["date", "security_id"], ascending=[False, True]).to_parquet(path, index=False)
    return Path(path)


def merge_events(new: pd.DataFrame, path: Path = EVENTS_PATH) -> pd.DataFrame:
    """Union new rows into the store; an existing event keeps its first_seen,
    alerted_at and terms - the store is append-only memory."""
    held = reclassify(load(path))
    if not len(new):
        if len(held):
            save(held, path)
        return held
    if len(held):
        known = set(held["event_id"])
        add = new[~new["event_id"].isin(known)]
        merged = pd.concat([held, add], ignore_index=True)
    else:
        merged = new.copy()
    save(merged, path)
    return merged


def reclassify(events: pd.DataFrame) -> pd.DataFrame:
    """Re-derive class/weight/direction from the headline so a taxonomy fix
    reaches events already in the store (event_id is headline-keyed, so
    identity is unchanged; first_seen/alerted_at/terms are kept). A row
    the taxonomy no longer recognises is dropped."""
    if not len(events):
        return events
    ev = events.copy()
    got = ev["headline"].map(lambda h: catalysts.classify_signed(h) or {})
    keep = got.map(bool)
    ev = ev[keep].copy()
    got = got[keep]
    ev["event_class"] = got.map(lambda g: g["class"]).values
    ev["weight"] = got.map(lambda g: int(g["weight"])).values
    ev["direction"] = got.map(lambda g: g["direction"]).values
    return ev.reset_index(drop=True)


# ---------------------------------------------------------------- TR-1
_PCT = r"([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%"
_TR1_RESULT = re.compile(r"Resulting situation[^%]{0,260}?" + _PCT, re.I)
_TR1_PREV = re.compile(r"Position of previous notification[^%]{0,200}?" + _PCT, re.I)
_TR1_HOLDER = re.compile(
    r"(?:notification obligation|Full name of shareholder\(s\))\s*:?\s*(?:\d\.\s*)?"
    r"(?:Name\s*:?\s*)?([A-Z][A-Za-z0-9&.,'()\- ]{3,90}?)\s+"
    r"(?=City|Registered|Country|\d\.\s|Date)", re.I)


def parse_tr1(text: str) -> dict:
    """Holder name, previous and resulting % of voting rights from a UK
    TR-1 body. Absent fields stay absent - never guessed."""
    t = " ".join((text or "").split())
    out: dict = {}
    m = _TR1_RESULT.search(t)
    if m:
        out["new_pct"] = float(m.group(1))
    m = _TR1_PREV.search(t)
    if m:
        out["prev_pct"] = float(m.group(1))
    m = _TR1_HOLDER.search(t)
    if m:
        out["holder"] = m.group(1).strip(" .,")[:90]
    if "new_pct" in out and "prev_pct" in out:
        out["direction"] = ("selling" if out["new_pct"] < out["prev_pct"]
                            else "buying" if out["new_pct"] > out["prev_pct"] else "flat")
    return out


def enrich_holdings(events: pd.DataFrame, session, budget: int = 40,
                    days: int = CHURN_WINDOW_DAYS, throttle: float = 1.5,
                    fetch=None) -> tuple[pd.DataFrame, dict]:
    """Read the TR-1 bodies of recent UK holdings notifications that carry no
    terms yet - bounded by `budget` fetches, throttled; the market is never
    asked twice for the same page. `fetch(url) -> text` may replace the
    session for tests."""
    stats = {"candidates": 0, "fetched": 0, "parsed": 0, "failed": 0}
    if not len(events):
        return events, stats
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    ev = events.copy()
    cand = ev[(ev["event_class"] == "holdings_notification") & (ev["market"] == "UK")
              & ev["terms"].isna() & (ev["date"] >= cutoff) & ev["url"].notna()]
    stats["candidates"] = int(len(cand))
    for i, (idx, r) in enumerate(cand.sort_values("date", ascending=False).iterrows()):
        if i >= budget:
            break
        try:
            if fetch is not None:
                text = fetch(r["url"])
            else:
                from bs4 import BeautifulSoup
                resp = session.get(r["url"], timeout=45)
                text = BeautifulSoup(resp.text, "html.parser").get_text(" ") \
                    if resp.status_code == 200 else ""
                time.sleep(throttle)
            stats["fetched"] += 1
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            continue
        got = parse_tr1(text)
        if got:
            stats["parsed"] += 1
        # a fetched-but-unparseable page is recorded so it is not refetched
        ev.at[idx, "terms"] = json.dumps(got or {"unparsed": True})
    return ev, stats


# ---------------------------------------------------------- realisations
_PCT_NUM = r"(?:approximately\s+|around\s+|c\.?\s*|circa\s+)?([0-9]{1,3}(?:\.[0-9]+)?)\s*(?:%|per\s*cent)"
_BASIS = (r"(?:its|the)?\s*(?:[0-9]{1,2}\s+\w+\s+\d{4}\s+|(?:most\s+recent|last|latest|previous|prior)\s+"
          r"(?:published\s+|reported\s+|audited\s+|unaudited\s+)?)?"
          r"(carrying\s+value|book\s+value|holding\s+value|(?:published\s+|reported\s+)?valuation"
          r"|net\s+asset\s+value|\bNAV\b)")
_REAL_A = re.compile(r"\b(premium|uplift|discount)\s+of\s+" + _PCT_NUM
                     + r"\s+(?:to|over|above|below|on)\s+" + _BASIS, re.I)
_REAL_B = re.compile(_PCT_NUM + r"\s+(premium|uplift|discount)\s+(?:to|over|above|below|on)\s+" + _BASIS, re.I)
_REAL_C = re.compile(r"\b(?:in\s+line\s+with|at|equal\s+to)\s+" + _BASIS + r"\b", re.I)
_REAL_UP = re.compile(r"\b(?:uplift|increase)\s+of\s+" + _PCT_NUM + r"\s+(?:to|over|on|against)\s+" + _BASIS, re.I)
# the owner and qualifier words that sit between a figure and its basis:
# "92% of THEIR net asset value", "105% of THE ASSET'S NAV", "no less than
# THE ASSETS' RECENT Net Asset Value", "a premium to ITS LAST PUBLISHED valuation"
_OWNER = (r"(?:(?:its|their|the|the\s+company['\u2019]?s|the\s+assets?['\u2019]?s?)\s+)?"
          r"(?:(?:recent|last|latest|most\s+recent(?:ly)?|previous|prior)\s+)?"
          r"(?:(?:published|reported|audited|unaudited)\s+)?")
_BASIS2 = (r"(carrying\s+value|book\s+value|holding\s+value|valuations?|net\s+asset\s+value"
           r"|\bNAV\b|values?\s+ascribed)")
# "representing 92% of their net asset value", "deliver approximately 105%
# of the asset's NAV": the price as a share of the carrying value
_REAL_PCT_OF = re.compile(r"\b(?:representing|represents|deliver(?:ing|s)?|equivalent\s+to|equal\s+to)\s+"
                          + _PCT_NUM + r"\s+of\s+" + _OWNER + _BASIS2, re.I)
# "no less than the assets' recent Net Asset Value", "at or above valuation"
_REAL_ATLEAST = re.compile(r"\b(?:no\s+less\s+than|not\s+less\s+than|at\s+least|at\s+or\s+above)\s+"
                           + _OWNER + _BASIS2, re.I)
# "a premium to its last published valuation" with no figure: the sign alone
_REAL_UNQ = re.compile(r"\b(premium|discount)\s+to\s+" + _OWNER + _BASIS2, re.I)
REALISATION_WINDOW_DAYS = 365
REALISATION_PARSER = "r2"      # bumped when the rules change; unparsed rows are re-read


def _basis(b: str) -> str:
    return " ".join(b.lower().split())


def parse_realisation(text: str) -> dict:
    """The price a disposal achieved against the fund's own carrying value,
    as the announcement states it: {"vs_carrying_pct": +12.0, "basis":
    "carrying value", "quote": "..."}. A discount is negative; "in line
    with carrying value" is 0; a premium stated without a figure carries
    "sign" and no percentage. Absent when the body does not say."""
    t = " ".join((text or "").split())
    for pat, order in ((_REAL_A, "wb"), (_REAL_B, "bw"), (_REAL_UP, "up")):
        m = pat.search(t)
        if not m:
            continue
        if order == "wb":
            word, pct, basis = m.group(1), m.group(2), m.group(3)
        elif order == "bw":
            pct, word, basis = m.group(1), m.group(2), m.group(3)
        else:
            word, pct, basis = "uplift", m.group(1), m.group(2)
        try:
            v = float(pct)
        except ValueError:
            continue
        if v > 300:
            continue
        sign = -1.0 if word.lower() == "discount" else 1.0
        return {"vs_carrying_pct": round(sign * v, 2), "sign": int(sign), "basis": _basis(basis),
                "quote": t[max(0, m.start() - 60):m.end() + 40]}
    m = _REAL_PCT_OF.search(t)
    if m:
        try:
            v = float(m.group(1)) - 100.0
        except ValueError:
            v = None
        if v is not None and -100 < v <= 200:
            return {"vs_carrying_pct": round(v, 2), "sign": (v > 0) - (v < 0),
                    "basis": _basis(m.group(2)), "quote": t[max(0, m.start() - 60):m.end() + 40]}
    m = _REAL_ATLEAST.search(t)
    if m and re.search(r"\b(?:sold|sale|dispos|realis|achiev)", t, re.I):
        return {"vs_carrying_pct": 0.0, "sign": 0, "at_least": True, "basis": _basis(m.group(1)),
                "quote": t[max(0, m.start() - 60):m.end() + 40]}
    m = _REAL_C.search(t)
    if m and re.search(r"\b(?:sold|sale|dispos|realis)", t, re.I):
        return {"vs_carrying_pct": 0.0, "sign": 0, "basis": _basis(m.group(1)),
                "quote": t[max(0, m.start() - 60):m.end() + 40]}
    m = _REAL_UNQ.search(t)
    if m and re.search(r"\b(?:sold|sale|sell|dispos|realis)", t, re.I):
        sign = -1 if m.group(1).lower() == "discount" else 1
        return {"vs_carrying_pct": None, "sign": sign, "basis": _basis(m.group(2)),
                "quote": t[max(0, m.start() - 60):m.end() + 40]}
    return {}


def enrich_realisations(events: pd.DataFrame, session, budget: int = 30,
                        days: int = REALISATION_WINDOW_DAYS, fetch=None) -> tuple[pd.DataFrame, dict]:
    """Read the bodies of recent realisation announcements that carry no
    terms yet (both markets; ASX PDFs through the same reader the term
    extractor uses), bounded by `budget` fetches."""
    stats = {"candidates": 0, "fetched": 0, "parsed": 0, "failed": 0}
    if not len(events):
        return events, stats
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    ev = events.copy()
    def _stale(t) -> bool:
        if t is None or (isinstance(t, float) and pd.isna(t)):
            return True
        try:
            d = json.loads(t) if isinstance(t, str) else dict(t)
        except Exception:  # noqa: BLE001
            return False
        return bool(d.get("unparsed")) and d.get("parser") != REALISATION_PARSER
    cand = ev[(ev["event_class"] == "realisation") & ev["terms"].map(_stale)
              & (ev["date"] >= cutoff) & ev["url"].notna()]
    stats["candidates"] = int(len(cand))
    for i, (idx, r) in enumerate(cand.sort_values("date", ascending=False).iterrows()):
        if i >= budget:
            break
        try:
            if fetch is not None:
                text = fetch(r["url"])
            else:
                from . import catalyst_terms as CT
                text = CT.fetch_body(r["url"], session)
            stats["fetched"] += 1
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            continue
        got = parse_realisation(text)
        if got:
            stats["parsed"] += 1
            ev.at[idx, "terms"] = json.dumps({"realisation": got})
        else:
            ev.at[idx, "terms"] = json.dumps({"unparsed": True, "parser": REALISATION_PARSER})
    return ev, stats


def realisation_evidence(events: pd.DataFrame, sid: str,
                         days: int = REALISATION_WINDOW_DAYS) -> dict | None:
    """What the fund's own disposals said about its NAV in the window:
    count, mean and last stated premium/discount to carrying value."""
    if events is None or not len(events):
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    g = events[(events["security_id"].astype(str) == str(sid))
               & (events["event_class"] == "realisation") & (events["date"] >= cutoff)
               & events["terms"].notna()].sort_values("date")
    rows = []
    for r in g.itertuples(index=False):
        try:
            t = json.loads(r.terms) if isinstance(r.terms, str) else dict(r.terms)
        except Exception:  # noqa: BLE001
            continue
        rl = t.get("realisation") if isinstance(t, dict) else None
        if not rl or "sign" not in rl and rl.get("vs_carrying_pct") is None:
            continue
        pct = rl.get("vs_carrying_pct")
        sign = rl.get("sign")
        if sign is None and pct is not None:
            sign = (pct > 0) - (pct < 0)
        rows.append({"date": r.date, "vs_carrying_pct": float(pct) if pct is not None else None,
                     "sign": int(sign), "at_least": bool(rl.get("at_least")),
                     "basis": rl.get("basis"), "headline": r.headline, "url": r.url})
    if not rows:
        return None
    vals = [x["vs_carrying_pct"] for x in rows if x["vs_carrying_pct"] is not None]
    signs = [x["sign"] for x in rows]
    return {"n": len(rows), "n_quantified": len(vals),
            "avg_vs_carrying_pct": round(sum(vals) / len(vals), 2) if vals else None,
            "min_vs_carrying_pct": min(vals) if vals else None,
            "max_vs_carrying_pct": max(vals) if vals else None,
            "premiums": sum(1 for x in signs if x > 0), "discounts": sum(1 for x in signs if x < 0),
            "in_line": sum(1 for x in signs if x == 0),
            "last": rows[-1], "window_days": days}


def realisation_line(ev: dict | None) -> str:
    if not ev:
        return ""
    n, avg = ev["n"], ev.get("avg_vs_carrying_pct")
    if avg is None:
        p, d, i = ev.get("premiums", 0), ev.get("discounts", 0), ev.get("in_line", 0)
        what = ", ".join(x for x in (f"{p} at a premium" if p else "", f"{d} at a discount" if d else "",
                                     f"{i} in line" if i else "") if x)
        what = f"{what} to carrying value (unquantified)"
    else:
        what = "in line with carrying value" if abs(avg) < 0.05 else f"at {avg:+.1f}% to carrying value"
    last = ev["last"]
    last_s = (f"{last['vs_carrying_pct']:+.1f}%" if last.get("vs_carrying_pct") is not None
              else {1: "premium", -1: "discount", 0: "in line"}.get(last.get("sign"), "stated"))
    return f"{n} realisation{'s' if n != 1 else ''} in 12m {what} (last {last['date']}: {last_s})"


def holder_churn(events: pd.DataFrame, days: int = CHURN_WINDOW_DAYS,
                 min_filings: int = CHURN_MIN_FILINGS) -> pd.DataFrame:
    """Per fund: holdings notifications in the window and, where TR-1 terms
    were read, the net direction of the largest mover."""
    if not len(events):
        return pd.DataFrame(columns=["security_id", "filings", "sellers", "buyers",
                                     "largest_move", "holder", "direction"])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    h = events[(events["event_class"] == "holdings_notification") & (events["date"] >= cutoff)]
    rows = []
    for sid, g in h.groupby("security_id"):
        if len(g) < min_filings:
            continue
        sellers = buyers = 0
        best = None
        for t in g["terms"].dropna():
            try:
                d = json.loads(t) if isinstance(t, str) else dict(t)
            except Exception:  # noqa: BLE001
                continue
            if d.get("direction") == "selling":
                sellers += 1
            elif d.get("direction") == "buying":
                buyers += 1
            if "new_pct" in d and "prev_pct" in d:
                mv = d["new_pct"] - d["prev_pct"]
                if best is None or abs(mv) > abs(best[0]):
                    best = (mv, d.get("holder"))
        rows.append({"security_id": sid, "filings": int(len(g)), "sellers": sellers,
                     "buyers": buyers,
                     "largest_move": round(best[0], 2) if best else None,
                     "holder": best[1] if best else None,
                     "direction": ("overhang" if sellers > buyers else
                                   "accumulation" if buyers > sellers else "churn")})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ alerting
def new_events(events: pd.DataFrame, live_sids: set | None = None,
               min_abs_weight: int = 2, days: int = 45) -> pd.DataFrame:
    """Events no brief has alerted on yet: catalysts of |weight| >= 2 in the
    window, for live funds, plus one synthetic holder-churn row per fund."""
    if not len(events):
        return pd.DataFrame(columns=COLUMNS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    ev = events[(events["alerted_at"].isna()) & (events["date"] >= cutoff)
                & (events["weight"].abs() >= min_abs_weight)]
    if live_sids is not None:
        ev = ev[ev["security_id"].astype(str).isin({str(s) for s in live_sids})]
    return ev.sort_values(["weight", "date"], key=lambda s: s.abs() if s.name == "weight" else s,
                          ascending=[False, False])


def mark_alerted(events: pd.DataFrame, ids, when: str | None = None,
                 path: Path = EVENTS_PATH) -> pd.DataFrame:
    ids = set(ids)
    if not ids or not len(events):
        return events
    ev = events.copy()
    ev.loc[ev["event_id"].isin(ids), "alerted_at"] = when or _now()
    save(ev, path)
    return ev


def summarise(events: pd.DataFrame, days: int = 30) -> dict:
    if not len(events):
        return {"events": 0, "funds": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    w = events[events["date"] >= cutoff]
    return {"events_total": int(len(events)), "events_30d": int(len(w)),
            "funds_30d": int(w["security_id"].nunique()),
            "by_direction_30d": w["direction"].value_counts().to_dict(),
            "by_class_30d": w["event_class"].value_counts().head(12).to_dict(),
            "unalerted": int(events["alerted_at"].isna().sum()),
            "holdings_with_terms": int((w["event_class"].eq("holdings_notification")
                                        & w["terms"].notna()).sum())}


# ----------------------------------------------------------- fund files
def _f(v):
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        v = v.item()
    return v


def fund_record(live_row: pd.Series, events: pd.DataFrame,
                registry_row: pd.Series | None = None,
                irr_row: pd.Series | None = None,
                churn_row: pd.Series | None = None, days: int = 90) -> dict:
    sid = str(live_row["security_id"])
    g = lambda k: _f(live_row.get(k)) if k in live_row.index else None  # noqa: E731
    rg = (lambda k: _f(registry_row.get(k)) if registry_row is not None and k in registry_row.index else None)  # noqa: E731
    ir = (lambda k: _f(irr_row.get(k)) if irr_row is not None and k in irr_row.index else None)  # noqa: E731
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    ev = events[(events["security_id"].astype(str) == sid) & (events["date"] >= cutoff)] \
        if len(events) else events
    ev_list = []
    for r in ev.sort_values("date", ascending=False).head(60).itertuples(index=False):
        terms = None
        if isinstance(r.terms, str):
            try:
                terms = json.loads(r.terms)
            except Exception:  # noqa: BLE001
                terms = None
        ev_list.append({"date": r.date, "class": r.event_class, "weight": int(r.weight),
                        "direction": r.direction, "headline": r.headline, "url": r.url,
                        "alerted_at": _f(r.alerted_at), "terms": terms})
    return {
        "security_id": sid, "name": g("name"), "market": g("market"),
        "ticker": rg("ticker"), "sector": g("sector"),
        "liveness": {"status": rg("status"), "reason": rg("liveness_reason"),
                     "source": rg("live_status_source"), "last_nav": rg("last_nav"),
                     "last_announcement": rg("last_announcement")},
        "nav": {"anchor": g("nav_anchor"), "anchor_date": g("anchor_date"),
                "anchor_source": g("anchor_source"), "estimate": g("nta_est"),
                "basis": g("basis"), "staleness_days": g("staleness_days"),
                "staleness_limit_days": g("staleness_limit_days"),
                "current": g("nav_current"), "unit": g("nav_unit"),
                "quality_ok": g("data_quality_ok"), "quality_reason": g("data_quality_reason")},
        "price": {"price": g("price"), "ccy": g("price_ccy"), "date": g("price_date"),
                  "source": g("price_source")},
        "discount": {"estimate": g("discount_est"), "z": g("z_adj"), "z_status": g("z_status"),
                     "z_source": g("z_source"), "z_window_months": g("z_window_months"),
                     "mu_36m": g("disc_mu_36m"), "sigma_36m": g("disc_sigma_36m"),
                     "alert_eligible": g("alert_eligible")},
        "forward_irr": {"central": ir("irr_central"), "discount_only": ir("irr_discount_only"),
                        "g_used": ir("g_used"), "g_source": ir("g_source")},
        "holder_churn": ({k: _f(v) for k, v in churn_row.items()} if churn_row is not None else None),
        "realisations": realisation_evidence(events, sid),
        "events_90d": ev_list,
        "updated_at": _now(),
    }


def write_fund_files(live: pd.DataFrame, events: pd.DataFrame,
                     registry: pd.DataFrame | None = None,
                     irr: pd.DataFrame | None = None,
                     out_dir: Path = FUND_FILES_DIR,
                     only_sids: set | None = None) -> dict:
    """One JSON per live fund, plus an index. Files for funds no longer in
    the live table are left as they are (history is not deleted)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reg_ix = registry.set_index("security_id") if registry is not None and len(registry) else None
    irr_ix = irr.drop_duplicates("security_id").set_index("security_id") \
        if irr is not None and len(irr) else None
    churn = holder_churn(events)
    churn_ix = churn.set_index("security_id") if len(churn) else None
    n = 0
    index = []
    for _, row in live.iterrows():
        sid = str(row["security_id"])
        if only_sids is not None and sid not in only_sids:
            continue
        rec = fund_record(
            row, events,
            registry_row=reg_ix.loc[sid] if reg_ix is not None and sid in reg_ix.index else None,
            irr_row=irr_ix.loc[sid] if irr_ix is not None and sid in irr_ix.index else None,
            churn_row=churn_ix.loc[sid] if churn_ix is not None and sid in churn_ix.index else None)
        fn = out_dir / (re.sub(r"[^A-Za-z0-9_.-]+", "_", sid) + ".json")
        fn.write_text(json.dumps(rec, indent=1, default=str))
        n += 1
        index.append({"security_id": sid, "name": rec["name"], "market": rec["market"],
                      "file": fn.name, "events_90d": len(rec["events_90d"]),
                      "z": rec["discount"]["z"], "nav_current": rec["nav"]["current"]})
    (out_dir / "index.json").write_text(json.dumps(
        {"written_at": _now(), "funds": n, "files": index}, indent=1, default=str))
    return {"fund_files": n, "holder_churn_funds": int(len(churn))}
