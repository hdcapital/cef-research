"""Build the market-wide discount history.

    python -m discount_history.cli build            # everything
    python -m discount_history.cli build --no-charts
    python -m discount_history.cli build --min-funds 5

Inputs are the two panels the existing pipelines produce; run those first:
    python -m uk_cef.cli build-panel
    python -m au_lic.cli build-panel
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from . import aggregate, charts, panel as panel_mod, workbook

log = logging.getLogger("discount_history")

OUT_DIR = Path("outputs/discount_history")
CHART_DIR = OUT_DIR / "charts"


def _coverage(panel: pd.DataFrame, elig: pd.DataFrame,
              excluded: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per market-year: rows held, rows with a discount, rows used.

    Published beside the results so a thin year is visible rather than
    implied by a jumpy line.
    """
    p = panel.copy()
    p["year"] = p["month"].str[:4]
    e = elig.copy()
    e["year"] = e["month"].str[:4]
    total = (p.groupby(["market", "year"])
              .agg(panel_rows=("security_id", "size"),
                   securities=("security_id", "nunique"),
                   with_discount=("discount", lambda s: int(s.notna().sum())))
              .reset_index())
    used = (e.groupby(["market", "year"])
             .agg(rows_used=("security_id", "size"),
                  securities_used=("security_id", "nunique"),
                  months_covered=("month", "nunique"))
             .reset_index())
    out = total.merge(used, on=["market", "year"], how="left").fillna({
        "rows_used": 0, "securities_used": 0, "months_covered": 0})
    for c in ("rows_used", "securities_used", "months_covered"):
        out[c] = out[c].astype(int)
    if excluded is not None and not excluded.empty:
        ex = excluded.copy()
        ex["year"] = ex["month"].str[:4]
        counts = (ex.groupby(["market", "year"]).size()
                    .rename("rows_outside_quality_bounds").reset_index())
        out = out.merge(counts, on=["market", "year"], how="left")
        out["rows_outside_quality_bounds"] = (
            out["rows_outside_quality_bounds"].fillna(0).astype(int))
    else:
        out["rows_outside_quality_bounds"] = 0
    out["pct_rows_used"] = (out["rows_used"] / out["panel_rows"] * 100).round(1)
    return out.sort_values(["market", "year"])


def _summary(market_wide: pd.DataFrame, by_market: pd.DataFrame,
             seg_summary: pd.DataFrame, elig: pd.DataFrame) -> dict:
    """Headline numbers, so a reader gets the result without opening a CSV."""
    out: dict = {
        "generated_rows": int(len(elig)),
        "securities": int(elig["security_id"].nunique()),
        "months": int(elig["month"].nunique()),
        "month_range": [elig["month"].min(), elig["month"].max()],
        "markets": {},
    }
    for market, sub in by_market[by_market["sufficient"]].groupby("market"):
        sub = sub.sort_values("month")
        widest = sub.loc[sub["mean_discount"].idxmin()]
        narrowest = sub.loc[sub["mean_discount"].idxmax()]
        latest = sub.iloc[-1]
        out["markets"][market] = {
            "first_month": sub["month"].min(),
            "last_month": sub["month"].max(),
            "months": int(sub["month"].nunique()),
            "avg_funds_per_month": round(float(sub["n_funds"].mean()), 1),
            "mean_discount_pct": round(float(sub["mean_discount"].mean() * 100), 2),
            "median_of_monthly_medians_pct": round(
                float(sub["median_discount"].median() * 100), 2),
            "latest_month": latest["month"],
            "latest_mean_discount_pct": round(float(latest["mean_discount"] * 100), 2),
            "latest_vs_history_pp": round(
                float((latest["mean_discount"] - sub["mean_discount"].mean()) * 100), 2),
            "widest_month": widest["month"],
            "widest_mean_discount_pct": round(float(widest["mean_discount"] * 100), 2),
            "narrowest_month": narrowest["month"],
            "narrowest_mean_discount_pct": round(float(narrowest["mean_discount"] * 100), 2),
        }
    if not seg_summary.empty:
        for market, sub in seg_summary.groupby("market"):
            ranked = sub.sort_values("avg_discount")
            out["markets"].setdefault(market, {})
            out["markets"][market]["widest_segments"] = [
                {"segment": r["segment"], "avg_discount_pct": round(r["avg_discount_pct"], 2),
                 "months": int(r["months"])}
                for _, r in ranked.head(5).iterrows()]
            out["markets"][market]["narrowest_segments"] = [
                {"segment": r["segment"], "avg_discount_pct": round(r["avg_discount_pct"], 2),
                 "months": int(r["months"])}
                for _, r in ranked.tail(5).iloc[::-1].iterrows()]
    if not market_wide.empty and "n_markets" in market_wide.columns:
        both = market_wide[market_wide["n_markets"] == 2]
        out["both_markets_months"] = int(len(both))
        if not both.empty:
            out["both_markets_range"] = [both["month"].min(), both["month"].max()]
    return out


