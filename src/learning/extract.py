"""Read the window documents for the ten features, under the contract.

The model is a feature extractor, never a learner: it answers ten fixed
questions per document and quotes the sentence for each non-silent answer.
This module enforces the contract in code (au_lic.extract.guards is the
precedent): out-of-vocabulary values, quotes not in the document, computed-
signal keys at any depth and low confidence are rejected and the rejection
recorded - a fund whose window yields nothing must be explainable.

Documents come from where they already are: an AU announcement is the PDF
archived under asx/announcements/, a UK announcement is fetched from
Investegate once (1.5s throttle) and archived under uk/announcements/ so
the next run reads S3, never the site again. A manifest of read
announcement ids in the bucket makes every run resumable.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from au_lic.extract.guards import _norm, forbidden_keys
from cef_live import catalyst_terms
from learning import schema as S

PROMPT_F = Path("config/prompts/resolution_features_v1.md")
OUT_DIR = Path("data/learning")
BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = "learning"
UK_ARCHIVE_PREFIX = "uk/announcements"
MANIFEST_KEY = f"{S3_PREFIX}/manifest/features_v2.json"
MAX_CONSECUTIVE_ERRORS = 3

FEATURE_COLUMNS = list(S.FEATURES)
ROW_COLUMNS = (["security_id", "market", "ticker", "ann_id", "date", "obs_month",
                "end_month", "headline", "family", "document_kind"] + FEATURE_COLUMNS
               + ["quotes", "stated_dates", "quote_matches", "feature_rejects",
                  "confidence", "model", "prompt_version",
                  "extracted_at", "input_tokens", "output_tokens", "cache_read"])


def prompt_text() -> str:
    return PROMPT_F.read_text()


def prompt_version() -> str:
    return "v1:" + hashlib.sha256(PROMPT_F.read_bytes()).hexdigest()[:16]


def model_name() -> str:
    return (os.environ.get("LEARNING_MODEL") or os.environ.get("CHEAP_MODEL")
            or "claude-opus-5")


def request_params(doc: dict, text: str) -> dict:
    """The instructions sit in `system` under a cache breakpoint; only the
    document varies between calls."""
    return {
        "model": model_name(),
        "max_tokens": 2048,
        "system": [{"type": "text", "text": prompt_text(),
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content":
                      f"security_id: {doc.get('security_id')}\n"
                      f"published: {doc.get('date')}\n"
                      f"headline: {doc.get('headline')}\n\n"
                      f"document_text:\n{(text or '')[:S.MAX_DOC_CHARS]}"}],
    }


# ------------------------------------------------------------------ contract
GUARD_VERSION = "g2"          # bumped when the contract's checks change


def _flat(text: str) -> str:
    """_norm plus what PDF extraction does to a sentence: a word broken by a
    hyphen at a line end, a page marker inside a paragraph, punctuation the
    model tidies. Digits and letters are untouched."""
    t = re.sub(r"--- PAGE \d+ ---", " ", text or "")
    t = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", t)   # a word broken at a line end
    t = _norm(t)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%$£€.]", " ", t)).strip()


def quote_match(quote: str, doc_text: str) -> str | None:
    """'exact' when the quote is in the document, 'fuzzy' when at least
    FUZZY_MIN of its word bigrams are and it is at least six words long,
    None otherwise. A number the model changed breaks bigrams either side
    of it, so fuzzy still fails on an invented figure."""
    if not quote or not doc_text:
        return None
    if _norm(quote) in _norm(doc_text):
        return "exact"
    q, d = _flat(quote), _flat(doc_text)
    if q and q in d:
        return "exact"
    words = q.split()
    if len(words) < 6:
        return None
    dwords = d.split()
    # a figure is never fuzzy: every number in the quote must be in the
    # document as written (its digits, whatever wraps them)
    nums = re.compile(r"\d(?:[\d,.]*\d)?")
    if not set(nums.findall(q)) <= set(nums.findall(d)):
        return None
    dset = set(zip(dwords, dwords[1:]))
    grams = list(zip(words, words[1:]))
    hit = sum(1 for g in grams if g in dset)
    return "fuzzy" if hit / len(grams) >= FUZZY_MIN else None


FUZZY_MIN = 0.85


def guard(rec, doc_text: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """(document problems, per-feature problems, per-feature quote match).

    A document problem rejects the record. A feature problem lapses that
    one feature to its silent value and is recorded against it - the
    features the document did quote survive."""
    if not isinstance(rec, dict):
        return ["not_an_object"], {}, {}
    problems: list[str] = []
    for key in forbidden_keys(rec):
        problems.append(f"computed_signal_field:{key}")
    kind = rec.get("document_kind")
    if kind not in S.DOCUMENT_KINDS:
        problems.append(f"enum:document_kind={kind!r}")
    feats = rec.get("features")
    if not isinstance(feats, dict):
        return problems + ["features_missing"], {}, {}
    quotes = rec.get("quotes") if isinstance(rec.get("quotes"), dict) else {}
    feature_problems: dict[str, str] = {}
    matches: dict[str, str] = {}
    for name, allowed in S.FEATURES.items():
        val = feats.get(name)
        if val not in allowed:
            feature_problems[name] = f"enum:{val!r}"
            continue
        if val == S.SILENT[name]:
            continue
        q = quotes.get(name)
        if not isinstance(q, str) or not q.strip():
            feature_problems[name] = "no_quote"
            continue
        m = quote_match(q, doc_text)
        if m is None:
            feature_problems[name] = "quote_not_in_document"
        else:
            matches[name] = m
    for d in rec.get("stated_dates") or []:
        if not isinstance(d, dict) or not isinstance(d.get("date"), str) \
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]):
            problems.append("bad_date")
            break
    conf = rec.get("confidence")
    if not isinstance(conf, (int, float)):
        problems.append("confidence_missing")
    elif not 0.0 <= float(conf) <= 1.0:
        problems.append(f"confidence_out_of_range:{conf}")
    elif float(conf) < S.MIN_CONFIDENCE:
        problems.append(f"confidence_below_floor:{conf}")
    return problems, feature_problems, matches


def apply_feature_problems(rec: dict, feature_problems: dict[str, str]) -> dict:
    """The record with every rejected feature lapsed to silent and its quote
    dropped."""
    out = json.loads(json.dumps(rec))
    for name in feature_problems:
        out["features"][name] = S.SILENT[name]
        if isinstance(out.get("quotes"), dict):
            out["quotes"].pop(name, None)
    return out


def extract_one(client, doc: dict, text: str) -> tuple[dict | None, dict]:
    """One document -> (accepted record or None, audit). An unparseable
    reply is asked for once more; a feature whose quote is not in the
    document lapses to silent and is recorded in audit["feature_rejects"]."""
    params = request_params(doc, text)
    rec = None
    audit: dict = {}
    for attempt in range(2):
        resp = client.messages.create(**params)
        usage = getattr(resp, "usage", None)
        audit = {"model": params["model"], "prompt_version": prompt_version(),
                 "stop_reason": getattr(resp, "stop_reason", None),
                 "input_tokens": getattr(usage, "input_tokens", None),
                 "output_tokens": getattr(usage, "output_tokens", None),
                 "cache_read": getattr(usage, "cache_read_input_tokens", None),
                 "attempts": attempt + 1}
        if getattr(resp, "stop_reason", None) == "refusal":
            audit["rejected"] = ["refusal"]
            return None, audit
        out_text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", [])
                           if getattr(b, "type", "") == "text")
        rec = catalyst_terms.parse_response(out_text)
        if rec is not None:
            break
    if rec is None:
        audit["rejected"] = ["unparseable"]
        return None, audit
    problems, feature_problems, matches = guard(rec, text)
    if problems:
        audit["rejected"] = problems
        audit["feature_rejects"] = feature_problems
        return None, audit
    audit["feature_rejects"] = feature_problems
    audit["quote_matches"] = matches
    return apply_feature_problems(rec, feature_problems), audit


def flatten(rec: dict, doc: dict, audit: dict) -> dict:
    feats = rec["features"]
    row = {k: doc.get(k) for k in ("security_id", "market", "ticker", "ann_id", "date",
                                   "obs_month", "end_month", "headline", "family")}
    row["document_kind"] = rec.get("document_kind")
    for name in FEATURE_COLUMNS:
        row[name] = feats.get(name)
    row["quotes"] = json.dumps({k: str(v)[:400] for k, v in (rec.get("quotes") or {}).items()
                                if isinstance(rec.get("quotes"), dict)})
    row["stated_dates"] = json.dumps(rec.get("stated_dates") or [])
    row["quote_matches"] = json.dumps(audit.get("quote_matches") or {})
    row["feature_rejects"] = json.dumps(audit.get("feature_rejects") or {})
    row["confidence"] = float(rec.get("confidence"))
    row.update({k: audit.get(k) for k in ("model", "prompt_version", "input_tokens",
                                          "output_tokens", "cache_read")})
    row["extracted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return row


# ------------------------------------------------------------------ documents
def _s3():
    if not BUCKET:
        return None
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION"))


def read_manifest(s3) -> set[str]:
    if s3 is None:
        return set()
    try:
        body = s3.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)["Body"].read()
        return set(json.loads(body).get("ids", []))
    except Exception:  # noqa: BLE001
        return set()


def write_manifest(s3, done: set[str]) -> None:
    if s3 is None:
        return
    s3.put_object(Bucket=BUCKET, Key=MANIFEST_KEY,
                  Body=json.dumps({"ids": sorted(done)}).encode())


def _uk_key(doc: dict) -> str:
    return f"{UK_ARCHIVE_PREFIX}/{doc['ticker']}/{doc['date']}_{doc['ann_id']}.json.gz"


def document_text(doc: dict, session=None, s3=None, throttle: float = 1.5) -> str:
    """The document's text from the archive, or (UK) one fetch that is then
    archived. '' when unreadable."""
    if doc.get("market") == "AU":
        if s3 is None:
            return ""
        from au_lic.extract import runner as au_runner
        return au_runner.fetch_pdf_text(
            s3, {"ticker": doc["ticker"], "day": doc["date"],
                 "announcement_id": doc["ann_id"]}) or ""
    if s3 is not None:
        try:
            body = s3.get_object(Bucket=BUCKET, Key=_uk_key(doc))["Body"].read()
            return json.loads(gzip.decompress(body)).get("text", "")
        except Exception:  # noqa: BLE001
            pass
    if session is None:
        return ""
    url = doc.get("url") or ""
    if url.startswith("/"):
        url = "https://www.investegate.co.uk" + url
    text = catalyst_terms.fetch_body(url, session, throttle=throttle)
    text = text.split("This information is provided by RNS")[0][:S.MAX_DOC_CHARS]
    if text and s3 is not None:
        payload = gzip.compress(json.dumps({**{k: doc.get(k) for k in doc},
                                            "url": url, "text": text}).encode())
        try:
            s3.put_object(Bucket=BUCKET, Key=_uk_key(doc), Body=payload)
        except Exception:  # noqa: BLE001
            pass
    return text


# ------------------------------------------------------------------ the run
def run(docs: pd.DataFrame, budget: int = 200, deadline_min: float = 240.0,
        client=None, session=None, s3=None, done: set[str] | None = None,
        text_fn=None) -> tuple[list[dict], list[dict], dict]:
    """Read up to `budget` documents not in the manifest. Errors are not
    verdicts on documents: three in a row abort the run."""
    start = time.time()
    if done is None:
        done = read_manifest(s3)
    if text_fn is None:
        def text_fn(d):
            return document_text(d, session=session, s3=s3)
    rows: list[dict] = []
    rejects: list[dict] = []
    stats = {"candidates": int(len(docs)), "read": 0, "accepted": 0, "rejected": 0,
             "empty": 0, "errors": 0, "aborted": False, "skipped_done": 0}
    errors_in_row = 0
    for doc in docs.to_dict("records"):
        if (time.time() - start) > deadline_min * 60 or stats["read"] >= budget:
            break
        key = f"{doc['security_id']}|{doc['ann_id']}"
        # a rejection is keyed with the prompt and guard version: a change
        # to either reads the document again, an acceptance stands
        rkey = f"{key}|{prompt_version()}|{GUARD_VERSION}"
        if key in done or rkey in done:
            stats["skipped_done"] += 1
            continue
        text = text_fn(doc)
        if not text or len(text) < 200:
            stats["empty"] += 1
            done.add(rkey)
            continue
        stats["read"] += 1
        try:
            if client is None:
                client = catalyst_terms.make_client()
            rec, audit = extract_one(client, doc, text)
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            stats["last_error"] = str(exc)[:200]
            errors_in_row += 1
            if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                stats["aborted"] = True
                break
            continue
        errors_in_row = 0
        if rec is None:
            stats["rejected"] += 1
            done.add(rkey)
            rejects.append({**{k: doc.get(k) for k in ("security_id", "market", "ann_id",
                                                        "date", "headline")},
                            "reasons": "|".join(audit.get("rejected") or []),
                            "feature_rejects": json.dumps(audit.get("feature_rejects") or {})})
            continue
        done.add(key)
        stats["accepted"] += 1
        fr = audit.get("feature_rejects") or {}
        stats["features_lapsed"] = stats.get("features_lapsed", 0) + len(fr)
        stats["features_fuzzy"] = stats.get("features_fuzzy", 0) + sum(
            1 for v in (audit.get("quote_matches") or {}).values() if v == "fuzzy")
        rows.append(flatten(rec, doc, audit))
    write_manifest(s3, done)
    return rows, rejects, stats


def write_outputs(rows: list[dict], rejects: list[dict], tag: str,
                  out_dir: Path = OUT_DIR, s3=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    if rows:
        p = out_dir / f"features_{tag}.parquet"
        new = pd.DataFrame(rows)
        if p.exists():
            old = pd.read_parquet(p)
            new = pd.concat([old, new], ignore_index=True).drop_duplicates(
                ["security_id", "ann_id"], keep="last")
        new.to_parquet(p, index=False)
        written["features"] = str(p)
    if rejects:
        p = out_dir / f"rejects_{tag}.parquet"
        new = pd.DataFrame(rejects)
        if p.exists():
            new = pd.concat([pd.read_parquet(p), new], ignore_index=True)
        new.to_parquet(p, index=False)
        written["rejects"] = str(p)
    if s3 is not None:
        for p in written.values():
            try:
                s3.upload_file(p, BUCKET, f"{S3_PREFIX}/{Path(p).name}")
            except Exception:  # noqa: BLE001
                pass
    return written


def load_features(out_dir: Path = OUT_DIR) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for p in sorted(out_dir.glob("features_*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=ROW_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(["security_id", "ann_id"], keep="last")
