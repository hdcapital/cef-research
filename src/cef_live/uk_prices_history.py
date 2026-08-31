"""Daily closing prices for UK-listed funds - the other half of a discount.

Two traps this module exists to avoid
-------------------------------------
1. **Adjusted close.** Yahoo's ``adjclose`` is retro-adjusted for every
   dividend and split that happened AFTER the date in question. It is not a
   price anyone could have traded at, and its value CHANGES whenever a new
   dividend is paid, so a discount built on it moves when the future moves.
   Both series are stored, labelled, and the discount builder is asserted by
   test to use the raw one. UK investment trusts yield 3-5%, so this is not
   a rounding difference: on a 2007 observation the two series differ by
   most of a decade of income.

2. **Units.** The LSE quotes ordinary trust lines in pence (``GBp``) but
   some in pounds (``GBP``), and a real minority of London lines - BH Macro's
   USD class, CVC's EUR class - quote in a foreign currency while the RNS
   NAV is in that currency too or in pence. Yahoo reports the quote currency
   in the series metadata - and the metadata has been caught lying: the
   round-2 probe recorded BSIF.L at 1.0227 labelled ``GBp`` for a trust that
   trades near 102p. So units are RECONCILED against each fund's own NAV
   (``reconcile_units`` below) and the currency label is only the fallback.
   A fund whose units cannot be reconciled gets no discount rather than one
   that is out by a factor of 100 - which would look like a collapse.

Coverage is measured, not assumed. A ticker Yahoo does not serve is
recorded missing, with the request that failed, so the gap is visible in
the coverage report instead of being silently absent from the panel.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
PRICE_DIR = Path("data/uk/prices")
OVERRIDES = Path("config/uk_yahoo_symbols.csv")

COLUMNS = ["ticker", "date", "close_raw", "close_adj", "volume",
           "price_ccy", "price_source"]

_last = 0.0


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def yahoo_symbol(ticker: str, overrides: dict[str, str] | None = None) -> str:
    """LSE symbol for a TIDM.

    The default is ``{TIDM}.L``, which is right for the overwhelming
    majority of London lines. Where it is not - a dual line, a renamed
    ticker, a symbol Yahoo spells differently - the mapping is stated by
    hand in ``config/uk_yahoo_symbols.csv`` and verified there, because a
    wrong symbol staples another company's share price onto this fund's NAV
    and nothing downstream can detect it.
    """
    tk = str(ticker).strip().upper()
    if overrides and tk in overrides:
        return overrides[tk]
    return f"{tk}.L"


def load_overrides(path: str | Path = OVERRIDES) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        df = pd.read_csv(p, comment="#")
    except pd.errors.EmptyDataError:
        # No overrides yet is the NORMAL state - the default {TIDM}.L is
        # right for nearly every London line, so this file is a header and
        # a comment block until a fund needs one. A comment-only file has no
        # columns to parse, and letting that raise took the whole price
        # stage down on its first CI run.
        return {}
    if not {"ticker", "yahoo_symbol"} <= set(df.columns):
        return {}
    return dict(zip(df["ticker"].astype(str).str.upper(),
                    df["yahoo_symbol"].astype(str)))


def fetch_history(s: requests.Session, ticker: str, symbol: str,
                  start: str | pd.Timestamp = "2006-01-01",
                  end: pd.Timestamp | None = None) -> pd.DataFrame | None:
    """Daily bars for one fund, from ``start``.

    Returns raw close, adjusted close, volume and the quote currency, or
    None when the symbol serves nothing. Raw and adjusted stay separate
    columns rather than one ``price``, so a later caller has to choose
    deliberately rather than inherit whichever was convenient.
    """
    global _last
    wait = THROTTLE - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int((end or pd.Timestamp.utcnow()).timestamp()) + 86400
    try:
        r = s.get(CHART.format(sym=symbol, p1=max(p1, 0), p2=p2), timeout=45)
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
        df["ticker"] = str(ticker).upper()
        df["price_ccy"] = j.get("meta", {}).get("currency")
        df["price_source"] = f"yahoo:{symbol}"
        df = df.dropna(subset=["close_raw"])
        df = df[~df["date"].duplicated(keep="last")]
        return df[COLUMNS] if len(df) else None
    except Exception:  # noqa: BLE001
        return None


def currency_scale(ccy: str | None) -> float | None:
    """What the QUOTE CURRENCY alone says the pence multiplier should be.

    ``GBp`` is already pence; ``GBP`` is pounds and multiplies by 100. Any
    other quote currency - USD, EUR - yields None rather than a conversion:
    an FX conversion would need a point-in-time rate on every one of ~4,900
    dates and would silently blend currency moves into the discount.

    This is only ever the FALLBACK. The metadata has been observed lying
    (see reconcile_units below), so where a fund has its own NAV to check
    against, the check wins.
    """
    c = str(ccy or "").strip()
    if c == "GBp":
        return 1.0
    if c.casefold() == "gbp":
        return 100.0
    return None


# ------------------------------------------------------------- persistence
def write_prices(px: pd.DataFrame, price_dir: Path = PRICE_DIR) -> list[Path]:
    """One file per calendar year, so a daily run rewrites only this year."""
    price_dir = Path(price_dir)
    price_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for year, g in px.groupby(px["date"].dt.year):
        p = price_dir / f"{int(year)}.parquet"
        g.sort_values(["ticker", "date"]).to_parquet(p, index=False)
        written.append(p)
    return written


def read_prices(price_dir: Path = PRICE_DIR) -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(Path(price_dir).glob("*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)


def merge_prices(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Newly fetched bars win over held ones for the same (ticker, date).

    Yahoo revises the most recent bars - an intraday close is replaced by
    the settled one - so the fresh copy is authoritative for a date it
    covers, while every date it does not cover is left exactly as held.
    """
    if existing is None or not len(existing):
        return new.sort_values(["ticker", "date"]).reset_index(drop=True)
    if new is None or not len(new):
        return existing.sort_values(["ticker", "date"]).reset_index(drop=True)
    both = pd.concat([existing, new], ignore_index=True)
    both["date"] = pd.to_datetime(both["date"])
    both = both.drop_duplicates(["ticker", "date"], keep="last")
    return both.sort_values(["ticker", "date"]).reset_index(drop=True)


