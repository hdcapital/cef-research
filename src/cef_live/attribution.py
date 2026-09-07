"""Widening attribution: WHY a fund's discount moved (Phase 3a, layer 1).

A z-score says a discount is unusually wide against the fund's own history.
It does not say whether the price fell or the NAV was marked up, whether
the whole sector moved or only this fund, or whether anyone was trading
it. Those three facts decide whether a dislocation is something to buy
(price-led, idiosyncratic, on volume, no seller overhang) or something to
explain (NAV-led lag, sector-wide repricing, a holder exiting). Each
dislocation alert carries one "why" line built here.

Sources, in the order each is preferred:
  * the daily live snapshot store (data/live_history/live_daily.parquet):
    one row per fund per day from the nightly table - price, live NAV
    estimate, discount, sector. Both markets; deepens by one day a night.
  * the UK daily discount panel (data/uk/discount, cef_live.uk_discount)
    where the runner holds the uk_daily state group - years deep.
  * Yahoo daily bars for the alert funds only (volume signature).
Nothing is estimated where a source is missing: the line says what it
could not measure.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import events as EV

STORE = Path("data/live_history/live_daily.parquet")
STORE_COLUMNS = ["date", "security_id", "market", "sector", "price", "nta_est",
                 "nav_anchor", "discount_est", "z_adj", "nav_current"]
LOOKBACK_DAYS = 21        # trading days: one month of widening
MIN_WINDOW = 5            # fewer rows than this and no attribution is made
SECTOR_MIN_N = 4          # peers needed before a sector median is quoted
DOMINANT_SHARE = 0.65     # one leg explains this much of the move -> "led"
SECTOR_SHARE = 0.6        # sector median explains this much -> "sector-wide"
SECTOR_PARTIAL = 0.3
SERIES_BREAK = 0.5        # |price or NAV move| in the window that is a data break
VOLUME_BASE_DAYS = 60
VOLUME_RECENT_DAYS = 5


# ------------------------------------------------------------ snapshot store
def snapshot(live: pd.DataFrame, path: Path = STORE,
             asof: str | None = None) -> pd.DataFrame:
    """Append today's live table to the daily store (one row per fund per
    day; a re-run replaces the day's row). Returns the whole store."""
    asof = asof or datetime.now(timezone.utc).date().isoformat()
    cols = [c for c in STORE_COLUMNS if c != "date"]
    rec = pd.DataFrame({c: live[c] if c in live.columns else np.nan for c in cols})
    rec.insert(0, "date", asof)
    rec = rec[rec["security_id"].notna()]
    held = load_store(path)
    if len(held):
        held = held[~((held["date"] == asof)
                      & held["security_id"].isin(set(rec["security_id"])))]
    out = pd.concat([held, rec], ignore_index=True) if len(held) else rec
    out = out.sort_values(["security_id", "date"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def load_store(path: Path = STORE) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=STORE_COLUMNS)
    df = pd.read_parquet(path)
    for c in STORE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    return df[STORE_COLUMNS]


_WB_COLS = {"Security ID": "security_id", "Market": "market", "Sector": "sector",
            "Last price": "price", "Live NAV estimate": "nta_est",
            "Last published NAV": "nav_anchor", "Discount now": "discount_est",
            "Z-score (own history)": "z_adj"}
_WB_DATE = re.compile(r"cef_universe_(\d{4}-\d{2}-\d{2})\.xlsx$")


def backfill_from_workbooks(glob_dir: Path = Path("outputs/live"),
                            path: Path = STORE) -> int:
    """Seed the store from the daily universe workbooks already committed
    (one per brief day). Idempotent. Returns rows added."""
    before = len(load_store(path))
    for f in sorted(Path(glob_dir).glob("cef_universe_*.xlsx")):
        m = _WB_DATE.search(f.name)
        if not m:
            continue
        try:
            wb = pd.read_excel(f, sheet_name="Universe")
        except Exception:  # noqa: BLE001
            continue
        keep = {k: v for k, v in _WB_COLS.items() if k in wb.columns}
        live = wb[list(keep)].rename(columns=keep)
        for c in ("price", "nta_est", "nav_anchor", "discount_est", "z_adj"):
            if c in live.columns:
                live[c] = pd.to_numeric(live[c], errors="coerce")
        # the workbook did not carry nav_current; NaN, never guessed
        snapshot(live, path, asof=m.group(1))
    return len(load_store(path)) - before


# ------------------------------------------------------------ histories
def fund_history(store: pd.DataFrame, sid: str) -> pd.DataFrame:
    """date-indexed price / nav / discount for one fund, discount rows only."""
    g = store[(store["security_id"] == sid) & store["discount_est"].notna()]
    g = g.sort_values("date").drop_duplicates("date", keep="last")
    return g.set_index("date")[["price", "nta_est", "discount_est"]]


def uk_panel_histories(tickers_by_sid: dict[str, str]) -> dict[str, pd.DataFrame]:
    """The UK daily discount panel as fund histories keyed by security_id
    (empty on a runner without the uk_daily state group)."""
    try:
        from . import uk_discount as UKD
        panel = UKD.read_panel()
    except Exception:  # noqa: BLE001
        return {}
    if panel is None or not len(panel):
        return {}
    need = {"ticker", "date", "price_pence", "nav_pence_adj", "discount"}
    if not need <= set(panel.columns):
        return {}
    by_ticker = {}
    for t, g in panel[panel["discount"].notna()].groupby("ticker"):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        h = pd.DataFrame({"price": g["price_pence"].astype(float).values,
                          "nta_est": g["nav_pence_adj"].astype(float).values,
                          "discount_est": g["discount"].astype(float).values},
                         index=pd.to_datetime(g["date"]).dt.date.astype(str))
        by_ticker[str(t).upper()] = h
    return {sid: by_ticker[t] for sid, t in tickers_by_sid.items()
            if isinstance(t, str) and t.upper() in by_ticker}


# ------------------------------------------------------------ the decomposition
def decompose(h: pd.DataFrame, lookback: int = LOOKBACK_DAYS,
              min_window: int = MIN_WINDOW) -> dict | None:
    """Discount change over the window split into its price and NAV legs.

    d = P/N - 1, so d_t - d_0 = (1+d_0) * [(1+rp)/(1+rn) - 1]. The price
    leg is the change had the NAV stood still, the NAV leg the remainder;
    the two sum to the observed change exactly.
    """
    h = h.dropna(subset=["discount_est"])
    if len(h) < min_window:
        return None
    win = h.tail(lookback + 1)
    d0, dt = float(win["discount_est"].iloc[0]), float(win["discount_est"].iloc[-1])
    p0, pt = win["price"].iloc[0], win["price"].iloc[-1]
    n0, nt = win["nta_est"].iloc[0], win["nta_est"].iloc[-1]
    out = {"window_days": int(len(win) - 1), "from": str(win.index[0]),
           "to": str(win.index[-1]), "d_then": d0, "d_now": dt,
           "delta_d": dt - d0, "price_ret": None, "nav_ret": None,
           "price_leg": None, "nav_leg": None, "driver": "unknown"}
    if all(pd.notna(v) and v > 0 for v in (p0, pt, n0, nt)):
        rp, rn = float(pt) / float(p0) - 1.0, float(nt) / float(n0) - 1.0
        price_leg = (1.0 + d0) * rp
        nav_leg = (dt - d0) - price_leg
        out.update(price_ret=rp, nav_ret=rn, price_leg=price_leg, nav_leg=nav_leg)
        total = abs(price_leg) + abs(nav_leg)
        # a price or NAV that moved by more than half in a month is a
        # series break (unit change, restatement, corporate action) far
        # more often than a market move; the line says so instead of
        # calling it a dislocation
        if abs(rp) >= SERIES_BREAK or abs(rn) >= SERIES_BREAK:
            out["driver"] = "series break - verify units"
        elif total > 1e-9:
            if abs(price_leg) / total >= DOMINANT_SHARE:
                out["driver"] = "price-led"
            elif abs(nav_leg) / total >= DOMINANT_SHARE:
                out["driver"] = "NAV-led"
            else:
                out["driver"] = "mixed"
        else:
            out["driver"] = "flat"
    return out


def sector_move(histories: dict[str, pd.DataFrame], peers: list[str],
                start: str, end: str) -> dict:
    """Median discount change across peers holding both dates (nearest
    on-or-before observation at each end)."""
    moves = []
    for sid in peers:
        h = histories.get(sid)
        if h is None or not len(h):
            continue
        h = h.dropna(subset=["discount_est"])
        a = h[h.index <= start]
        b = h[h.index <= end]
        if not len(a) or not len(b) or a.index[-1] < _shift(start, -7):
            continue
        moves.append(float(b["discount_est"].iloc[-1]) - float(a["discount_est"].iloc[-1]))
    if len(moves) < SECTOR_MIN_N:
        return {"sector_n": len(moves), "sector_delta": None}
    return {"sector_n": len(moves), "sector_delta": float(np.median(moves))}


def _shift(day: str, days: int) -> str:
    return (pd.Timestamp(day) + pd.Timedelta(days=days)).date().isoformat()


def scope(delta_d: float, sector_delta: float | None) -> str:
    if sector_delta is None:
        return "unknown"
    if abs(delta_d) < 1e-9:
        return "flat"
    if np.sign(sector_delta) != np.sign(delta_d):
        return "idiosyncratic"
    share = abs(sector_delta) / abs(delta_d)
    if share >= SECTOR_SHARE:
        return "sector-wide"
    if share >= SECTOR_PARTIAL:
        return "partly sector"
    return "idiosyncratic"


# ------------------------------------------------------------ flow signature
def volume_ratio(bars: pd.DataFrame | None, recent: int = VOLUME_RECENT_DAYS,
                 base: int = VOLUME_BASE_DAYS) -> float | None:
    """Mean volume over the last `recent` bars against the `base` bars
    before them. None without at least 20 base bars."""
    if bars is None or "volume" not in bars.columns:
        return None
    v = pd.to_numeric(bars["volume"], errors="coerce").dropna()
    v = v[v > 0]
    if len(v) < recent + 20:
        return None
    r, b = v.tail(recent), v.iloc[-(recent + base):-recent]
    if b.mean() <= 0:
        return None
    return float(r.mean() / b.mean())


def flow_note(sid: str, churn: pd.DataFrame | None, vol_ratio: float | None) -> str:
    parts = []
    if vol_ratio is not None:
        parts.append(f"volume {vol_ratio:.1f}× its {VOLUME_BASE_DAYS}d average")
    if churn is not None and len(churn):
        c = churn[churn["security_id"] == sid]
        if len(c):
            c = c.iloc[0]
            who = f" ({c['holder']})" if isinstance(c.get("holder"), str) else ""
            parts.append(f"{int(c['filings'])} holder notices in "
                         f"{EV.CHURN_WINDOW_DAYS}d, {c['direction']}{who}")
    return "; ".join(parts)


# ------------------------------------------------------------ the line
def why_line(a: dict) -> str:
    if a.get("driver") is None and a.get("window_days") is None:
        return a.get("note", "no discount history to attribute")
    pt = a["delta_d"] * 100.0
    verb = "widened" if pt < 0 else "narrowed"
    s = (f"Discount {verb} {abs(pt):.1f}pt over {a['window_days']}d "
         f"({a['d_then']:+.1%} → {a['d_now']:+.1%})")
    if a.get("price_ret") is not None:
        s += (f": price {a['price_ret']:+.1%}, NAV {a['nav_ret']:+.1%} "
              f"→ {a['driver']}")
    if a.get("sector_delta") is not None:
        s += (f"; sector median {a['sector_delta'] * 100:+.1f}pt "
              f"(n={a['sector_n']}) → {a['scope']}")
    elif a.get("sector_n") is not None:
        s += f"; sector: too few peers with history (n={a['sector_n']})"
    if a.get("flow"):
        s += f"; {a['flow']}"
    return s


def attribute(sid: str, market: str, sector: str | None,
              histories: dict[str, pd.DataFrame], peers: list[str],
              bars: pd.DataFrame | None = None,
              churn: pd.DataFrame | None = None,
              lookback: int = LOOKBACK_DAYS) -> dict:
    """The attribution record for one fund; `why` is the rendered line."""
    h = histories.get(sid)
    a: dict = {"security_id": sid, "window_days": None, "driver": None}
    dec = decompose(h, lookback) if h is not None else None
    if dec is None:
        n = int(len(h.dropna(subset=["discount_est"]))) if h is not None else 0
        a["note"] = f"no attribution: {n} day(s) of discount history (need {MIN_WINDOW})"
        a["flow"] = flow_note(sid, churn, volume_ratio(bars))
        if a["flow"]:
            a["note"] += f"; {a['flow']}"
        a["why"] = a["note"]
        return a
    a.update(dec)
    a.update(sector_move(histories, [p for p in peers if p != sid], dec["from"], dec["to"]))
    a["scope"] = scope(dec["delta_d"], a.get("sector_delta"))
    a["volume_ratio"] = volume_ratio(bars)
    a["flow"] = flow_note(sid, churn, a["volume_ratio"])
    a["why"] = why_line(a)
    return a


def attribute_all(verdicts: pd.DataFrame, live: pd.DataFrame,
                  store: pd.DataFrame, bars_by_sid: dict[str, pd.DataFrame] | None = None,
                  events: pd.DataFrame | None = None,
                  uk_histories: dict[str, pd.DataFrame] | None = None,
                  lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """One attribution row per verdict row (security_id, why, and the
    measured fields). Peers are the live funds in the same market+sector."""
    if verdicts is None or not len(verdicts):
        return pd.DataFrame(columns=["security_id", "why"])
    histories = {sid: fund_history(store, sid) for sid in store["security_id"].unique()} \
        if len(store) else {}
    # a deeper UK panel history replaces the store's where it has more days
    for sid, h in (uk_histories or {}).items():
        if sid not in histories or len(h) > len(histories[sid]):
            histories[sid] = h
    sect = live.set_index("security_id")["sector"] if "sector" in live.columns else pd.Series(dtype=object)
    mkt = live.set_index("security_id")["market"] if "market" in live.columns else pd.Series(dtype=object)
    churn = EV.holder_churn(events) if events is not None and len(events) else None
    rows = []
    for sid in verdicts["security_id"]:
        sector = sect.get(sid)
        market = mkt.get(sid)
        peers = [s for s in live["security_id"]
                 if sect.get(s) == sector and mkt.get(s) == market] \
            if isinstance(sector, str) else []
        bars = (bars_by_sid or {}).get(sid)
        rows.append(attribute(sid, market, sector, histories, peers, bars, churn, lookback))
    return pd.DataFrame(rows)


def to_json(a: dict) -> str:
    return json.dumps({k: (None if isinstance(v, float) and np.isnan(v) else v)
                       for k, v in a.items() if k != "security_id"}, default=str)
