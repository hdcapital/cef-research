"""Probe the AIC data-archive pages to learn their real structure.

This is a bootstrap/reconnaissance script, run from GitHub Actions (the
development sandbox has no network access to theaic.co.uk). It fetches the
data-archive listing pages, saves the raw HTML, extracts every link, and
downloads a small sample of linked files so parsers can be written against
the *actual* formats rather than assumed ones.

Results are written under data/probe/ and committed back to the working
branch by the workflow so they can be inspected offline.

It is deliberately gentle: ~1 request/second, small page budget, and it
checks robots.txt first and records (and respects) any disallow rules that
apply to the paths we touch.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.theaic.co.uk"
ARCHIVE_PATH = "/research-tools/data-archive"
OUT = Path("data/probe")
UA = (
    "uk-cef-research/0.1 (open-source academic backtest research; "
    "contact: danielconorsims@gmail.com; respects robots.txt; ~1 req/s)"
)
THROTTLE_SECONDS = 1.2
PAGE_BUDGET = 12          # listing pages to fetch in this probe
SAMPLE_FILE_BUDGET = 12   # linked data files to sample
SAMPLE_MAX_BYTES = 8_000_000

session = requests.Session()
session.headers["User-Agent"] = UA

_last_request = 0.0


def fetch(url: str, **kw) -> requests.Response:
    global _last_request
    wait = THROTTLE_SECONDS - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    resp = session.get(url, timeout=60, **kw)
    print(f"GET {url} -> {resp.status_code} ({len(resp.content)} bytes)")
    return resp


def robots_disallows() -> list[str]:
    try:
        r = fetch(BASE + "/robots.txt")
        (OUT / "robots.txt").write_bytes(r.content)
        rules, active = [], False
        for line in r.text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                active = val == "*"
            elif key == "disallow" and active and val:
                rules.append(val)
        return rules
    except Exception as exc:  # noqa: BLE001
        print(f"robots.txt fetch failed: {exc}")
        return []


def allowed(url: str, rules: list[str]) -> bool:
    path = urllib.parse.urlparse(url).path
    return not any(path.startswith(rule) for rule in rules)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rules = robots_disallows()
    print(f"robots.txt disallow rules for *: {rules}")

    notes: dict[str, object] = {"robots_disallow": rules, "pages": []}

    # --- 1. listing pages -------------------------------------------------
    start = BASE + ARCHIVE_PATH
    if not allowed(start, rules):
        print(f"BLOCKED BY ROBOTS: {start}")
        notes["archive_blocked_by_robots"] = True
        (OUT / "probe_notes.json").write_text(json.dumps(notes, indent=2))
        return 0

    seen_pages: set[str] = set()
    queue: list[str] = [start]
    all_links: list[dict[str, str]] = []
    pages_fetched = 0

    while queue and pages_fetched < PAGE_BUDGET:
        url = queue.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            resp = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"fetch failed: {exc}")
            continue
        pages_fetched += 1
        slug = re.sub(r"\W+", "_", url.replace(BASE, "")).strip("_") or "root"
        (OUT / f"page_{pages_fetched:02d}_{slug[:80]}.html").write_bytes(resp.content)
        notes["pages"].append({"url": url, "status": resp.status_code})
        if resp.status_code != 200:
            continue

        soup = BeautifulSoup(resp.content, "html.parser")

        # record forms (year/type filters are likely a form)
        for form in soup.find_all("form"):
            controls = []
            for sel in form.find_all(["select", "input"]):
                opts = [o.get("value") for o in sel.find_all("option")][:40]
                controls.append(
                    {
                        "tag": sel.name,
                        "name": sel.get("name"),
                        "type": sel.get("type"),
                        "options_sample": opts,
                    }
                )
            notes.setdefault("forms", []).append(
                {
                    "page": url,
                    "action": form.get("action"),
                    "method": form.get("method"),
                    "controls": controls,
                }
            )

        for a in soup.find_all("a", href=True):
            href = urllib.parse.urljoin(url, a["href"])
            text = " ".join(a.get_text(" ", strip=True).split())[:200]
            all_links.append({"page": url, "href": href, "text": text})
            # follow pagination within the archive listing only
            parsed = urllib.parse.urlparse(href)
            if (
                parsed.netloc.endswith("theaic.co.uk")
                and ARCHIVE_PATH in parsed.path
                and href not in seen_pages
                and ("page=" in (parsed.query or "") or parsed.query)
            ):
                queue.append(href)

    with (OUT / "links.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["page", "href", "text"])
        writer.writeheader()
        writer.writerows(all_links)
    print(f"captured {len(all_links)} links from {pages_fetched} pages")

    # --- 2. sample linked files ------------------------------------------
    file_ext = re.compile(r"\.(xlsx?|pdf|csv|zip|docx?)($|\?)", re.I)
    samples = []
    seen_files: set[str] = set()
    for link in all_links:
        href = link["href"]
        if href in seen_files or not file_ext.search(href):
            continue
        if not urllib.parse.urlparse(href).netloc.endswith("theaic.co.uk"):
            continue
        if not allowed(href, rules):
            samples.append({"href": href, "status": "blocked_by_robots"})
            continue
        seen_files.add(href)
        if len(seen_files) > SAMPLE_FILE_BUDGET:
            break
        try:
            resp = fetch(href, stream=True)
            content = b""
            for chunk in resp.iter_content(65536):
                content += chunk
                if len(content) > SAMPLE_MAX_BYTES:
                    break
            name = Path(urllib.parse.urlparse(href).path).name or "unnamed"
            (OUT / "samples").mkdir(exist_ok=True)
            (OUT / "samples" / name).write_bytes(content)
            samples.append(
                {
                    "href": href,
                    "status": resp.status_code,
                    "bytes": len(content),
                    "content_type": resp.headers.get("Content-Type"),
                    "saved_as": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "link_text": link["text"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            samples.append({"href": href, "status": f"error: {exc}"})

    notes["samples"] = samples
    (OUT / "probe_notes.json").write_text(json.dumps(notes, indent=2))
    print("probe complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
