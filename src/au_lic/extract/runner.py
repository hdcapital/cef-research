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

MODEL = os.environ.get("EXTRACT_MODEL", "claude-opus-5")
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
def load_queue(limit: int = 0, ticker: str | None = None) -> pd.DataFrame:
    idx = pd.read_parquet(INDEX_F)
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


def read_manifest() -> set[str]:
    if not BUCKET:
        return set()
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    done: set[str] = set()
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=BUCKET, Prefix=MANIFEST_PREFIX):
            for o in page.get("Contents", []):
                try:
                    body = s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
                    done |= set(json.loads(body).get("ids", []))
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        print(f"manifest listing failed ({exc}); starting from empty")
    return done


def write_manifest(done: set[str]) -> None:
    if not BUCKET:
        return
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    key = (f"{MANIFEST_PREFIX}.json" if SHARDS == 1
           else f"{MANIFEST_PREFIX}_s{SHARD}of{SHARDS}.json")
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


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sync", "submit", "collect", "queue"])
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--ticker")
    ap.add_argument("--batch-id")
    a = ap.parse_args(argv)

    if a.mode == "queue":
        q = load_queue(limit=a.limit, ticker=a.ticker)
        print(json.dumps({"queued": int(len(q)),
                          "prompt_version": prompt_version(),
                          "sample": q.head(5)[["announcement_id", "ticker",
                                               "published_at"]].to_dict("records")},
                         indent=2, default=str))
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set - extraction skipped")
        return 0
    if a.mode == "sync":
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
