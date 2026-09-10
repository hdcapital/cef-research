"""python -m learning.runner <mode>

  episodes   the resolution-episode table (free, offline)
  windows    the document window and the headline features (free; UK needs
             the Investegate listing cache, S3 group uk_announcements)
  extract    read window documents for the ten features (model-backed,
             --limit documents, --market UK|AU, sharded by SHARD_INDEX/COUNT)
  evaluate   the anticipation test on the monthly research panel (needs the
             panel; python -m uk_cef.cli build-panel first)

Every mode writes reports/build/learning_status.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

from learning import episodes as E  # noqa: E402
from learning import evaluate as V  # noqa: E402
from learning import extract as X  # noqa: E402
from learning import labels as L  # noqa: E402
from learning import windows as W  # noqa: E402

STATUS = Path("reports/build/learning_status.json")
OUT = Path("outputs/learning")


def _params() -> dict:
    try:
        import yaml
        cfg = yaml.safe_load(Path("config/params.yaml").read_text()) or {}
        return cfg.get("learning") or {}
    except Exception:  # noqa: BLE001
        return {}


def _status(mode: str, payload: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    cur = {}
    if STATUS.exists():
        try:
            cur = json.loads(STATUS.read_text())
        except Exception:  # noqa: BLE001
            cur = {}
    cur[mode] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 **payload}
    STATUS.write_text(json.dumps(cur, indent=2, default=str))
    print(json.dumps(cur[mode], indent=1, default=str))


def _episodes() -> pd.DataFrame:
    p = E.OUT_DIR / "episodes.parquet"
    return pd.read_parquet(p) if p.exists() else E.build()


def mode_episodes() -> int:
    ep = E.build()
    _status("episodes", E.write(ep))
    return 0


def mode_windows() -> int:
    prm = _params()
    ep = _episodes()
    before = int(prm.get("window_months_before", 18))
    cap = int(prm.get("docs_per_fund_cap", 40))
    docs = W.select_documents(ep, before=before, per_fund_cap=cap)
    docs["cohort"] = "case"
    docs["case_id"] = docs["security_id"]
    # the control cohort: survivors read over the same months under the
    # same rules, so a feature's rate among endings has a comparison
    reg = pd.read_parquet(E.REGISTRY) if E.REGISTRY.exists() else pd.DataFrame()
    ctrl = W.select_controls(ep, reg, before=before,
                             clearance=int(prm.get("control_clearance_months", 12)),
                             per_case=int(prm.get("controls_per_case", 1))) if len(reg) else pd.DataFrame()
    if len(ctrl):
        cdocs = W.select_documents(ctrl, before=before, per_fund_cap=cap)
        cid = ctrl.drop_duplicates(["security_id", "end_month"]).set_index(
            ["security_id", "end_month"])["case_id"]
        cdocs["case_id"] = [cid.get((a, b)) for a, b in zip(cdocs["security_id"], cdocs["end_month"])]
        cdocs["cohort"] = "control"
        docs = pd.concat([docs, cdocs], ignore_index=True)
    hf = W.headline_features(ep, before=before)
    hfu = W.headline_features_universe(reg) if len(reg) else pd.DataFrame()
    E.OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs.to_parquet(E.OUT_DIR / "window_docs.parquet", index=False)
    hf.to_parquet(E.OUT_DIR / "headline_features.parquet", index=False)
    hfu.to_parquet(E.OUT_DIR / "headline_features_universe.parquet", index=False)
    ctrl.to_csv(E.OUT_DIR / "controls.csv", index=False)
    cov = W.listing_coverage(ep, before=int(prm.get("window_months_before", 18)))
    cov.to_csv(E.OUT_DIR / "window_coverage.csv", index=False)
    cov_note = {}
    if len(cov):
        for mkt, g in cov.groupby("market"):
            cov_note[mkt] = {
                "episodes_with_ticker": int(len(g)),
                "listing_present": int((g["listing_rows"] > 0).sum()),
                "listing_reaches_window": int(g["reaches_window"].sum()),
                "window_has_rows": int((g["window_rows"] > 0).sum())}
    _status("windows", {
        "listing_coverage": cov_note,
        "by_cohort": docs["cohort"].value_counts().to_dict() if len(docs) else {},
        "controls": int(len(ctrl)),
        "universe_headline_feature_rows": int(len(hfu)),
        "universe_funds": int(hfu["security_id"].nunique()) if len(hfu) else 0,
        "documents": int(len(docs)), "funds_with_documents": int(docs["security_id"].nunique()),
        "by_market": docs["market"].value_counts().to_dict() if len(docs) else {},
        "by_family": docs["family"].value_counts().to_dict() if len(docs) else {},
        "headline_feature_rows": int(len(hf)),
        "funds_with_headline_features": int(hf["security_id"].nunique()) if len(hf) else 0,
        "uk_listing_cache_present": W.UK_LISTINGS.exists()})
    return 0


def mode_extract(limit: int, market: str | None) -> int:
    prm = _params()
    p = E.OUT_DIR / "window_docs.parquet"
    if not p.exists():
        mode_windows()
    docs = pd.read_parquet(p)
    if market:
        docs = docs[docs["market"].eq(market.upper())]
    shard = int(os.environ.get("SHARD_INDEX", "0"))
    shards = max(1, int(os.environ.get("SHARD_COUNT", "1")))
    if shards > 1:
        docs = docs[docs["security_id"].map(lambda s: zlib.crc32(s.encode()) % shards == shard)]
    # a case and its control are read together, newest first within each,
    # so a budget cut leaves matched pairs rather than cases without controls
    if "case_id" not in docs.columns:
        docs["case_id"] = docs["security_id"]
        docs["cohort"] = "case"
    docs = docs.sort_values(["case_id", "cohort", "security_id", "date"],
                            ascending=[True, True, True, False])
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _status("extract", {"skipped": "no ANTHROPIC_API_KEY", "candidates": int(len(docs))})
        return 0
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = ("Mozilla/5.0 (research; cef-research learning layer)")
    s3 = X._s3()
    rows, rejects, stats = X.run(
        docs, budget=limit or int(prm.get("docs_per_run", 200)),
        deadline_min=float(prm.get("deadline_min", 240)), session=session, s3=s3)
    tag = f"s{shard}of{shards}" if shards > 1 else "all"
    written = X.write_outputs(rows, rejects, tag, s3=s3)
    _status("extract" if shards == 1 else f"extract_{tag}",
            {**stats, **written, "model": X.model_name(),
             "prompt_version": X.prompt_version()})
    return 0


def mode_listings(budget_minutes: float, limit: int = 0) -> int:
    """Index the announcements of the UK endings whose Investegate listing
    the crawl never reached (listings only, identity verified by the
    crawler's H1 check, sharded by SHARD_INDEX/COUNT). The workflow pushes
    the uk_announcements state group afterwards."""
    from uk_cef.data_sources.investegate import InvestegateCrawler
    ep = _episodes()
    todo = W.listing_targets(ep)
    shard = int(os.environ.get("SHARD_INDEX", "0"))
    shards = max(1, int(os.environ.get("SHARD_COUNT", "1")))
    if shards > 1 and len(todo):
        todo = todo[todo["ticker"].astype(str).map(lambda t: zlib.crc32(t.encode()) % shards == shard)]
    if limit:
        todo = todo.head(limit)
    print(f"shard {shard + 1}/{shards}: {len(todo)} endings to index")
    crawler = InvestegateCrawler(budget_minutes=budget_minutes, listings_only=True)
    results = []
    for r in todo.itertuples(index=False):
        names = [r.name] if isinstance(r.name, str) else []
        status = crawler.crawl_company(r.security_id, str(r.ticker), names)
        f = crawler.listings / f"{r.ticker}.csv"
        rows = 0
        if f.exists():
            try:
                rows = int(len(pd.read_csv(f, dtype=str)))
            except Exception:  # noqa: BLE001
                rows = 0
        results.append({"security_id": r.security_id, "ticker": r.ticker, "end_month": r.end_month,
                        "status": status, "rows_indexed": rows})
        print(f"  {r.ticker:6s} {r.end_month} {status} rows={rows}")
        if status == "budget_exhausted":
            break
    res = pd.DataFrame(results)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"listings_s{shard}of{shards}.csv", index=False)
    _status("listings" if shards == 1 else f"listings_s{shard}of{shards}", {
        "targets": int(len(todo)), "attempted": int(len(res)),
        "status_counts": res["status"].value_counts().to_dict() if len(res) else {},
        "indexed": int((res["rows_indexed"] > 0).sum()) if len(res) else 0,
        "rows_indexed": int(res["rows_indexed"].sum()) if len(res) else 0})
    return 0


def mode_evaluate() -> int:
    prm = _params()
    from uk_cef.config import load_config
    from uk_cef.signals import build_all_signals
    panels = []
    z_col = "discount_z_36m"
    for cfg_path, loader in (("config/default.yaml", "uk"), ("config/au_default.yaml", "au")):
        try:
            cfg = load_config(cfg_path)
            if loader == "uk":
                from uk_cef.panel import load_panel
            else:
                from au_lic.panel import load_panel
            panel = load_panel(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"{loader} panel unavailable: {exc}")
            continue
        panel = panel[panel["obs_month"] >= cfg["project"]["start_month"]]
        elig = build_all_signals(panel[panel["eligible"]].copy(), cfg)
        z_col = f"discount_z_{cfg['signals']['zscore_window_months']}m"
        elig["panel_market"] = loader.upper()
        panels.append(elig)
    if not panels:
        _status("evaluate", {"error": "no monthly panel available"})
        return 1
    elig = pd.concat(panels, ignore_index=True)
    ep = _episodes()
    lab = L.attach(elig, ep)
    feats = X.load_features()
    persist = int(prm.get("persist_months", 6))
    fbm = V.features_by_month(feats, lab, persist_months=persist)
    lab = lab.merge(fbm, on=["security_id", "obs_month"], how="left")
    # only fund-months whose fund had a document read in the trailing
    # window take part in the extracted-feature tests: silence elsewhere is
    # an unread fund, not a fund that said nothing
    lab["documented"] = V.documented(feats, lab, persist_months=persist)
    cols = list(X.FEATURE_COLUMNS)
    hfu = E.OUT_DIR / "headline_features_universe.parquet"
    hfp = hfu if hfu.exists() else E.OUT_DIR / "headline_features.parquet"
    if hfp.exists():
        hf = V.headline_feature_flags(pd.read_parquet(hfp))
        lab = lab.merge(hf, on=["security_id", "obs_month"], how="left")
        for c in V.HEADLINE_SILENT:
            lab[c] = lab[c].fillna(V.HEADLINE_SILENT[c])
    hcols = list(V.HEADLINE_SILENT)
    doc = lab[lab["documented"]]
    cohorts = {}
    for c in ("case", "control"):
        if "cohort" in feats.columns:
            sids = set(feats.loc[feats["cohort"].eq(c), "security_id"])
            cohorts[c] = int(doc["security_id"].isin(sids).sum())
    ant = pd.concat([V.anticipation(doc, cols).assign(universe="documented"),
                     V.anticipation(lab, hcols).assign(universe="all_funds" if hfu.exists() else "episodes")],
                    ignore_index=True)
    cheap = pd.concat([V.cheap_cohort_returns(doc, cols, z_col=z_col).assign(universe="documented"),
                       V.cheap_cohort_returns(lab, hcols, z_col=z_col).assign(universe="all_funds")],
                      ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ant.to_csv(OUT / "anticipation.csv", index=False)
    cheap.to_csv(OUT / "cheap_cohort_returns.csv", index=False)

    def _top(u):
        t = ant[(ant["universe"] == u) & (ant["period"] == "development") & (ant["horizon_months"] == 12)
                & ~ant["is_silent"] & (ant["n"] >= 30)].sort_values("z_vs_rest", ascending=False)
        return t.head(8)[["feature", "value", "n", "rate", "base_rate", "z_vs_rest"]].to_dict("records")
    _status("evaluate", {
        "panel_rows": int(len(lab)), "funds": int(lab["security_id"].nunique()),
        "panels": [p["panel_market"].iloc[0] for p in panels],
        "feature_documents": int(len(feats)),
        "documented_fund_months": int(lab["documented"].sum()), "documented_by_cohort": cohorts,
        "base_rates_all": L.base_rates(lab), "base_rates_documented": L.base_rates(doc),
        "strongest_12m_development_documented": _top("documented"),
        "strongest_12m_development_headline": _top("all_funds" if hfu.exists() else "episodes"),
        "cheap_cohort": cheap.to_dict("records")})
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["episodes", "windows", "extract", "evaluate", "listings"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--market", default="")
    ap.add_argument("--budget-minutes", type=float, default=240.0)
    a = ap.parse_args(argv)
    if a.mode == "listings":
        return mode_listings(a.budget_minutes, a.limit)
    if a.mode == "episodes":
        return mode_episodes()
    if a.mode == "windows":
        return mode_windows()
    if a.mode == "extract":
        return mode_extract(a.limit, a.market or None)
    return mode_evaluate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
