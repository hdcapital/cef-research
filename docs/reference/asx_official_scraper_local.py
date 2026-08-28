#!/usr/bin/env python3
"""
ASX Harvester — HTTP download via Playwright cookies
SURGICAL MOD: Added Content Hashing & S3 Deduping for 10am/5pm split.

NEW (minor but important):
- Pre-flight HEAD check to skip downloading PDFs that are already in S3,
  but ONLY when we're certain (source ETag looks like MD5 AND S3 ETag matches).
- Collision-safe upload: if a rare filename hash-prefix collision occurs, upload under a full-MD5 key.

SPEED MOD (Playwright phase untouched):
- Download phase now runs on a thread pool (ASX_DL_WORKERS, default 8;
  set to 1 to restore fully sequential behaviour).
- Per-thread requests.Session with connection pooling: TLS handshakes happen
  once per worker instead of once per request.
- Uploads go straight from memory (no disk write/read/delete round trip, and
  no OneDrive sync interference); ASX_SAVE_LOCAL=1 restores local copies.
- Limited GET retries (ASX_DL_RETRIES, default 2) so a transient network blip
  never silently loses a PDF.
- Identical URLs appearing on both announcement pages are fetched once.
Filenames, dedup logic, collision handling, skip conditions and the 15KB
upload threshold are unchanged.
"""

from __future__ import annotations

import os
import re
import threading
import time
import requests
import boto3
import hashlib  # For digital fingerprinting
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

load_dotenv()

ASX_ANNOUNCEMENT_URLS = [
    "https://www.asx.com.au/asx/v2/statistics/todayAnns.do",
    "https://www.asx.com.au/asx/v2/statistics/prevBusDayAnns.do",
]
BASE_DOMAIN = "https://www.asx.com.au"
STORAGE_STATE_PATH = "asx_storage.json"
DOWNLOAD_DIR = "downloads"

AWS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_BUCKET = os.environ.get("AWS_BUCKET_NAME")

# Download-phase performance knobs. The Playwright phase is untouched.
# Workers are deliberately modest by default to stay polite to asx.com.au;
# raise gradually (e.g. 12) if runs stay clean, drop to 1 to restore fully
# sequential behaviour.
DL_WORKERS = max(1, int(os.environ.get("ASX_DL_WORKERS", "8")))
DL_RETRIES = max(0, int(os.environ.get("ASX_DL_RETRIES", "2")))
SAVE_LOCAL = os.environ.get("ASX_SAVE_LOCAL", "") == "1"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Per-thread HTTP sessions (connection reuse) and orderly console output.
_thread_local = threading.local()
_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        print(message)


def _get_session(cookies_dict: dict) -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.cookies.update(cookies_dict)
        _thread_local.session = session
    return session

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

_ETAG_MD5_RE = re.compile(r"^[a-f0-9]{32}$", re.IGNORECASE)


def _md5_from_etag(etag_value: str) -> Optional[str]:
    """Return a 32-hex MD5 string if the ETag looks like an MD5, else None."""
    if not etag_value:
        return None
    et = str(etag_value).strip()
    if et.startswith("W/"):
        et = et[2:].strip()
    et = et.strip('"').strip()
    if _ETAG_MD5_RE.fullmatch(et):
        return et.lower()
    return None


def _s3_object_matches_md5(s3_client, key: str, source_md5: str, expected_size: Optional[int] = None) -> bool:
    """True only if object exists and S3 ETag (MD5) matches source_md5 (and optional size matches)."""
    try:
        meta = s3_client.head_object(Bucket=AWS_BUCKET, Key=key)
        if expected_size is not None and meta.get("ContentLength") != expected_size:
            return False
        s3_md5 = _md5_from_etag(meta.get("ETag", ""))
        return bool(s3_md5) and (s3_md5 == source_md5)
    except Exception:
        return False


