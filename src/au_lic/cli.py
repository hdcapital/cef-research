"""AU study CLI:

    python -m au_lic.cli download      # all monthly report XLSX (cached)
    python -m au_lic.cli build-panel
    python -m au_lic.cli backtest
    python -m au_lic.cli run-all
"""

from __future__ import annotations

import argparse
import logging
import sys

from uk_cef.config import load_config

log = logging.getLogger("au_lic")


def cmd_download(cfg, args):
    from .data_sources.asx import ASXClient

    client = ASXClient(cfg)
    rows = client.download_reports(limit=args.limit)
    ok = sum(1 for r in rows.values() if r["status"] == "ok")
    log.info("AU manifest: %d rows, %d ok", len(rows), ok)
    return 0


def cmd_build_panel(cfg, args):
    from .panel import build_panel

    build_panel(cfg)
    return 0


def cmd_backtest(cfg, args):
    from .runner import run_backtests

    run_backtests(cfg)
    return 0


def cmd_announcements(cfg, args):
    """Crawl per-company/per-year announcement listings and cross-validate
    the panel (NTA statement coverage; dividend events vs TR-price gaps)."""
    from pathlib import Path

    import pandas as pd

    from .announcements import AnnouncementsCrawler, validate_against_panel
    from .panel import load_panel

    panel = load_panel(cfg)
    elig = panel[panel["eligible"]]
    spans = elig.groupby("security_id")["obs_month"].agg(["min", "max"])
    crawler = AnnouncementsCrawler(cfg, budget_minutes=args.budget_minutes)
    codes_years = []
    for sid, row in spans.iterrows():
        code = sid.replace("ASX:", "")
        y0, y1 = int(row["min"][:4]), int(row["max"][:4]) + 1
        codes_years.append((code, range(y0, min(y1, 2027))))
    # crawl per company over exactly its active years
    for code, years in codes_years:
        crawler.crawl([code], years)
        if crawler.requests_made and crawler.deadline < __import__("time").time():
            log.info("budget exhausted; resumable state saved")
            break
    ann_path = Path("data/asx_ann_cache/announcements.csv")
    ann = pd.read_csv(ann_path) if ann_path.exists() else pd.DataFrame()
    validate_against_panel(panel, ann, Path(cfg["paths"]["outputs_dir"]))
    return 0


def cmd_run_all(cfg, args):
    for fn in (cmd_download, cmd_build_panel, cmd_backtest):
        rc = fn(cfg, args)
        if rc:
            return rc
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="au_lic")
    p.add_argument("--config", default="config/au_default.yaml")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in [("download", cmd_download), ("build-panel", cmd_build_panel),
                     ("backtest", cmd_backtest), ("announcements", cmd_announcements),
                     ("run-all", cmd_run_all)]:
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
        sp.add_argument("--limit", type=int, default=None)
        sp.add_argument("--budget-minutes", type=float, default=70.0)
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
