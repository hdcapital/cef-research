"""Diag 2: panel-level eligibility forensics for the 2012-05 universe hole.

Builds the panel from the cached raw files exactly as the pipeline does and
dumps, for the problem months, how many rows each eligibility condition
removes plus a sample of the removed rows. Results committed under
data/probe/diag/ for offline inspection.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd  # noqa: E402

from uk_cef.config import load_config  # noqa: E402
from uk_cef.entities import EntityRegistry  # noqa: E402
from uk_cef.panel import _dedupe_bitemporal, parse_all_corporate_activity, parse_all_mir  # noqa: E402

OUT = Path("data/probe/diag")
PROBLEM_MONTHS = ["2012-04", "2012-05", "2012-06", "2023-12", "2020-04", "2019-02"]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    raw_dir = Path(cfg["download"]["raw_dir"])
    mir = parse_all_mir(raw_dir)
    ca = parse_all_corporate_activity(raw_dir)
    registry = EntityRegistry(cfg["paths"].get("entity_overrides"))
    registry.load_name_changes(ca)
    mir = mir.sort_values(["obs_month", "company_name"])
    mir["security_id"] = [
        registry.resolve(n, c, t)
        for n, c, t in zip(mir["company_name"], mir["code"], mir["share_type"])
    ]

    report_lines = []
    for month in PROBLEM_MONTHS:
        raw_rows = mir[mir["obs_month"] == month]
        report_lines.append(f"===== {month} =====")
        report_lines.append(f"raw parsed rows: {len(raw_rows)} from files {sorted(raw_rows['source_file'].unique())}")
        report_lines.append(f"  with price: {raw_rows['price'].notna().sum()}, with nav: {raw_rows['nav'].notna().sum()}")

    panel = _dedupe_bitemporal(mir)
    for month in PROBLEM_MONTHS:
        sub = panel[panel["obs_month"] == month].copy()
        report_lines.append(f"===== {month} after dedupe =====")
        report_lines.append(f"rows: {len(sub)}")
        stype = sub["share_type"].fillna("ordinary share").str.lower()
        sector = sub["sector"].fillna("").str.lower()
        checks = {
            "has_price": sub["price"].notna(),
            "has_nav": sub["nav"].notna(),
            "is_ordinary": stype.str.contains("ordinary"),
            "not_vct": ~(sector.str.contains("venture capital") | sector.str.startswith("vct")),
            "not_late": sub["first_release_month"] <= sub["obs_month"],
            "gbx": sub["currency"].isna() | (sub["currency"] == "GBX"),
        }
        for name, mask in checks.items():
            report_lines.append(f"  {name}: pass={int(mask.sum())} fail={int((~mask).sum())}")
        combined = checks["has_price"] & checks["has_nav"] & checks["is_ordinary"] & checks["not_vct"] & checks["not_late"] & checks["gbx"]
        report_lines.append(f"  ALL: {int(combined.sum())}")
        failed = sub[checks["has_price"] & checks["has_nav"] & checks["is_ordinary"] & checks["not_vct"] & ~checks["not_late"]]
        if len(failed):
            report_lines.append(f"  late-flagged sample (first_release vs obs):")
            for _, r in failed.head(8).iterrows():
                report_lines.append(
                    f"    {r['company_name'][:40]:42s} first_release={r['first_release_month']} release={r['release_month']} src={r['source_file']}"
                )
        # currency values seen
        report_lines.append(f"  currencies: {sub['currency'].value_counts(dropna=False).to_dict()}")

    (OUT / "eligibility_forensics.txt").write_text("\n".join(report_lines))
    print("\n".join(report_lines[:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
