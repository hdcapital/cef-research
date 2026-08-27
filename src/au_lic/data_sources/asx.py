"""ASX data source: monthly investment-products reports + announcements API.

Verified facts (data/probe/asx*, live site 2026-08):
- The monthly report page links every vintage directly in static HTML under
  /content/dam/asx/issuers/asx-investment-products-reports/{year}/excel/…,
  Jan 2017 -> present. Filenames vary by era (full month names early,
  'apr-2021-abs' style later), so URLs are harvested verbatim from the
  page, never constructed.
- Announcements are served by the same public API the ASX website calls:
  asx.api.markitdigital.com/asx-research/1.0/companies/{code}/announcements
  with items carrying announcementType ('DISTRIBUTION ANNOUNCEMENT',
  'PERIODIC REPORTS' incl. NTA statements), ISO date, documentKey and
  headline. Delisted codes may not resolve (400) - coverage is measured.

Downloads are throttled (~1 req/1.5s), cached, hashed and recorded in
data/au_manifest.csv.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
MONTHS.update({m[:3].lower(): i for m, i in
               [(k.capitalize(), v) for k, v in list(MONTHS.items())]})


@dataclass
class ReportFile:
    url: str
    local_name: str
    observation_month: str  # YYYY-MM


class ASXClient:
    def __init__(self, cfg: dict):
        d = cfg["download"]
        self.base = d["base_url"].rstrip("/")
        self.report_page = self.base + d["report_page"]
        self.api = d["announcements_api"].rstrip("/")
        self.raw_dir = Path(d["raw_dir"])
        self.cache_dir = Path(d["cache_dir"])
        self.manifest_path = Path(d["manifest"])
        self.throttle = float(d["throttle_seconds"])
        self.max_retries = int(d["max_retries"])
        self.backoff = float(d["retry_backoff_seconds"])
        self.session = requests.Session()
        self.session.headers["User-Agent"] = d["user_agent"]
        self._last = 0.0
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get(self, url: str, **kw) -> requests.Response:
        wait = self.throttle - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._last = time.time()
            try:
                resp = self.session.get(url, timeout=120, **kw)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(self.backoff * (2 ** attempt))
        raise RuntimeError(f"download failed after retries: {url}") from last_exc

    # -------------------------------------------------------------- reports
    def list_report_files(self, refresh: bool = False) -> list[ReportFile]:
        """Harvest every monthly-report XLSX href from the landing page."""
        cached = self.cache_dir / "report_page.html"
        if cached.exists() and not refresh:
            html = cached.read_text(errors="replace")
        else:
            resp = self._get(self.report_page)
            resp.raise_for_status()
            html = resp.text
            cached.write_text(html)
        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, ReportFile] = {}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "asx-investment-products-reports" not in href or not href.endswith(".xlsx"):
                continue
            # filename eras: 'january-2017', '201801' (numeric), 'apr-2021-abs',
            # 'jul_2021_abs' (underscores)
            name = Path(href).name.replace("_", "-")
            month = None
            m = re.search(r"asx-investment-products-([a-z]+)-(\d{4})", name)
            if m and m.group(1).lower() in MONTHS:
                month = f"{int(m.group(2)):04d}-{MONTHS[m.group(1).lower()]:02d}"
            else:
                m = re.search(r"asx-investment-products-(\d{4})(\d{2})", name)
                if m and 1 <= int(m.group(2)) <= 12:
                    month = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
            if month is None:
                log.warning("unrecognised report filename: %s", href)
                continue
            url = urljoin(self.base, href)
            out[month] = ReportFile(url=url, observation_month=month,
                                    local_name=f"{month}_ipr_{Path(href).name}")
        files = sorted(out.values(), key=lambda f: f.observation_month)
        log.info("report page: %d monthly XLSX vintages (%s .. %s)",
                 len(files), files[0].observation_month if files else "-",
                 files[-1].observation_month if files else "-")
        return files

    def download_reports(self, limit: int | None = None) -> dict[str, dict]:
        rows = self._load_manifest()
        done = 0
        try:
            for rf in self.list_report_files():
                dest = self.raw_dir / rf.local_name
                if dest.exists() and rows.get(rf.local_name, {}).get("status") == "ok":
                    continue
                if limit is not None and done >= limit:
                    break
                resp = self._get(rf.url)
                done += 1
                if resp.status_code != 200:
                    rows[rf.local_name] = self._row(rf, dest, f"http_{resp.status_code}")
                    continue
                dest.write_bytes(resp.content)
                rows[rf.local_name] = self._row(rf, dest, "ok")
        finally:
            self._save_manifest(rows)
        return rows

    # -------------------------------------------------------- announcements
    def announcements(self, code: str, **params) -> dict | None:
        url = f"{self.api}/companies/{code.lower()}/announcements"
        resp = self._get(url, params=params, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # ------------------------------------------------------------- manifest
    def _load_manifest(self) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    rows[row["file"]] = row
        return rows

    def _save_manifest(self, rows: dict[str, dict]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["file", "source", "date_downloaded", "observation_month",
                  "publication_type", "sha256", "bytes", "parser_version", "status"]
        with open(self.manifest_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for key in sorted(rows):
                w.writerow(rows[key])

    def _row(self, rf: ReportFile, dest: Path, status: str) -> dict:
        sha = size = ""
        if dest.exists() and status == "ok":
            content = dest.read_bytes()
            sha, size = hashlib.sha256(content).hexdigest(), str(len(content))
        return {"file": rf.local_name, "source": rf.url,
                "date_downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "observation_month": rf.observation_month,
                "publication_type": "investment_products_report",
                "sha256": sha, "bytes": size, "parser_version": "", "status": status}
