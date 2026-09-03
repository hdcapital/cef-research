"""EOD price layer - probe-verified endpoints only (docs/data-sources.md).

Round-2 probe verdict (data/probe/prices/notes.json): the Yahoo v8 chart
API serves everything needed unauthenticated from CI runners - indices
(^FTAS 1984-, ^AXJO 1992-), ACWI world proxy (2008-), FX pairs (2003-),
and daily fund closes for large and small names; Stooq is quota-blocked
from runners (HTML interstitial); the old ASX price endpoint is 404.
Yahoo is therefore the primary source; every observation records its
source so a future second adapter can fail over per series.

Factor construction (pre-specified):
  local  - market total-index proxy in local ccy (^FTAS / ^AXJO)
  world  - ACWI (USD) converted into local ccy via the FX pair
  fx     - the local ccy vs USD rate itself (global-mandate exposure)
Price-index caveat: these are price (not total-return) indices; the gap is
dividend yield drift, absorbed into the fitted intercept and the tracked
sigma - documented, not hidden.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}"

FACTOR_SYMS = {
    "UK": {"local": "^FTAS", "world_usd": "ACWI", "fxusd": "GBPUSD=X"},
    "AU": {"local": "^AXJO", "world_usd": "ACWI", "fxusd": "AUDUSD=X"},
}

_last = 0.0


def _get(s: requests.Session, sym: str, rng: str, iv: str) -> pd.Series | None:
    global _last
    wait = 1.5 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        r = s.get(CHART.format(sym=sym, rng=rng, iv=iv), timeout=45)
        if r.status_code != 200:
            return None
        j = r.json()["chart"]["result"][0]
        ts = j.get("timestamp") or []
        closes = (j["indicators"]["quote"][0].get("close") or [])
        idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
        ser = pd.Series(closes, index=idx, dtype=float).dropna()
        ser = ser[~ser.index.duplicated(keep="last")]
        ser.attrs["ccy"] = j.get("meta", {}).get("currency")
        return ser if len(ser) else None
    except Exception:  # noqa: BLE001
        return None


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def monthly_factor_returns(s: requests.Session, market: str) -> pd.DataFrame | None:
    """Monthly factor returns indexed by obs_month (str YYYY-MM)."""
    syms = FACTOR_SYMS[market]
    series = {k: _get(s, v, "max", "1mo") for k, v in syms.items()}
    if any(v is None for v in series.values()):
        return None
    px = pd.DataFrame(series).sort_index()
    px = px.groupby(px.index.to_period("M")).last()
    out = pd.DataFrame(index=px.index.astype(str))
    out["local"] = px["local"].pct_change().values
    world_local = px["world_usd"] / px["fxusd"]     # ACWI USD -> local ccy
    out["world"] = world_local.pct_change().values
    out["fx"] = px["fxusd"].pct_change().values
    return out.dropna(how="all")


def daily_factor_returns(s: requests.Session, market: str,
                         rng: str = "6mo") -> pd.DataFrame | None:
    """Daily factor returns (same construction), indexed by date."""
    syms = FACTOR_SYMS[market]
    series = {k: _get(s, v, rng, "1d") for k, v in syms.items()}
    if any(v is None for v in series.values()):
        return None
    px = pd.DataFrame(series).sort_index().ffill().dropna()
    out = pd.DataFrame(index=px.index)
    out["local"] = px["local"].pct_change()
    out["world"] = (px["world_usd"] / px["fxusd"]).pct_change()
    out["fx"] = px["fxusd"].pct_change()
    return out.dropna(how="all")


def latest_prices(s: requests.Session, symbols: dict[str, str],
                  rng: str = "5d") -> pd.DataFrame:
    """Latest close per security_id. symbols: {security_id: yahoo_symbol}.

    Returns security_id, price, price_date, price_source. Missing symbols
    are simply absent - never filled.
    """
    rows = []
    for sid, sym in symbols.items():
        ser = _get(s, sym, rng, "1d")
        if ser is None or not len(ser):
            continue
        rows.append({"security_id": sid, "price": float(ser.iloc[-1]),
                     "price_date": ser.index[-1].date().isoformat(),
                     "price_source": f"yahoo:{sym}",
                     "price_ccy": ser.attrs.get("ccy")})
    return pd.DataFrame(rows)


# GBP cross rates as DAILY LEVELS (units of foreign currency per 1 GBP),
# for converting a NAV a fund states in dollars or euros into the pence its
# London line trades in. Levels, not returns: a conversion needs the rate on
# the NAV's own date, and the factor series above are returns.
FX_PAIRS = {"UK": {"USD": "GBPUSD=X", "EUR": "GBPEUR=X", "CAD": "GBPCAD=X"}}


def fx_levels(s: requests.Session, market: str,
              rng: str = "10y") -> dict[str, pd.Series]:
    """{foreign ccy: daily level series} for the market's supported pairs.

    A pair Yahoo does not serve is simply absent - the caller then leaves
    that currency's NAVs unconverted (and unused) rather than guessing.
    """
    out: dict[str, pd.Series] = {}
    for ccy, sym in FX_PAIRS.get(market, {}).items():
        ser = _get(s, sym, rng, "1d")
        if ser is not None and len(ser):
            out[ccy] = ser
    return out
