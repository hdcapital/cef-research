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

if cands:
    target = cands[-1]
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

Path("reports/build").mkdir(parents=True, exist_ok=True)
Path("reports/build/mir_coverage.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps(out, indent=2, default=str)[:4000])
