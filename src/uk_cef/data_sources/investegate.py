"""Investegate RNS crawler: dividends + announcement-dated catalysts.

Purpose: recover, from a free public archive, the two things the AIC files
cannot provide - (1) per-share dividend histories with real ex-dates, so
genuine total returns can be computed, and (2) corporate-action
announcement dates, so catalysts can be tested prospectively.

Verified against the live site (see data/probe/investegate*/):
- /company/<TIDM> lists a company's announcements (55/page, ?page=N),
  including DELISTED companies under their final ticker (e.g. ADIG);
  an unknown ticker silently falls back to the market-wide feed, and
  tickers get reused (AIC = Achilles), so every company is identity-checked
  against the page H1 ("<Name> (<TICKER>) RNS Announcements") before
  anything is stored. Mismatches are recorded, never guessed around.
- rows live in table.table-investegate (Date | Time | Source | headline+link);
  page footers leak other companies' announcements, so only links whose
  slug matches the company's own slug are harvested.
- robots.txt allows /company/ and /announcement/ (search endpoints are
  disallowed and are not used).

The crawl is resumable: state and per-company CSVs live under
data/cache/investegate/ (persisted via the CI cache between nightly runs),
throttled at ~1 req/1.3s, with a wall-clock budget per run.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..entities import normalize_name
from ..parsers.dividends import classify_headline, parse_dividend_announcement

log = logging.getLogger(__name__)

BASE = "https://www.investegate.co.uk"
UA = ("uk-cef-research/0.1 (academic CEF dividend/catalyst research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.3s; resumable nightly crawl)")
THROTTLE = 1.3
CUTOFF_DATE = "2006-06-01"   # stop paging once a page is entirely older
MAX_PAGES_PER_COMPANY = 400

H1_RE = re.compile(r"^(.*?)\s*\(([A-Z0-9.]{2,8})\)\s+RNS\s+Announcements", re.I)


def _tokens_compatible(a: str, b: str) -> bool:
    """Lenient name equivalence tolerating abbreviations ('inv'~'investment',
    'tst'~'trust') and dropped suffix words. At least 60% of the shorter
    name's tokens must prefix-match tokens of the other, in order."""
    ta, tb = normalize_name(a).split(), normalize_name(b).split()
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    j = 0
    hits = 0
    for tok in short:
        while j < len(long_):
            other = long_[j]
            j += 1
            if tok.startswith(other) or other.startswith(tok):
                hits += 1
                break
    return hits >= max(1, int(0.6 * len(short)))


