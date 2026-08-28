"""Why does the live UK table carry ~141 funds when the AIC universe is ~300?

Prints the exclusion funnel for the most recent panel months: how many
securities the AIC files carry, how many survive each eligibility rule,
and how many have the price/NAV pair the live table needs. Writes
reports/build/uk_universe_funnel.json so the answer is auditable rather
than argued from memory.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import yaml

from uk_cef import panel as P

cfg = yaml.safe_load(Path("config/default.yaml").read_text())
pan = pd.read_parquet("data/processed/monthly_panel.parquet")

PX = next(c for c in ("price", "share_price") if c in pan.columns)
NV = next(c for c in ("nav", "nav_per_share") if c in pan.columns)
out = {"columns": sorted(pan.columns.tolist()), "price_col": PX, "nav_col": NV}
months = sorted(pan["obs_month"].unique())[-3:]
for m in months:
    g = pan[pan["obs_month"] == m]
    rec = {
        "rows": int(len(g)),
        "securities": int(g["security_id"].nunique()),
        "has_price": int(g[PX].notna().sum()),
        "has_nav": int(g[NV].notna().sum()),
        "has_both": int((g[PX].notna() & g[NV].notna()).sum()),
        "has_discount": int(g["discount"].notna().sum()),
        "eligible": int(g["eligible"].sum()),
    }
    for col in ("is_vct", "non_gbx_quote", "late_reported", "split_adjusted"):
        if col in g.columns:
            rec[f"flag_{col}"] = int(g[col].fillna(False).sum())
    # share-class / type breakdown where available
    for col in ("share_class", "type", "sector", "currency"):
        if col in g.columns:
            rec[f"top_{col}"] = g[col].astype(str).value_counts().head(6).to_dict()
    out[str(m)] = rec

# what do the excluded-but-priced securities look like?
last = months[-1]
g = pan[pan["obs_month"] == last]
excluded = g[~g["eligible"] & g[PX].notna()]
out["excluded_sample"] = excluded[
    [c for c in ("security_id", "company_name", "sector", "currency",
                 "price", "nav", "discount", "is_vct", "non_gbx_quote")
     if c in excluded.columns]
].head(25).to_dict("records")

# how many distinct securities appear in the last 3 months at all
recent = pan[pan["obs_month"].isin(months)]
out["distinct_securities_last3m"] = int(recent["security_id"].nunique())
out["with_nav_last3m"] = int(recent[recent[NV].notna()]["security_id"].nunique())
out["with_price_last3m"] = int(recent[recent[PX].notna()]["security_id"].nunique())

Path("reports/build").mkdir(parents=True, exist_ok=True)
Path("reports/build/uk_universe_funnel.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps({k: v for k, v in out.items() if k != "excluded_sample"}, indent=2, default=str)[:3000])
