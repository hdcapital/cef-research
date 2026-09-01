"""Daily discount to NAV/NTA for UK-listed funds.

    discount_t = close_t / NAV_last_published_before_t - 1

Three decisions carry the whole construction, and each is the kind that is
easy to get wrong in a way nothing downstream can detect.

**Join on the publication date, never the valuation date.** A 30 June NAV
announced on 15 July was not knowable on 1 July. Joining on the as-at date
would hand every observation in the panel a hindsight window as wide as the
fund's own reporting lag - for the quarterly infrastructure names, a
fortnight or more, on every single row. So the as-of join is backward on
``published_at``, and the valuation date is carried alongside purely to
measure how old the NAV was.

**Raw close, never adjusted.** See ``uk_prices_history``: an adjusted close
embeds dividends paid after the date, so a discount built on it changes
whenever the fund next pays out.

**Staleness is relative to the fund's own cadence.** A daily publisher whose
NAV is a fortnight old has stopped publishing and its discount is not
evidence of anything. A quarterly publisher whose NAV is a fortnight old is
completely normal. One fixed threshold cannot express both: at 45 days it
blanks most of the property and infrastructure cohort's history, and at 200
it lets a suspended daily publisher look tradable. So the rule is per fund -
a multiple of that fund's own median publication gap, floored so a daily
name still gets a sane window - and the flag rides alongside the discount
rather than deleting it. The ``discount`` column is always the market's own
convention (price against the last published NAV, however old); the
``nav_stale`` flag and ``nav_age_days`` let any analysis take the stricter
reading, and ``discount_fresh`` is that stricter reading precomputed.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import uk_prices_history as PH

DISCOUNT_DIR = Path("data/uk/discount")

# a stale NAV is one older than this multiple of the fund's own median gap,
# never less than the floor (a daily publisher gets a fortnight of grace for
# holidays and short suspensions, not a quarter).
STALE_MULTIPLE = 3.0
STALE_FLOOR_DAYS = 14
STALE_CEILING_DAYS = 400

# |price/NAV - 1| beyond this is more likely a unit or identity error than a
# market price. Real dislocations reach -70%; -99% is a decimal point. The
# row is kept and flagged, never dropped and never trusted.
IMPLAUSIBLE_ABS = 0.90


def staleness_limit(median_gap_days: float | None) -> float:
    if median_gap_days is None or pd.isna(median_gap_days):
        return float(STALE_FLOOR_DAYS)
    return float(min(STALE_CEILING_DAYS,
                     max(STALE_FLOOR_DAYS, STALE_MULTIPLE * float(median_gap_days))))


def build(nav: pd.DataFrame, px: pd.DataFrame,
          frequency: pd.DataFrame | None = None,
          units: pd.DataFrame | None = None,
          unreliable: set[str] | None = None,
          start: str = "2007-01-01") -> pd.DataFrame:
    """The daily panel: one row per fund per trading day it traded.

    A day with a price but no NAV published yet keeps the row, with the
    discount absent - the fund traded, and saying so is not the same as
    claiming a discount for it.
    """
    if nav is None or not len(nav) or px is None or not len(px):
        return pd.DataFrame()

    n = nav.copy()
    n["published_at"] = pd.to_datetime(n["published_at"])
    n["nav_date"] = pd.to_datetime(n["nav_date"])
    # The discount is computed on the SPLIT-ADJUSTED NAV, because the price
    # series is split-adjusted and the published NAV is not. `nav_pence`
    # stays in the panel exactly as the fund announced it - a restated figure
    # must never overwrite the fund's own number - and `split_factor` says
    # what was applied.
    if "nav_pence_adj" not in n.columns:
        n["nav_pence_adj"] = n["nav_pence"]
        n["split_factor"] = 1.0
    n["nav_pence_adj"] = n["nav_pence_adj"].fillna(n["nav_pence"])
    n = n.dropna(subset=["nav_pence_adj", "published_at"]).sort_values("published_at")

    p = px.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p[p["date"] >= pd.Timestamp(start)].sort_values("date")
    # Units come from the reconciliation against each fund's own NAV, not
    # from the quote metadata: Yahoo has been observed labelling a pounds
    # series "GBp", and trusting that label turns a healthy trust into a
    # -99% discount (see uk_prices_history.reconcile_units).
    if units is None:
        units = PH.reconcile_units(n, p)
    scale = p["ticker"].map(dict(zip(units["ticker"], units["price_scale"])))
    p["price_scale"] = pd.to_numeric(scale, errors="coerce")
    p["price_unit_status"] = p["ticker"].map(
        dict(zip(units["ticker"], units["price_unit_status"])))
    p["price_pence"] = pd.to_numeric(p["close_raw"], errors="coerce") * p["price_scale"]

    limits = {}
    if frequency is not None and len(frequency):
        limits = {r.ticker: staleness_limit(r.median_gap_days)
                  for r in frequency.itertuples(index=False)}

    # Index the NAV side ONCE. Re-filtering `n["ticker"] == ticker` inside the
    # loop rescans all ~375k NAV rows for every one of ~290 funds - a hundred
    # million row comparisons to answer a question a single groupby answers
    # once, on a job that is meant to run every evening.
    nav_by_ticker = {tk: g for tk, g in n.groupby("ticker", sort=False)}

    out = []
    for ticker, pg in p.groupby("ticker"):
        ng = nav_by_ticker.get(ticker, n.iloc[0:0])
        left = pg[["date", "ticker", "close_raw", "price_pence", "price_ccy",
                   "price_scale", "price_unit_status",
                   "price_source"]].sort_values("date")
        if ng.empty:
            # a fund that trades but has published no NAV we can read still
            # gets its rows: the panel says it traded and says why there is
            # no discount, rather than dropping the fund and looking like it
            # never existed. Typed rather than pd.NA-filled, so concatenating
            # it with the funds that DO have NAV cannot change their dtypes.
            left = left.assign(
                nav_pence=pd.Series(dtype="float64"),
                nav_pence_adj=pd.Series(dtype="float64"),
                split_factor=pd.Series(dtype="float64"),
                nav_date=pd.Series(dtype="datetime64[ns]"),
                published_at=pd.Series(dtype="datetime64[ns]"),
                cum_assumed=pd.Series(dtype="object"),
                nav_source=pd.Series(dtype="object"),
                ann_id=pd.Series(dtype="object"))
        else:
            right = ng[["published_at", "nav_pence", "nav_pence_adj",
                        "split_factor", "nav_date", "cum_assumed",
                        "nav_source", "ann_id"]].copy()
            # merge_asof CONSUMES the join key, overwriting it with the left
            # frame's date. Keeping a copy is the only way the panel can
            # still say when each NAV was published - without it every row
            # would claim its NAV was published on the trading date.
            right["published_at_kept"] = right["published_at"]
            right = right.rename(columns={"published_at": "date"}).sort_values("date")
            left = pd.merge_asof(left, right, on="date", direction="backward")
            left = left.rename(columns={"published_at_kept": "published_at"})
        out.append(left)

    d = pd.concat(out, ignore_index=True)
    d["nav_date"] = pd.to_datetime(d.get("nav_date"), errors="coerce")
    d["nav_age_days"] = (d["date"] - d["nav_date"]).dt.days
    d["nav_pence"] = pd.to_numeric(d["nav_pence"], errors="coerce")
    d["nav_pence_adj"] = pd.to_numeric(d.get("nav_pence_adj"), errors="coerce")

    d["discount"] = (pd.to_numeric(d["price_pence"], errors="coerce")
                     / d["nav_pence_adj"] - 1.0)
    d.loc[d["nav_pence_adj"].isna() | (d["nav_pence_adj"] <= 0), "discount"] = pd.NA
    d.loc[d["price_pence"].isna(), "discount"] = pd.NA

    limit = d["ticker"].map(limits).fillna(float(STALE_FLOOR_DAYS))
    d["nav_stale_limit_days"] = limit
    d["nav_stale"] = d["nav_age_days"] > limit
    d["discount_fresh"] = d["discount"].where(~d["nav_stale"].fillna(True))

    d["quality"] = "ok"
    d.loc[d["price_pence"].isna() & d["close_raw"].notna(),
          "quality"] = "price_unit_unresolved"
    d.loc[d["nav_pence_adj"].isna(), "quality"] = "no_nav_published_yet"
    imp = d["discount"].abs() > IMPLAUSIBLE_ABS
    d.loc[imp.fillna(False), "quality"] = "implausible_discount"
    if unreliable:
        # A fund whose NAV series is measured unreliable keeps its rows and
        # its numbers - deleting them would hide the problem rather than
        # state it - but the discount is withdrawn, because dividing a real
        # price by a mis-parsed NAV produces a percentage that reads exactly
        # like a dislocation.
        bad = d["ticker"].isin(unreliable)
        d.loc[bad, "quality"] = "unreliable_nav_series"
        d.loc[bad, "discount"] = pd.NA
        d.loc[bad, "discount_fresh"] = pd.NA

    cols = ["ticker", "date", "close_raw", "price_pence", "price_ccy",
            "price_scale", "price_unit_status",
            "nav_pence", "nav_pence_adj", "split_factor",
            "nav_date", "published_at", "nav_age_days", "nav_stale",
            "nav_stale_limit_days", "discount", "discount_fresh",
            "cum_assumed", "nav_source", "ann_id", "price_source", "quality"]
    return d[[c for c in cols if c in d.columns]].sort_values(
        ["ticker", "date"]).reset_index(drop=True)


def with_zscores(panel: pd.DataFrame, window_days: int = 756,
                 min_obs: int = 252) -> pd.DataFrame:
    """Add each fund's discount z-score against its OWN trailing history.

    Trailing and inclusive of today only - a rolling window computed over
    the whole sample would give every early observation knowledge of a
    decade it had not lived through, which is the same look-ahead as joining
    on the valuation date, wearing a different hat.
    """
    if panel is None or not len(panel):
        return panel
    d = panel.sort_values(["ticker", "date"]).copy()
    g = d.groupby("ticker")["discount"]
    roll = g.rolling(window_days, min_periods=min_obs)
    d["disc_mu"] = roll.mean().reset_index(level=0, drop=True)
    d["disc_sigma"] = roll.std().reset_index(level=0, drop=True)
    d["disc_z"] = (d["discount"] - d["disc_mu"]) / d["disc_sigma"].replace(0, pd.NA)
    return d


def monthly_history(out_dir: Path = DISCOUNT_DIR) -> pd.DataFrame:
    """Month-end discount per fund from the daily panel, for the live z.

    The nightly table's z-score reads the aggregator's monthly panel, which
    never priced the announcements-only cohort (infrastructure, property,
    PE) - so those funds had a NAV, a price, a daily discount series, and
    no z. This is that series resampled to the same monthly spec the z was
    validated on: the STRICT reading only (`discount_fresh` - the NAV was
    fresh by the fund's own cadence - on rows whose quality is 'ok'), last
    observation per calendar month.

    Returns ticker, obs_month (YYYY-MM), discount. Empty when the panel is
    not on disk (a runner without the uk_daily state group).
    """
    frames = []
    for f in sorted(Path(out_dir).glob("*.parquet")):
        t = pd.read_parquet(f, columns=["ticker", "date", "discount_fresh",
                                        "quality"])
        t = t[t["quality"].eq("ok") & t["discount_fresh"].notna()]
        if len(t):
            frames.append(t)
    if not frames:
        return pd.DataFrame(columns=["ticker", "obs_month", "discount"])
    d = pd.concat(frames, ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d["obs_month"] = d["date"].dt.to_period("M").astype(str)
    d = (d.sort_values("date").groupby(["ticker", "obs_month"], as_index=False)
         .last())
    d["discount"] = d["discount_fresh"].astype(float)
    return d[["ticker", "obs_month", "discount"]]


# ------------------------------------------------------------- persistence
def write_panel(panel: pd.DataFrame, out_dir: Path = DISCOUNT_DIR) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for year, g in panel.groupby(panel["date"].dt.year):
        p = out_dir / f"{int(year)}.parquet"
        g.sort_values(["ticker", "date"]).to_parquet(p, index=False)
        written.append(p)
    return written


def read_panel(out_dir: Path = DISCOUNT_DIR) -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(Path(out_dir).glob("*.parquet"))]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)


def latest_snapshot(panel: pd.DataFrame,
                    universe: pd.DataFrame | None = None) -> pd.DataFrame:
    """Today's row per fund - the daily deliverable, small enough to commit.

    Every fund appears, including the ones with no usable number, and the
    `usable` column says which is which. That column exists because of what
    the first full run produced: sorting the snapshot by `discount` put Law
    Debenture at -86% and CQS New City High Yield at -43% at the top of the
    list, on NAVs 1,291 days old. Both numbers were computed exactly as
    specified - price against the last published NAV - and both are useless.
    A file whose most eye-catching rows are its least trustworthy ones is
    not a deliverable, however correct each cell is.
    """
    if panel is None or not len(panel):
        return pd.DataFrame()
    last = (panel.sort_values("date").groupby("ticker", as_index=False).tail(1)
                 .sort_values("ticker").reset_index(drop=True))
    if universe is not None and len(universe):
        keep = [c for c in ["ticker", "name", "sector", "security_id", "is_vct",
                            "currency"] if c in universe.columns]
        last = last.merge(universe[keep], on="ticker", how="left")
    last["usable"] = (last["discount_fresh"].notna()
                      & last["quality"].eq("ok"))
    return last.sort_values(["usable", "discount_fresh"],
                            ascending=[False, True]).reset_index(drop=True)


def coverage_report(panel: pd.DataFrame, universe: pd.DataFrame,
                    frequency: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per fund: what was asked for and what the panel actually holds.

    Every live fund appears, including the ones with nothing - a fund with
    no NAV history is a measured gap in this dataset, and it is reported as
    one rather than being absent from the file.
    """
    base = universe[[c for c in ["ticker", "name", "sector", "is_vct",
                                 "nav_route", "currency"]
                     if c in universe.columns]].drop_duplicates("ticker")
    if panel is None or not len(panel):
        return base.assign(price_days=0, discount_days=0)
    g = panel.groupby("ticker").agg(
        price_days=("date", "size"),
        price_first=("date", "min"),
        price_last=("date", "max"),
        nav_first=("nav_date", "min"),
        nav_last=("nav_date", "max"),
        discount_days=("discount", "count"),
        fresh_days=("discount_fresh", "count"),
        median_nav_age=("nav_age_days", "median"),
        median_discount=("discount", "median"),
        last_discount=("discount", "last"),
        price_ccy=("price_ccy", "last"),
        price_unit_status=("price_unit_status", "last"),
    ).reset_index()
    # Which store the fund's NAV history actually came from. This is the
    # column that tells an analyst whether a short history is the fund's own
    # (it listed in 2022) or ours (its announcements were never archived, so
    # it only has history from the night the live poller first read it).
    src = (panel.dropna(subset=["nav_pence"]).groupby("ticker")["nav_source"]
                .agg(lambda v: "+".join(sorted(set(v.dropna()))))
                .rename("nav_history_source").reset_index())
    g = g.merge(src, on="ticker", how="left")
    out = base.merge(g, on="ticker", how="left")
    if frequency is not None and len(frequency):
        out = out.merge(frequency[["ticker", "nav_frequency", "median_gap_days",
                                   "n_obs"]].rename(columns={"n_obs": "nav_obs"}),
                        on="ticker", how="left")
    for c in ("price_days", "discount_days", "fresh_days"):
        if c in out.columns:
            out[c] = out[c].fillna(0).astype(int)
    return out.sort_values("ticker").reset_index(drop=True)