def upload_bytes_to_s3(
    data: bytes,
    s3_filename: str,
    *,
    s3_client=None,
    content_md5: Optional[str] = None,
) -> bool:
    """Upload PDF bytes to S3 (deduping safely when possible).

    Logic is identical to the original file-based upload:
    - If the object exists and its ETag (MD5) matches the content, skip upload.
    - If the object exists but the MD5 differs (extremely rare hash-prefix collision), upload
      under a collision-safe key that includes the full MD5 so we never miss a PDF.
    """
    if not (AWS_KEY and AWS_SECRET and AWS_BUCKET):
        return False

    try:
        s3 = s3_client or boto3.client(
            "s3",
            aws_access_key_id=AWS_KEY,
            aws_secret_access_key=AWS_SECRET,
        )

        if content_md5 is None:
            content_md5 = hashlib.md5(data).hexdigest()

        # --- Dedup check: only skip if we're sure it's the same content ---
        try:
            meta = s3.head_object(Bucket=AWS_BUCKET, Key=s3_filename)
            existing_md5 = _md5_from_etag(meta.get("ETag", ""))
            if existing_md5 and existing_md5 == content_md5:
                _log(f"      ☁️  EXISTS IN CLOUD (Skipping Upload): {s3_filename}")
                return True

            # If exists but MD5 differs, it's a (rare) filename hash-prefix collision.
            # Upload under a collision-safe key that includes the full MD5.
            ticker = s3_filename.split("_", 1)[0] if "_" in s3_filename else "DOC"
            collision_safe_key = f"{ticker}_{content_md5}.pdf"

            try:
                meta2 = s3.head_object(Bucket=AWS_BUCKET, Key=collision_safe_key)
                existing2 = _md5_from_etag(meta2.get("ETag", ""))
                if existing2 and existing2 == content_md5:
                    _log(f"      ☁️  EXISTS IN CLOUD (Skipping Upload): {collision_safe_key}")
                    return True
            except Exception:
                pass

            _log(f"      ⚠️  HASH COLLISION on {s3_filename} — uploading as {collision_safe_key}")
            s3_filename = collision_safe_key
        except Exception:
            # Key does not exist; proceed to upload
            pass
        # ---------------------------------------------------------------

        s3.put_object(Bucket=AWS_BUCKET, Key=s3_filename, Body=data)

        _log(f"      ☁️  UPLOAD SUCCESS: {s3_filename}")
        return True
    except Exception as e:
        _log(f"      ❌ UPLOAD FAILED: {e}")
        return False


def upload_file_to_s3(
    local_path: str,
    s3_filename: str,
    *,
    s3_client=None,
    content_md5: Optional[str] = None,
) -> bool:
    """Backwards-compatible file-based wrapper around upload_bytes_to_s3."""
    try:
        with open(local_path, "rb") as f:
            data = f.read()
    except Exception as e:
        _log(f"      ❌ UPLOAD FAILED: {e}")
        return False
    return upload_bytes_to_s3(
        data, s3_filename, s3_client=s3_client, content_md5=content_md5
    )


def accept_terms_if_present(context) -> bool:
    """
    Look for the ASX 'Access to this site' interstitial on ANY page in this
    browser context and click 'Agree and proceed' if present.
    """
    handled = False

    for pg in context.pages:
        try:
            locator = pg.locator(
                "button:has-text('Agree and proceed'), "
                "input[value='Agree and proceed']"
            )

            if locator.count() == 0:
                continue

            try:
                locator.first.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                continue

            print(f"      ⚠️ Terms of Use found on {pg.url} — clicking 'Agree and proceed'...")
            locator.first.click()

            try:
                pg.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            handled = True

        except Exception:
            continue

    if handled:
        print("      ✅ Terms accepted.")
    return handled