def update(tickers: list[str], existing: pd.DataFrame | None = None,
           full: bool = False, start: str = "2006-01-01",
           tail_days: int = 30, deadline_min: float = 240.0,
           overrides: dict[str, str] | None = None,
           progress_every: int = 25) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bring the daily price panel up to date.

    A fund already in the panel is topped up over a ``tail_days`` window
    rather than refetched from 2006: the window is far wider than any
    weekend or bank holiday, so an ordinary daily run costs one small
    request per fund, and a fund that was missed for a fortnight still
    closes its own gap without anyone intervening.

    Returns the merged panel and a per-fund coverage frame - what was asked
    for, what came back, and what did not, which is the survivorship
    measurement rather than an assumption about it.
    """
    existing = existing if existing is not None else pd.DataFrame(columns=COLUMNS)
    overrides = overrides if overrides is not None else load_overrides()
    have_last = {}
    if len(existing):
        have_last = (existing.groupby("ticker")["date"].max()).to_dict()

    s = session()
    started = time.time()
    got, report = [], []
    for i, tk in enumerate(sorted(set(t.upper() for t in tickers)), start=1):
        if (time.time() - started) > deadline_min * 60:
            report.append({"ticker": tk, "status": "deadline_not_attempted",
                           "rows": 0})
            continue
        sym = yahoo_symbol(tk, overrides)
        last = have_last.get(tk)
        if full or last is None:
            frm = start
            mode = "full"
        else:
            frm = (pd.Timestamp(last) - pd.Timedelta(days=tail_days)).date().isoformat()
            mode = "tail"
        df = fetch_history(s, tk, sym, start=frm)
        if df is None or not len(df):
            report.append({"ticker": tk, "symbol": sym, "mode": mode,
                           "status": "no_history", "rows": 0})
            continue
        got.append(df)
        report.append({"ticker": tk, "symbol": sym, "mode": mode,
                       "status": "ok", "rows": int(len(df)),
                       "first": df["date"].min().date().isoformat(),
                       "last": df["date"].max().date().isoformat(),
                       "price_ccy": df["price_ccy"].iloc[-1]})
        if progress_every and i % progress_every == 0:
            print(f"  prices: {i}/{len(set(tickers))} funds, "
                  f"{sum(len(d) for d in got):,} bars this run")
    new = pd.concat(got, ignore_index=True) if got else pd.DataFrame(columns=COLUMNS)
    return merge_prices(existing, new), pd.DataFrame(report)


def coverage(px: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Per-fund span of the price panel, including the funds with no span."""
    asked = sorted(set(t.upper() for t in tickers))
    if px is None or not len(px):
        return pd.DataFrame({"ticker": asked, "price_days": 0})
    g = px.groupby("ticker").agg(price_days=("date", "size"),
                                 price_first=("date", "min"),
                                 price_last=("date", "max"),
                                 price_ccy=("price_ccy", "last")).reset_index()
    out = pd.DataFrame({"ticker": asked}).merge(g, on="ticker", how="left")
    out["price_days"] = out["price_days"].fillna(0).astype(int)
    return out


# ------------------------------------------------------- unit reconciliation
# The evidence that forced this (data/probe/prices/notes.json, round 2):
# Yahoo returned BSIF.L as last_close 1.0227 with currency "GBp". Bluefield
# Solar trades around 102p, so that series is in POUNDS carrying a pence
# label. Trusting the label would have divided a ~102p price by a ~110p NAV
# after reading it as 1.02p - a -99% discount, indistinguishable in the panel
# from a fund in terminal collapse, on a perfectly healthy trust.
#
# So units are reconciled against the fund's own NAV rather than against the
# metadata. A factor of 100 is the only correction ever applied, and only
# where the evidence is unambiguous: real listed funds do not trade at 1% or
# 100x of NAV for years, so a ratio clustered tightly at 1/100 is arithmetic,
# not a market. Anything in between is left unresolved and carries no
# discount - a mis-scaled price is worse than a missing one.
#
# A line quoted in USD whose RNS NAV is also in USD reconciles at 1.0 and is
# correct despite the parser calling the NAV "pence": both halves are in the
# same unit, which is all a ratio needs.

