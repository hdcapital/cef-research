"""ASX announcements crawler (legacy per-company/per-year listings) and the
cross-validation of the monthly-report panel against them.

Source: /asx/v2/statistics/announcements.do?by=asxCode&asxCode={code}
        &timeframe=Y&year={year}  (verified live; ~40-200 rows per
        company-year; each row: timestamp, headline, displayAnnouncement
        PDF link). Delisted codes may return empty pages - measured.

Validation performed (no PDFs needed):
1. NTA coverage - LICs must publish monthly NTA statements; headlines carry
   the as-at date ('NTA ... as at 31 October 2020'). Every panel fund-month
   is checked for a matching NTA announcement in/after that month.
2. Dividend events - 'Dividend/Distribution' headlines give announcement
   dates; months where the panel's total return exceeds its price return
   (a distribution was paid) should have a recent dividend announcement.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://www.asx.com.au"
LIST_URL = BASE + "/asx/v2/statistics/announcements.do"
THROTTLE = 1.5

NTA_RE = re.compile(r"\bNTA\b|net tangible asset", re.I)
DIV_RE = re.compile(r"dividend|distribution", re.I)
ASAT_RE = re.compile(
    r"as at\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)[a-z]*\s+(\d{4})", re.I)

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
PDF_RE = re.compile(r"displayAnnouncement\.do\?display=pdf&(?:amp;)?idsId=(\d+)")


class AnnouncementsCrawler:
    def __init__(self, cfg: dict, cache_dir: str | Path = "data/asx_ann_cache",
                 budget_minutes: float = 70.0):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.state_path = self.cache / "state.json"
        self.state: dict = (json.loads(self.state_path.read_text())
                            if self.state_path.exists() else {})
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg["download"]["user_agent"]
        self.deadline = time.time() + budget_minutes * 60
        self._last = 0.0
        self.requests_made = 0

    def _fetch(self, params: dict) -> str | None:
        wait = THROTTLE - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self.requests_made += 1
        for attempt in range(3):
            try:
                r = self.session.get(LIST_URL, params=params, timeout=60)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(8 * (attempt + 1))
                    continue
                return r.text if r.status_code == 200 else None
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch failed (%s)", exc)
                time.sleep(5)
        return None

    @staticmethod
    def _parse(html: str, code: str, year: int) -> list[dict]:
        out = []
        for row in ROW_RE.findall(html):
            cells = CELL_RE.findall(row)
            if len(cells) < 3:
                continue
            date_txt = re.sub(r"<[^>]+>|\s+", " ", cells[0]).strip()
            m = re.match(r"(\d{2}/\d{2}/\d{4})", date_txt)
            if not m:
                continue
            head_raw = cells[-1]
            headline = re.sub(r"<[^>]+>", " ", head_raw)
            headline = re.sub(r"\s+", " ", headline)
            headline = re.sub(r"\d+ pages?\s+[\d.]+\s*[KM]B", "", headline).strip()
            pdf = PDF_RE.search(head_raw)
            date_iso = pd.to_datetime(m.group(1), format="%d/%m/%Y").date().isoformat()
            kind = "nta" if NTA_RE.search(headline) else (
                "dividend" if DIV_RE.search(headline) else "other")
            asat = None
            am = ASAT_RE.search(headline)
            if am:
                try:
                    asat = pd.to_datetime(
                        f"{am.group(1)} {am.group(2)} {am.group(3)}").date().isoformat()
                except ValueError:
                    pass
            out.append({"code": code, "year": year, "date": date_iso,
                        "headline": headline[:200], "kind": kind,
                        "asat_date": asat, "pdf_id": pdf.group(1) if pdf else None})
        return out

    def crawl(self, codes: list[str], years: range) -> pd.DataFrame:
        rows_path = self.cache / "announcements.csv"
        existing = pd.read_csv(rows_path) if rows_path.exists() else pd.DataFrame()
        new_rows: list[dict] = []
        exhausted = False
        for code in codes:
            for year in years:
                key = f"{code}:{year}"
                if self.state.get(key):
                    continue
                if time.time() > self.deadline:
                    exhausted = True
                    break
                html = self._fetch({"by": "asxCode", "asxCode": code,
                                    "timeframe": "Y", "year": year})
                if html is None:
                    self.state[key] = "error"
                    continue
                rows = self._parse(html, code, year)
                new_rows.extend(rows)
                self.state[key] = f"ok:{len(rows)}"
                if len(new_rows) % 500 < len(rows):
                    self._checkpoint(existing, new_rows, rows_path)
            if exhausted:
                break
        df = self._checkpoint(existing, new_rows, rows_path)
        log.info("announcements: %d requests this run, %d rows total, exhausted=%s",
                 self.requests_made, len(df), exhausted)
        return df

    def _checkpoint(self, existing: pd.DataFrame, new_rows: list[dict],
                    rows_path: Path) -> pd.DataFrame:
        df = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True) \
            if new_rows else existing
        if not df.empty:
            df = df.drop_duplicates(subset=["code", "date", "headline"])
            df.to_csv(rows_path, index=False)
        self.state_path.write_text(json.dumps(self.state, indent=1))
        return df


def validate_against_panel(panel: pd.DataFrame, ann: pd.DataFrame,
                           out_dir: Path) -> None:
    """Cross-checks (existence-level, PDF-free)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if ann.empty:
        log.warning("no announcements crawled yet")
        return
    ann = ann.copy()
    ann["month"] = pd.to_datetime(ann["date"]).dt.to_period("M").astype(str)
    ann["asat_month"] = pd.to_datetime(ann["asat_date"], errors="coerce") \
        .dt.to_period("M").astype(str)

    # 1. NTA statement coverage per panel fund-month: an NTA announcement
    # whose as-at month equals the observation month (or published within
    # the following month) should exist for every LIC row.
    nta = ann[ann["kind"] == "nta"]
    nta_keys = set(zip(nta["code"], nta["asat_month"].fillna("")))
    nta_pub_keys = set(zip(nta["code"], nta["month"]))
    elig = panel[panel["eligible"]].copy()
    elig["code"] = elig["security_id"].str.replace("ASX:", "", regex=False)
    next_month = (pd.PeriodIndex(elig["obs_month"], freq="M") + 1).astype(str)
    elig["nta_announced"] = [
        (c, m) in nta_keys or (c, nm) in nta_pub_keys
        for c, m, nm in zip(elig["code"], elig["obs_month"], next_month)
    ]
    cov = (elig.groupby("code")
           .agg(months=("obs_month", "size"), announced=("nta_announced", "sum"))
           .assign(coverage=lambda d: (d["announced"] / d["months"]).round(3))
           .sort_values("coverage"))
    cov.to_csv(out_dir / "au_nta_announcement_coverage.csv")

    # 2. dividend events vs TR-price gaps: months where TR - price return
    # > 1% (a distribution) should show a dividend announcement within the
    # prior 3 months
    div = ann[ann["kind"] == "dividend"]
    div_keys = set(zip(div["code"], div["month"]))
    gaps = elig[(elig["tr_minus_price"] > 0.01)].copy()
    def has_recent_div(code, month):
        p = pd.Period(month, freq="M")
        return any((code, str(p - k)) in div_keys for k in range(0, 4))
    gaps["div_announced"] = [has_recent_div(c, m)
                             for c, m in zip(gaps["code"], gaps["obs_month"])]
    summary = {
        "panel_fund_months": int(len(elig)),
        "nta_coverage_overall": float(elig["nta_announced"].mean()),
        "distribution_months_checked": int(len(gaps)),
        "distribution_months_with_announcement": float(gaps["div_announced"].mean())
        if len(gaps) else None,
        "announcement_rows": int(len(ann)),
        "codes_covered": int(ann["code"].nunique()),
    }
    (out_dir / "au_validation_summary.json").write_text(json.dumps(summary, indent=2))
    div.to_csv(out_dir / "au_dividend_announcements.csv", index=False)
    log.info("validation: %s", summary)
