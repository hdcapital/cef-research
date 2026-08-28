"""Where do MIR rows go? ~290 rows in the source file, ~139 in the panel.

Parses the most recent raw MIR file directly and reports, stage by stage:
rows parsed, how many carry a share price, how many carry each NAV basis,
how many resolve to a security_id, and what the lost rows look like. The
answer decides whether the live universe is limited by a filter (fixable
by config) or by the parser/entity join (fixable by code).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import yaml

from uk_cef.parsers import mir as MIR

cfg = yaml.safe_load(Path("config/default.yaml").read_text())
raw = Path(cfg["paths"].get("raw_dir", "data/raw"))

# newest MIR csv/xlsx we hold
cands = sorted([p for p in raw.rglob("*")
                if p.is_file() and "mir" in p.name.lower()
                and p.suffix.lower() in (".csv", ".xlsx", ".xls")])
out = {"raw_dir": str(raw), "mir_files_found": len(cands),
       "newest": [str(p) for p in cands[-5:]]}

# parse EVERY MIR file for the newest month, not just the alphabetically
# last one (post-errata files carry only a handful of corrections)
newest_month = max(p.name[:7] for p in cands if p.name[:4].isdigit()) if cands else None
month_files = [p for p in cands if p.name.startswith(str(newest_month))]
out["newest_month"] = newest_month
out["month_files"] = [p.name for p in month_files]
per_file = {}
for f in month_files:
    try:
        per_file[f.name] = len(MIR.parse_mir_csv(f))
    except Exception as exc:  # noqa: BLE001
        per_file[f.name] = f"error: {exc}"
out["rows_per_file"] = per_file
best = max((f for f in month_files if isinstance(per_file.get(f.name), int)),
           key=lambda f: per_file[f.name], default=None)

if best is not None:
    target = best
    out["target"] = str(target)
    try:
        recs = MIR.parse_mir_csv(target)
        df = pd.DataFrame(recs)
        out["parser_fn"] = "parse_mir_csv"
        out["parsed_rows"] = int(len(df))
        if df.empty:
            raise RuntimeError("parser returned zero rows")
        out["parsed_cols"] = sorted(df.columns.tolist())[:40]
        for col in df.columns:
            nn = int(df[col].notna().sum())
            if nn:
                out.setdefault("non_null_by_col", {})[col] = nn
        # what identifies rows?
        for idc in ("sedol", "isin", "tidm", "ticker", "company_name", "name"):
            if idc in df.columns:
                out.setdefault("id_coverage", {})[idc] = int(df[idc].notna().sum())
        # rows with a usable price
        for pc in ("share_price", "price", "mid_price"):
            if pc in df.columns:
                out["rows_with_price"] = int(df[pc].notna().sum())
                break
        # NAV basis columns present
        navcols = [c for c in df.columns if "nav" in c.lower()]
        out["nav_like_cols"] = navcols[:25]
        out["nav_col_coverage"] = {c: int(df[c].notna().sum()) for c in navcols[:25]}
        # sample of rows with NO nav at all
        if navcols:
            no_nav = df[df[navcols].isna().all(axis=1)]
            out["rows_without_any_nav"] = int(len(no_nav))
            keep = [c for c in ("company_name", "name", "sedol", "isin", "sector")
                    if c in no_nav.columns]
            out["no_nav_sample"] = no_nav[keep].head(15).to_dict("records") if keep else []
    except Exception as exc:  # noqa: BLE001
        out["parse_error"] = f"{type(exc).__name__}: {exc}"

# decisive: are the no-price rows genuinely empty in the SOURCE file, or is
# our column finder missing them? Dump raw cells for a few of each kind.
try:
    from uk_cef.parsers.mir import _read_rows
    rows = _read_rows(target)
    hdr_idx = [i for i, r in enumerate(rows[:8]) if r and r[0].strip() == "AIC"]
    h = hdr_idx[0] if hdr_idx else 0
    out["raw_header_1"] = rows[h][:18]
    out["raw_header_2"] = rows[h + 1][:18] if len(rows) > h + 1 else []
    body = [r for r in rows[h + 2:] if r and any(c.strip() for c in r)]
    out["raw_body_rows"] = len(body)
    # cell-fill profile: how many non-empty cells per row
    prof = {}
    for r in body:
        n = sum(1 for c in r if c.strip())
        prof[n] = prof.get(n, 0) + 1
    out["cells_filled_histogram"] = dict(sorted(prof.items())[:20])
    # hypothesis: the AIC publishes market data only for UK-registered (GB
    # ISIN) funds, listing offshore (Guernsey/Jersey) trusts by name only.
    from collections import Counter
    fill_by_prefix = {}
    for r in body:
        isin = (r[4] if len(r) > 4 else "").strip()
        pre = isin[:2] if isin else "(none)"
        filled = sum(1 for c in r if c.strip()) > 6
        d2 = fill_by_prefix.setdefault(pre, {"with_data": 0, "name_only": 0})
        d2["with_data" if filled else "name_only"] += 1
    out["fill_by_isin_prefix"] = dict(sorted(
        fill_by_prefix.items(), key=lambda kv: -(kv[1]["with_data"] + kv[1]["name_only"])))
    sparse = [r for r in body if sum(1 for c in r if c.strip()) <= 6]
    out["sparse_row_samples"] = [r[:14] for r in sparse[:8]]
    full = [r for r in body if sum(1 for c in r if c.strip()) > 6]
    out["full_row_samples"] = [r[:14] for r in full[:3]]
except Exception as exc:  # noqa: BLE001
    out["raw_dump_error"] = str(exc)

Path("reports/build").mkdir(parents=True, exist_ok=True)
Path("reports/build/mir_coverage.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps(out, indent=2, default=str)[:4000])
