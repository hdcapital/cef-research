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

from . import (catalysts, forward_irr, harvest_nav, nta_live, prices, tickers,
               universe, universe_report)


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


def build_universe() -> int:
    """Registry of every listed vehicle - priced or not, live or dead."""
    import yaml as _yaml
    cfg_uk = None
    p = Path("config/default.yaml")
    if p.exists():
        cfg_uk = _yaml.safe_load(p.read_text())
    reg = universe.build(cfg_uk)
    summary = {
        "vehicles": int(len(reg)),
        "by_market": reg.groupby("market").size().to_dict(),
        "live": int((reg["status"] == "live").sum()),
        "delisted": int((reg["status"] == "delisted").sum()),
        "priced_by_source": int(reg["source_prices_it"].sum()),
        "announcements_only": int((reg["nav_route"] == "announcements_only").sum()),
        "live_announcements_only": int(((reg["status"] == "live") &
                                        (reg["nav_route"] == "announcements_only")).sum()),
        "offshore": int(reg["offshore"].sum()),
        "vct": int(reg["is_vct"].sum()),
        "live_by_domicile": reg[reg["status"] == "live"]["domicile"]
                            .value_counts(dropna=False).head(8).to_dict(),
        # the cohort the rebuild exists for: listed, live, never priced by
        # the registry source - these must come from their own announcements
        "live_announcements_only_funds": reg[
            (reg["status"] == "live") & (reg["nav_route"] == "announcements_only")
        ][["security_id", "name", "sector", "domicile", "isin", "first_seen",
           "months_listed"]].sort_values("name").to_dict("records"),
        "live_announcements_only_by_sector": reg[
            (reg["status"] == "live") & (reg["nav_route"] == "announcements_only")
        ]["sector"].value_counts().to_dict(),
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/universe_registry.json").write_text(
        json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def resolve_tickers(budget: int = 400) -> int:
    """Resolve Investegate/Yahoo tickers for live registry funds with none."""
    reg = pd.read_parquet("data/universe/registry.parquet")
    cfg_uk = None
    p = Path("config/default.yaml")
    if p.exists():
        cfg_uk = yaml.safe_load(p.read_text())
    tickers.seed_known(reg, cfg_uk)      # free: already-verified identifiers
    out = tickers.resolve(reg, budget=budget)
    live_uk = reg[(reg["status"] == "live") & (reg["market"] == "UK")]
    got = out[out["status"] == "verified"]
    summary = {"attempted_total": int(len(out)),
               "verified": int(len(got)),
               "unresolved": int((out["status"] != "verified").sum()),
               "live_uk_funds": int(len(live_uk)),
               "coverage": round(len(got) / max(1, len(live_uk)), 4)}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/ticker_resolution.json").write_text(
        json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    return 0


def universe_sheet() -> int:
    """Build the universe spreadsheet and email it."""
    reg = pd.read_parquet("data/universe/registry.parquet")
    live = pd.read_parquet("data/nta_live/latest.parquet")
    hist = None
    hp = Path("data/processed/monthly_panel.parquet")
    if hp.exists():
        hist = pd.read_parquet(hp)
        if "discount" not in hist.columns and {"share_price", "nav_per_share"} <= set(hist.columns):
            hist["discount"] = hist["share_price"] / hist["nav_per_share"] - 1.0
    irr = None
    if hist is not None:
        try:
            irr = forward_irr.build(live, hist, _params())
            Path("data/forward_irr").mkdir(parents=True, exist_ok=True)
            irr.to_parquet("data/forward_irr/latest.parquet", index=False)
        except Exception as exc:  # noqa: BLE001
            print(f"forward IRR failed ({exc}); sheet will omit it")

    # attach resolved tickers so the sheet is addressable
    tp = Path("config/resolved_tickers.csv")
    if tp.exists():
        tk = pd.read_csv(tp)
        tk = tk[tk["status"] == "verified"][["security_id", "ticker"]]
        reg = reg.merge(tk, on="security_id", how="left")

    cats = None
    cp = Path("outputs/live/catalysts_recent.csv")
    if cp.exists():
        cats = pd.read_csv(cp)

    stamp = datetime.now(timezone.utc).date().isoformat()
    path, summary = universe_report.build(
        reg, live, irr, hist, Path(f"outputs/live/cef_universe_{stamp}.xlsx"), cats)
    print(json.dumps(summary, indent=2, default=str))

    from .notify import notify
    body = (f"CEF universe spreadsheet, {stamp}.\n\n"
            f"Funds tracked: {summary['rows']} ({summary['live']} live)\n"
            f"With a market price: {summary['with_price']}\n"
            f"With a live NAV estimate: {summary['with_live_nav']}\n"
            f"With a forward IRR: {summary['with_irr']}\n"
            f"Catalysts announced (30d): {summary.get('catalysts', 0)}\n\n"
            "Sheets: Universe (all), Live only, Most dislocated (lowest z), "
            "Catalysts.\n"
            "Published NAVs and modelled estimates are separate columns; a "
            "blank means we hold no value, never a filled-in one.")
    sent = notify(f"universe spreadsheet {stamp}", body, attachments=[str(path)])
    print("emailed:" , sent)
    Path("reports/build/universe_sheet.json").write_text(
        json.dumps({**summary, "emailed": sent, "file": str(path)}, indent=2, default=str))
    return 0


def nightly(markets: list[str]) -> int:
    params = _params()
    today = datetime.now(timezone.utc).date()
    tables = []
    all_anns: list[dict] = []
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
            tmap = None
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

            # Tier 0 BEFORE the table so fresh published NAVs anchor it
            cache = Path("data/investegate_cache")
            uk_tier0 = None
            census = pd.DataFrame()
            if cache.exists():
                census = harvest_nav.uk_frequency_census(cache)
                Path("data/nta_live").mkdir(parents=True, exist_ok=True)
                census.to_csv("data/nta_live/uk_nav_frequency_census.csv", index=False)
                if tmap is not None and len(census):
                    try:
                        uk_tier0, uk_anns = harvest_nav.harvest_uk(tmap, census)
                        all_anns.extend(uk_anns)
                        if len(uk_tier0):
                            uk_tier0.to_csv("data/nta_live/uk_tier0_latest.csv", index=False)
                    except Exception as exc:  # noqa: BLE001
                        notes["markets"]["uk_tier0_error"] = str(exc)

            t = nta_live.build_table(
                panel, "UK", ret_col="nav_total_return", nav_col=nav_col,
                price_col=price_col, params=params, tier0=uk_tier0,
                market_factors=mf, daily_factors=df, live_prices=live_px)
            tables.append(t)
            if len(census):
                notes["markets"]["uk"] = {
                    "tier0_navs": int(len(uk_tier0)) if uk_tier0 is not None else 0,
                    "nav_publishers_found": int(len(census)),
                    "daily_weekly": int((census["nav_frequency"].isin(["daily", "weekly"])).sum())
                    if len(census) else 0}
        else:
            notes["markets"]["uk"] = "panel_missing_skipped"

    # ---- catalyst scan: the reason to read announcements beyond NAV ----
    cat_frames = []
    if all_anns:
        cat_frames.append(catalysts.scan_rows(all_anns))
    au_idx = "data/asx_ann_cache/asx1/lic_announcement_index.parquet"
    au_cat = catalysts.scan_au(au_idx)
    if len(au_cat):
        cat_frames.append(au_cat)
    cats = pd.concat([c for c in cat_frames if len(c)], ignore_index=True) \
        if any(len(c) for c in cat_frames) else pd.DataFrame()
    if len(cats):
        Path("outputs/live").mkdir(parents=True, exist_ok=True)
        cats.to_csv("outputs/live/catalysts_recent.csv", index=False)
    notes["catalysts"] = catalysts.summarise(cats)

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
    cat_lines = []
    if len(cats):
        top = cats.merge(out[["security_id", "name", "z_adj", "discount_est"]],
                         on="security_id", how="left").head(12)
        for r in top.itertuples(index=False):
            z = "" if pd.isna(getattr(r, "z_adj", None)) else f"  z={r.z_adj:+.2f}"
            cat_lines.append(f"  {r.date}  {(r.name or r.security_id)[:34]:<34} "
                             f"{r.catalyst_class}{z}")
    notify(f"nightly OK - {len(out)} funds, {int(out['alert_eligible'].sum())} eligible"
           + (f", {catalysts.summarise(cats)['catalysts']} catalysts" if len(cats) else ""),
           "Nightly live NTA table built.\n"
           f"Basis counts: {basis_counts}\nSnapshot: {notes['snapshot']}\n"
           f"Deepest eligible dislocations:\n" + "\n".join(lines)
           + ("\n\nCatalysts announced (last 30 days):\n" + "\n".join(cat_lines)
              if cat_lines else "\n\nNo catalysts in the last 30 days."),
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
    sub.add_parser("universe")
    rt = sub.add_parser("resolve-tickers")
    rt.add_argument("--budget", type=int, default=400)
    sub.add_parser("universe-sheet")
    args = ap.parse_args()
    if args.cmd == "universe":
        return build_universe()
    if args.cmd == "resolve-tickers":
        return resolve_tickers(args.budget)
    if args.cmd == "universe-sheet":
        return universe_sheet()
    if args.cmd == "nightly":
        return nightly([m.strip() for m in args.markets.split(",") if m.strip()])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
