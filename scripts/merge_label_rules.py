"""Aggregate label evidence ACROSS shards, then apply the repetition bar.

Discovery is sharded by announcement id so eight runners can share the
fetching, but the repetition bar is per FUND: a label must win in three
distinct months before it is a rule. Applying that bar inside each shard
tests it against one eighth of a fund's months, so a fund with six months of
evidence sees roughly one month per shard and can never clear it - 88 funds
had evidence and only 21 got a rule.

Evidence is per-observation and shard-independent, so the fix is to gather
every shard's evidence and run the bar once over the whole of it.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

from au_lic.extract import label_discovery as LD  # noqa: E402

OUT_RULES = Path("outputs/au/au_nta_label_rules.csv")
OUT_STATUS = Path("reports/build/asx_label_rules_merged.json")


def main() -> int:
    files = sorted(glob.glob("outputs/au/au_nta_label_evidence*.csv"))
    if not files:
        print("no evidence files - run the labels mode first")
        return 0
    ev = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    ev = ev.drop_duplicates(["ticker", "month", "label", "unit"])
    learn = ev[ev["split"] == "learn"] if "split" in ev.columns else ev
    rules = LD.discover(learn)
    firm = rules[rules["is_rule"]]

    OUT_RULES.parent.mkdir(parents=True, exist_ok=True)
    rules.to_csv(OUT_RULES, index=False)

    status = {
        "evidence_files": len(files),
        "evidence_rows": int(len(ev)),
        "funds_with_evidence": int(ev["ticker"].nunique()),
        "learn_rows": int(len(learn)),
        "holdout_rows": int(len(ev) - len(learn)),
        "labels_seen": int(len(rules)),
        "rules": int(len(firm)),
        "funds_covered": int(firm["ticker"].nunique()) if len(firm) else 0,
        # a label with the support but not the vocabulary is exactly the
        # address case: surfaced for review, never auto-promoted
        "high_support_no_vocab": int(((~rules["has_nav_vocab"])
                                      & (rules["months_clean"] >= 3)).sum()),
        "note": ("the bar is applied once over all shards' evidence; applying "
                 "it per shard tested it against an eighth of each fund's "
                 "months and under-counted funds by roughly four times"),
    }
    OUT_STATUS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STATUS.write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
