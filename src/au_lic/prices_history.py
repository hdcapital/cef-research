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


# ----------------------------------------------------------- delisted funds
# A delisted fund's price history is the hardest part of this dataset to get
# for free, and it is also the part that decides whether a discount study is
# honest: cheap funds are disproportionately the ones that die, so a panel
# built only on survivors flatters exactly the strategy being tested.
#
# Three survivorship-free sources, all already in the archive, plus Yahoo
# where it can be trusted:
#
#   1. the ASX monthly investment-products reports. These are POINT-IN-TIME
#      snapshots: a fund listed in March 2019 is in the March 2019 file
#      forever, whatever happened to it later. Month-end share price for
#      every fund that existed that month, from 2017.
#   2. Appendix 3E daily buyback notices - a real traded price, from the
#      fund's own filing, on every day it bought stock. Discounted funds buy
#      back heavily, so coverage is best exactly where it matters most.
#   3. scheme booklets, takeover documents and wind-up notices, which state
#      VWAPs, offer prices and final NTA at the end of a fund's life.
#
# Yahoo is used where it still serves the code AND passes the reuse check
# below.

REUSE_GRACE_DAYS = 120


def guard_ticker_reuse(px: pd.DataFrame, last_announcement: str | pd.Timestamp,
                       grace_days: int = REUSE_GRACE_DAYS) -> tuple[pd.DataFrame, str]:
    """Drop price history that cannot belong to this fund.

    ASX codes are RECYCLED. A three-letter code freed by a 2015 delisting is
    routinely reassigned to an unrelated company, and Yahoo will happily
    serve one continuous series spanning both. Splicing a dead LIC to a
    mining company's later prices would produce a fabricated return series
    that looks completely ordinary - no gap, no error, nothing downstream
    could detect it.

    So history is truncated at the fund's last announcement plus a grace
    window (delisting formalities and final distributions trail the last
    substantive filing). Anything after that belongs to whoever holds the
    code now.
    """
    if px is None or px.empty:
        return px, "empty"
    last = pd.to_datetime(last_announcement, errors="coerce")
    if pd.isna(last):
        return px, "no_last_announcement"
    cutoff = last + pd.Timedelta(days=grace_days)
    out = px[pd.to_datetime(px["date"]) <= cutoff]
    dropped = len(px) - len(out)
    if dropped == 0:
        return out, "ok"
    # a large tail after a fund stopped filing is a reused code, not a fund
    # that quietly kept trading
    status = "truncated_probable_reuse" if dropped > 250 else "truncated"
    return out, status


def prices_from_monthly_reports(panel: pd.DataFrame) -> pd.DataFrame:
    """Month-end price per fund from the point-in-time ASX report snapshots.

    Survivorship-free by construction: the file for a given month lists the
    funds that existed that month, and it is never rewritten.
    """
    if panel is None or panel.empty or "share_price" not in panel.columns:
        return pd.DataFrame(columns=["ticker", "date", "close_raw", "price_source"])
    p = panel[["security_id", "obs_month", "share_price"]].dropna(
        subset=["share_price"]).copy()
    p["ticker"] = p["security_id"].astype(str).str.replace("^ASX:", "", regex=True)
    p["date"] = pd.PeriodIndex(p["obs_month"], freq="M").to_timestamp(how="end").normalize()
    p["close_raw"] = p["share_price"].astype(float)
    p["price_source"] = "asx_monthly_report"
    return p[["ticker", "date", "close_raw", "price_source"]]


def prices_from_buybacks(facts: pd.DataFrame) -> pd.DataFrame:
    """Traded prices from the fund's own Appendix 3E notices."""
    if facts is None or facts.empty:
        return pd.DataFrame(columns=["ticker", "date", "close_raw", "price_source"])
    import json as _json

    rows = []
    for r in facts.itertuples(index=False):
        if getattr(r, "family", None) != "buyback_daily":
            continue
        try:
            payload = _json.loads(r.payload)
        except Exception:  # noqa: BLE001
            continue
        price = payload.get("price")
        if price is None:
            continue
        rows.append({"ticker": str(r.ticker).upper(),
                     "date": pd.to_datetime(r.published_at, errors="coerce"),
                     "close_raw": float(price),
                     "price_source": "appendix_3e_traded"})
    return pd.DataFrame(rows).dropna(subset=["date"])


PRICE_PRIORITY = ["yahoo", "asx_monthly_report", "appendix_3e_traded"]


def assemble_price_panel(*frames: pd.DataFrame) -> pd.DataFrame:
    """One price per (ticker, date), best available source, always labelled.

    Priority is exchange close, then the ASX's own published month-end, then
    a traded print from a buyback notice. Every row keeps price_source so a
    result can be re-run on the daily-only subset and compared - a finding
    that only survives on month-end prints is a different, weaker claim, and
    the panel has to make that checkable rather than hide it.
    """
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=["ticker", "date", "close_raw", "price_source"])
    allpx = pd.concat(frames, ignore_index=True)
    allpx["date"] = pd.to_datetime(allpx["date"]).dt.normalize()
    allpx["_rank"] = allpx["price_source"].map(
        lambda s: next((i for i, p in enumerate(PRICE_PRIORITY)
                        if str(s).startswith(p)), len(PRICE_PRIORITY)))
    allpx = allpx.sort_values(["ticker", "date", "_rank"])
    allpx = allpx.drop_duplicates(["ticker", "date"], keep="first")
    return allpx.drop(columns="_rank").reset_index(drop=True)
