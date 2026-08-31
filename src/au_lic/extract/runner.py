"""Run the extraction over the archived ASX announcement PDFs.

Inputs already exist: the market-wide announcement index
(data/asx_ann_cache/asx1/lic_announcement_index.parquet) and the 86,692 PDFs
archived to s3://$S3_BUCKET/asx/announcements/. Nothing is re-fetched from the
ASX here - this reads what was archived.

Two modes:
  sync   - a handful of documents, answers immediately. For validating the
           prompt against real announcements before spending on the corpus.
  batch  - the Batches API at half price, which is the right shape for a
           historical backfill: nothing about it is latency-sensitive.

Resumable the same way the archivers are: a manifest of completed
announcement ids lives in the bucket, unioned across shard layouts, so a run
continues where the last stopped and re-sharding never loses progress.

Every accepted record carries the prompt's content hash. The extraction is
only reproducible if you can tell which version of the instructions produced
a given row, and the prompt WILL change.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, "src")

from au_lic.extract import guards
from au_lic.extract import deterministic
from au_lic.extract import router

MODEL = os.environ.get("EXTRACT_MODEL") or "claude-opus-5"
PROMPT_F = Path("config/prompts/asx_extraction_v1.md")
INDEX_F = Path("data/asx_ann_cache/asx1/lic_announcement_index.parquet")
BUCKET = os.environ.get("S3_BUCKET", "")
SHARD = int(os.environ.get("SHARD_INDEX", "0"))
SHARDS = max(1, int(os.environ.get("SHARD_COUNT", "1")))
MAX_DOC_CHARS = int(os.environ.get("EXTRACT_MAX_DOC_CHARS", "180000"))
MANIFEST_PREFIX = "asx/extract/manifest"


def prompt_text() -> str:
    return PROMPT_F.read_text()


def prompt_version() -> str:
    return "v1:" + hashlib.sha256(PROMPT_F.read_bytes()).hexdigest()[:16]


def pdf_to_text(data: bytes) -> str:
    """Page-marked plain text, matching the format the prompt describes."""
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            out.append(f"--- PAGE {i} ---")
            out.append(page.extract_text() or "")
    text = "\n".join(out)
    return re.sub(r"[ \t]+", " ", text)


def user_block(rec: dict, doc_text: str) -> str:
    return (f"announcement_id: {rec['announcement_id']}\n"
            f"ticker: {rec['ticker']}\n"
            f"company_name: {rec.get('company_name') or ''}\n"
            f"published_at: {rec['published_at']}\n"
            f"announcement_title: {rec.get('headline') or ''}\n"
            f"document_text:\n{doc_text}")


def _client():
    import anthropic
    return anthropic.Anthropic()


def _request_params(rec: dict, doc_text: str) -> dict:
    """One extraction request.

    The instruction block is identical on every call and goes in `system` with
    a cache breakpoint; only the document varies. Across tens of thousands of
    documents that turns the largest fixed cost into a cache read.
    """
    return {
        "model": MODEL,
        "max_tokens": 16000,
        "system": [{"type": "text", "text": prompt_text(),
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_block(rec, doc_text)}],
    }


def parse_payload(text: str) -> dict | None:
    """The response as JSON, tolerating a stray code fence but nothing more."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        obj = json.loads(t)
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


# ------------------------------------------------------------------ work queue
def load_queue(limit: int = 0, ticker: str | None = None,
               routes: tuple[str, ...] = ("llm", "llm_audit")) -> pd.DataFrame:
    """Outstanding work, restricted to the routes a model is needed for.

    Default is the LLM routes only. Reading a prescribed ASX form with a
    frontier model 60,000 times is paying for parsing we can already do.
    """
    idx = pd.read_parquet(INDEX_F)
    idx = router.route_index(idx)
    if routes:
        idx = idx[idx["route"].isin(routes)]
    idx = idx[idx["url"].notna() & (idx["url"] != "")].copy()
    idx["announcement_id"] = idx["id"].astype(str)
    idx["published_at"] = pd.to_datetime(idx["release_date"], utc=True,
                                         errors="coerce").dt.strftime("%Y-%m-%d")
    idx = idx[idx["published_at"].notna()]
    idx["day"] = idx["published_at"]
    if ticker:
        idx = idx[idx["code"].astype(str).str.upper() == ticker.upper()]
    idx = idx.rename(columns={"code": "ticker"})
    done = read_manifest()
    idx = idx[~idx["announcement_id"].isin(done)]
    if SHARDS > 1:
        keep = idx["announcement_id"].map(
            lambda i: zlib.crc32(i.encode()) % SHARDS == SHARD)
        idx = idx[keep]
    idx = idx.sort_values("published_at", ascending=False)
    return idx.head(limit) if limit else idx


def read_manifest(prefix: str = MANIFEST_PREFIX) -> set[str]:
    if not BUCKET:
        return set()
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    done: set[str] = set()
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=BUCKET, Prefix=prefix):
            for o in page.get("Contents", []):
                try:
                    body = s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
                    done |= set(json.loads(body).get("ids", []))
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        print(f"manifest listing failed ({exc}); starting from empty")
    return done


def write_manifest(done: set[str], prefix: str = MANIFEST_PREFIX) -> None:
    if not BUCKET:
        return
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    key = (f"{prefix}.json" if SHARDS == 1
           else f"{prefix}_s{SHARD}of{SHARDS}.json")
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps({"ids": sorted(done)}).encode())