def collect_links_and_tickers(page):
    """
    From an ASX announcements table, collect a list of:
        [{'ticker': 'ABC', 'url': 'https://...pdf'}, ...]
    """
    links_info = []

    rows = page.locator("table tr")
    row_count = rows.count()
    print(f"   -> Found {row_count} rows.")

    for idx in range(row_count):
        row = rows.nth(idx)
        cells = row.locator("td")
        if cells.count() < 2:
            continue

        ticker = cells.nth(1).inner_text().strip()

        if not ticker or not re.fullmatch(r"[A-Z0-9]{3,6}", ticker):
            row_text = row.inner_text().strip()
            m = re.search(r"\b[A-Z0-9]{3,6}\b", row_text)
            ticker = m.group(0) if m else f"DOC_{idx}"

        link_loc = row.locator("a[href*='display'], a[href$='.pdf']")
        if link_loc.count() == 0:
            continue

        href = link_loc.first.get_attribute("href")
        if not href:
            continue

        if href.startswith("/"):
            href = BASE_DOMAIN + href
        elif not href.startswith("http"):
            href = BASE_DOMAIN + "/" + href.lstrip("/")

        links_info.append({"ticker": ticker, "url": href})

    print(f"   -> Collected {len(links_info)} PDF links.")
    return links_info


# ---------------------------------------------------------------------------
# MAIN HARVESTER
# ---------------------------------------------------------------------------

def run_harvester():
    print("--- 🩸 STARTING ASX HARVESTER (HTTP download) ---")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if os.path.exists(STORAGE_STATE_PATH):
            context = browser.new_context(storage_state=STORAGE_STATE_PATH)
        else:
            context = browser.new_context()

        page = context.new_page()

        print("1. Loading table page(s)...")
        links_info = []

        for url in ASX_ANNOUNCEMENT_URLS:
            print(f"   -> Loading {url}")
            page.goto(url, timeout=60000)

            if accept_terms_if_present(context):
                if not page.url.endswith(("todayAnns.do", "prevBusDayAnns.do")):
                    page.goto(url, timeout=60000)

            try:
                page.wait_for_selector("table", timeout=60000)
            except PlaywrightTimeoutError:
                print(f"   ⚠️ Failed to load table for {url} (skipping).")
                continue

            links_info.extend(collect_links_and_tickers(page))

        if not links_info:
            print("   ❌ No PDF links found.")
            browser.close()
            return

        first_url = links_info[0]["url"]
        print("2. Touching first PDF to ensure terms are accepted on PDF path...")
        pdf_page = context.new_page()
        try:
            pdf_page.goto(first_url, timeout=60000)
        except PlaywrightTimeoutError:
            print("   ⚠️ Timeout opening first PDF (continuing anyway).")

        accept_terms_if_present(context)

        try:
            pdf_page.close()
        except Exception:
            pass

        cookies = context.cookies(BASE_DOMAIN)
        cookies_dict = {c["name"]: c["value"] for c in cookies}

        try:
            context.storage_state(path=STORAGE_STATE_PATH)
        except Exception:
            pass

        browser.close()

    print(f"3. Downloading {len(links_info)} PDFs via HTTP ({DL_WORKERS} workers)...")
    download_phase(links_info, cookies_dict)


