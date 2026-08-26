"""Command-line interface.

    python -m uk_cef.cli discover        # enumerate the AIC archive -> data_inventory.csv
    python -m uk_cef.cli download        # download & cache all archive files
    python -m uk_cef.cli build-entities  # point-in-time entity table
    python -m uk_cef.cli build-panel     # monthly security x month panel
    python -m uk_cef.cli validate        # data-quality report + look-ahead checks
    python -m uk_cef.cli backtest        # strategies, deciles, robustness
    python -m uk_cef.cli report          # charts + report.md
    python -m uk_cef.cli run-all         # everything, in order
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config

log = logging.getLogger("uk_cef")


def cmd_discover(cfg: dict, args) -> int:
    from .data_sources.aic import AICClient, build_inventory

    client = AICClient(cfg)
    build_inventory(client, Path(cfg["paths"]["outputs_dir"]) / "data_inventory.csv")
    files = client.list_files()
    log.info("inventory written: %d archive files known", len(files))
    return 0


def cmd_download(cfg: dict, args) -> int:
    from .data_sources.aic import AICClient, build_inventory

    client = AICClient(cfg)
    types = tuple(args.types.split(",")) if args.types else (
        "mir", "keyfacts", "corporate_activity",
    )
    # Skip files the pipeline does not parse: MIR sub-components (geographic
    # split, prior-charge detail, warrants, convertibles), all PDFs (the
    # company table lives in the XLS/XLSX of the same bundle), and the
    # sector-summary / manager-rankings workbooks.
    skip = ("_mir_GEO", "_mir_PC", "_mir_WAR", "_mir_CNV",
            "_keyfacts_AICSectorSummary", "_keyfacts_AICManagementGroupRankings",
            ".pdf", ".PDF")
    rows = client.download_all(publication_types=types, limit=args.limit, skip_patterns=skip)
    ok = sum(1 for r in rows.values() if r["status"] == "ok")
    log.info("download manifest: %d rows, %d ok", len(rows), ok)
    build_inventory(client, Path(cfg["paths"]["outputs_dir"]) / "data_inventory.csv")
    return 0


def cmd_build_entities(cfg: dict, args) -> int:
    from .entities import build_entities

    build_entities(cfg)
    return 0


def cmd_build_panel(cfg: dict, args) -> int:
    from .panel import build_panel

    build_panel(cfg)
    return 0


def cmd_validate(cfg: dict, args) -> int:
    from .panel import load_panel
    from .validation import check_no_lookahead, run_quality_checks
    import pandas as pd

    panel = load_panel(cfg)
    report = run_quality_checks(panel, cfg)
    out = Path(cfg["paths"]["outputs_dir"]) / "data_quality_report.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(out, index=False)
    log.info("quality report: %d issues -> %s", len(report), out)
    problems = check_no_lookahead(panel)
    for p in problems:
        log.error("LOOK-AHEAD: %s", p)
    return 1 if problems else 0


def cmd_backtest(cfg: dict, args) -> int:
    from .runner import run_backtests

    run_backtests(cfg)
    return 0


def cmd_report(cfg: dict, args) -> int:
    from .reporting import generate_report

    generate_report(cfg)
    return 0


def cmd_run_all(cfg: dict, args) -> int:
    for fn in (cmd_discover, cmd_download, cmd_build_entities, cmd_build_panel,
               cmd_validate, cmd_backtest, cmd_report):
        rc = fn(cfg, args)
        if rc not in (0, None) and fn is not cmd_validate:
            return rc
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="uk_cef")
    parser.add_argument("--config", default=None, help="path to YAML config")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn in [
        ("discover", cmd_discover),
        ("download", cmd_download),
        ("build-entities", cmd_build_entities),
        ("build-panel", cmd_build_panel),
        ("validate", cmd_validate),
        ("backtest", cmd_backtest),
        ("report", cmd_report),
        ("run-all", cmd_run_all),
    ]:
        p = sub.add_parser(name)
        p.set_defaults(func=fn)
        if name in ("download", "run-all"):
            p.add_argument("--types", default=None, help="comma-separated publication types")
            p.add_argument("--limit", type=int, default=None, help="max new downloads this run")

    args = parser.parse_args(argv)
    if not hasattr(args, "types"):
        args.types = None
        args.limit = None
    cfg = load_config(args.config)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
