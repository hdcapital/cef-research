"""Re-resolve the live UK funds whose Investegate page we cannot find.

Seven funds are live and trading but the crawler reports `not_found` for
them. The suspicion is stale tickers left behind by fund renames - we ask
Investegate for a symbol the fund no longer trades under, get the generic
market feed page instead of a company page, and silently drop a live fund
from the live system.

The fix is an identifier join, not a name search: OpenFIGI maps the ISIN we
already hold to the London listing's current code. But a FIGI answer is a
CANDIDATE, never an answer - a wrong ticker staples another company's share
price onto this fund's NAV - so every candidate is verified against
Investegate's own company page (H1 ticker must match, H1 name must be token
compatible with the registry name) using the crawler's own checks. Only
verified candidates are written to config/investegate_tickers.csv.

Nothing is written for a fund whose candidate fails verification. It stays
unresolved and is reported as such, because a wrong ticker is far worse
than a missing one.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, "src")

from cef_live.tickers import from_openfigi  # noqa: E402
from uk_cef.data_sources.investegate import (  # noqa: E402
    BASE, InvestegateCrawler, _tokens_compatible)

COV = Path("outputs/investegate_coverage.csv")
REG = Path("data/universe/registry.parquet")
OVERRIDES = Path("config/investegate_tickers.csv")
REPORT = Path("outputs/uk_ticker_reresolution.csv")
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/2s)")
THROTTLE = 2.0


def verify(ticker: str, names: list[str], s: requests.Session) -> tuple[bool, str]:
    """Does Investegate's company page for `ticker` name one of `names`?

    Reuses the crawler's H1 parse and token check so verification here and
    at crawl time cannot drift apart.
    """
    url = f"{BASE}/company/{ticker.upper()}?page=1"
    time.sleep(THROTTLE)
    try:
        r = s.get(url, timeout=45, headers={"User-Agent": UA})
    except Exception as exc:  # noqa: BLE001
        return False, f"fetch_failed:{type(exc).__name__}"
    if r.status_code != 200:
        return False, f"http_{r.status_code}"
    soup = BeautifulSoup(r.content, "html.parser")
    h1_name, h1_ticker = InvestegateCrawler._parse_h1(soup)
    if not h1_name:
        return False, "no_h1"
    if (h1_ticker or "").upper() != ticker.upper():
        # the generic market feed page, not a company page
        return False, f"not_a_company_page:h1={h1_name!r}"
    if not any(_tokens_compatible(h1_name, n) for n in names if n):
        return False, f"identity_mismatch:h1={h1_name!r}"
    return True, h1_name


def main() -> int:
    if not COV.exists() or not REG.exists():
        print("coverage or registry missing - run the UK crawl first")
        return 0
    cov = pd.read_csv(COV)
    reg = pd.read_parquet(REG)
    reg["security_id"] = reg["security_id"].astype(str)

    nf = cov[cov["status"] == "not_found"].copy()
    nf["security_id"] = nf["security_id"].astype(str)
    nf = nf.merge(reg[["security_id", "name", "status", "isin"]],
                  on="security_id", how="left", suffixes=("", "_reg"))
    # only the LIVE ones: a delisted fund having no page is expected, and
    # re-resolving dead tickers would spend requests for nothing
    live = nf[nf["status_reg"] == "live"].copy()
    live = live[live["isin"].notna()]
    print(f"{len(live)} live funds with an ISIN to re-resolve")
    if not len(live):
        return 0

    figi = from_openfigi(live[["isin"]])

    s = requests.Session()
    rows = []
    for _, r in live.iterrows():
        isin = str(r["isin"]).strip().upper()
        old = str(r["ticker"]).upper()
        cand = figi.get(isin)
        rec = {"security_id": r["security_id"], "fund": r["name"], "isin": isin,
               "old_ticker": old, "figi_ticker": None, "figi_name": None,
               "outcome": None, "detail": None}
        if not cand:
            rec["outcome"] = "no_figi_match"
            rows.append(rec)
            continue
        new, figi_name, _sec_type = cand
        new = str(new).upper()
        rec["figi_ticker"], rec["figi_name"] = new, figi_name
        names = [n for n in (r["name"], figi_name) if isinstance(n, str)]
        ok, detail = verify(new, names, s)
        rec["detail"] = detail
        if not ok:
            # unchanged AND unverifiable means the ticker was never the problem
            rec["outcome"] = "unverified_not_written"
        elif new == old:
            rec["outcome"] = "verified_same_ticker"
        else:
            rec["outcome"] = "verified_new_ticker"
        rows.append(rec)

    out = pd.DataFrame(rows)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(REPORT, index=False)

    # write ONLY verified changes into the override file
    write = out[out["outcome"] == "verified_new_ticker"]
    if len(write):
        today = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        add = pd.DataFrame({
            "security_id": write["security_id"],
            "ticker": write["figi_ticker"],
            "note": ["renamed; OpenFIGI ISIN join, Investegate H1 verified"] * len(write),
            "verified_by": ["openfigi+investegate_h1"] * len(write),
            "verified_date": [today] * len(write)})
        if OVERRIDES.exists():
            cur = pd.read_csv(OVERRIDES, comment="#")
            add = pd.concat([cur[~cur["security_id"].isin(add["security_id"])], add],
                            ignore_index=True)
        header = ("# Manually verified TIDMs for securities whose ticker is not in "
                  "any AIC file\n# (mostly trusts that died before the AIC added "
                  "ISIN/TIDM columns in 2019).\n")
        OVERRIDES.write_text(header + add.to_csv(index=False))
        print(f"wrote {len(write)} verified ticker override(s)")

    print("\n" + out[["fund", "old_ticker", "figi_ticker", "outcome"]].to_string(index=False))
    print("\n" + out["outcome"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
