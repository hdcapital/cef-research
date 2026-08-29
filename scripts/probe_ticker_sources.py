"""Why the 105 unpriced UK funds still have no ticker - and what can supply one.

Four consecutive resolution attempts returned 0/105. Each fix addressed a
real defect (a guessed search URL, a cache that skipped unresolved rows, an
entity-resolution pass that minted different security_ids) and none moved
the number, which means the cause was never in the join - it is that the
source being joined does not carry these funds.

This probe settles that and tests the identifier-based alternative:

  A. the AIC keyfacts companies file - how many funds it lists, how many
     carry a ticker, and whether the 105 appear in it at all (by ISIN and
     by normalised name);
  B. OpenFIGI's public v3 mapping endpoint - ISIN -> LSE ticker. Every one
     of the 105 has an ISIN in the registry, and an identifier join cannot
     make the class of error a name search makes (Yahoo mapped
     "British & American" to British American Tobacco).

Raw responses are printed and written to reports/build/ticker_sources.json.
Nothing here writes to the ticker cache; this run only establishes what a
source actually returns. The dev sandbox has no egress to either host, so
it runs on a GitHub runner.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests
import yaml

UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com)")
out: dict = {}

reg = pd.read_parquet("data/universe/registry.parquet")
ao = reg[(reg["market"] == "UK") & (reg["status"] == "live")
         & (reg["nav_route"] == "announcements_only")].copy()
out["announcements_only_funds"] = int(len(ao))
out["with_isin"] = int(ao["isin"].notna().sum())

# ---------------------------------------------------------------- A. keyfacts
try:
    from uk_cef.entities import normalize_name
    from uk_cef.panel import parse_all_companies

    cfg = yaml.safe_load(Path("config/default.yaml").read_text())
    raw = Path(cfg["download"]["raw_dir"])
    comp = parse_all_companies(raw)
    out["keyfacts"] = {
        "rows": int(len(comp)),
        "columns": sorted(comp.columns.tolist()) if len(comp) else [],
        "distinct_funds": int(comp["company_name"].nunique()) if len(comp) else 0,
    }
    if len(comp):
        has_tk = comp["ticker"].notna() if "ticker" in comp.columns else pd.Series(False, index=comp.index)
        out["keyfacts"]["rows_with_ticker"] = int(has_tk.sum())
        if "isin" in comp.columns:
            kf_isins = set(comp.loc[comp["isin"].notna(), "isin"].astype(str).str.upper())
            kf_isins_tk = set(comp.loc[has_tk & comp["isin"].notna(), "isin"].astype(str).str.upper())
            tgt = set(ao["isin"].dropna().astype(str).str.upper())
            out["keyfacts"]["target_isins_present_in_file"] = len(tgt & kf_isins)
            out["keyfacts"]["target_isins_with_ticker"] = len(tgt & kf_isins_tk)
        kf_names = {normalize_name(str(n)) for n in comp["company_name"].dropna()}
        tgt_names = {normalize_name(str(n)) for n in ao["name"].dropna()}
        out["keyfacts"]["target_names_present_in_file"] = len(tgt_names & kf_names)
        out["keyfacts"]["sample_rows"] = comp.head(3).to_dict("records")
except Exception as exc:  # noqa: BLE001
    out["keyfacts_error"] = f"{type(exc).__name__}: {exc}"

# ---------------------------------------------------------------- B. OpenFIGI
# Public mapping endpoint, no key required (rate-limited); 10 jobs per POST.
# exchCode "LN" is the London listing. Both a scoped and an unscoped request
# are sent so the response shape is on the record either way.
sess = requests.Session()
sess.headers.update({"Content-Type": "application/json", "User-Agent": UA})
sample = ao["isin"].dropna().astype(str).str.upper().tolist()[:10]
out["openfigi"] = {"sample_isins": sample}
for label, jobs in (
        ("exch_LN", [{"idType": "ID_ISIN", "idValue": i, "exchCode": "LN"} for i in sample]),
        ("unscoped", [{"idType": "ID_ISIN", "idValue": i} for i in sample[:3]])):
    try:
        r = sess.post("https://api.openfigi.com/v3/mapping",
                      data=json.dumps(jobs), timeout=60)
        rec: dict = {"http": r.status_code}
        try:
            rec["body"] = r.json()
        except Exception:  # noqa: BLE001
            rec["text"] = r.text[:2000]
        out["openfigi"][label] = rec
    except Exception as exc:  # noqa: BLE001
        out["openfigi"][label] = {"error": f"{type(exc).__name__}: {exc}"}

Path("reports/build").mkdir(parents=True, exist_ok=True)
Path("reports/build/ticker_sources.json").write_text(json.dumps(out, indent=2, default=str))
print(json.dumps(out, indent=2, default=str)[:6000])