SCALE_CANDIDATES = (1.0, 100.0, 0.01)
RATIO_OK_LO, RATIO_OK_HI = 0.2, 5.0
# how tight the ratio must cluster before a x100 correction is called
# arithmetic rather than a guess (IQR of price/NAV in log10 terms)
MAX_LOG_SPREAD = 0.5


def reconcile_units(nav: pd.DataFrame, px: pd.DataFrame,
                    max_nav_age_days: int = 40) -> pd.DataFrame:
    """Per fund: the scale that puts price and NAV in the same unit.

    Returns ticker, price_scale, price_unit_status, the median ratio before
    and after, the spread that justified it, and how many days it was
    measured over - the evidence, not just the verdict, so a wrong call is
    visible in the committed report instead of buried in a multiplication.
    """
    cols = ["ticker", "price_scale", "price_unit_status", "ratio_raw",
            "ratio_scaled", "log_spread", "n_days", "price_ccy"]
    if px is None or not len(px):
        return pd.DataFrame(columns=cols)

    n = pd.DataFrame(columns=["ticker", "date", "nav_pence"])
    if nav is not None and len(nav):
        n = nav[["ticker", "published_at", "nav_pence"]].rename(
            columns={"published_at": "date"}).dropna()
        n["date"] = pd.to_datetime(n["date"])
        n = n.sort_values("date")

    nav_by_ticker = {tk: g for tk, g in n.groupby("ticker", sort=False)} if len(n) else {}

    rows = []
    for tk, g in px.groupby("ticker"):
        ccy = str(g["price_ccy"].iloc[-1]) if "price_ccy" in g.columns else ""
        ng = nav_by_ticker.get(tk, n.iloc[0:0])
        if not len(ng):
            # no NAV to reconcile against: fall back to the quote currency,
            # which is right for the great majority and is labelled as an
            # assumption rather than a measurement.
            scale = currency_scale(ccy)
            rows.append({"ticker": tk, "price_scale": scale,
                         "price_unit_status": "assumed_from_currency" if scale
                         else "unresolved_no_nav_no_currency",
                         "ratio_raw": None, "ratio_scaled": None,
                         "log_spread": None, "n_days": 0, "price_ccy": ccy})
            continue
        m = pd.merge_asof(g[["date", "close_raw"]].sort_values("date"),
                          ng[["date", "nav_pence"]], on="date",
                          direction="backward",
                          tolerance=pd.Timedelta(days=max_nav_age_days))
        m = m.dropna(subset=["nav_pence", "close_raw"])
        m = m[(m["nav_pence"] > 0) & (m["close_raw"] > 0)]
        if len(m) < 20:
            scale = currency_scale(ccy)
            rows.append({"ticker": tk, "price_scale": scale,
                         "price_unit_status": "assumed_from_currency_thin_overlap"
                         if scale else "unresolved_thin_overlap",
                         "ratio_raw": None, "ratio_scaled": None,
                         "log_spread": None, "n_days": int(len(m)),
                         "price_ccy": ccy})
            continue
        ratio = (m["close_raw"] / m["nav_pence"]).astype(float)
        med = float(ratio.median())
        import numpy as np
        lr = np.log10(ratio.replace(0, np.nan).dropna())
        spread = float(lr.quantile(0.75) - lr.quantile(0.25)) if len(lr) else float("nan")
        best, best_dist = None, None
        for s in SCALE_CANDIDATES:
            v = med * s
            if not (RATIO_OK_LO <= v <= RATIO_OK_HI):
                continue
            d = abs(np.log(v))
            if best is None or d < best_dist:
                best, best_dist = s, d
        if best is None:
            status, scale = "unresolved_scale", None
        elif best == 1.0:
            status, scale = "ok", 1.0
        elif spread > MAX_LOG_SPREAD or pd.isna(spread):
            # the ratio would need a x100 correction but does not cluster
            # tightly enough for that to be arithmetic rather than a guess
            status, scale = "unresolved_dispersed", None
        else:
            status, scale = f"rescaled_x{best:g}", best
        rows.append({"ticker": tk, "price_scale": scale,
                     "price_unit_status": status, "ratio_raw": round(med, 6),
                     "ratio_scaled": round(med * best, 6) if best else None,
                     "log_spread": round(spread, 4) if pd.notna(spread) else None,
                     "n_days": int(len(m)), "price_ccy": ccy})
    return pd.DataFrame(rows, columns=cols).sort_values("ticker").reset_index(drop=True)
