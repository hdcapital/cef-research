"""Tier 0 harvester: issuer-published high-frequency NAV/NTA announcements.

AU: the open market-wide announcement index (validation work) already
carries every fund's announcements with direct PDF links. The research
validation deliberately EXCLUDED weekly/daily NTA statements; Tier 0 is
exactly those plus any-day month-end statements. This module selects them,
fetches + parses the PDFs through the same battle-tested extraction and
scored label parser as the validation (scripts/sample_nta_pdfs.py), and
returns published NAV observations - real numbers with real dates,
never estimates.

UK: Investegate "Net Asset Value(s)" RNS announcements; the frequency
census runs off the existing crawler cache, value harvesting lands with
the incremental crawler extension (see docs/RUNBOOK.md).
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "nta_parse", Path(__file__).resolve().parents[2] / "scripts" / "sample_nta_pdfs.py")
P = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(P)

# Tier 0 headline: an NTA/NAV statement with an as-at date at ANY day of
# month, or an explicit daily/weekly update. Amendments still excluded.
BAD = re.compile(r"amendment|amended|correction|withdraw", re.I)


DOTTED_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d\d)\b")


def _asat_date(head: str) -> pd.Timestamp | None:
    m = P.ASAT.search(head or "")
    if m:
        try:
            return pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            return None
    # "NTA at 21.08.2026", "Daily Estimate NTA for 26.08.2026"
    m = DOTTED_DATE.search(head or "")
    if m:
        try:
            return pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def harvest_au(codes: set[str], lookback_days: int = 14,
               pdf_budget: int = 200) -> pd.DataFrame:
    """Published NAVs from recent AU NTA announcements.

    Returns DataFrame: security_id, nav_date, nav_value, unit, basis_note,
    source (announcement id), headline. Only successfully parsed,
    unambiguous values are returned - everything else is left absent.
    """
    import requests

    if not P.INDEX_F.exists():
        return pd.DataFrame(columns=["security_id", "nav_date", "nav_value",
                                     "unit", "basis_note", "source", "headline"])
    idx = pd.read_parquet(P.INDEX_F)
    idx = idx[idx["code"].isin(codes) & idx["url"].notna()]
    idx["release"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    idx = idx[idx["release"] >= cutoff]

    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    counters = {"pdf_calls": 0}
    rows = []
    for r in idx.sort_values("release", ascending=False).itertuples(index=False):
        head = r.headline or ""
        if not P.NTA_HEAD.search(head) or BAD.search(head):
            continue
        asat = _asat_date(head)
        if asat is None:
            # daily/weekly updates often carry no as-at in the headline;
            # use the release date as the observation date, labelled so
            if not re.search(r"daily|weekly|\bNTA\b|net tangible|\bNAV\b", head, re.I):
                continue
            asat = pd.Timestamp(r.release.date())
            date_src = "release_date"
        else:
            date_src = "as_at_headline"
        if counters["pdf_calls"] >= pdf_budget:
            break
        res = P.derive_stated(P.parse_pdf(s, str(r.id), r.url, counters))
        if res.get("status") != "parsed":
            continue
        val, unit = res["stated_raw"], res.get("unit")
        if unit == "ambiguous":
            continue                      # flagged, never guessed
        rows.append({"security_id": f"ASX:{r.code}",
                     "nav_date": asat.date().isoformat(),
                     "nav_value": val / 100.0 if unit == "cents" else val,
                     "unit": unit,
                     "basis_note": res.get("basis", "pre_tax") + f"|{date_src}",
                     "source": f"asx_ann:{r.id}", "headline": head[:120]})
    return pd.DataFrame(rows)


def uk_frequency_census(cache_dir: Path) -> pd.DataFrame:
    """Per-fund NAV-announcement publication frequency from the existing
    Investegate crawl cache (per-ticker listing CSVs - no new fetches).

    The crawler stores listings/{ticker}.csv with ann_id, date, headline
    (and url) for every announcement it has paged over. Counts 'Net Asset
    Value' titled rows per fund and classifies nav_frequency: daily /
    weekly / monthly / adhoc. Feeds the universe config; value harvesting
    follows via the same detail-page path the dividend crawler uses.
    """
    pat = re.compile(r"net asset value", re.I)
    listings = cache_dir / "listings"
    rows = []
    files = sorted(listings.glob("*.csv")) if listings.exists() else []
    for f in files:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if "headline" not in df.columns:
            continue
        nav = df[df["headline"].fillna("").str.contains(pat)]
        for r in nav.itertuples(index=False):
            if getattr(r, "date", None):
                rows.append({"ticker": f.stem, "date": r.date,
                             "ann_id": getattr(r, "ann_id", None)})
    if not rows:
        return pd.DataFrame(columns=["ticker", "n_navs", "first", "last",
                                     "per_month", "nav_frequency"])
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker", "ann_id"])
    g = df.groupby("ticker")["date"].agg(["count", "min", "max"]).reset_index()
    months = ((pd.to_datetime(g["max"]) - pd.to_datetime(g["min"])).dt.days / 30.4).clip(lower=1)
    g["per_month"] = g["count"] / months
    g["nav_frequency"] = pd.cut(g["per_month"], [-1, 0.5, 2.5, 12, 1e9],
                                labels=["adhoc", "monthly", "weekly", "daily"])
    return g.rename(columns={"count": "n_navs", "min": "first", "max": "last"})


# ---------------------------------------------------------------- UK Tier 0
# Patterns written against real RNS text (reports/build/uk_nav_samples.json):
#   "NAV per Share (including current financial year revenue items) 264.21p"
#   "Ordinary Share (including current year revenue) = 102.05p"
#   "unaudited net asset value ... per ordinary share ... was ... 264.21p"
# ZDP / preference-share lines are excluded; cum-income is primary and both
# variants are stored when published (brief §2 Tier 0).
UK_ASAT = re.compile(r"(?:as at|at)\s+(?:(?:the\s+)?close of business on\s+)?"
                     r"(\d{1,2}\s+\w{3,9}\s+\d{4})", re.I)
UK_PENCE = r"(?:=\s*)?([0-9]+(?:\.[0-9]+)?)\s*p(?:ence)?\b"
UK_INC = re.compile(r"\((?:including|incl\.?|cum)[^)]{0,60}(?:revenue|income)[^)]{0,20}\)"
                    r"[^0-9]{0,30}" + UK_PENCE, re.I)
UK_EXC = re.compile(r"\((?:excluding|excl\.?|ex)[^)]{0,60}(?:revenue|income)[^)]{0,20}\)"
                    r"[^0-9]{0,30}" + UK_PENCE, re.I)
# bare-label variants: "EX Income 439.95p", "Cum Income 443.57p"
UK_INC2 = re.compile(r"\b(?:cum|incl?\.?)[- ]income\b[^0-9]{0,25}" + UK_PENCE, re.I)
UK_EXC2 = re.compile(r"\bex[- ]income\b[^0-9]{0,25}" + UK_PENCE, re.I)
UK_PLAIN = re.compile(r"net asset value[^0-9]{0,220}?" + UK_PENCE, re.I)
UK_ZDP = re.compile(r"zero dividend|preference share", re.I)
# group announcements: "Net Asset Value(s) <Fund Name> <date> <manager>
# announces the unaudited net asset values ... of the following investment
# companies" - the fund's own value sits in a table naming it again
UK_HDR_NAME = re.compile(r"Net Asset Value\(s\)\s+(.{5,90}?)\s+\d{1,2}\s+\w{3,9}\s+20\d\d")


def parse_uk_nav_text(text: str) -> dict:
    """Cum/ex-income NAV per share (pence) from RNS text.

    ZDP / preference-share entitlements are excluded per match (a ZDP
    mention in the 90 chars before a candidate value disqualifies it),
    not by dropping text - RNS pages often put all share classes in one
    run-on line.
    """
    def _clean_hit(pat):
        for m in pat.finditer(text):
            if UK_ZDP.search(text[max(0, m.start() - 90):m.start()]):
                continue
            return float(m.group(1))
        return None

    # as-at date from the FULL text (a group notice's date sits in its
    # preamble, outside the fund-specific segment restricted to below)
    asat = None
    m = UK_ASAT.search(text)
    if m:
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True).date().isoformat()
        except Exception:  # noqa: BLE001
            pass

    # group announcements: restrict parsing to the segment after the SECOND
    # mention of the fund's own name (the first is the page header) so a
    # multi-fund abrdn/BlackRock notice yields THIS fund's value, not the
    # first row of somebody else's
    hdr = UK_HDR_NAME.search(text)
    if hdr:
        name = re.sub(r"\s+(plc|limited|ltd|trust)\.?$", "", hdr.group(1).strip(),
                      flags=re.I)
        key = name[:28]
        second = text.find(key, hdr.end())
        if second > -1:
            text = text[second:second + 600]

    out: dict = {}
    for pat in (UK_INC, UK_INC2):
        v = _clean_hit(pat)
        if v is not None:
            out["nav_cum_pence"] = v
            break
    for pat in (UK_EXC, UK_EXC2):
        v = _clean_hit(pat)
        if v is not None:
            out["nav_ex_pence"] = v
            break
    if "nav_cum_pence" not in out:
        v = _clean_hit(UK_PLAIN)
        if v is not None:
            out["nav_cum_pence"] = v
            out["cum_assumed"] = True
    if asat:
        out["asat"] = asat
    return out


def harvest_uk(ticker_map: pd.DataFrame, census: pd.DataFrame,
               lookback_days: int = 7, budget: int = 220) -> pd.DataFrame:
    """Published UK NAVs: refresh page 1 of each NAV publisher's listing,
    fetch the newest NAV announcement, parse cum/ex-income NAV.

    ticker_map: security_id<->ticker (verified TIDMs). census: from
    uk_frequency_census - only funds that actually publish NAVs are
    polled. Returns security_id, nav_date, nav_value (pence, cum-income
    primary), nav_ex, source, headline.
    """
    import requests
    from bs4 import BeautifulSoup
    import time as _t

    tick2sid = dict(zip(ticker_map["ticker"], ticker_map["security_id"]))
    unmapped = [t for t in census["ticker"] if t not in tick2sid]
    targets = [t for t in census["ticker"] if t in tick2sid][:budget]
    stats = {"targets": len(targets), "unmapped_tickers": len(unmapped),
             "listing_fail": 0, "no_recent_nav": 0, "detail_fail": 0,
             "parse_fail": 0, "parsed": 0, "fail_samples": []}
    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    pat = re.compile(r"net asset value", re.I)
    rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
    for tk in targets:
        _t.sleep(1.5)
        try:
            r = s.get(f"https://www.investegate.co.uk/company/{tk}", timeout=45)
            if r.status_code != 200:
                stats["listing_fail"] += 1
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:  # noqa: BLE001
            stats["listing_fail"] += 1
            continue
        best = None
        for tr in soup.select("table.table-investegate tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tds[3].find("a", href=True)
            if a is None or "/announcement/" not in a.get("href", ""):
                continue
            if not pat.search(a.get_text(" ", strip=True)):
                continue
            try:
                d = pd.to_datetime(tds[0].get_text(" ", strip=True),
                                   dayfirst=True).date().isoformat()
            except Exception:  # noqa: BLE001
                continue
            if d >= cutoff:
                best = {"date": d, "url": "https://www.investegate.co.uk" + a["href"]
                        if a["href"].startswith("/") else a["href"],
                        "headline": a.get_text(" ", strip=True)}
                break       # rows are newest-first
        if best is None:
            stats["no_recent_nav"] += 1
            continue
        _t.sleep(1.5)
        try:
            r = s.get(best["url"], timeout=45)
            text = re.sub(r"\s+", " ",
                          BeautifulSoup(r.text, "html.parser").get_text(" "))
        except Exception:  # noqa: BLE001
            stats["detail_fail"] += 1
            continue
        got = parse_uk_nav_text(text)
        if "nav_cum_pence" not in got:
            stats["parse_fail"] += 1
            if len(stats["fail_samples"]) < 8:
                stats["fail_samples"].append({"ticker": tk, "url": best["url"],
                                              "text_head": text[200:1600]})
            continue
        stats["parsed"] += 1
        rows.append({"security_id": tick2sid[tk],
                     "nav_date": got.get("asat", best["date"]),
                     "nav_value": got["nav_cum_pence"],
                     "nav_ex": got.get("nav_ex_pence"),
                     "cum_assumed": got.get("cum_assumed", False),
                     "source": f"investegate:{best['url'].rsplit('/', 1)[-1]}",
                     "headline": best["headline"][:120]})
    try:
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        import json as _json
        Path("reports/build/uk_tier0_debug.json").write_text(
            _json.dumps(stats, indent=1))
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame(rows)


def uk_nav_samples(cache_dir: Path, n: int = 5) -> list[dict]:
    """Fetch a handful of recent UK NAV announcement pages (throttled) and
    return their text heads - parser-design evidence, committed to
    reports/build so the value parser is written against real RNS text,
    never guessed."""
    import requests
    from bs4 import BeautifulSoup

    pat = re.compile(r"net asset value", re.I)
    listings = cache_dir / "listings"
    cands = []
    for f in sorted(listings.glob("*.csv")) if listings.exists() else []:
        try:
            df = pd.read_csv(f, dtype=str)
        except Exception:  # noqa: BLE001
            continue
        if not {"headline", "url", "date"} <= set(df.columns):
            continue
        nav = df[df["headline"].fillna("").str.contains(pat) & df["url"].notna()]
        for r in nav.itertuples(index=False):
            cands.append({"ticker": f.stem, "date": r.date, "url": r.url})
    cands.sort(key=lambda c: str(c["date"]), reverse=True)
    # one per ticker, most recent first, for layout diversity
    seen, picked = set(), []
    for c in cands:
        if c["ticker"] in seen:
            continue
        seen.add(c["ticker"])
        picked.append(c)
        if len(picked) >= n:
            break
    s = requests.Session()
    s.headers["User-Agent"] = P.UA
    out = []
    import time as _t
    for c in picked:
        _t.sleep(1.5)
        url = c["url"]
        if url.startswith("/"):
            url = "https://www.investegate.co.uk" + url
        try:
            r = s.get(url, timeout=45)
            text = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" "))
            out.append({**c, "status": r.status_code, "text_head": text[:2500]})
        except Exception as exc:  # noqa: BLE001
            out.append({**c, "status": f"error:{exc}"})
    return out
