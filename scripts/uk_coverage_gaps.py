"""Which UK funds have no Investegate page, and does it matter?

The crawler's coverage summary reports a bare count of `not_found`, which
says nothing about whether those funds are dead (a page is not expected)
or live and trading (a real hole in the live system). This splits them and
joins the registry so the answer is legible.

Writes outputs/uk_no_investegate_page.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

COV = Path("outputs/investegate_coverage.csv")
REG = Path("data/universe/registry.parquet")
OUT = Path("outputs/uk_no_investegate_page.csv")


def fallback_name(security_id: str) -> str:
    """Recover a readable name from a NAME:-style security_id.

    SEDOL:-keyed ids carry no name, so these stay unhelpful until the
    registry join supplies one - which is why the join is not optional.
    """
    s = str(security_id)
    if s.upper().startswith("NAME:"):
        s = s[5:]
    return s.split("|")[0].strip().title()


def main() -> int:
    if not COV.exists():
        print(f"{COV} missing - run the Investegate crawl first")
        return 0
    cov = pd.read_csv(COV)
    nf = cov[cov["status"] == "not_found"].copy()
    nf["fund"] = nf["security_id"].map(fallback_name)

    if REG.exists():
        reg = pd.read_parquet(REG)
        reg["security_id"] = reg["security_id"].astype(str)
        keep = [c for c in ("security_id", "name", "status", "isin", "last_seen")
                if c in reg.columns]
        nf["security_id"] = nf["security_id"].astype(str)
        nf = nf.merge(reg[keep], on="security_id", how="left",
                      suffixes=("", "_reg"))
        # the registry name is authoritative; the id-derived one is a fallback
        if "name" in nf.columns:
            nf["fund"] = nf["name"].fillna(nf["fund"])
        nf = nf.rename(columns={"status_reg": "listing_status"})
    else:
        nf["listing_status"] = None

    cols = [c for c in ("ticker", "fund", "listing_status", "last_seen",
                        "isin", "security_id") if c in nf.columns]
    nf = nf[cols].sort_values(
        ["listing_status", "fund"], na_position="last")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    nf.to_csv(OUT, index=False)

    counts = nf["listing_status"].value_counts(dropna=False).to_dict()
    print(f"{len(nf)} funds with no Investegate page: {counts}")
    live = nf[nf["listing_status"] == "live"]
    if len(live):
        # these are the ones that cost us something today
        print("\nLIVE funds missing a page (real gaps in the live system):")
        for _, r in live.iterrows():
            print(f"  {r['ticker']:<6} {r['fund']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