def cmd_build(args) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    full = panel_mod.build()
    elig, excluded = panel_mod.eligible_only(full)
    if elig.empty:
        log.error("no eligible rows in either panel - nothing to aggregate")
        return 2
    log.info("aggregating %d eligible security-months (min_funds=%d)",
             len(elig), args.min_funds)

    mw = aggregate.market_wide(elig, min_funds=args.min_funds)
    bm = aggregate.by_market(elig, min_funds=args.min_funds)
    seg = aggregate.by_segment(elig, min_funds=args.min_funds)
    sec = aggregate.by_sector(elig, min_funds=args.min_funds)
    seg_sum = aggregate.segment_summary(seg)
    cov = _coverage(full, elig, excluded)

    tables = {
        "monthly_market_wide.csv": mw,
        "monthly_by_market.csv": bm,
        "monthly_by_segment.csv": seg,
        "monthly_by_sector.csv": sec,
        "segment_summary.csv": seg_sum,
        "coverage_by_market_year.csv": cov,
        "excluded_rows.csv": excluded.drop(columns=["month_end"], errors="ignore"),
        "security_month_panel.csv": elig.drop(columns=["month_end"], errors="ignore"),
    }
    for name, df in tables.items():
        target = OUT_DIR / name
        df.to_csv(target, index=False)
        log.info("%-32s %7d rows -> %s", name, len(df), target)

    summary = _summary(mw, bm, seg_sum, elig)
    summary["rows_outside_quality_bounds"] = int(len(excluded))
    if not excluded.empty:
        summary["quality_bounds"] = {
            m: list(b) for m, b in panel_mod.load_quality_bounds().items()}
        summary["exclusions_by_market"] = (
            excluded.groupby("market").size().to_dict())
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("summary -> %s", OUT_DIR / "summary.json")

    workbook.write(
        OUT_DIR / "cef_discount_history.xlsx",
        market_wide=mw, by_market=bm, by_segment=seg, by_sector=sec,
        segment_summary=seg_sum, panel=elig, excluded=excluded)

    if not args.no_charts:
        made = charts.render_all(by_market_df=bm, seg_df=seg,
                                 summary_df=seg_sum, out_dir=CHART_DIR)
        log.info("%d charts -> %s", len(made), CHART_DIR)

    for market, stats in summary.get("markets", {}).items():
        log.info("%s: %s..%s, avg %.2f%%, latest %.2f%% (%s)",
                 market, stats.get("first_month"), stats.get("last_month"),
                 stats.get("mean_discount_pct", float("nan")),
                 stats.get("latest_mean_discount_pct", float("nan")),
                 stats.get("latest_month"))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="discount_history")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build every discount-history output")
    b.add_argument("--min-funds", type=int, default=aggregate.MIN_FUNDS_DEFAULT,
                   help="suppress distribution stats for groups thinner than this")
    b.add_argument("--no-charts", action="store_true", help="skip PNG rendering")
    b.set_defaults(func=cmd_build)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
