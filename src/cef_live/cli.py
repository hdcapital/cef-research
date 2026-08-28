"""CEF-LIVE CLI.

  python -m cef_live.cli nightly [--markets au,uk]

Builds the live NTA table for each market from the research panels,
harvests Tier 0 NAV announcements (AU via the open index; UK census from
the crawler cache), writes data/nta_live/latest.parquet + a readable CSV,
snapshots to S3 (append-only, never overwritten), and emits the Phase 1
acceptance report to reports/build/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from . import harvest_nav, nta_live, prices


def _params() -> dict:
    raw = Path("config/params.yaml").read_text()
    raw = re.sub(r"\$\{[^}]+\}", "", raw)   # env placeholders not needed here
    return yaml.safe_load(raw)


def _snapshot_s3(path: Path, key: str) -> str:
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return "s3_skipped_no_bucket"
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    # append-only: refuse to overwrite an existing snapshot
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return f"s3_exists_not_overwritten:{key}"
    except Exception:  # noqa: BLE001
        pass
    s3.upload_file(str(path), bucket, key)
    return f"s3_uploaded:{key}"


def nightly(markets: list[str]) -> int:
    params = _params()
    today = datetime.now(timezone.utc).date()
    tables = []
    notes = {"date": today.isoformat(), "markets": {}}
    ysess = prices.session()

    if "au" in markets:
        panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
        codes = set(panel["security_id"].str.replace("ASX:", "", regex=False))
        tier0 = harvest_nav.harvest_au(codes)
        mf = prices.monthly_factor_returns(ysess, "AU")
        df = prices.daily_factor_returns(ysess, "AU")
        syms = {f"ASX:{c}": f"{c}.AX" for c in sorted(codes)}
        live_px = prices.latest_prices(ysess, syms)
        notes["markets"]["au"] = {"tier0_navs": int(len(tier0)),
                                  "factors_monthly": mf is not None,
                                  "factors_daily": df is not None,
                                  "live_prices": int(len(live_px))}
        t = nta_live.build_table(
            panel, "AU", ret_col="nta_total_return", nav_col="nta_derived",
            price_col="share_price", params=params, tier0=tier0,
            market_factors=mf, daily_factors=df, live_prices=live_px)
        tables.append(t)
        if len(tier0):
            Path("data/nta_live").mkdir(parents=True, exist_ok=True)
            tier0.to_csv("data/nta_live/au_tier0_latest.csv", index=False)

    if "uk" in markets:
        p = Path("data/processed/monthly_panel.parquet")
        if p.exists():
            panel = pd.read_parquet(p)
            nav_col = next(c for c in ("nav", "nav_per_share") if c in panel.columns)
            price_col = next(c for c in ("price", "share_price") if c in panel.columns)
            mf = prices.monthly_factor_returns(ysess, "UK")
            df = prices.daily_factor_returns(ysess, "UK")
            live_px = pd.DataFrame()
            try:
                import yaml as _yaml  # uk config for the SEDOL->TIDM map
                from uk_cef.data_sources.investegate import build_ticker_map
                ucfg = _yaml.safe_load(Path("config/default.yaml").read_text())
                tmap = build_ticker_map(ucfg)
                tmap = tmap[tmap["ticker"].notna()]
                # live universe only: funds present in the last panel month
                last_m = panel["obs_month"].max()
                alive = set(panel.loc[panel["obs_month"] == last_m, "security_id"])
                syms = {r.security_id: f"{r.ticker}.L"
                        for r in tmap.itertuples(index=False)
                        if r.security_id in alive}
                live_px = prices.latest_prices(ysess, syms)
            except Exception as exc:  # noqa: BLE001
                notes["markets"].setdefault("uk", {})
                notes["markets"]["uk_ticker_map_error"] = str(exc)
            t = nta_live.build_table(
                panel, "UK", ret_col="nav_total_return", nav_col=nav_col,
                price_col=price_col, params=params, tier0=None,
                market_factors=mf, daily_factors=df, live_prices=live_px)
            tables.append(t)
            cache = Path("data/investegate_cache")
            if cache.exists():
                census = harvest_nav.uk_frequency_census(cache)
                census.to_csv("data/nta_live/uk_nav_frequency_census.csv", index=False)
                Path("reports/build").mkdir(parents=True, exist_ok=True)
                Path("reports/build/uk_nav_samples.json").write_text(
                    json.dumps(harvest_nav.uk_nav_samples(cache), indent=1))
                notes["markets"]["uk"] = {
                    "nav_publishers_found": int(len(census)),
                    "daily_weekly": int((census["nav_frequency"].isin(["daily", "weekly"])).sum())
                    if len(census) else 0}
        else:
            notes["markets"]["uk"] = "panel_missing_skipped"

    if not tables:
        print("no market tables built"); return 1
    out = pd.concat(tables, ignore_index=True)
    Path("data/nta_live").mkdir(parents=True, exist_ok=True)
    out.to_parquet("data/nta_live/latest.parquet", index=False)
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/live/nta_live_latest.csv", index=False)
    notes["snapshot"] = _snapshot_s3(Path("data/nta_live/latest.parquet"),
                                     f"nta_live/{today.isoformat()}.parquet")

    # ---- Phase 1 acceptance metrics ----
    basis_counts = out["basis"].value_counts().to_dict()
    covered = float((out["basis"] <= 3).mean())
    sig_ok = out["sigma_1m"].notna().mean()
    accept = {
        "rows": int(len(out)),
        "basis_counts": {str(k): int(v) for k, v in sorted(basis_counts.items())},
        "share_basis_le3_labelled": round(covered, 4),
        "share_with_sigma": round(float(sig_ok), 4),
        "alert_eligible": int(out["alert_eligible"].sum()),
        "z_extremes": out.loc[out["z_adj"].notna()]
                         .nsmallest(10, "z_adj")[["security_id", "z_adj",
                                                  "discount_est", "staleness_days"]]
                         .to_dict("records"),
        **notes,
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/phase1_nightly.json").write_text(
        json.dumps(accept, indent=2, default=str))

    # daily heartbeat - silence must be distinguishable from failure
    from .notify import notify
    top = out[out["alert_eligible"] & out["z_adj"].notna()].nsmallest(5, "z_adj")
    lines = [f"  {r.security_id:>16}  z={r.z_adj:+.2f}  disc={r.discount_est:+.1%}"
             f"  basis={r.basis} stale={r.staleness_days}d"
             for r in top.itertuples(index=False)]
    notify(f"nightly OK - {len(out)} funds, {int(out['alert_eligible'].sum())} eligible",
           "Nightly live NTA table built.\n"
           f"Basis counts: {basis_counts}\nSnapshot: {notes['snapshot']}\n"
           f"Deepest eligible dislocations:\n" + "\n".join(lines),
           priority="heartbeat")
    print(json.dumps({k: accept[k] for k in
                      ("rows", "basis_counts", "share_with_sigma",
                       "alert_eligible", "snapshot")}, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nightly")
    n.add_argument("--markets", default="au,uk")
    args = ap.parse_args()
    if args.cmd == "nightly":
        return nightly([m.strip() for m in args.markets.split(",") if m.strip()])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
