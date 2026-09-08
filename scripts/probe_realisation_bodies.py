"""Print the wording of the realisation announcements the parser missed.

The first nightly with realisation enrichment read 30 bodies and parsed
none. Only a runner has egress to Investegate, so this fetches the
realisation-class events in the store that are not treasury sales and
prints every sentence carrying a valuation word, plus what
parse_realisation makes of the body. Evidence for the next rule; writes
reports/build/realisation_probe.json.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, "src")
from cef_live import catalyst_terms, events  # noqa: E402

KEY = re.compile(r"premium|discount|carrying|book value|valuation|net asset value|\bNAV\b|"
                 r"uplift|in line with|consideration|proceeds", re.I)


def main() -> int:
    ev = pd.read_parquet("data/fund_events/events.parquet")
    r = ev[ev["event_class"].eq("realisation")
           & ~ev["headline"].str.contains("treasury", case=False, na=False)]
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (research; cef-research probe)"
    out = []
    for row in r.head(12).itertuples(index=False):
        text = catalyst_terms.fetch_body(row.url, s)
        sents = [m.group(0).strip() for m in re.finditer(r"[^.]{0,220}\.", text)
                 if KEY.search(m.group(0))]
        out.append({"security_id": row.security_id, "date": row.date, "headline": row.headline,
                    "url": row.url, "chars": len(text),
                    "parsed": events.parse_realisation(text),
                    "sentences": sents[:12]})
        print(json.dumps(out[-1], indent=1, default=str)[:3000])
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/realisation_probe.json").write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