def download_phase(links_info: list, cookies_dict: dict) -> tuple[int, int, int]:
    """Fetch and upload all collected PDF links. Returns (downloaded, uploaded, skipped)."""
    started = time.time()

    # De-duplicate identical URLs across the two announcement pages so the
    # same PDF is never fetched twice in one run.
    seen_urls = set()
    unique_links = []
    for info in links_info:
        if info["url"] in seen_urls:
            continue
        seen_urls.add(info["url"])
        unique_links.append(info)
    if len(unique_links) < len(links_info):
        print(f"   -> {len(links_info) - len(unique_links)} duplicate links removed; {len(unique_links)} to fetch.")

    # Create S3 client once (boto3 clients are thread-safe; size the
    # connection pool for the worker count).
    s3_client = None
    if AWS_KEY and AWS_SECRET and AWS_BUCKET:
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=AWS_KEY,
            aws_secret_access_key=AWS_SECRET,
            config=BotoConfig(max_pool_connections=max(10, DL_WORKERS * 2)),
        )

    def process_link(position: int, info: dict) -> tuple[int, int, int]:
        """Fetch one PDF and upload it. Returns (downloaded, uploaded, skipped)."""
        ticker, url = info["ticker"], info["url"]
        _log(f"   ⬇️  [{position}/{len(unique_links)}] {ticker} -> {url}")
        session = _get_session(cookies_dict)

        try:
            # --- Pre-flight skip (no download) if already in S3 ---
            # Only skip when we're sure:
            #   - HEAD succeeds
            #   - source ETag looks like a real MD5
            #   - predicted S3 key exists AND its S3 ETag matches the source MD5
            # Otherwise, fall back to downloading (safe / no misses).
            if s3_client is not None:
                try:
                    head = session.head(url, timeout=25, allow_redirects=True)
                    if head.status_code == 200:
                        source_md5 = _md5_from_etag(head.headers.get("ETag", ""))

                        clen = head.headers.get("Content-Length", "")
                        expected_size = int(clen) if clen.isdigit() else None

                        ctype_h = (head.headers.get("Content-Type", "") or "").lower()
                        looks_like_pdf = ("pdf" in ctype_h) or ("octet-stream" in ctype_h)

                        if source_md5 and looks_like_pdf:
                            predicted_key = f"{ticker}_{source_md5[:8]}.pdf"
                            if _s3_object_matches_md5(s3_client, predicted_key, source_md5, expected_size=expected_size):
                                _log(f"      ✅ Already in S3 (skipping download): {predicted_key}")
                                return (0, 0, 1)
                except Exception:
                    # If HEAD fails for any reason, download as normal.
                    pass
            # --- end pre-flight skip ---

            # GET with limited retries so one transient blip never loses a PDF.
            resp = None
            last_error: Optional[Exception] = None
            for attempt in range(DL_RETRIES + 1):
                try:
                    resp = session.get(url, timeout=60)
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < DL_RETRIES:
                        time.sleep(1.5 * (attempt + 1))
            if resp is None:
                _log(f"      ❌ Error downloading {url}: {last_error}")
                return (0, 0, 0)

            ctype = resp.headers.get("Content-Type", "").lower()

            if resp.status_code != 200:
                _log(f"      ⚠️ HTTP {resp.status_code} (skipping).")
                return (0, 0, 0)

            if "text/html" in ctype and "pdf" not in ctype:
                _log("      ⚠️ Got HTML instead of PDF (skipping).")
                return (0, 0, 0)

            # --- Content hash for stable filename ---
            # Stable across runs: identical content -> identical filename prefix.
            full_md5 = hashlib.md5(resp.content).hexdigest()
            file_hash = full_md5[:8]
            filename = f"{ticker}_{file_hash}.pdf"
            # -------------------------------------------

            size_kb = len(resp.content) / 1024
            _log(f"      Saved {size_kb:.1f} KB ({filename})")

            if SAVE_LOCAL:
                try:
                    with open(os.path.join(DOWNLOAD_DIR, filename), "wb") as f:
                        f.write(resp.content)
                except Exception as exc:
                    _log(f"      ⚠️ Local save failed (continuing): {exc}")

            uploaded_flag = 0
            if size_kb > 15:
                if upload_bytes_to_s3(
                    resp.content, filename, s3_client=s3_client, content_md5=full_md5
                ):
                    uploaded_flag = 1

            return (1, uploaded_flag, 0)

        except Exception as e:
            _log(f"      ❌ Error downloading {url}: {e}")
            return (0, 0, 0)

    downloaded = 0
    uploaded = 0
    skipped_existing = 0

    if DL_WORKERS == 1:
        results = [process_link(i, info) for i, info in enumerate(unique_links, start=1)]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=DL_WORKERS) as pool:
            futures = [
                pool.submit(process_link, i, info)
                for i, info in enumerate(unique_links, start=1)
            ]
            for future in as_completed(futures):
                results.append(future.result())

    for got, sent, skipped in results:
        downloaded += got
        uploaded += sent
        skipped_existing += skipped

    elapsed = time.time() - started
    print(f"\n--- DONE. Downloaded {downloaded} PDFs, uploaded {uploaded}. ---")
    print(f"          Download phase took {elapsed:.0f}s with {DL_WORKERS} workers.")
    if skipped_existing:
        print(f"          Skipped {skipped_existing} PDFs already in S3 (no re-download).")
    return downloaded, uploaded, skipped_existing


if __name__ == "__main__":
    run_harvester()