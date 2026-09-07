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
    held = load(path)
    if not len(new):
        return held
    if len(held):
        known = set(held["event_id"])
        add = new[~new["event_id"].isin(known)]
        merged = pd.concat([held, add], ignore_index=True)
    else:
        merged = new.copy()
    save(merged, path)
    return merged


# ---------------------------------------------------------------- TR-1
_PCT = r"([0-9]{1,2}(?:\.[0-9]{1,3})?)\s*%"
_TR1_RESULT = re.compile(r"Resulting situation[^%]{0,260}?" + _PCT, re.I)
_TR1_PREV = re.compile(r"Position of previous notification[^%]{0,200}?" + _PCT, re.I)
_TR1_HOLDER = re.compile(r"(?:Full name of shareholder\(s\)|Name of the shareholder|"
                         r"Details of person subject to the notification obligation)\s*:?\s*"
                         r"(?:\([^)]{0,80}\))?\s*(?:\d\.\s*)?(?:Name\s*:?\s*)?([A-Z][A-Za-z0-9&.,'\- ]{3,90}?)"
                         r"(?=\s+(?:City|Registered|4\.|5\.|Country|\d+\.\s|Date))", re.I)


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
