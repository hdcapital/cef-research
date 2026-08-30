"""Daily ASX price history - the other half of a discount.

The NTA extractor gives NAV. A discount needs price on the same dates, and
price is a different source with a different failure mode.

THE TRAP THIS MODULE EXISTS TO AVOID: Yahoo's chart endpoint returns two
close series. `adjclose` is retro-adjusted for every dividend and split that
happened AFTER the date in question, so it is not the price anyone could have
traded at, and the adjustment CHANGES whenever a new dividend is paid. Using
it against a contemporaneous NAV produces a discount that is both wrong and
contaminated by the future - the exact bias this project is built to avoid,
arriving through a column name. So:

    discount        raw close only        (what the market actually paid)
    total return    adjclose, or raw close plus the extracted distributions

Both are stored, labelled, and the discount builder is asserted by test to
use the raw one.

Survivorship is the second risk. The announcement index includes funds that
have since been delisted - that is the point of a point-in-time universe -
and Yahoo drops many delisted tickers. Coverage is therefore MEASURED per
code and recorded, never assumed; a code with no Yahoo history is missing,
not excluded, and the Appendix 3E buyback notices give a real traded price
for some of those days from the fund's own filings.
"""

from __future__ import annotations

import os
import time
import zlib
from pathlib import Path

import pandas as pd
import requests

CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1=0&period2={p2}&interval=1d&events=div%2Csplit")
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
_last = 0.0


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def fetch_history(s: requests.Session, code: str) -> pd.DataFrame | None:
    """Full daily history for one ASX code.

    Returns raw close, adjusted close, volume and currency. Raw and adjusted
    are kept as separate columns rather than one 'price', so a later caller
    has to choose deliberately.
    """
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    url = CHART.format(sym=f"{code}.AX", p2=int(time.time()))
    try:
        r = s.get(url, timeout=45)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    try:
        j = r.json()["chart"]["result"][0]
        ts = j.get("timestamp") or []
        if not ts:
            return None
        q = j["indicators"]["quote"][0]
        adj = ((j["indicators"].get("adjclose") or [{}])[0] or {}).get("adjclose")
        idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
        df = pd.DataFrame({
            "date": idx,
            "close_raw": q.get("close"),
            "close_adj": adj if adj is not None else [None] * len(ts),
            "volume": q.get("volume"),
        })
        df["ticker"] = code.upper()
        df["currency"] = j.get("meta", {}).get("currency")
        df["price_source"] = f"yahoo:{code}.AX"
        df = df.dropna(subset=["close_raw"])
        df = df[~df["date"].duplicated(keep="last")]
        return df if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def build_discount(nav: pd.DataFrame, px: pd.DataFrame,
                   max_nav_staleness_days: int = 45) -> pd.DataFrame:
    """Point-in-time discount: price/NAV - 1, using only what was known.

    Two rules, both of which are easy to lose and expensive to lose:

    1. RAW close. Adjusted close embeds dividends paid after the date, so a
       discount built on it moves when the future changes.
    2. As-of join on PUBLISHED date, not valuation date. A 31 March NAV
       announced on 8 April was not knowable on 1 April, and joining on
       valuation date would hand the backtest a week of hindsight on every
       observation.

    A NAV older than max_nav_staleness_days carries no discount rather than a
    stale one.
    """
    if nav.empty or px.empty:
        return pd.DataFrame()
    n = nav.copy()
    n["published_at"] = pd.to_datetime(n["published_at"])
    n = n.dropna(subset=["nav_per_share"]).sort_values("published_at")
    p = px.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values("date")

    out = []
    for ticker, pg in p.groupby("ticker"):
        ng = n[n["ticker"] == ticker]
        if ng.empty:
            continue
        merged = pd.merge_asof(
            pg[["date", "close_raw", "ticker"]],
            ng[["published_at", "nav_per_share", "nav_basis", "valuation_date",
                "announcement_id"]].rename(columns={"published_at": "date"}),
            on="date", direction="backward")
        merged["nav_known_at"] = merged["date"]
        merged["nav_age_days"] = (
            merged["date"] - pd.to_datetime(merged["valuation_date"],
                                            errors="coerce")).dt.days
        merged["discount"] = merged["close_raw"] / merged["nav_per_share"] - 1.0
        merged.loc[merged["nav_age_days"] > max_nav_staleness_days, "discount"] = pd.NA
        merged["ticker"] = ticker
        out.append(merged)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def coverage_report(codes: list[str], fetched: dict[str, pd.DataFrame | None]) -> dict:
    """What Yahoo actually returned, per code - the survivorship measurement."""
    have = {c: df for c, df in fetched.items() if df is not None and len(df)}
    missing = sorted(set(codes) - set(have))
    spans = {c: {"first": str(df["date"].min().date()),
                 "last": str(df["date"].max().date()),
                 "rows": int(len(df))} for c, df in have.items()}
    return {"codes_requested": len(codes), "codes_with_history": len(have),
            "coverage": round(len(have) / max(1, len(codes)), 4),
            "codes_missing": missing[:200], "missing_count": len(missing),
            "spans_sample": dict(list(spans.items())[:10])}
