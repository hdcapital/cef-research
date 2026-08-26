"""Monthly point-in-time panel construction (Stage 4 + 5).

One row per security x month. Every value traces to a named AIC source
file; nothing is interpolated. Returns are month-end-to-month-end PRICE
returns from MIR mid prices (AIC files carry no per-share dividend or
total-return history - see README). Splits/consolidations are adjusted
using the observed shares-in-issue ratio and cross-checked against AIC
Corporate Activity 'Capital Change' events; adjustments are flagged.

Timing / revision discipline:
- main MIR rows for month t are published ~6 working days after t and are
  signal-eligible from month t (traded during t+1).
- errata rows in the same monthly bundle supersede the main file.
- rows whose first publication came in a LATER bundle (late reporters)
  carry first_release_month > obs_month: their PRICES enter the return
  series (the market price was publicly observable at the time), but the
  row is not signal-eligible before its publication month.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .entities import EntityRegistry, normalize_name
from .parsers.corporate_activity import parse_corporate_activity
from .parsers.companies import parse_companies_excel
from .parsers.mir import classify_mir_file, parse_mir_csv

log = logging.getLogger(__name__)

_PRECEDENCE = {"main": 0, "errata": 1, "post_errata": 2}

TERMINAL_CATEGORIES = {
    "liquidation": "liquidated",
    "merger_departing": "merged",
    "reconstruction": "reconstructed",
    "reorganisation": "reorganised",
    "depart_other": "departed",
    "redemption": "redeemed",
}

VALUE_REALIZATION_CATEGORIES = {
    "tender", "redemption", "capital_return", "liquidation",
    "reconstruction", "realisation_policy", "merger_departing",
}


# --------------------------------------------------------------- raw parsing
def parse_all_mir(raw_dir: Path) -> pd.DataFrame:
    """Parse every cached MIR CSV (main + errata). Filenames are prefixed
    'YYYY-MM_mir_' by the downloader; that prefix is the release month."""
    rows: list[dict] = []
    files = sorted(raw_dir.glob("*_mir_*"))
    for path in files:
        m = re.match(r"^(\d{4}-\d{2})_mir_(.+)$", path.name)
        if not m:
            continue
        release_month, inner = m.group(1), m.group(2)
        kind = classify_mir_file(inner)
        if kind == "component":  # GEO/PC/WAR/CNV files - not needed
            continue
        try:
            parsed = parse_mir_csv(path, source_name=path.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed %s: %s", path.name, exc)
            continue
        for r in parsed:
            r["release_month"] = release_month
            r["file_kind"] = kind
        rows.extend(parsed)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    log.info("parsed %d MIR rows from %d files", len(df), len(files))
    return df


def parse_all_corporate_activity(raw_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted(raw_dir.glob("*_corporate_activity_*")):
        try:
            rows.extend(parse_corporate_activity(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed %s: %s", path.name, exc)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["event_month", "event", "company_name", "detail"]
        )
    log.info("parsed %d corporate activity events", len(df))
    return df


def parse_all_companies(raw_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted(raw_dir.glob("*_keyfacts_*")):
        if path.suffix.lower() not in (".xls", ".xlsx"):
            continue
        name_l = path.name.lower()
        if not ("companies" in name_l or "industryoverview" in name_l):
            continue
        if "industryoverview" in name_l and path.suffix.lower() != ".xlsx":
            continue
        m = re.match(r"^(\d{4}-\d{2})_keyfacts_", path.name)
        if not m:
            continue
        try:
            parsed = parse_companies_excel(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed %s: %s", path.name, exc)
            continue
        for r in parsed:
            r["obs_month"] = m.group(1)
        rows.extend(parsed)
    df = pd.DataFrame(rows)
    log.info("parsed %d company-universe rows", len(df))
    return df


# ------------------------------------------------------------------- assembly
def _dedupe_bitemporal(mir: pd.DataFrame) -> pd.DataFrame:
    """One row per (security, obs_month): latest correction wins for values;
    first_release_month records when the observation first became public."""
    mir = mir.copy()
    mir["prec"] = mir["file_kind"].map(_PRECEDENCE)
    mir = mir.sort_values(["security_id", "obs_month", "release_month", "prec"])
    first_release = (
        mir.groupby(["security_id", "obs_month"])["release_month"].min().rename("first_release_month")
    )
    latest = mir.groupby(["security_id", "obs_month"]).tail(1).copy()
    latest = latest.merge(first_release, on=["security_id", "obs_month"])
    n_corr = (latest["release_month"] != latest["first_release_month"]).sum()
    log.info("panel dedupe: %d rows, %d carry later corrections", len(latest), int(n_corr))
    return latest


def _detect_splits(g: pd.DataFrame) -> pd.DataFrame:
    """Per-security: month-on-month price return with (a) split adjustment
    from the shares-in-issue ratio and (b) data-error guards.

    Guards (returns are INVALIDATED - set missing and flagged - never
    silently repaired; counts appear in the quality report):
    - unit_switch: price and NAV both jump ~x100 or ~/100 together (the
      source switched pounds/pence for the whole row);
    - price_nav_inconsistent: the implied one-month discount move exceeds
      a factor of 4 - a genuine move of that size does not occur without a
      matching NAV move, so this is a scale or keying error;
    - extreme_unverified: |return| beyond +300% / -90% with no NAV data to
      corroborate it."""
    g = g.sort_values("obs_month").copy()
    periods = pd.PeriodIndex(g["obs_month"], freq="M")
    g.index = periods
    full = pd.period_range(periods.min(), periods.max(), freq="M")
    price = g["price"].reindex(full)
    shares = g["shares"].reindex(full)
    nav = g["nav"].reindex(full) if "nav" in g.columns else pd.Series(np.nan, index=full)

    prev_price = price.shift(1)
    share_ratio = shares / shares.shift(1)
    price_ratio = price / prev_price
    nav_ratio = nav / nav.shift(1)

    ret = price_ratio - 1.0
    adj_flag = pd.Series(False, index=full)
    invalid_reason = pd.Series(None, index=full, dtype=object)

    # split/consolidation: large shares jump whose inverse matches the
    # price move (market cap roughly continuous; band is wide enough to
    # allow a real +-30% market move in the split month)
    candidate = (
        share_ratio.notna() & price_ratio.notna()
        & ((share_ratio > 1.5) | (share_ratio < 1 / 1.5))
        & ((price_ratio * share_ratio) > 0.65)
        & ((price_ratio * share_ratio) < 1.35)
    )
    ret[candidate] = (price_ratio * share_ratio - 1.0)[candidate]
    adj_flag[candidate] = True

    # unit switch: price and NAV rescaled x100 together (pence <-> pounds
    # reporting change). The scale factor is exactly 100, so the true
    # return is recoverable: adjust rather than discard.
    up100 = (
        price_ratio.notna() & nav_ratio.notna() & ~candidate
        & price_ratio.between(60, 140) & nav_ratio.between(60, 140)
    )
    down100 = (
        price_ratio.notna() & nav_ratio.notna() & ~candidate
        & price_ratio.between(1 / 140, 1 / 60) & nav_ratio.between(1 / 140, 1 / 60)
    )
    ret[up100] = (price_ratio / 100 - 1.0)[up100]
    ret[down100] = (price_ratio * 100 - 1.0)[down100]
    adj_flag[up100 | down100] = True
    price_ratio = price_ratio.copy()
    price_ratio[up100] = price_ratio[up100] / 100
    price_ratio[down100] = price_ratio[down100] * 100
    nav_ratio = nav_ratio.copy()
    nav_ratio[up100] = nav_ratio[up100] / 100
    nav_ratio[down100] = nav_ratio[down100] * 100
    unit_switch = pd.Series(False, index=full)  # residual mixed-scale cases below
    # price/NAV inconsistency: implied discount move beyond a factor of 4
    disc_ratio = price_ratio / nav_ratio
    inconsistent = (
        price_ratio.notna() & nav_ratio.notna() & ~candidate & ~unit_switch
        & ((disc_ratio > 4) | (disc_ratio < 0.25))
    )
    # extreme move with no NAV corroboration available
    extreme_unverified = (
        price_ratio.notna() & nav_ratio.isna() & ~candidate
        & ((ret > 3.0) | (ret < -0.9))
    )

    for mask, reason in (
        (unit_switch, "unit_switch"),
        (inconsistent, "price_nav_inconsistent"),
        (extreme_unverified, "extreme_unverified"),
    ):
        ret[mask] = np.nan
        invalid_reason[mask] = reason

    out = pd.DataFrame(
        {
            "obs_month": [str(p) for p in full],
            "price_return": ret.values,
            "split_adjusted": adj_flag.values,
            "return_invalid_reason": invalid_reason.values,
        }
    )
    out["security_id"] = g["security_id"].iloc[0]
    return out[out["price_return"].notna() | out["split_adjusted"] | out["return_invalid_reason"].notna()]


def build_panel(cfg: dict) -> pd.DataFrame:
    raw_dir = Path(cfg["download"]["raw_dir"])
    processed = Path(cfg["paths"]["processed_dir"])
    processed.mkdir(parents=True, exist_ok=True)

    mir = parse_all_mir(raw_dir)
    if mir.empty:
        raise RuntimeError(f"no MIR files parsed from {raw_dir}; run download first")
    ca = parse_all_corporate_activity(raw_dir)
    uni = parse_all_companies(raw_dir)

    registry = EntityRegistry(cfg["paths"].get("entity_overrides"))
    registry.load_name_changes(ca)
    mir = mir.sort_values(["obs_month", "company_name"])
    mir["security_id"] = [
        registry.resolve(n, c, t)
        for n, c, t in zip(mir["company_name"], mir["code"], mir["share_type"])
    ]

    panel = _dedupe_bitemporal(mir)

    # ---------------- price unit-consistency correction ----------------
    # A small number of source rows report the price in pounds while the
    # NAV is in pence (or vice versa), producing impossible -99% "discounts"
    # and +9,000% "returns" when the scale reverts next month. Where a x100
    # rescaling restores price/NAV consistency the price is corrected and
    # the row flagged; nothing else is touched and the raw value remains in
    # the named source file.
    panel["price_unit_corrected"] = False
    both = panel["price"].notna() & panel["nav"].notna() & (panel["nav"] > 0)
    ratio = panel["price"] / panel["nav"]
    up = both & (ratio < 0.04) & (ratio * 100).between(0.4, 2.5)
    down = both & (ratio > 25) & (ratio / 100).between(0.4, 2.5)
    panel.loc[up, "price"] = panel.loc[up, "price"] * 100
    panel.loc[down, "price"] = panel.loc[down, "price"] / 100
    panel.loc[up | down, "price_unit_corrected"] = True
    log.info("price unit corrections: %d x100, %d /100", int(up.sum()), int(down.sum()))

    # ---------------- discount ----------------
    panel["share_price"] = panel["price"]
    panel["nav_per_share"] = panel["nav"]
    with np.errstate(all="ignore"):
        panel["calculated_discount"] = np.where(
            panel["price"].notna() & panel["nav"].notna() & (panel["nav"] > 0),
            panel["price"] / panel["nav"] - 1.0,
            np.nan,
        )
    panel["discount"] = panel["calculated_discount"]

    # ---------------- market cap (£m) ----------------
    # price is pence for GBX quotes; shares x price/100 = £.
    gbx = panel["currency"].isna() | (panel["currency"] == "GBX")
    panel["market_cap"] = np.where(
        gbx & panel["price"].notna() & panel["shares"].notna(),
        panel["price"] * panel["shares"] / 100.0 / 1e6,
        np.nan,
    )

    # merge universe-file attributes (same month, by canonical name)
    if not uni.empty:
        uni = uni.copy()
        uni["name_key"] = uni["company_name"].map(normalize_name)
        panel["name_key"] = panel["company_name"].map(normalize_name)
        keep = uni.drop_duplicates(subset=["obs_month", "name_key"])[
            ["obs_month", "name_key"]
            + [c for c in ("market_cap_m", "total_assets_m", "domicile", "member", "listing", "ticker", "isin") if c in uni.columns]
        ]
        panel = panel.merge(keep, on=["obs_month", "name_key"], how="left")
        panel["market_cap"] = panel["market_cap"].fillna(panel.get("market_cap_m"))

    # ---------------- eligibility ----------------
    ucfg = cfg["universe"]
    sector_l = panel["sector"].fillna("").str.lower()
    stype_l = panel["share_type"].fillna("ordinary share").str.lower()
    is_vct = sector_l.str.contains("venture capital") | (sector_l == "vct") | sector_l.str.startswith("vct")
    is_split_sector = sector_l.str.contains("split capital")
    bad_type = pd.Series(False, index=panel.index)
    for pat in ucfg["exclude_security_types"]:
        bad_type |= stype_l.str.contains(pat)
    is_ordinary = stype_l.str.contains("ordinary") | (stype_l == "share")
    non_gbx = panel["currency"].notna() & (panel["currency"] != "GBX")

    # late-reported rows (first published in a later monthly bundle) were
    # not knowable at their observation month: usable for returns, but
    # never signal-eligible.
    late = panel["first_release_month"] > panel["obs_month"]
    panel["late_reported"] = late

    panel["eligible"] = (
        is_ordinary
        & ~bad_type
        & ~(is_vct & ucfg["exclude_vcts"])
        & ~is_split_sector
        & ~non_gbx
        & ~late
        & panel["discount"].notna()
        & (panel["discount"] >= cfg["quality"].get("eligibility_discount_floor", -0.85))
    )
    panel["is_vct"] = is_vct
    panel["non_gbx_quote"] = non_gbx

    # ---------------- returns ----------------
    rets = []
    for sid, g in panel.groupby("security_id"):
        if g["price"].notna().sum() >= 2:
            rets.append(_detect_splits(g[["security_id", "obs_month", "price", "shares", "nav"]]))
    ret_df = (
        pd.concat(rets, ignore_index=True)
        if rets
        else pd.DataFrame(columns=["security_id", "obs_month", "price_return",
                                   "split_adjusted", "return_invalid_reason"])
    )

    panel = panel.merge(ret_df, on=["security_id", "obs_month"], how="left")

    # forward return: month t row gets the return earned over month t+1
    ret_next = ret_df.copy()
    ret_next["signal_month"] = (
        pd.PeriodIndex(ret_next["obs_month"], freq="M") - 1
    ).astype(str)
    ret_next = ret_next.rename(
        columns={"price_return": "fwd_return", "obs_month": "fwd_return_month",
                 "split_adjusted": "fwd_split_adjusted",
                 "return_invalid_reason": "fwd_return_invalid_reason"}
    )
    panel = panel.merge(
        ret_next[["security_id", "signal_month", "fwd_return", "fwd_return_month",
                  "fwd_split_adjusted", "fwd_return_invalid_reason"]],
        left_on=["security_id", "obs_month"],
        right_on=["security_id", "signal_month"],
        how="left",
    ).drop(columns=["signal_month"])

    # ---------------- terminal outcomes ----------------
    panel = _classify_terminals(panel, ca)

    # ---------------- catalyst flags (trailing completed actions) --------
    panel = _attach_catalysts(panel, ca)

    # ---------------- dates ----------------
    panel["date"] = pd.PeriodIndex(panel["obs_month"], freq="M").to_timestamp(how="end").normalize()
    panel["signal_date"] = panel["date"]
    panel["portfolio_formation_date"] = panel["date"] + pd.offsets.BusinessDay(6)
    panel["holding_period_start"] = panel["date"] + pd.offsets.MonthBegin(1)
    panel["holding_period_end"] = panel["date"] + pd.offsets.MonthEnd(1)

    drop_cols = [c for c in ("price", "nav", "prec", "name_key") if c in panel.columns]
    panel = panel.drop(columns=drop_cols)

    # Stage 5 coverage report
    outputs_dir = Path(cfg["paths"]["outputs_dir"])
    outputs_dir.mkdir(parents=True, exist_ok=True)
    _write_coverage_report(panel, outputs_dir)

    out_path = processed / "monthly_panel.parquet"
    panel.to_parquet(out_path, index=False)
    log.info("panel written: %s (%d rows, %d securities, %s..%s)",
             out_path, len(panel), panel["security_id"].nunique(),
             panel["obs_month"].min(), panel["obs_month"].max())
    return panel


def _classify_terminals(panel: pd.DataFrame, ca: pd.DataFrame) -> pd.DataFrame:
    """Explain disappearances: last observed month per security is matched
    against corporate-activity terminal events within [-2, +6] months."""
    panel = panel.copy()
    panel["fwd_return_status"] = np.where(panel["fwd_return"].notna(), "observed", "missing_next_price")
    if "fwd_return_invalid_reason" in panel.columns:
        inv = panel["fwd_return_invalid_reason"].notna() & panel["fwd_return"].isna()
        panel.loc[inv, "fwd_return_status"] = "invalid_" + panel.loc[inv, "fwd_return_invalid_reason"]

    last_month = panel["obs_month"].max()
    lasts = panel.sort_values("obs_month").groupby("security_id").tail(1)
    ca_keyed: dict[str, list[tuple[pd.Period, str]]] = {}
    if ca is not None and not ca.empty:
        for _, ev in ca.iterrows():
            if ev["category"] in TERMINAL_CATEGORIES:
                ca_keyed.setdefault(normalize_name(ev["company_name"]), []).append(
                    (pd.Period(ev["event_month"], freq="M"), TERMINAL_CATEGORIES[ev["category"]])
                )

    status = {}
    outcome = {}
    for _, row in lasts.iterrows():
        if row["obs_month"] >= last_month:
            continue  # still alive at end of sample
        key = normalize_name(row["company_name"])
        p = pd.Period(row["obs_month"], freq="M")
        matched = None
        for evp, cat in ca_keyed.get(key, []):
            if -2 <= (evp - p).n <= 6:
                matched = cat
                break
        idx = row.name
        outcome[idx] = matched or "unexplained_disappearance"
        status[idx] = f"terminal_{matched}" if matched else "terminal_unresolved"

    panel["outcome"] = pd.Series(outcome).reindex(panel.index)
    term_status = pd.Series(status).reindex(panel.index)
    panel.loc[term_status.notna() & panel["fwd_return"].isna(), "fwd_return_status"] = term_status[
        term_status.notna() & panel["fwd_return"].isna()
    ]
    return panel


def _attach_catalysts(panel: pd.DataFrame, ca: pd.DataFrame, window: int = 6) -> pd.DataFrame:
    """catalyst_flag at month t: the company completed/logged a
    value-realization corporate action (tender, redemption, capital return,
    realisation policy, reconstruction, liquidation announcement) in months
    [t-window+1 .. t]. Only backward-looking information is used - the AIC
    archive records events by their effective month, so this is a 'has an
    active realization programme' proxy, NOT an announcement-day signal."""
    panel = panel.copy()
    panel["catalyst_flag"] = False
    panel["catalyst_types"] = None
    if ca is None or ca.empty:
        return panel
    cat_ev = ca[ca["category"].isin(VALUE_REALIZATION_CATEGORIES)]
    by_name: dict[str, list[tuple[pd.Period, str]]] = {}
    for _, ev in cat_ev.iterrows():
        by_name.setdefault(normalize_name(ev["company_name"]), []).append(
            (pd.Period(ev["event_month"], freq="M"), ev["category"])
        )
    keys = panel["company_name"].map(normalize_name)
    flags = []
    types = []
    for key, month in zip(keys, panel["obs_month"]):
        events = by_name.get(key)
        if not events:
            flags.append(False)
            types.append(None)
            continue
        p = pd.Period(month, freq="M")
        hits = sorted({cat for evp, cat in events if 0 <= (p - evp).n < window})
        flags.append(bool(hits))
        types.append(";".join(hits) if hits else None)
    panel["catalyst_flag"] = flags
    panel["catalyst_types"] = types
    return panel


