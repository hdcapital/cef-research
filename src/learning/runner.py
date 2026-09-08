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
    docs = W.select_documents(ep, before=int(prm.get("window_months_before", 18)),
                              per_fund_cap=int(prm.get("docs_per_fund_cap", 40)))
    hf = W.headline_features(ep, before=int(prm.get("window_months_before", 18)))
    E.OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs.to_parquet(E.OUT_DIR / "window_docs.parquet", index=False)
    hf.to_parquet(E.OUT_DIR / "headline_features.parquet", index=False)
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
    # newest window first per fund keeps the reads closest to the ending
    docs = docs.sort_values(["security_id", "date"], ascending=[True, False])
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


def mode_evaluate() -> int:
    prm = _params()
    from uk_cef.config import load_config
    from uk_cef.panel import load_panel
    from uk_cef.signals import build_all_signals
    cfg = load_config()
    panel = load_panel(cfg)
    panel = panel[panel["obs_month"] >= cfg["project"]["start_month"]]
    elig = build_all_signals(panel[panel["eligible"]].copy(), cfg)
    z_col = f"discount_z_{cfg['signals']['zscore_window_months']}m"
    ep = _episodes()
    lab = L.attach(elig, ep)
    feats = X.load_features()
    fbm = V.features_by_month(feats, lab, persist_months=int(prm.get("persist_months", 6)))
    lab = lab.merge(fbm, on=["security_id", "obs_month"], how="left")
    cols = list(X.FEATURE_COLUMNS)
    hfp = E.OUT_DIR / "headline_features.parquet"
    if hfp.exists():
        hf = V.headline_feature_flags(pd.read_parquet(hfp))
        lab = lab.merge(hf, on=["security_id", "obs_month"], how="left")
        for c in V.HEADLINE_SILENT:
            lab[c] = lab[c].fillna(V.HEADLINE_SILENT[c])
        cols += list(V.HEADLINE_SILENT)
    ant = V.anticipation(lab, cols)
    cheap = V.cheap_cohort_returns(lab, cols, z_col=z_col)
    OUT.mkdir(parents=True, exist_ok=True)
    ant.to_csv(OUT / "anticipation.csv", index=False)
    cheap.to_csv(OUT / "cheap_cohort_returns.csv", index=False)
    top = ant[(ant["period"] == "development") & (ant["horizon_months"] == 12)
              & ~ant["is_silent"] & (ant["n"] >= 30)].sort_values("z_vs_rest", ascending=False)
    _status("evaluate", {
        "panel_rows": int(len(lab)), "funds": int(lab["security_id"].nunique()),
        "feature_documents": int(len(feats)), "base_rates": L.base_rates(lab),
        "strongest_12m_development": top.head(8)[
            ["feature", "value", "n", "rate", "base_rate", "z_vs_rest"]].to_dict("records"),
        "cheap_cohort": cheap.to_dict("records")})
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["episodes", "windows", "extract", "evaluate"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--market", default="")
    a = ap.parse_args(argv)
    if a.mode == "episodes":
        return mode_episodes()
    if a.mode == "windows":
        return mode_windows()
    if a.mode == "extract":
        return mode_extract(a.limit, a.market or None)
    return mode_evaluate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