class InvestegateCrawler:
    def __init__(self, cache_dir: str | Path = "data/cache/investegate",
                 budget_minutes: float = 250.0):
        self.cache = Path(cache_dir)
        self.listings = self.cache / "listings"
        self.details = self.cache / "details"
        for d in (self.cache, self.listings, self.details):
            d.mkdir(parents=True, exist_ok=True)
        self.state_path = self.cache / "state.json"
        self.state: dict = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.deadline = time.time() + budget_minutes * 60
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self._last = 0.0
        self.page_len_param: str | None = self.state.get("__page_len_param")
        self.requests_made = 0

    # ------------------------------------------------------------------ http
    def _fetch(self, url: str) -> requests.Response | None:
        wait = THROTTLE - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self.requests_made += 1
        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=45)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(10 * (attempt + 1))
                    continue
                return r
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch %s failed (%s)", url, exc)
                time.sleep(5 * (attempt + 1))
        return None

    def _save_state(self) -> None:
        if self.page_len_param is not None:
            self.state["__page_len_param"] = self.page_len_param
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def _out_of_time(self) -> bool:
        return time.time() > self.deadline

    # ------------------------------------------------------------- list page
    def _detect_page_len(self, ticker: str) -> None:
        """One-off: try server-side page-size params; keep whichever works."""
        if self.page_len_param is not None:
            return
        for param in ("length=300", "entries=300", "per_page=300", "show=300"):
            r = self._fetch(f"{BASE}/company/{ticker}?page=1&{param}")
            if r is None or r.status_code != 200:
                continue
            n = len(BeautifulSoup(r.content, "html.parser").select("table.table-investegate tbody tr"))
            log.info("page-size probe %s -> %d rows", param, n)
            if n > 100:
                self.page_len_param = param
                self._save_state()
                return
        self.page_len_param = ""  # server ignores size params; 55/page
        self._save_state()

    def _company_url(self, ticker: str, page: int) -> str:
        url = f"{BASE}/company/{ticker}?page={page}"
        if self.page_len_param:
            url += f"&{self.page_len_param}"
        return url

    @staticmethod
    def _parse_h1(soup: BeautifulSoup):
        h1 = soup.find("h1")
        if not h1:
            return None, None
        m = H1_RE.match(h1.get_text(" ", strip=True))
        return (m.group(1), m.group(2).upper()) if m else (h1.get_text(" ", strip=True), None)

    @staticmethod
    def _parse_rows(soup: BeautifulSoup) -> list[dict]:
        rows = []
        for tr in soup.select("table.table-investegate tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tds[3].find("a", href=True)
            if a is None or "/announcement/" not in a["href"]:
                continue
            url = urljoin(BASE, a["href"])
            parts = [p for p in urlparse(url).path.split("/") if p]
            slug = parts[2] if len(parts) >= 3 else ""
            ann_id = parts[-1]
            date_txt = tds[0].get_text(" ", strip=True)
            try:
                date_iso = datetime.strptime(date_txt, "%d %b %Y").date().isoformat()
            except ValueError:
                date_iso = None
            rows.append(
                {"ann_id": ann_id, "date": date_iso, "time": tds[1].get_text(strip=True),
                 "source": tds[2].get_text(strip=True), "headline": a.get_text(" ", strip=True),
                 "slug": slug, "url": url}
            )
        return rows

    # ----------------------------------------------------------- per company
    def crawl_company(self, security_id: str, ticker: str, names: list[str]) -> str:
        st = self.state.setdefault(ticker, {"security_id": security_id, "status": "pending",
                                            "pages_done": 0, "oldest_date": None})
        if st["status"] in ("done", "identity_mismatch", "not_found"):
            return st["status"]
        self._detect_page_len(ticker)
        listing_path = self.listings / f"{ticker}.csv"
        fields = ["ann_id", "date", "time", "source", "headline", "slug", "url", "category"]

        own_slug = st.get("own_slug")
        while st["status"] in ("pending", "listing"):
            if self._out_of_time():
                self._save_state()
                return "budget_exhausted"
            page = st["pages_done"] + 1
            if page > MAX_PAGES_PER_COMPANY:
                st["status"] = "details"
                break
            r = self._fetch(self._company_url(ticker, page))
            if r is None or r.status_code != 200:
                st["status"] = "not_found" if page == 1 else "details"
                break
            soup = BeautifulSoup(r.content, "html.parser")
            if page == 1:
                h1_name, h1_ticker = self._parse_h1(soup)
                if not h1_name or (h1_ticker or "").upper() != ticker.upper():
                    st["status"] = "not_found"  # fell back to market feed page
                    st["h1"] = h1_name
                    log.info("%s: no dedicated company page (h1=%r)", ticker, h1_name)
                    break
                if not any(_tokens_compatible(h1_name, n) for n in names):
                    st["status"] = "identity_mismatch"
                    st["h1"] = h1_name
                    log.warning("%s: identity mismatch: page says %r, expected one of %r",
                                ticker, h1_name, names[:3])
                    break
                st["h1"] = h1_name
                st["status"] = "listing"
            rows = self._parse_rows(soup)
            if own_slug is None and rows:
                # the company's own slug = the modal slug on its page 1
                slugs = pd.Series([r_["slug"] for r_ in rows])
                own_slug = slugs.mode().iloc[0]
                st["own_slug"] = own_slug
            own_prefix = (own_slug or "").rsplit("--", 1)[0]
            own_rows = [
                r_ for r_ in rows
                if r_["slug"] == own_slug
                or (r_["slug"].rsplit("--", 1)[-1].upper() == ticker.upper())
                or (own_prefix and r_["slug"].startswith(own_prefix))
            ]
            for r_ in own_rows:
                r_["category"] = classify_headline(r_["headline"]) or ""
            write_header = not listing_path.exists()
            with open(listing_path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                if write_header:
                    w.writeheader()
                w.writerows(own_rows)
            dates = [r_["date"] for r_ in own_rows if r_["date"]]
            if dates:
                st["oldest_date"] = min(dates)
            st["pages_done"] = page
            self._save_state()
            if not rows or (dates and max(dates) < CUTOFF_DATE):
                st["status"] = "details"
        # ------------------------------------------------------- detail phase
        if st["status"] == "details":
            done = st.setdefault("details_done", [])
            done_set = set(done)
            if listing_path.exists():
                listing = pd.read_csv(listing_path, dtype=str).drop_duplicates("ann_id")
                todo = listing[listing["category"].isin(["dividend", "catalyst"])]
                detail_path = self.details / f"{ticker}.csv"
                dfields = ["ann_id", "date", "category", "headline", "url", "title",
                           "amount", "unit", "currency", "amount_gbx", "ex_date", "pay_date",
                           "record_date", "special", "period", "confidence", "body_excerpt"]
                for _, row in todo.iterrows():
                    if row["ann_id"] in done_set:
                        continue
                    if self._out_of_time():
                        st["details_done"] = sorted(done_set)
                        self._save_state()
                        return "budget_exhausted"
                    r = self._fetch(row["url"])
                    rec = {k: None for k in dfields}
                    rec.update({"ann_id": row["ann_id"], "date": row["date"],
                                "category": row["category"], "headline": row["headline"],
                                "url": row["url"]})
                    if r is not None and r.status_code == 200:
                        soup = BeautifulSoup(r.content, "html.parser")
                        title = soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else ""
                        body = " ".join(
                            t.get_text(" ", strip=True) for t in soup.find_all(["p", "td", "li"])
                        )
                        rec["title"] = title[:300]
                        rec["body_excerpt"] = body[:600]
                        if row["category"] == "dividend":
                            parsed = parse_dividend_announcement(title, body)
                            if parsed:
                                rec.update(parsed)
                    write_header = not detail_path.exists()
                    with open(detail_path, "a", newline="", encoding="utf-8") as fh:
                        w = csv.DictWriter(fh, fieldnames=dfields, extrasaction="ignore")
                        if write_header:
                            w.writeheader()
                        w.writerow(rec)
                    done_set.add(row["ann_id"])
                    if len(done_set) % 25 == 0:
                        st["details_done"] = sorted(done_set)
                        self._save_state()
            st["details_done"] = sorted(done_set)
            st["status"] = "done"
            self._save_state()
        return st["status"]

    # ------------------------------------------------------------- aggregate
    def build_outputs(self, processed_dir: str | Path, ticker_map: pd.DataFrame) -> None:
        """Aggregate per-ticker detail CSVs into dividends.parquet and
        catalysts_announced.parquet keyed by security_id."""
        processed = Path(processed_dir)
        processed.mkdir(parents=True, exist_ok=True)
        sid_by_ticker = dict(zip(ticker_map["ticker"], ticker_map["security_id"]))
        div_rows, cat_rows = [], []
        for path in sorted(self.details.glob("*.csv")):
            ticker = path.stem
            sid = sid_by_ticker.get(ticker)
            if sid is None:
                continue
            df = pd.read_csv(path, dtype=str)
            df["security_id"] = sid
            df["ticker"] = ticker
            div_rows.append(df[df["category"] == "dividend"])
            cat_rows.append(df[df["category"] == "catalyst"])
        if div_rows:
            div = pd.concat(div_rows, ignore_index=True).drop_duplicates(["ticker", "ann_id"])
            for c in ("amount", "amount_gbx"):
                div[c] = pd.to_numeric(div[c], errors="coerce")
            div.to_parquet(processed / "dividends.parquet", index=False)
            log.info("dividends.parquet: %d rows, %d securities",
                     len(div), div["security_id"].nunique())
        if cat_rows:
            cat = pd.concat(cat_rows, ignore_index=True).drop_duplicates(["ticker", "ann_id"])
            cat.to_parquet(processed / "catalysts_announced.parquet", index=False)
            log.info("catalysts_announced.parquet: %d rows", len(cat))

    def coverage_summary(self) -> pd.DataFrame:
        rows = []
        for ticker, st in self.state.items():
            if ticker.startswith("__"):
                continue
            rows.append({"ticker": ticker, "security_id": st.get("security_id"),
                         "status": st.get("status"), "pages_done": st.get("pages_done"),
                         "oldest_date": st.get("oldest_date"),
                         "n_details": len(st.get("details_done", [])),
                         "h1": st.get("h1")})
        return pd.DataFrame(rows)


def build_ticker_map(cfg: dict) -> pd.DataFrame:
    """(security_id, ticker, names) for the eligible universe, from the panel's
    merged AIC identifiers plus config/investegate_tickers.csv overrides
    (manually verified TIDMs for pre-2019 dead trusts)."""
    from ..panel import load_panel

    panel = load_panel(cfg)
    elig = panel[panel["eligible"]]
    rows = []
    for sid, g in elig.groupby("security_id"):
        names = sorted(set(g["company_name"].dropna()))
        ticker = None
        if "ticker" in g.columns:
            tick = g["ticker"].dropna()
            if len(tick):
                ticker = str(tick.iloc[-1]).strip().upper()
        rows.append({"security_id": sid, "ticker": ticker, "names": names,
                     "first_month": g["obs_month"].min(), "last_month": g["obs_month"].max()})
    out = pd.DataFrame(rows)

    overrides = Path(cfg["paths"].get("investegate_tickers", "config/investegate_tickers.csv"))
    if overrides.exists():
        ov = pd.read_csv(overrides, comment="#")
        ov_map = dict(zip(ov["security_id"], ov["ticker"].str.upper()))
        out["ticker"] = out.apply(
            lambda r: ov_map.get(r["security_id"], r["ticker"]), axis=1
        )
    return out
