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
        splits = [{"ticker": str(ticker).upper(),
                   "date": pd.Timestamp(int(v["date"]), unit="s").normalize(),
                   "numerator": float(v.get("numerator") or 0),
                   "denominator": float(v.get("denominator") or 0),
                   "ratio": (float(v["numerator"]) / float(v["denominator"]))
                   if v.get("numerator") and v.get("denominator") else None}
                  for v in (j.get("events", {}).get("splits") or {}).values()]
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
        if not len(df):
            return None
        out = df[COLUMNS]
        out.attrs["splits"] = [v for v in splits if v["ratio"]]
        return out
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
    got, report, split_rows = [], [], []
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
        split_rows.extend(df.attrs.get("splits") or [])
        report.append({"ticker": tk, "symbol": sym, "mode": mode,
                       "status": "ok", "rows": int(len(df)),
                       "first": df["date"].min().date().isoformat(),
                       "last": df["date"].max().date().isoformat(),
                       "price_ccy": df["price_ccy"].iloc[-1]})
        if progress_every and i % progress_every == 0:
            print(f"  prices: {i}/{len(set(tickers))} funds, "
                  f"{sum(len(d) for d in got):,} bars this run")
    new = pd.concat(got, ignore_index=True) if got else pd.DataFrame(columns=COLUMNS)
    # Split events are only served alongside the bars, so they are collected
    # on the same request rather than costing another one. A tail-mode fetch
    # only sees splits inside its window, so the held set is merged rather
    # than replaced - losing an old split would silently un-adjust a decade
    # of NAV.
    held = read_splits()
    fresh = pd.DataFrame(split_rows, columns=["ticker", "date", "numerator",
                                              "denominator", "ratio"])
    both = pd.concat([held, fresh], ignore_index=True)
    if len(both):
        both["date"] = pd.to_datetime(both["date"])
        both = both.drop_duplicates(["ticker", "date"], keep="last")
        write_splits(both)
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
        # reconcile against the SPLIT-ADJUSTED NAV where one is present, so a
        # subdivision is not mistaken for a unit error (and then refused).
        col = "nav_pence_adj" if "nav_pence_adj" in nav.columns else "nav_pence"
        n = nav[["ticker", "published_at", col]].rename(
            columns={"published_at": "date", col: "nav_pence"}).dropna()
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


# ------------------------------------------------------------------ splits
# THE SECOND UNIT TRAP, and a subtler one than the pence/pounds label.
#
# Yahoo's `close` is retro-adjusted for SPLITS. A trust that subdivided its
# shares 10-for-1 in 2021 has its entire pre-2021 price history divided by
# ten, so it is comparable with today's price. The NAV in a 2015 RNS
# announcement is not: it states pence per share on the share count that
# existed in 2015.
#
# Divide one by the other and the discount is out by the split factor - and
# it does not look like an error. It looks like a fund that traded at a 90%
# discount for years and then abruptly did not, on the day of the split.
#
# The first run measured exactly this: 17 live trusts - Bankers, Caledonia,
# Temple Bar, Polar Capital Technology, Lowland, Murray International,
# Alliance Witan among them - came back with price/NAV clustered near 0.10
# or, for consolidations, near 170. The unit reconciliation correctly refused
# to price any of them (a 10x correction is not one of the scales it will
# apply, and it never guesses), so they had no discount at all. The fix is
# not a scale: it is putting NAV on the same share basis as the price.
#
# The log-spread told which was which. A tight spread (Capital Gearing
# 0.013, Temple Bar 0.036) is a constant offset - the split predates the
# window. A wide one (Bankers 0.99, Polar Cap Tech 0.98) is a ratio that
# steps mid-history, which is the split itself, visible in the data.

SPLIT_FILE = Path("data/uk/splits.parquet")


def write_splits(splits: pd.DataFrame, path: Path = SPLIT_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    splits.sort_values(["ticker", "date"]).to_parquet(path, index=False)


def read_splits(path: Path = SPLIT_FILE) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["ticker", "date", "numerator",
                                     "denominator", "ratio"])
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    return df


def split_factor(dates: pd.Series, ticker_splits: pd.DataFrame) -> pd.Series:
    """Cumulative split factor applying AFTER each date.

    Yahoo's close on date t has already been divided by this. So a NAV
    published on date t is put on today's share basis by dividing by the
    same number - and a fund with no splits gets 1.0, changing nothing.
    """
    f = pd.Series(1.0, index=dates.index)
    if ticker_splits is None or not len(ticker_splits):
        return f
    for r in ticker_splits.itertuples(index=False):
        if not r.ratio:
            continue
        f = f.where(dates >= r.date, f * float(r.ratio))
    return f


def nav_on_price_basis(nav: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Restate published NAV per share on the price series' share basis.

    Adds `split_factor` and `nav_pence_adj`, keeping `nav_pence` exactly as
    published: the announcement said what it said, and a restated figure
    must never overwrite the fund's own number.
    """
    out = nav.copy()
    out["split_factor"] = 1.0
    if splits is not None and len(splits):
        by_ticker = {tk: g for tk, g in splits.groupby("ticker", sort=False)}
        for tk, idx in out.groupby("ticker", sort=False).groups.items():
            g = by_ticker.get(tk)
            if g is None or not len(g):
                continue
            out.loc[idx, "split_factor"] = split_factor(
                out.loc[idx, "published_at"], g)
    out["nav_pence_adj"] = pd.to_numeric(out["nav_pence"], errors="coerce") \
        / out["split_factor"]
    return out