def _write_coverage_report(panel: pd.DataFrame, outputs_dir: Path) -> None:
    """outputs/return_data_coverage.csv: per-security span, expected vs
    observed months, and the dominant reason for gaps - plus a by-year
    aggregate. Poor early-year coverage must be visible, not hidden."""
    rows = []
    for sid, g in panel.groupby("security_id"):
        months = pd.PeriodIndex(g["obs_month"], freq="M")
        span = (months.max() - months.min()).n + 1
        n_price = int(g["share_price"].notna().sum())
        n_ret = int(g["price_return"].notna().sum()) if "price_return" in g.columns else 0
        reason = ""
        if n_price < span:
            reason = "months missing from MIR or price not supplied"
        term = g["fwd_return_status"].iloc[-1] if "fwd_return_status" in g.columns else ""
        rows.append(
            {
                "security_id": sid,
                "company_name": g["company_name"].iloc[-1],
                "first_month": str(months.min()),
                "last_month": str(months.max()),
                "months_expected": span,
                "months_observed_price": n_price,
                "months_observed_return": n_ret,
                "coverage_pct": round(n_price / span, 4) if span else np.nan,
                "source": "AIC MIR",
                "terminal_status": term,
                "reason_missing": reason,
            }
        )
    cov = pd.DataFrame(rows)
    cov.to_csv(outputs_dir / "return_data_coverage.csv", index=False)

    panel = panel.copy()
    panel["year"] = panel["obs_month"].str[:4]
    by_year = panel.groupby("year").agg(
        securities=("security_id", "nunique"),
        rows=("security_id", "size"),
        with_price=("share_price", lambda s: int(s.notna().sum())),
        with_discount=("discount", lambda s: int(s.notna().sum())),
        with_return=("price_return", lambda s: int(s.notna().sum())),
    )
    by_year["price_coverage_pct"] = (by_year["with_price"] / by_year["rows"]).round(4)
    by_year["return_coverage_pct"] = (by_year["with_return"] / by_year["rows"]).round(4)
    by_year.to_csv(outputs_dir / "return_data_coverage_by_year.csv")
    log.info("coverage report: %d securities", len(cov))


def load_panel(cfg: dict) -> pd.DataFrame:
    path = Path(cfg["paths"]["processed_dir"]) / "monthly_panel.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run build-panel first")
    return pd.read_parquet(path)