def fetch_pdf_text(s3, rec: dict) -> str | None:
    """Archived PDF -> text. Never re-fetches from the ASX."""
    key = f"asx/announcements/{rec['ticker']}/{rec['day']}_{rec['announcement_id']}.pdf"
    try:
        data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except Exception:  # noqa: BLE001
        return None
    try:
        return pdf_to_text(data)[:MAX_DOC_CHARS]
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ flattening
def flatten(clean: dict, rec: dict, usage: dict | None) -> list[dict]:
    """One row per extracted fact, tagged with its provenance."""
    base = {"announcement_id": rec["announcement_id"], "ticker": rec["ticker"],
            "published_at": rec["published_at"], "prompt_version": prompt_version(),
            "model": MODEL,
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ann = clean.get("announcement") or {}
    qc = clean.get("quality_control") or {}
    rows = []
    for section in guards.S.SECTIONS:
        for r in clean.get(section) or []:
            src = r.get("source") or {}
            rows.append({**base, "section": section,
                         "primary_document_type": ann.get("primary_document_type"),
                         "requires_manual_review": qc.get("requires_manual_review"),
                         "document_parse_quality": qc.get("document_parse_quality"),
                         "source_page": src.get("page"),
                         "source_quote": src.get("quote"),
                         "payload": json.dumps(r, default=str),
                         "input_tokens": (usage or {}).get("input_tokens"),
                         "output_tokens": (usage or {}).get("output_tokens")})
    if not rows:      # the document itself is still a fact worth recording
        rows.append({**base, "section": "announcement",
                     "primary_document_type": ann.get("primary_document_type"),
                     "requires_manual_review": qc.get("requires_manual_review"),
                     "document_parse_quality": qc.get("document_parse_quality"),
                     "source_page": None, "source_quote": None,
                     "payload": json.dumps({"no_extractable_facts": True}),
                     "input_tokens": (usage or {}).get("input_tokens"),
                     "output_tokens": (usage or {}).get("output_tokens")})
    return rows


def _write_outputs(rows: list[dict], rejects: list[dict], tag: str) -> dict:
    out = Path("data/asx_extract")
    out.mkdir(parents=True, exist_ok=True)
    stats = {"fact_rows": len(rows), "rejected_records": len(rejects)}
    if rows:
        pd.DataFrame(rows).to_parquet(out / f"facts_{tag}.parquet", index=False)
    if rejects:
        pd.DataFrame([{**r, "reasons": "|".join(r["reasons"])} for r in rejects]
                     ).to_parquet(out / f"rejects_{tag}.parquet", index=False)
    if BUCKET:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
        for name in (f"facts_{tag}.parquet", f"rejects_{tag}.parquet"):
            p = out / name
            if p.exists():
                s3.upload_file(str(p), BUCKET, f"asx/extract/{name}")
    return stats


# ------------------------------------------------------------------ sync mode
def run_sync(limit: int, ticker: str | None = None) -> dict:
    """Extract a small sample now. For validating the prompt on real documents."""
    import boto3

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    client = _client()
    queue = load_queue(limit=limit, ticker=ticker)
    print(f"sync: {len(queue)} announcements")
    rows: list[dict] = []
    rejects: list[dict] = []
    done: set[str] = set()
    stats = {"no_pdf": 0, "unparseable_json": 0, "extracted": 0}
    for rec in queue.to_dict("records"):
        text = fetch_pdf_text(s3, rec)
        if not text:
            stats["no_pdf"] += 1
            continue
        resp = client.messages.create(**_request_params(rec, text))
        body = next((b.text for b in resp.content if b.type == "text"), "")
        payload = parse_payload(body)
        if payload is None:
            stats["unparseable_json"] += 1
            rejects.append({"announcement_id": rec["announcement_id"],
                            "section": "*", "index": -1,
                            "reasons": ["response_not_json"]})
            continue
        clean, rj = guards.validate(payload, text, rec["published_at"],
                                    rec["announcement_id"])
        usage = {"input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        rows += flatten(clean, rec, usage)
        rejects += rj
        done.add(rec["announcement_id"])
        stats["extracted"] += 1
    stats |= _write_outputs(rows, rejects, f"sync_s{SHARD}")
    if done:
        write_manifest(read_manifest() | done)
    return stats


# ------------------------------------------------------------------ batch mode
def submit_batch(limit: int) -> dict:
    """Queue a batch of extractions at half price; returns the batch id."""
    import boto3
    from anthropic.types.messages.batch_create_params import Request

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    client = _client()
    queue = load_queue(limit=limit)
    requests, meta = [], {}
    for rec in queue.to_dict("records"):
        text = fetch_pdf_text(s3, rec)
        if not text:
            continue
        cid = f"a{rec['announcement_id']}"
        requests.append(Request(custom_id=cid, params=_request_params(rec, text)))
        meta[cid] = {k: rec[k] for k in
                     ("announcement_id", "ticker", "published_at", "day")}
        meta[cid]["doc_sha"] = hashlib.sha256(text.encode()).hexdigest()
    if not requests:
        return {"submitted": 0}
    batch = client.messages.batches.create(requests=requests)
    if BUCKET:
        s3.put_object(Bucket=BUCKET, Key=f"asx/extract/batches/{batch.id}.json",
                      Body=json.dumps({"batch_id": batch.id, "meta": meta,
                                       "prompt_version": prompt_version(),
                                       "model": MODEL}).encode())
    print(f"submitted batch {batch.id} with {len(requests)} requests")
    return {"submitted": len(requests), "batch_id": batch.id}


def collect_batch(batch_id: str) -> dict:
    """Fetch a finished batch, re-verify every quote, and write the facts.

    The document text is re-derived from the archived PDF rather than trusted
    from the request, so quote provenance is checked against the source, not
    against whatever was sent.
    """
    import boto3

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        return {"batch_id": batch_id, "status": batch.processing_status}

    meta = {}
    if BUCKET:
        try:
            body = s3.get_object(Bucket=BUCKET,
                                 Key=f"asx/extract/batches/{batch_id}.json")["Body"].read()
            meta = json.loads(body).get("meta", {})
        except Exception:  # noqa: BLE001
            pass

    rows: list[dict] = []
    rejects: list[dict] = []
    done: set[str] = set()
    stats = {"succeeded": 0, "errored": 0, "unparseable_json": 0, "no_pdf": 0}
    for result in client.messages.batches.results(batch_id):
        rec = meta.get(result.custom_id)
        if rec is None:
            continue
        if result.result.type != "succeeded":
            stats["errored"] += 1
            rejects.append({"announcement_id": rec["announcement_id"], "section": "*",
                            "index": -1, "reasons": [f"batch_{result.result.type}"]})
            continue
        msg = result.result.message
        body = next((b.text for b in msg.content if b.type == "text"), "")
        payload = parse_payload(body)
        text = fetch_pdf_text(s3, rec)
        if text is None:
            stats["no_pdf"] += 1
            continue
        if payload is None:
            stats["unparseable_json"] += 1
            rejects.append({"announcement_id": rec["announcement_id"], "section": "*",
                            "index": -1, "reasons": ["response_not_json"]})
            continue
        clean, rj = guards.validate(payload, text, rec["published_at"],
                                    rec["announcement_id"])
        rows += flatten(clean, rec, {"input_tokens": msg.usage.input_tokens,
                                     "output_tokens": msg.usage.output_tokens})
        rejects += rj
        done.add(rec["announcement_id"])
        stats["succeeded"] += 1
    stats |= _write_outputs(rows, rejects, f"batch_{batch_id[-8:]}")
    if done:
        write_manifest(read_manifest() | done)
    return {"batch_id": batch_id, **stats}



# -------------------------------------------------------------- deterministic
DET_MANIFEST_PREFIX = "asx/extract/det_manifest"


def _load_index_union() -> pd.DataFrame:
    """The announcement index, unioned across every copy we hold.

    The index lives in two places - committed in git, and in S3 - and the
    workflow restores S3 over the checkout. Tonight the S3 copy is 91,010
    rows while the committed one is 117,514: extracting from the restored
    copy alone would silently skip all of 2024 and 2025, which is the exact
    history this run exists to process. The index is append-only and keyed
    by a unique id, so the union is the only safe reading of two copies that
    disagree.
    """
    frames: list[pd.DataFrame] = []
    if INDEX_F.exists():
        try:
            frames.append(pd.read_parquet(INDEX_F))
        except Exception as exc:  # noqa: BLE001
            print(f"on-disk index unreadable ({exc})")
    try:
        import io
        import subprocess
        r = subprocess.run(["git", "show", f"HEAD:{INDEX_F.as_posix()}"],
                           capture_output=True, timeout=120)
        if r.returncode == 0 and r.stdout:
            frames.append(pd.read_parquet(io.BytesIO(r.stdout)))
    except Exception as exc:  # noqa: BLE001
        print(f"committed index unavailable ({exc})")
    if not frames:
        raise SystemExit(f"no announcement index found at {INDEX_F}")
    out = (pd.concat(frames, ignore_index=True).drop_duplicates("id")
           if len(frames) > 1 else frames[0])
    print(f"index copies {[len(f) for f in frames]} -> union {len(out):,} rows")
    return out.reset_index(drop=True)


def run_deterministic(limit: int = 0, deadline_min: float = 300.0) -> dict:
    """Parse the prescribed-form route in Python, straight from the archive.

    No model, no per-document cost, and re-runnable: when a parser improves,
    the whole corpus can be reparsed for the price of the compute.

    A document the parser cannot read is recorded as an ESCALATION with its
    family and headline, not dropped. That queue is what the model pass later
    consumes, so 'the parser could not read it' stays distinguishable from
    'the document contained nothing'.
    """
    import boto3

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    done = read_manifest(DET_MANIFEST_PREFIX)
    raw = _load_index_union()
    # The index this reads is restored from S3 and can differ from the
    # committed copy. All eight shards died here with KeyError:
    # 'published_at' on a frame that carries that column when built from the
    # committed index, so what the runner ACTUALLY received is worth stating
    # rather than inferring from a traceback.
    print(f"index: {len(raw)} rows, columns={list(raw.columns)}, "
          f"dtypes={ {c: str(t) for c, t in raw.dtypes.items()} }")
    missing = {"id", "code", "release_date", "headline"} - set(raw.columns)
    if missing:
        raise SystemExit(
            f"announcement index is missing required column(s) {sorted(missing)}; "
            f"it has {list(raw.columns)}. The S3 copy and the committed copy "
            "have diverged - do not guess a mapping, fix the source.")
    if raw.columns.duplicated().any():
        dupes = sorted(raw.columns[raw.columns.duplicated()])
        raise SystemExit(f"announcement index has duplicate columns {dupes}; "
                         "idx[col] would return a frame, not a series.")
    idx = router.route_index(raw)
    idx = idx[idx["route"] == "deterministic"].copy()
    idx["announcement_id"] = idx["id"].astype(str)
    # build the column explicitly so an empty or object-dtype date column
    # cannot leave it undefined further down
    _pub = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
    idx["published_at"] = pd.Series(_pub, index=idx.index).dt.strftime("%Y-%m-%d") \
        if len(idx) else pd.Series([], dtype="object", index=idx.index)
    if "published_at" not in idx.columns:  # belt and braces; see above
        raise SystemExit("published_at could not be derived from release_date")
    idx = idx[idx["published_at"].notna() & ~idx["announcement_id"].isin(done)]
    idx = idx.rename(columns={"code": "ticker"})
    idx["day"] = idx["published_at"]
    if SHARDS > 1:
        idx = idx[idx["announcement_id"].map(
            lambda i: zlib.crc32(i.encode()) % SHARDS == SHARD)]
    if "published_at" not in idx.columns:
        # it WAS present a few lines above; say what happened rather than
        # let pandas raise a bare KeyError from inside sort_values
        raise SystemExit(
            "published_at was constructed but is gone by the sort; "
            f"columns now = {list(idx.columns)}, rows = {len(idx)}")
    idx = idx.sort_values("published_at", ascending=False)
    if limit:
        idx = idx.head(limit)
    print(f"deterministic: {len(idx)} documents (shard {SHARD + 1}/{SHARDS})")

    started = time.time()
    rows: list[dict] = []
    escalations: list[dict] = []
    stats = {"documents": 0, "no_pdf": 0, "unreadable": 0, "image_only": 0,
             "parsed": 0, "escalated": 0, "by_family": {}}
    for rec in idx.to_dict("records"):
        if (time.time() - started) > deadline_min * 60:
            print("deadline reached - stopping cleanly")
            break
        stats["documents"] += 1
        key = (f"asx/announcements/{rec['ticker']}/"
               f"{rec['day']}_{rec['announcement_id']}.pdf")
        try:
            data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        except Exception:  # noqa: BLE001
            stats["no_pdf"] += 1
            continue
        try:
            text, tbl = deterministic.pdf_pages(data)
        except Exception:  # noqa: BLE001
            stats["unreadable"] += 1
            escalations.append({**{k: rec[k] for k in
                                   ("announcement_id", "ticker", "published_at",
                                    "family", "headline")},
                                "reason": "pdf_unreadable"})
            continue
        if not deterministic.has_text_layer(text):
            # a scanned PDF is not a parse failure and must not be sent to a
            # TEXT model, which would read exactly as little from it and
            # charge for the attempt. Counted apart so the parse rate
            # measures parsing, and queued as an OCR candidate.
            stats["image_only"] = stats.get("image_only", 0) + 1
            escalations.append({**{k: rec[k] for k in
                                   ("announcement_id", "ticker", "published_at",
                                    "family", "headline")},
                                "reason": "no_text_layer_needs_ocr"})
            done.add(rec["announcement_id"])
            continue
        facts = deterministic.extract(rec["family"], text, tbl, rec.get("headline") or "")
        if not facts:
            stats["escalated"] += 1
            escalations.append({**{k: rec[k] for k in
                                   ("announcement_id", "ticker", "published_at",
                                    "family", "headline")},
                                "reason": "no_facts_parsed"})
            done.add(rec["announcement_id"])
            continue
        fam = rec["family"]
        stats["by_family"][fam] = stats["by_family"].get(fam, 0) + len(facts)
        for f in facts:
            rows.append({"announcement_id": rec["announcement_id"],
                         "ticker": rec["ticker"],
                         "published_at": rec["published_at"],
                         "family": fam, "source": "deterministic",
                         "payload": json.dumps(f, default=str),
                         **{k: f.get(k) for k in
                            ("section", "valuation_date", "nav_per_share",
                             "nav_basis", "extractor", "raw_nav_label")}})
        stats["parsed"] += 1
        done.add(rec["announcement_id"])

    out = Path("data/asx_extract")
    out.mkdir(parents=True, exist_ok=True)
    tag = f"det_s{SHARD}of{SHARDS}"
    if rows:
        pd.DataFrame(rows).to_parquet(out / f"facts_{tag}.parquet", index=False)
    if escalations:
        pd.DataFrame(escalations).to_parquet(out / f"escalations_{tag}.parquet",
                                             index=False)
    if BUCKET:
        import boto3 as _b
        c = _b.client("s3", region_name=os.environ.get("AWS_REGION"))
        for name in (f"facts_{tag}.parquet", f"escalations_{tag}.parquet"):
            f = out / name
            if f.exists():
                c.upload_file(str(f), BUCKET, f"asx/extract/{name}")
        write_manifest(done, DET_MANIFEST_PREFIX)
    stats["fact_rows"] = len(rows)
    stats["escalation_rows"] = len(escalations)
    # two rates, because they answer different questions: how much of the
    # corpus landed, and how good the parsers are on documents that HAVE text
    readable = max(1, stats["documents"] - stats["no_pdf"] - stats["unreadable"]
                   - stats["image_only"])
    stats["parse_rate"] = round(stats["parsed"] / max(1, stats["documents"]), 4)
    stats["parse_rate_text_bearing"] = round(stats["parsed"] / readable, 4)
    return stats



def run_label_discovery(limit: int = 0, deadline_min: float = 300.0) -> dict:
    """Discover which label carries each fund's NTA, using the exchange.

    Reads only fund-months where we hold BOTH the fund's announcement and the
    exchange's published NTA, so the right answer is known and the label that
    carries it can be identified rather than guessed. See
    au_lic.extract.label_discovery for why this is supervision and not
    overfitting - the short version is that it learns the label, demands the
    label repeat across months, and reports its result on funds it never
    learned from.

    Writes outputs/au/au_nta_label_rules.csv (the auditable rule table) and
    reports/build/asx_label_discovery.json. It changes no parser: applying a
    discovered rule is a separate decision, made by a person reading the
    table.
    """
    import boto3

    from . import label_discovery as LD

    panel_p = Path("data/au_processed/au_monthly_panel.parquet")
    if not panel_p.exists():
        print("no AU monthly panel - the exchange NTA is the whole point of "
              "this mode, so there is nothing to learn from")
        return {"status": "no_panel"}
    panel = pd.read_parquet(panel_p)
    if "nta_price" not in panel.columns:
        print(f"panel has no nta_price column; has {list(panel.columns)[:12]}")
        return {"status": "panel_has_no_nta"}
    panel = panel[panel["nta_price"].notna()].copy()
    panel["ticker"] = (panel["security_id"].astype(str)
                       .str.replace("^ASX:", "", regex=True).str.upper())
    panel["month"] = panel["obs_month"].astype(str).str.slice(0, 7)
    truth = {(r.ticker, r.month): float(r.nta_price)
             for r in panel.itertuples(index=False)}
    print(f"exchange NTA available for {len(truth):,} fund-months")

    idx = router.route_index(_load_index_union())
    idx = idx[idx["family"] == "nta"].copy()
    idx["announcement_id"] = idx["id"].astype(str)
    idx["ticker"] = idx["code"].astype(str).str.upper()
    pub = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
    idx["day"] = pub.dt.strftime("%Y-%m-%d")
    # An NTA announced in April reports MARCH. Joining on the publication
    # month would look up the wrong month's answer and manufacture
    # disagreement on every correctly parsed document.
    idx["val_month"] = (pub - pd.offsets.MonthBegin(1)).dt.strftime("%Y-%m")
    idx = idx[idx["day"].notna() & idx["val_month"].notna()]
    idx["k"] = list(zip(idx["ticker"], idx["val_month"]))
    idx = idx[idx["k"].isin(truth.keys())]
    if SHARDS > 1:
        idx = idx[idx["announcement_id"].map(
            lambda i: zlib.crc32(i.encode()) % SHARDS == SHARD)]
    idx = idx.sort_values("day", ascending=False)
    if limit:
        idx = idx.head(limit)
    print(f"{len(idx)} announcements have a known answer (shard {SHARD + 1}/{SHARDS})")

    split = LD.holdout_split(sorted(set(idx["ticker"])))
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    obs: list[dict] = []
    started = time.time()
    stats = {"documents": 0, "no_pdf": 0, "unreadable": 0,
             "with_evidence": 0, "no_label_matched": 0}
    for rec in idx.to_dict("records"):
        if (time.time() - started) > deadline_min * 60:
            print("deadline reached - stopping cleanly")
            break
        stats["documents"] += 1
        key = f"asx/announcements/{rec['ticker']}/{rec['day']}_{rec['announcement_id']}.pdf"
        try:
            data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        except Exception:  # noqa: BLE001
            stats["no_pdf"] += 1
            continue
        try:
            text, tbl = deterministic.pdf_pages(data)
        except Exception:  # noqa: BLE001
            stats["unreadable"] += 1
            continue
        found = LD.observe(rec["ticker"], rec["val_month"],
                           deterministic.normalise_nta_text(text), tbl,
                           truth[(rec["ticker"], rec["val_month"])])
        if found:
            stats["with_evidence"] += 1
            for f in found:
                f["split"] = split.get(rec["ticker"], "learn")
                f["announcement_id"] = rec["announcement_id"]
            obs.extend(found)
        else:
            # the document does not contain the exchange's figure anywhere:
            # a real disagreement between the two sources, not a parse bug
            stats["no_label_matched"] += 1

    o = pd.DataFrame(obs)
    learn = o[o["split"] == "learn"] if len(o) else o
    rules = LD.discover(learn)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    tag = "" if SHARDS == 1 else f"_s{SHARD}of{SHARDS}"
    if len(rules):
        rules.to_csv(f"outputs/au/au_nta_label_rules{tag}.csv", index=False)
    if len(o):
        o.to_csv(f"outputs/au/au_nta_label_evidence{tag}.csv", index=False)

    firm = rules[rules["is_rule"]] if len(rules) else rules
    out = {**stats,
           "fund_months_with_truth": len(truth),
           "observations": int(len(o)),
           "funds_with_evidence": int(o["ticker"].nunique()) if len(o) else 0,
           "labels_seen": int(len(rules)),
           "labels_promoted_to_rules": int(len(firm)),
           "funds_covered_by_a_rule": int(firm["ticker"].nunique()) if len(firm) else 0,
           "holdout_funds": sum(1 for v in split.values() if v == "holdout"),
           "learn_funds": sum(1 for v in split.values() if v == "learn"),
           "top_rules": (firm.head(20).to_dict("records") if len(firm) else []),
           "note": ("rules are learned ONLY from the learn split; the holdout "
                    "funds exist so agreement can be measured without "
                    "circularity. No parser is changed by this mode."),
           }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path(f"reports/build/asx_label_discovery{tag}.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(json.dumps({k: v for k, v in out.items() if k != "top_rules"},
                     indent=2, default=str))
    return out


def _key_overlap_diag(nav, nav_months, pan_t, panel) -> dict:
    """Composite-key overlap, and one shared ticker's months on each side."""
    try:
        nk = pd.DataFrame({"t": nav.get("ticker", pd.Series(dtype=str))
                           .astype(str).str.upper(), "m": nav_months}).dropna()
        pk = pd.DataFrame({"t": pan_t.astype(str).str.upper(),
                           "m": panel["obs_month"].astype(str)}).dropna()
        ns = set(map(tuple, nk.values))
        ps = set(map(tuple, pk.values))
        both = ns & ps
        shared_t = sorted(set(nk["t"]) & set(pk["t"]))
        probe = shared_t[0] if shared_t else None
        return {
            "composite_key_overlap": len(both),
            "nav_unique_keys": len(ns), "panel_unique_keys": len(ps),
            "key_overlap_sample": [f"{a}|{b}" for a, b in sorted(both)[:5]],
            "probe_ticker": probe,
            "probe_nav_months": sorted(nk.loc[nk["t"] == probe, "m"])[-6:]
                                if probe else [],
            "probe_panel_months": sorted(pk.loc[pk["t"] == probe, "m"])[-6:]
                                  if probe else [],
        }
    except Exception as exc:  # noqa: BLE001
        return {"key_overlap_error": f"{type(exc).__name__}: {exc}"}


def run_validate(limit: int = 0) -> dict:
    """Compare extracted NTA with the exchange's published NTA.

    Reads the facts written by the deterministic pass (from S3 if the local
    shard files are absent) and the monthly panel, and reports agreement,
    unit errors, basis gaps and the worst disagreements by name.
    """
    from au_lic import panel as AUP
    from au_lic import validate_nta as V

    # one loader for "where the extracted facts live", shared with
    # cef_live so the nightly and this validation cannot disagree about it
    from au_lic.extract import facts as AUF
    facts = AUF.load()
    if not len(facts):
        return {"error": "no extracted facts found - run deterministic first"}
    nav = facts[facts["section"] == "nav_observations"].copy()

    # The builder takes a config and REBUILDS from raw; the loader reads the
    # panel that was already built. Calling the builder with no argument
    # raises TypeError - and would have done so AFTER the full extraction had
    # run, an hour spent to discover a one-line mistake.
    import yaml
    cfg = yaml.safe_load(Path("config/au_default.yaml").read_text()) \
        if Path("config/au_default.yaml").exists() else None
    panel = pd.DataFrame()
    try:
        if cfg is not None:
            panel = AUP.load_panel(cfg)
    except FileNotFoundError:
        pass
    if panel.empty:
        for cand in (Path("data/au_processed/au_monthly_panel.parquet"),):
            if cand.exists():
                panel = pd.read_parquet(cand)
                break
    if panel.empty:
        return {"error": "au_monthly_panel.parquet not found - "
                         "run `python -m au_lic.cli build-panel` first"}
    cmp_df = V.compare(nav, panel)
    out = V.summarise(V.classify(cmp_df), extracted_total=len(nav))
    # "no fund-month had a value from both sources" is a conclusion, not a
    # diagnosis. When the join is empty the question is always WHICH SIDE is
    # empty and on WHAT KEY, so record both sides' shapes and keys.
    # use the same helper the join uses, or the diagnostic lies in the same
    # way the join did: nav_month_max came back as the STRING "NaT", which
    # sorts after every digit
    nav_months = V._month(nav["valuation_date"]) if "valuation_date" in nav.columns \
        else pd.Series(dtype=str)
    pan_t = (panel["security_id"].astype(str).str.replace("^ASX:", "", regex=True)
             if "security_id" in panel.columns else pd.Series(dtype=str))
    out["compare_stages"] = getattr(cmp_df, "attrs", {}).get("stages", {})
    out["diagnostics"] = {
        "fact_rows_total": int(len(facts)),
        "nav_rows": int(len(nav)),
        "nav_rows_with_valuation_date": int(
            nav["valuation_date"].notna().sum()) if "valuation_date" in nav.columns else 0,
        "nav_tickers": int(nav["ticker"].nunique()) if "ticker" in nav.columns else 0,
        "nav_months_sample": sorted(set(nav_months.dropna()))[:6],
        "nav_month_min": min(nav_months.dropna(), default=None),
        "nav_month_max": max(nav_months.dropna(), default=None),
        "nav_rows_by_year": nav_months.dropna().str[:4].value_counts()
                            .sort_index().tail(12).to_dict(),
        "panel_rows": int(len(panel)),
        "panel_has_nta_price": "nta_price" in panel.columns,
        "panel_tickers": int(pan_t.nunique()),
        "panel_months_sample": sorted(
            set(panel["obs_month"].astype(str)))[:6] if "obs_month" in panel.columns else [],
        "panel_month_min": str(panel["obs_month"].min()) if "obs_month" in panel.columns else None,
        "panel_month_max": str(panel["obs_month"].max()) if "obs_month" in panel.columns else None,
        "ticker_overlap": int(len(set(nav.get("ticker", pd.Series(dtype=str))
                                      .astype(str).str.upper())
                                  & set(pan_t.str.upper()))),
        "nav_rows_with_value": int(nav["nav_per_share"].notna().sum())
                               if "nav_per_share" in nav.columns else 0,
        # the actual keys, verbatim and un-normalised: every empty-join
        # diagnosis so far has been guesswork because these were missing
        "nav_key_sample": [f"{a}|{b}" for a, b in zip(
            nav.get("ticker", pd.Series(dtype=str)).astype(str).head(5),
            nav_months.head(5).astype(str))],
        "panel_key_sample": [f"{a}|{b}" for a, b in zip(
            pan_t.head(5), panel["obs_month"].astype(str).head(5))]
            if "obs_month" in panel.columns else [],
        # The composite key intersection itself. Every diagnosis so far has
        # inferred overlap from ticker sets and month RANGES separately, and
        # both can look fine while no (ticker, month) PAIR coincides. This is
        # the only figure that settles it.
        **_key_overlap_diag(nav, nav_months, pan_t, panel),
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/asx_nta_validation.json").write_text(
        json.dumps(out, indent=2, default=str))
    return out


def diagnose_failures(limit: int = 240, per_family: int = 12) -> dict:
    """Why the deterministic parsers failed, in the documents' own words.

    A parse rate is not actionable; the TEXT the parser was handed is. This
    samples failures across families and records what pdfplumber actually
    extracted, so parser fixes are written against real pages instead of an
    imagined layout - which is how the NTA parser reached its accuracy in the
    first place.
    """
    import boto3

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    idx = router.route_index(pd.read_parquet(INDEX_F))
    idx = idx[idx["route"] == "deterministic"].copy()
    idx["announcement_id"] = idx["id"].astype(str)
    idx["published_at"] = pd.to_datetime(idx["release_date"], utc=True,
                                         errors="coerce").dt.strftime("%Y-%m-%d")
    idx = idx[idx["published_at"].notna()].rename(columns={"code": "ticker"})
    idx["day"] = idx["published_at"]
    # spread across families and eras rather than taking the newest N: a
    # sample of last month's filings would miss every legacy layout
    idx = idx.sort_values("published_at")
    step = max(1, len(idx) // max(1, limit))
    sample = idx.iloc[::step].head(limit)

    fails: dict[str, list] = {}
    counts: dict[str, dict] = {}
    for rec in sample.to_dict("records"):
        fam = rec["family"]
        counts.setdefault(fam, {"tried": 0, "parsed": 0, "failed": 0,
                                "image_only": 0})
        counts[fam]["tried"] += 1
        key = (f"asx/announcements/{rec['ticker']}/"
               f"{rec['day']}_{rec['announcement_id']}.pdf")
        try:
            data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            text, tbl = deterministic.pdf_pages(data)
        except Exception as exc:  # noqa: BLE001
            counts[fam]["failed"] += 1
            fails.setdefault(fam, []).append({"headline": rec.get("headline"),
                                              "error": f"{type(exc).__name__}",
                                              "text": ""})
            continue
        if not deterministic.has_text_layer(text):
            counts[fam]["image_only"] = counts[fam].get("image_only", 0) + 1
            continue
        got = deterministic.extract(fam, text, tbl, rec.get("headline") or "")
        if got:
            counts[fam]["parsed"] += 1
            continue
        counts[fam]["failed"] += 1
        if len(fails.get(fam, [])) < per_family:
            fails.setdefault(fam, []).append({
                "announcement_id": rec["announcement_id"],
                "ticker": rec["ticker"], "published_at": rec["published_at"],
                "headline": rec.get("headline"),
                "text_head": text[:700],
                "table_rows": [r[:6] for r in tbl[:4]],
                "chars": len(text)})
    for fam, c in counts.items():
        c["parse_rate"] = round(c["parsed"] / max(1, c["tried"]), 4)
        c["parse_rate_text_bearing"] = round(
            c["parsed"] / max(1, c["tried"] - c.get("image_only", 0)), 4)
    return {"sampled": int(len(sample)), "by_family": counts, "failures": fails}


# ------------------------------------------------------------------- prices
def run_prices(limit: int = 0) -> dict:
    """Daily price history for every code in the announcement index.

    Every code, including funds that have since been delisted - they are in
    the index precisely because the universe is point-in-time. Yahoo drops
    many delisted tickers, so coverage is measured per code and the missing
    ones are named. A code with no history is MISSING, never quietly excluded:
    silently dropping them would compute every AU result on survivors.
    """
    import boto3

    from au_lic import prices_history as PH

    idx = pd.read_parquet(INDEX_F)
    codes = sorted(idx["code"].astype(str).str.upper().unique())
    if SHARDS > 1:
        codes = [c for c in codes if zlib.crc32(c.encode()) % SHARDS == SHARD]
    if limit:
        codes = codes[:limit]
    print(f"prices: {len(codes)} codes (shard {SHARD + 1}/{SHARDS})")

    s = PH.session()
    fetched: dict[str, pd.DataFrame | None] = {}
    frames = []
    for c in codes:
        df = PH.fetch_history(s, c)
        fetched[c] = df
        if df is not None:
            frames.append(df)
    rep = PH.coverage_report(codes, fetched)

    out = Path("data/asx_prices")
    out.mkdir(parents=True, exist_ok=True)
    tag = f"s{SHARD}of{SHARDS}"
    if frames:
        allpx = pd.concat(frames, ignore_index=True)
        allpx.to_parquet(out / f"asx_daily_{tag}.parquet", index=False)
        rep["price_rows"] = int(len(allpx))
        if BUCKET:
            boto3.client("s3", region_name=os.environ.get("AWS_REGION")).upload_file(
                str(out / f"asx_daily_{tag}.parquet"), BUCKET,
                f"asx/prices/asx_daily_{tag}.parquet")
    return rep


# ------------------------------------------------------------------ estimate
# Published per-million-token rates. Batch is half. Cached input reads at 0.1x,
# which matters here because the instruction block is ~identical every call.
PRICES = {
    "claude-opus-5":     {"in": 5.00, "out": 25.00},
    "claude-sonnet-5":   {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5":  {"in": 1.00, "out": 5.00},
}
CACHE_READ_MULTIPLIER = 0.1
BATCH_MULTIPLIER = 0.5


def estimate_cost(sample: int = 40, assumed_output_tokens: int = 800) -> dict:
    """Price the corpus from MEASURED token counts, not assumed ones.

    Samples real archived PDFs, counts their tokens with the API's own
    counter, and extrapolates. A guess at "about 2,000 tokens a document" is
    the kind of number that is wrong by 3x on a corpus containing both
    one-page NTA notices and 80-page annual reports, and the whole decision
    here is a cost decision.

    Output tokens cannot be counted without generating, so they stay an
    explicit assumption rather than a hidden one.
    """
    import boto3

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    client = _client()
    queue = load_queue(limit=0)
    total_docs = int(len(queue))
    routing = router.summarise(router.route_index(pd.read_parquet(INDEX_F)))
    # spread the sample across the queue rather than taking the newest N -
    # document length varies with era and with document type
    step = max(1, total_docs // max(1, sample))
    picked = queue.iloc[::step].head(sample).to_dict("records")

    sys_tokens = client.messages.count_tokens(
        model=MODEL, system=[{"type": "text", "text": prompt_text()}],
        messages=[{"role": "user", "content": "x"}]).input_tokens

    counts, missing = [], 0
    for rec in picked:
        text = fetch_pdf_text(s3, rec)
        if not text:
            missing += 1
            continue
        n = client.messages.count_tokens(
            model=MODEL,
            system=[{"type": "text", "text": prompt_text()}],
            messages=[{"role": "user", "content": user_block(rec, text)}]).input_tokens
        counts.append(max(0, n - sys_tokens))     # the document's own share

    if not counts:
        return {"error": "no sampled documents could be read", "missing": missing}

    ser = pd.Series(counts)
    doc_mean = float(ser.mean())
    per_call_in = doc_mean + sys_tokens * CACHE_READ_MULTIPLIER
    out = {
        "routing": {k: routing[k] for k in
                    ("total", "by_route", "llm_share", "deterministic_share",
                     "skip_share")},
        "documents_outstanding": total_docs,
        "sampled": len(counts), "sample_pdfs_missing": missing,
        "system_prompt_tokens": int(sys_tokens),
        "doc_tokens": {"mean": round(doc_mean), "median": int(ser.median()),
                       "p90": int(ser.quantile(0.9)), "max": int(ser.max())},
        "assumed_output_tokens": assumed_output_tokens,
        "note": ("input measured with count_tokens on real archived PDFs; "
                 "system prompt priced as a cache read at 0.1x; output is an "
                 "assumption - it cannot be counted without generating"),
        "cost_usd": {},
    }
    # price every published model, plus whatever the run was actually
    # configured with - a model the table does not know must report as
    # unpriced, never as free
    for name in dict.fromkeys(list(PRICES) + [MODEL]):
        px = PRICES.get(name)
        if px is None:
            out["cost_usd"][name] = {"error": "no published price in PRICES; "
                                              "add it before trusting a total"}
            continue
        std = (per_call_in / 1e6 * px["in"] + assumed_output_tokens / 1e6 * px["out"])
        out["cost_usd"][name] = {
            "per_document": round(std, 5),
            "corpus_standard": round(std * total_docs, 2),
            "corpus_batch": round(std * total_docs * BATCH_MULTIPLIER, 2),
        }
    out["configured_model"] = MODEL
    return out


def compare_models(models: list[str], limit: int = 25) -> dict:
    """Same documents, several models, judged by the guards.

    The guards turn model weakness into a MEASURABLE quantity: a weaker model
    does not silently corrupt the dataset, it fails quote provenance and
    vocabulary checks more often and yields fewer accepted facts. So the
    cheap-vs-capable question is answerable with a number - accepted facts
    per document, and why the rest were rejected - instead of an opinion.
    """
    import boto3
    from collections import Counter

    global MODEL
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    client = _client()
    queue = load_queue(limit=limit)
    docs = []
    for rec in queue.to_dict("records"):
        text = fetch_pdf_text(s3, rec)
        if text:
            docs.append((rec, text))
    print(f"compare: {len(docs)} documents x {len(models)} models")

    results = {}
    original = MODEL
    for m in models:
        MODEL = m
        accepted = rejected = unparseable = 0
        reasons: Counter = Counter()
        in_tok = out_tok = 0
        for rec, text in docs:
            try:
                resp = client.messages.create(**_request_params(rec, text))
            except Exception as exc:  # noqa: BLE001
                reasons[f"api_error:{type(exc).__name__}"] += 1
                continue
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            payload = parse_payload(
                next((b.text for b in resp.content if b.type == "text"), ""))
            if payload is None:
                unparseable += 1
                continue
            clean, rj = guards.validate(payload, text, rec["published_at"],
                                        rec["announcement_id"])
            accepted += sum(len(clean.get(s_) or []) for s_ in guards.S.SECTIONS)
            rejected += len(rj)
            for r in rj:
                for reason in r["reasons"]:
                    reasons[reason.split(":")[0]] += 1
        px = PRICES.get(m, {"in": 0, "out": 0})
        results[m] = {
            "documents": len(docs),
            "accepted_facts": accepted,
            "accepted_per_document": round(accepted / max(1, len(docs)), 2),
            "rejected_records": rejected,
            "unparseable_json": unparseable,
            "acceptance_rate": round(accepted / max(1, accepted + rejected), 4),
            "rejection_reasons": dict(reasons.most_common(8)),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "sample_cost_usd": round(in_tok / 1e6 * px["in"]
                                     + out_tok / 1e6 * px["out"], 4),
        }
    MODEL = original
    return results

def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sync", "submit", "collect", "queue",
                                     "estimate", "compare", "deterministic",
                                     "prices", "diagnose", "validate",
                                     "labels"])
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--ticker")
    ap.add_argument("--batch-id")
    ap.add_argument("--models",
                    default=os.environ.get("COMPARE_MODELS")
                    or "claude-opus-5,claude-haiku-4-5")
    a = ap.parse_args(argv)

    if a.mode == "queue":
        q = load_queue(limit=a.limit, ticker=a.ticker)
        print(json.dumps({"queued": int(len(q)),
                          "prompt_version": prompt_version(),
                          "sample": q.head(5)[["announcement_id", "ticker",
                                               "published_at"]].to_dict("records")},
                         indent=2, default=str))
        return 0
    if a.mode == "validate":
        out = run_validate(limit=a.limit)
        print(json.dumps({k: v for k, v in out.items() if k != "worst"},
                         indent=2, default=str))
        return 0
    if a.mode == "diagnose":
        out = diagnose_failures(limit=a.limit or 240)
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        Path("reports/build/asx_parse_failures.json").write_text(
            json.dumps(out, indent=2, default=str))
        print(json.dumps({"sampled": out["sampled"],
                          "by_family": out["by_family"]}, indent=2))
        return 0
    if a.mode == "prices":
        out = run_prices(limit=a.limit)
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        Path("reports/build/asx_price_coverage.json").write_text(
            json.dumps(out, indent=2, default=str))
        print(json.dumps({k: v for k, v in out.items()
                          if k != "codes_missing"}, indent=2, default=str))
        return 0
    if a.mode == "labels":
        # writes its own outputs (asx_label_discovery*.json and the rule
        # table) inside run_label_discovery. It must NOT reach the
        # deterministic status write below: a mode that overwrites another
        # mode's record destroys the only evidence of what that run did,
        # which is how a status file ends up lying about a job that
        # succeeded.
        run_label_discovery(limit=a.limit)
        return 0
    if a.mode == "deterministic":
        out = run_deterministic(limit=a.limit)
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        # per-shard filename: all eight shards wrote the SAME path, so the
        # committed status was whichever shard happened to finish last and
        # the run's real totals were unrecoverable from it
        Path(f"reports/build/asx_deterministic_status_s{SHARD}of{SHARDS}.json"
             if SHARDS > 1 else
             "reports/build/asx_deterministic_status.json").write_text(
            json.dumps({**out, "shard": SHARD, "shards": SHARDS},
                       indent=2, default=str))
        print(json.dumps(out, indent=2, default=str))
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - extraction skipped")
        return 0
    if a.mode == "estimate":
        out = estimate_cost(sample=a.limit or 40)
    elif a.mode == "compare":
        out = compare_models([m.strip() for m in a.models.split(",") if m.strip()],
                             limit=a.limit)
    elif a.mode == "sync":
        out = run_sync(a.limit, a.ticker)
    elif a.mode == "submit":
        out = submit_batch(a.limit)
    else:
        out = collect_batch(a.batch_id)
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/asx_extract_status.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
