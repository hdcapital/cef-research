"""Which headlines do the NAV-blocked UK funds actually publish?

The UK NAV archiver, the frequency census and the Tier-0 harvest all select
announcements by the same phrase: ``net asset value``. Underwood Capital
taught the ASX side that a fund can publish its NAV under a headline
containing none of the expected words - this probe measures whether the UK
cohort blocked on ``nav`` in signal_coverage.csv has the same shape, BEFORE
any pattern is widened.

Reads the Investegate listing caches (state group ``uk_announcements``) and
writes reports/build/uk_nav_reach_probe.json:

  per fund: announcements since 2024, how many the current pattern matches,
  the newest matched date, and the most frequent NON-matching headlines.
  aggregate: non-matching headlines ranked across all blocked funds, with
  which of the candidate wider patterns would catch each.

No network access. Evidence only; it changes nothing.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

OLD = re.compile(r"net asset value", re.I)
CANDIDATES = {
    "nav_word": re.compile(r"\bNAVs?\b", re.I),
    "net_tangible": re.compile(r"net tangible", re.I),
    "estimated_nav": re.compile(r"estimated (?:weekly |monthly )?net asset", re.I),
}
SINCE = "2024-01-01"

def main() -> int:
    sc = pd.read_csv("outputs/live/signal_coverage.csv")
    blocked = sc[(sc["market"] == "UK")
                 & sc["blockers"].fillna("").str.contains("nav")]
    rt = pd.read_csv("config/resolved_tickers.csv")
    rt = rt[(rt["status"] == "verified") & rt["ticker"].notna()]
    tick = dict(zip(rt["security_id"], rt["ticker"].astype(str).str.upper()))

    listings = Path("data/investegate_cache/listings")
    files = {f.stem.upper(): f for f in listings.glob("*.csv")} \
        if listings.exists() else {}
    print(f"listing caches on disk: {len(files)}")

    per_fund = []
    miss_counter: Counter[str] = Counter()
    for r in blocked.itertuples(index=False):
        t = tick.get(r.security_id)
        f = files.get(t) if t else None
        if f is None:
            per_fund.append({"security_id": r.security_id, "ticker": t,
                             "name": r.name, "status": "no_listing_cache"})
            continue
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception as exc:  # noqa: BLE001
            per_fund.append({"security_id": r.security_id, "ticker": t,
                             "name": r.name, "status": f"unreadable:{exc}"})
            continue
        if "headline" not in df.columns or "date" not in df.columns:
            per_fund.append({"security_id": r.security_id, "ticker": t,
                             "name": r.name,
                             "status": f"columns:{list(df.columns)}"})
            continue
        recent = df[df["date"].fillna("") >= SINCE]
        heads = recent["headline"].fillna("")
        m_old = heads.str.contains(OLD)
        # newest date the CURRENT pattern would have fetched
        newest_old = recent.loc[m_old, "date"].max() if m_old.any() else None
        misses = recent.loc[~m_old, "headline"].fillna("")
        # collapse per-fund repeats so one chatty fund cannot dominate
        top_misses = misses.str.strip().value_counts().head(12)
        for h in top_misses.index:
            miss_counter[h] += 1
        per_fund.append({
            "security_id": r.security_id, "ticker": t, "name": r.name,
            "status": "ok",
            "announcements_since_2024": int(len(recent)),
            "matched_by_current_pattern": int(m_old.sum()),
            "newest_matched_date": newest_old,
            "newest_any_date": recent["date"].max() if len(recent) else None,
            "top_nonmatching_headlines": top_misses.to_dict(),
        })

    agg = []
    for h, n in miss_counter.most_common(80):
        agg.append({"headline": h, "funds": n,
                    "caught_by": [k for k, p in CANDIDATES.items()
                                  if p.search(h)]})

    out = {"since": SINCE, "blocked_funds": int(len(blocked)),
           "with_listing_cache": sum(1 for p in per_fund
                                     if p.get("status") == "ok"),
           "aggregate_nonmatching_headlines": agg,
           "per_fund": per_fund}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/uk_nav_reach_probe.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(json.dumps({k: out[k] for k in
                      ("blocked_funds", "with_listing_cache")}, indent=2))
    print("top non-matching headlines:",
          json.dumps(agg[:15], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
