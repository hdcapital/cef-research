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
    idx = router.route_index(pd.read_parquet(INDEX_F))
    idx = idx[idx["route"] == "deterministic"].copy()
    idx["announcement_id"] = idx["id"].astype(str)
    idx["published_at"] = pd.to_datetime(idx["release_date"], utc=True,
                                         errors="coerce").dt.strftime("%Y-%m-%d")
    idx = idx[idx["published_at"].notna() & ~idx["announcement_id"].isin(done)]
    idx = idx.rename(columns={"code": "ticker"})
    idx["day"] = idx["published_at"]
    if SHARDS > 1:
        idx = idx[idx["announcement_id"].map(
            lambda i: zlib.crc32(i.encode()) % SHARDS == SHARD)]
    idx = idx.sort_values("published_at", ascending=False)
    if limit:
        idx = idx.head(limit)
    print(f"deterministic: {len(idx)} documents (shard {SHARD + 1}/{SHARDS})")

    started = time.time()
    rows: list[dict] = []
    escalations: list[dict] = []
    stats = {"documents": 0, "no_pdf": 0, "unreadable": 0, "parsed": 0,
             "escalated": 0, "by_family": {}}
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
                             "nav_basis", "extractor")}})
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
    stats["parse_rate"] = round(stats["parsed"] / max(1, stats["documents"]), 4)
    return stats


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
                                     "prices"])
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
    if a.mode == "prices":
        out = run_prices(limit=a.limit)
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        Path("reports/build/asx_price_coverage.json").write_text(
            json.dumps(out, indent=2, default=str))
        print(json.dumps({k: v for k, v in out.items()
                          if k != "codes_missing"}, indent=2, default=str))
        return 0
    if a.mode == "deterministic":
        out = run_deterministic(limit=a.limit)
        Path("reports/build").mkdir(parents=True, exist_ok=True)
        Path("reports/build/asx_deterministic_status.json").write_text(
            json.dumps(out, indent=2, default=str))
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
