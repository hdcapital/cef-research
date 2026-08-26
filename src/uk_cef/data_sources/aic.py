"""AIC data-archive source adapter.

The AIC's Data Archive page (https://www.theaic.co.uk/research-tools/data-archive)
is a React app driven by a single public JSON manifest:

    /sites/default/files/data-archive/data-archive.json

Each manifest entry has: id, title, date, year, month, datestamp, type,
files[], postFiles[]. Observed type codes (verified against the live site,
2026-08):

    106  "AIC Stats"          monthly PDF (Monthly Statistics publication)
    107  "Corporate Activity" annual XLSX (one file per calendar year)
    108  "Keyfacts"           monthly PDF(s) + company-universe XLS/XLSX
    109  "MIR"                monthly CSV bundle (MIR + GEO/PC/WAR/CNV + errata)

Downloading is cached, hashed, throttled and resumable; nothing is ever
fetched twice once the cached copy's hash is recorded in data/manifest.csv.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TYPE_NAMES = {
    "106": "monthly_statistics",
    "107": "corporate_activity",
    "108": "keyfacts",
    "109": "mir",
}

MONTHS = {
    m: i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"],
        start=1,
    )
}


@dataclass
class ArchiveFile:
    url: str
    local_name: str
    publication_type: str
    observation_month: str  # YYYY-MM (December for annual corporate activity)
    entry_title: str
    entry_id: str


class AICClient:
    def __init__(self, cfg: dict):
        d = cfg["download"]
        self.base = d["base_url"].rstrip("/")
        self.manifest_url = self.base + "/sites/default/files/data-archive/data-archive.json"
        self.raw_dir = Path(d["raw_dir"])
        self.cache_dir = Path(d["cache_dir"])
        self.manifest_path = Path(d["manifest"])
        self.throttle = float(d["throttle_seconds"])
        self.max_retries = int(d["max_retries"])
        self.backoff = float(d["retry_backoff_seconds"])
        self.session = requests.Session()
        self.session.headers["User-Agent"] = d["user_agent"]
        self._last_request = 0.0
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ http
    def _get(self, url: str) -> requests.Response:
        wait = self.throttle - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self._last_request = time.time()
            try:
                resp = self.session.get(url, timeout=120)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                sleep = self.backoff * (2**attempt)
                log.warning("GET %s failed (%s); retry in %.1fs", url, exc, sleep)
                time.sleep(sleep)
        raise RuntimeError(f"download failed after {self.max_retries} retries: {url}") from last_exc

    # ------------------------------------------------------------- manifest
    def fetch_archive_manifest(self, refresh: bool = False) -> list[dict]:
        cached = self.cache_dir / "data-archive.json"
        if cached.exists() and not refresh:
            return json.loads(cached.read_text())
        resp = self._get(self.manifest_url)
        resp.raise_for_status()
        data = resp.json()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, indent=1))
        return data

    def list_files(self, manifest: list[dict] | None = None) -> list[ArchiveFile]:
        manifest = manifest or self.fetch_archive_manifest()
        out: list[ArchiveFile] = []
        for entry in manifest:
            ptype = TYPE_NAMES.get(str(entry.get("type")), f"unknown_{entry.get('type')}")
            month_num = MONTHS.get(entry.get("month"), 12)
            obs_month = f"{entry.get('year')}-{month_num:02d}"
            for path in list(entry.get("files") or []) + list(entry.get("postFiles") or []):
                url = path if path.startswith("http") else self.base + path
                name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
                # Prefix with observation month for uniqueness & readability
                local_name = f"{obs_month}_{ptype}_{name}"
                out.append(
                    ArchiveFile(
                        url=url,
                        local_name=local_name,
                        publication_type=ptype,
                        observation_month=obs_month,
                        entry_title=str(entry.get("title")),
                        entry_id=str(entry.get("id")),
                    )
                )
        return out

    # ------------------------------------------------------------- download
    def _load_download_manifest(self) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    rows[row["file"]] = row
        return rows

    def _save_download_manifest(self, rows: dict[str, dict]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "file", "source", "date_downloaded", "observation_month",
            "publication_type", "sha256", "bytes", "parser_version", "status",
        ]
        with open(self.manifest_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])

    def download_all(
        self,
        publication_types: tuple[str, ...] = ("mir", "keyfacts", "corporate_activity", "monthly_statistics"),
        limit: int | None = None,
        skip_patterns: tuple[str, ...] = (),
    ) -> dict[str, dict]:
        """Download every archive file of the requested types into raw_dir,
        skipping files already present with a recorded hash. Returns the
        updated manifest rows."""
        files = [f for f in self.list_files() if f.publication_type in publication_types]
        files = [f for f in files if not any(p in f.local_name for p in skip_patterns)]
        rows = self._load_download_manifest()
        done = 0
        try:
            for af in files:
                if limit is not None and done >= limit:
                    break
                dest = self.raw_dir / af.local_name
                existing = rows.get(af.local_name)
                if dest.exists() and existing and existing.get("status") == "ok":
                    continue
                try:
                    resp = self._get(af.url)
                except RuntimeError as exc:
                    rows[af.local_name] = self._row(af, dest, status=f"error: {exc}")
                    continue
                done += 1
                if resp.status_code != 200:
                    rows[af.local_name] = self._row(af, dest, status=f"http_{resp.status_code}")
                    continue
                dest.write_bytes(resp.content)
                rows[af.local_name] = self._row(af, dest, status="ok")
        finally:
            self._save_download_manifest(rows)
        return rows

    def _row(self, af: ArchiveFile, dest: Path, status: str) -> dict:
        sha = ""
        size = ""
        if dest.exists() and status == "ok":
            content = dest.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            size = str(len(content))
        return {
            "file": af.local_name,
            "source": af.url,
            "date_downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "observation_month": af.observation_month,
            "publication_type": af.publication_type,
            "sha256": sha,
            "bytes": size,
            "parser_version": "",
            "status": status,
        }


def build_inventory(client: AICClient, out_path: Path) -> None:
    """Stage 1 output: outputs/data_inventory.csv - one row per archive file
    with what we know about it (download/parse status filled in later)."""
    files = client.list_files()
    dl = client._load_download_manifest()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["observation_month", "publication_type", "source", "file_format",
             "download_success", "parser_success", "fields_available", "notes"]
        )
        for af in sorted(files, key=lambda f: (f.observation_month, f.publication_type, f.local_name)):
            fmt = Path(af.local_name).suffix.lstrip(".").lower() or "unknown"
            row = dl.get(af.local_name, {})
            writer.writerow(
                [
                    af.observation_month,
                    af.publication_type,
                    af.url,
                    fmt,
                    row.get("status", ""),
                    row.get("parser_version", ""),
                    "",
                    af.entry_title,
                ]
            )
