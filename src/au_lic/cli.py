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
                     ("backtest", cmd_backtest), ("run-all", cmd_run_all)]:
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
        sp.add_argument("--limit", type=int, default=None)
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
