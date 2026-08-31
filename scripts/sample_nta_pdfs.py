"""Independent number-level NTA validation across the full panel history.

Probes 7/8 (browser capture of the public announcements page + follow-up
endpoint verification) established that www.asx.com.au/asx/1/announcement/list
serves the ENTIRE market's announcement history unauthenticated - roughly
2,000 rows per call paginated backwards by end_date, delisted issuers
included, each row carrying a direct, working PDF URL on
announcements.asx.com.au. That replaces both the 5-item capped per-company
API and the tokened Markit listing.

This script:
1. sweeps that index backwards to the panel start (2016-11), keeping only
   rows for LIC codes in our panel (cached incrementally - a full first
   sweep is ~700 throttled calls, later runs only fetch the new top);
2. picks month-end NTA statements from headlines (explicit "as at" dates,
   or month-year titles like "Monthly NTA Statement - July 2020");
3. samples up to one per code per year, fetches the PDF, parses the stated
   pre-tax NTA PER SHARE (per-share patterns take priority; $-totals in
   millions are excluded; cents vs dollars resolved from context only -
   ambiguity is flagged, never silently corrected);
4. compares with the panel's derived NTA for that month.

Writes outputs/au/au_nta_pdf_check.csv + au_nta_pdf_check_summary.json.
First run is budget-bounded; caches make later runs incremental.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import pandas as pd
import requests

INDEX_URL = "https://www.asx.com.au/asx/1/announcement/list"
UA = ("uk-cef-research/0.1 (academic closed-end-fund research; "
      "contact: danielconorsims@gmail.com; ~1 req/1.5s)")
THROTTLE = 1.5
# Index calls get their own spacing. The endpoint answers the first request
# and then times out, which is a rate limit expressed as a timeout, so the
# only respectful lever is to ask less often. 60s is the slow-crawl setting.
INDEX_THROTTLE = float(os.environ.get("NTA_INDEX_THROTTLE", "1.5"))
INDEX_DEADLINE_MIN = float(os.environ.get("NTA_INDEX_DEADLINE_MIN", "0"))  # 0 = off
MAX_CONSECUTIVE_INDEX_FAILURES = int(
    os.environ.get("NTA_MAX_CONSECUTIVE_FAILURES", "5"))
# forward = daily keep-up: crawl back only until we overlap the newest row we
#   already hold, which is a handful of calls and never gets throttled.
# gapfill = close a historical hole: crawl back to the top of the CONTIGUOUS
#   block, which is ~1,000 calls and does get throttled.
# The original code only had the forward behaviour. That is correct for
# keeping up and wrong for filling a hole, and when a hole opened it silently
# became the wrong tool - which is how 2024-2025 went missing while every run
# reported success.
SWEEP_MODE = os.environ.get("NTA_SWEEP_MODE", "forward")
EARLIEST = pd.Timestamp("2016-11-01", tz="Australia/Sydney")
SWEEP_BUDGET = int(os.environ.get("NTA_SWEEP_BUDGET", "800"))   # index calls per run
PDF_BUDGET = int(os.environ.get("NTA_PDF_BUDGET", "600"))       # new PDFs per run
# how deep to read into one document before giving up on finding an NTA
PDF_PAGES = int(os.environ.get("NTA_PDF_PAGES", "8"))
# ...and how much of what those pages contain to keep. This is DERIVED from
# the page budget rather than set beside it, because the two bounds have to
# move together: 20,000 characters never bound anything while the reader
# took two pages, and would have started clipping the moment it took eight.
# UWC's seven-page July statement is 15,371 characters with its NTA table at
# character 5,684, so a longer monthly report would have lost the table to a
# bound chosen for a smaller read - the same silent truncation as the page
# cap, one layer down.
PDF_TEXT_CHARS = int(os.environ.get("NTA_PDF_TEXT_CHARS", str(PDF_PAGES * 8000)))
PDF_TABLE_ROWS = int(os.environ.get("NTA_PDF_TABLE_ROWS", str(PDF_PAGES * 20)))
DEADLINE_MIN = int(os.environ.get("NTA_DEADLINE_MIN", "180"))   # wall-clock cap
START = time.time()

CACHE = Path("data/asx_ann_cache/asx1")
CACHE.mkdir(parents=True, exist_ok=True)
INDEX_F = CACHE / "lic_announcement_index.parquet"
STATE_F = CACHE / "sweep_state.json"
# v2: caches the *extracted* text/table rows, so parser improvements can be
# re-applied to already-fetched PDFs without re-downloading anything
PARSE_DIR = CACHE / "pdf_extract"
PARSE_DIR.mkdir(exist_ok=True)

NTA_HEAD = re.compile(r"\bNTA\b|net tangible|net asset|\bNAV\b", re.I)
ASAT = re.compile(r"as at\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(20[0-9]{2})\b", re.I)
WEEKLY = re.compile(r"week|daily|amendment|amended|correction|withdraw", re.I)

_NUM = r"([0-9]+(?:\.[0-9]{1,4})?)"
_UNIT = r"\s*(cents|cps|c\b|¢)?"
# strict per-share/pre-tax patterns only: the generic "net tangible
# assets ... $X" forms matched fund-level totals and boilerplate
NTA_PATTERNS = [
    re.compile(r"(?:pre|before)[- ]tax\s+NTA(?:\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit))?"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:per\s+(?:share|security|unit)\s+)?(?:before|pre)[- ]tax"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"net\s+tangible\s+assets?\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9%]{0,80}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NTA\s+(?:backing\s+)?per\s+(?:ordinary\s+)?(?:share|security|unit)"
               r"[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
    re.compile(r"NAV\s+per\s+(?:share|security|unit)[^0-9%]{0,60}(\$)?\s*" + _NUM + _UNIT, re.I),
]
MILLIONS = re.compile(r"^\s*(?:million|billion|m\b|bn\b|'?000)", re.I)

# table-row labels, most specific first; "after tax" rows are rejected
ROW_PRETAX = re.compile(r"(?:pre|before)[- ]tax", re.I)
ROW_POSTTAX = re.compile(r"(?:post|after)[- ]tax", re.I)
ROW_PERSHARE = re.compile(r"per\s+(?:ordinary\s+)?(?:share|security|unit)", re.I)
ROW_NTA = re.compile(r"\bNTA\b|net\s+tangible|NAV\b|net\s+asset\s+value", re.I)
ROW_EXCLUDE = re.compile(r"premium|discount|total|million|change|return|%", re.I)
NOT_NTA = re.compile(r"dividend|distribution|paid|declared|buy[- ]?back|issue\s+price|exercise", re.I)
CELL_VAL = re.compile(r"(\$)?\s*([0-9]+(?:\.[0-9]{1,4})?)\s*(cents|cps|¢|c\b)?", re.I)

_last = 0.0


def throttled_get(s: requests.Session, url: str, throttle: float | None = None,
                  **kw) -> requests.Response:
    global _last
    wait = (THROTTLE if throttle is None else throttle) - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return s.get(url, timeout=60, **kw)



def contiguous_frontier(idx: pd.DataFrame, max_gap_days: int = 45) -> pd.Timestamp | None:
    """Newest date reachable from the OLDEST record without a long silence.

    The top-up sweep used to stop when it overlapped the index's global
    maximum date. After one budget-limited pass wrote a block of recent
    announcements, that maximum jumped to the recent frontier - so the next
    pass overlapped it on its FIRST call and stopped immediately. The
    un-swept middle became permanent and the bug sealed itself: the more
    recent data it fetched, the sooner it stopped.

    The frontier that matters is the top of the CONTIGUOUS block, not the
    newest row anywhere. A market-wide index has announcements every trading
    day, so a gap beyond a few weeks is a hole, not a quiet patch.
    """
    if idx is None or not len(idx):
        return None
    d = pd.to_datetime(idx["release_date"], utc=True, errors="coerce").dropna()
    if d.empty:
        return None
    days = pd.Series(sorted(d.dt.normalize().unique()))
    if len(days) == 1:
        return days.iloc[0]
    gaps = days.diff().dt.days
    breaks = gaps[gaps > max_gap_days]
    if breaks.empty:
        return days.iloc[-1]
    return days.iloc[breaks.index[0] - 1]


def _load_existing_index() -> list[pd.DataFrame]:
    """Every copy of the index we hold, unioned - never just one of them.

    The index lives in TWO places: committed in git, and in S3. The workflow
    restores from S3 over the checkout, then commits the result back to git.
    So whenever S3 was behind git - which happens whenever a run crawled,
    committed, and then failed before its S3 push, exactly what the crashed
    22:51 run did - the next run silently reverted git to S3's older content
    and 4,269 real 2025 announcements were deleted by a job whose logs said
    it had added rows.

    The index is append-only and keyed by a unique id, so the only safe
    reading of two disagreeing copies is their union. Replacing is never
    correct, in either direction.
    """
    frames: list[pd.DataFrame] = []
    if INDEX_F.exists():
        try:
            frames.append(pd.read_parquet(INDEX_F))
        except Exception as exc:  # noqa: BLE001
            print(f"on-disk index unreadable ({exc})")
    # the committed copy, which the S3 restore has just written over
    try:
        import subprocess
        r = subprocess.run(["git", "show", f"HEAD:{INDEX_F.as_posix()}"],
                           capture_output=True, timeout=120)
        if r.returncode == 0 and r.stdout:
            import io
            frames.append(pd.read_parquet(io.BytesIO(r.stdout)))
    except Exception as exc:  # noqa: BLE001
        print(f"committed index unavailable ({exc}); using on-disk copy only")
    if len(frames) > 1:
        merged = pd.concat(frames, ignore_index=True).drop_duplicates("id")
        print(f"index: unioned {[len(f) for f in frames]} -> {len(merged):,} rows")
        return [merged]
    return frames


def sweep_index(s: requests.Session, codes: set[str], counters: dict) -> pd.DataFrame:
    """Backward sweep of the market-wide announcement index; keep our codes.

    The kept-code set is recorded in the sweep state. If the registry later
    gains codes (a fund lists, or entity resolution improves), the history
    is re-swept for them rather than leaving those funds permanently absent
    from the index - which is how UWC, AIX, MRE, PCX and WHI ended up with
    no announcements and therefore no Tier 0 NAV.
    """
    import hashlib
    frames = _load_existing_index()
    state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    code_sig = hashlib.md5(",".join(sorted(codes)).encode()).hexdigest()[:12]
    if state.get("code_sig") and state["code_sig"] != code_sig:
        have = set(frames[0]["code"]) if frames else set()
        new_codes = codes - have
        if new_codes:
            print(f"registry gained {len(new_codes)} codes since last sweep "
                  f"({sorted(new_codes)[:8]}...); re-sweeping history")
            state = {"hist_done": False, "earliest_ms": None}
            counters["resweep_for_new_codes"] = sorted(new_codes)
    # two frontiers: history (sweep back to EARLIEST once) and the live top
    hist_done = state.get("hist_done", False)
    end_ms = state.get("earliest_ms")  # resume point for the history sweep
    frontier = contiguous_frontier(frames[0]) if frames else None
    newest_held = (pd.to_datetime(frames[0]["release_date"], utc=True,
                                  errors="coerce").max() if frames else None)
    counters["sweep_mode"] = SWEEP_MODE
    if hist_done or end_ms is None:
        # the endpoint returns nothing without an end_date (probe 8).
        # Resume a part-finished top pass where it stopped; otherwise start
        # from "now". Overlap is deduped by id.
        end_ms = state.get("top_cursor_ms") or int(time.time() * 1000)
    if frontier is not None:
        counters["contiguous_frontier"] = str(frontier.date())
    new_rows: list[dict] = []
    top_pass_calls = 0

    def _checkpoint() -> None:
        """Make what has been crawled so far durable, mid-run.

        The index used to be written only after the loop, so ANY exception
        threw away the whole run's rows - a JSONDecodeError on one malformed
        payload discarded 22 minutes of real crawling. Crawled rows are
        expensive (a throttled endpoint, ~6 days per call); losing them to a
        later failure is never acceptable, so they are flushed periodically
        and deduped by id on the way in.
        """
        if not new_rows:
            return
        cur = pd.concat(frames + [pd.DataFrame(new_rows)],
                        ignore_index=True).drop_duplicates("id")
        cur.to_parquet(INDEX_F, index=False)

    while counters["index_calls"] < SWEEP_BUDGET:
        if INDEX_DEADLINE_MIN and (time.time() - START) > INDEX_DEADLINE_MIN * 60:
            counters["index_deadline_hit"] = True
            state["top_cursor_ms"] = end_ms
            STATE_F.write_text(json.dumps(state))
            break
        url = f"{INDEX_URL}?end_date={end_ms}"
        counters["index_calls"] += 1
        # flush on BOTH passes: the history sweep is the longer of the two,
        # so checkpointing only the top-up pass would have left the bigger
        # crawl exposed to exactly the loss this guards against
        if counters["index_calls"] % 10 == 0:
            _checkpoint()
        # A timeout used to propagate out of the sweep and end it - one bad
        # call killed a whole run, and `|| true` in the workflow hid it. Now
        # it is counted and the crawl continues, because on a throttled
        # endpoint SOME calls timing out is the expected condition, not a
        # failure of the run.
        try:
            r = throttled_get(s, url, throttle=INDEX_THROTTLE,
                              headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001
            counters["index_timeouts"] = counters.get("index_timeouts", 0) + 1
            counters["consecutive_failures"] = counters.get("consecutive_failures", 0) + 1
            counters["last_index_error"] = f"{type(exc).__name__}"
            state["top_cursor_ms"] = end_ms
            STATE_F.write_text(json.dumps(state))
            _checkpoint()
            if counters["consecutive_failures"] >= MAX_CONSECUTIVE_INDEX_FAILURES:
                counters["index_error"] = "consecutive_timeouts"
                break
            time.sleep(min(120, 15 * counters["consecutive_failures"]))  # back off
            continue
        # NB: the failure streak is reset after a successful PARSE, not
        # here. Resetting on transport success alone let a run that got a
        # 200 with an unusable body every single time loop until the budget
        # ran out, streak stuck at 1 and the stop condition never reached.
        counters["index_ok"] = counters.get("index_ok", 0) + 1
        if r.status_code != 200:
            counters["index_error"] = f"http_{r.status_code}"
            break
        txt = r.text
        m = re.match(r"^[\w$]+\((.*)\)\s*;?\s*$", txt, re.S)
        # A malformed or truncated body is the same kind of event as a
        # timeout - the endpoint under load returning something unusable -
        # and it must be handled the same way. It was not: json.loads sat
        # outside the try, so one clipped payload propagated out and ended
        # the run, which is exactly the failure the transport retry above
        # was written to prevent. Retry the SAME window; never guess at the
        # content of a body that did not arrive intact.
        try:
            data = json.loads(m.group(1) if m else txt)
            items = data.get("announcement_data") or []
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            counters["index_bad_payloads"] = counters.get("index_bad_payloads", 0) + 1
            counters["consecutive_failures"] = counters.get("consecutive_failures", 0) + 1
            counters["last_index_error"] = f"{type(exc).__name__}"
            state["top_cursor_ms"] = end_ms
            STATE_F.write_text(json.dumps(state))
            _checkpoint()
            if counters["consecutive_failures"] >= MAX_CONSECUTIVE_INDEX_FAILURES:
                counters["index_error"] = "consecutive_bad_payloads"
                break
            time.sleep(min(120, 15 * counters["consecutive_failures"]))
            continue
        counters["consecutive_failures"] = 0
        if not items:
            break
        for it in items:
            if it.get("issuer_code") in codes:
                new_rows.append({
                    "id": it.get("id"),
                    "code": it.get("issuer_code"),
                    "release_date": it.get("document_release_date"),
                    "headline": it.get("header"),
                    "url": it.get("url"),
                })
        dates = pd.to_datetime([i["document_release_date"] for i in items],
                               utc=True, errors="coerce")
        oldest = dates.min()
        counters.setdefault("days_per_call", []).append(
            int((dates.max() - oldest).days) + 1)
        end_ms = int(oldest.value // 10**6) - 1
        if hist_done:
            # top-up pass: sweep back until it reaches the top of the
            # CONTIGUOUS block, closing any gap, rather than the newest row
            # anywhere - which a previous partial pass may have planted far
            # ahead of the real frontier
            top_pass_calls += 1
            # forward mode stops at the newest row already held: it is
            # keeping up, not repairing. gapfill mode aims at the contiguous
            # frontier and will crawl through a hole to reach it.
            target = frontier if SWEEP_MODE == "gapfill" else newest_held
            if target is not None and oldest <= target:
                state["top_cursor_ms"] = None
                STATE_F.write_text(json.dumps(state))
                counters["reached_target"] = str(target.date())
                break
            # One control, not two. The 60-call cap here was a second,
            # tighter budget than SWEEP_BUDGET, and 60 calls is a few weeks
            # of a market-wide index - nowhere near a 2.5-year gap, so the
            # hole could never close within a run however large the real
            # budget was. SWEEP_BUDGET now governs; the cursor makes a
            # part-finished pass resumable.
            state["top_cursor_ms"] = end_ms
            STATE_F.write_text(json.dumps(state))
            counters["top_pass_resuming_at"] = end_ms
        else:
            state = {"hist_done": False, "earliest_ms": end_ms,
                     "code_sig": code_sig}
            STATE_F.write_text(json.dumps(state))
            if oldest.tz_convert("Australia/Sydney") < EARLIEST:
                state["hist_done"] = True
                state["code_sig"] = code_sig
                STATE_F.write_text(json.dumps(state))
                break
    else:
        counters["sweep_budget_hit"] = True
    if new_rows:
        frames.append(pd.DataFrame(new_rows))
    if not frames:
        return pd.DataFrame(columns=["id", "code", "release_date", "headline", "url"])
    idx = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    _d = pd.to_datetime(idx["release_date"], utc=True, errors="coerce").dropna()
    if len(_d):
        counters["index_span"] = f"{_d.min().date()} -> {_d.max().date()}"
        counters["index_rows_by_year"] = {
            str(k): int(v) for k, v in _d.dt.year.value_counts().sort_index().items()}
        _f = contiguous_frontier(idx)
        if _f is not None:
            counters["contiguous_frontier"] = str(_f.date())
            counters["gap_days_remaining"] = int((_d.max() - _f).days)
    idx.to_parquet(INDEX_F, index=False)
    counters["index_rows"] = len(idx)
    counters["hist_done"] = json.loads(STATE_F.read_text()).get("hist_done", False) \
        if STATE_F.exists() else False
    return idx


def headline_month(head: str) -> str | None:
    """Panel month an NTA headline refers to, or None."""
    if not NTA_HEAD.search(head) or WEEKLY.search(head):
        return None
    m = ASAT.search(head)
    if m:
        try:
            asat = pd.to_datetime(m.group(1), dayfirst=True)
        except Exception:  # noqa: BLE001
            return None
        return str(asat.to_period("M")) if asat.day >= 24 else None
    m = MONTH_YEAR.search(head)
    if m:  # "Monthly NTA Statement - July 2020" style: month-end implied
        return str(pd.Period(f"{m.group(2)}-{m.group(1)[:3]}", freq="M"))
    return None


def pick_candidates(idx: pd.DataFrame) -> pd.DataFrame:
    """One month-end NTA announcement per (code, month) - latest release."""
    rows = []
    for r in idx.itertuples(index=False):
        month = headline_month(r.headline or "")
        if month and r.url:
            rows.append({"code": r.code, "month": month, "id": r.id,
                         "headline": r.headline, "url": r.url,
                         "release_date": r.release_date})
    if not rows:
        return pd.DataFrame(columns=["code", "month", "id", "headline", "url"])
    df = pd.DataFrame(rows).sort_values("release_date")
    return df.groupby(["code", "month"], as_index=False).last()


def sample_months(months: list[str]) -> list[str]:
    """Up to two months per calendar year: June AND December when present.

    June is Australia's fiscal year-end, when cum/ex-dividend NTA effects
    peak - December pairs let genuine basis differences be told apart from
    June-specific timing artifacts.
    """
    by_year: dict[str, list[str]] = {}
    for m in months:
        by_year.setdefault(m[:4], []).append(m)
    picked = []
    for _, ms in sorted(by_year.items()):
        ms = sorted(ms)
        year_pick = [m for m in ms if m.endswith("-06")] + [m for m in ms if m.endswith("-12")]
        picked.extend(year_pick or ms[:1])
    return picked


def _classify_value(dollar: str | None, num: str, unit: str | None) -> dict:
    val = float(num)
    if unit:
        return {"stated_raw": val, "unit": "cents"}
    if dollar:
        return {"stated_raw": val, "unit": "dollars"}
    # bare number: LIC per-share NTAs are quoted both as dollars (1.23)
    # and cents (123.45); only context can separate them
    return {"stated_raw": val, "unit": "dollars" if val < 20 else "ambiguous"}


def parse_nta_rows(rows: list[list[str]]) -> dict | None:
    """Pre-tax per-share NTA from table rows: label cell -> value cell.

    Rows are ranked: pre-tax + per-share beats pre-tax beats plain
    NTA-per-share; 'after tax', premium/discount, totals and %-cells are
    never used.
    """
    best = None
    for row in rows:
        cells = [str(c) if c is not None else "" for c in row]
        label_end = 0
        label = ""
        for i, c in enumerate(cells):
            if CELL_VAL.search(c) and re.search(r"[0-9]", c):
                label_end = i
                break
            label += " " + c
        else:
            continue
        if not ROW_NTA.search(label) or ROW_POSTTAX.search(label) \
                or ROW_EXCLUDE.search(label):
            continue
        score = 1 + (2 if ROW_PRETAX.search(label) else 0) \
            + (1 if ROW_PERSHARE.search(label) else 0)
        if score < 2:   # bare "NTA" labels grab stray numbers ("Top 25...")
            continue
        for c in cells[label_end:]:
            if "%" in c or MILLIONS.search(c):
                continue
            m = CELL_VAL.search(c)
            if m:
                unit_hint = "cents" if re.search(r"cent|cps|¢", label, re.I) else None
                got = _classify_value(m.group(1), m.group(2), m.group(3) or unit_hint)
                if best is None or score > best[0]:
                    best = (score, got)
                break
    return best[1] if best else None


def parse_nta_text(text: str) -> dict:
    """Stated pre-tax per-share NTA from flowing text; unit from context.

    Candidate scoring, not first-rule-wins: real documents mix explicit
    labels with generic per-share phrasing, and either can appear first.
    Every rule contributes candidates; label specificity decides -
      3 = explicit pre-tax NTA label ("Pre-Tax NTA Backing per share")
      2 = per-share NTA/NAV label ("NTA per share", "NAV per unit ... was")
      1 = contextual ("$X per share" near an NTA mention; lazy before-tax)
    Ties break on document position (earliest wins). Evidence base:
    outputs/au/au_nta_parse_debug.json.
    """
    # newsletters glue footnote markers to labels: "NTA per share1 $8.45"
    text = re.sub(r"(?i)\b(share|security|unit)s?(\d)\b", r"\1 ", text)
    cands: list[tuple[int, int, dict]] = []   # (-score, position, value)

    def add(score, pos, d):
        cands.append((-score, pos, d))

    # score 1: $-value immediately followed by "per share", NTA named just
    # before it; never a dividend/buy-back/issue amount (same phrasing)
    for m in re.finditer(r"\$\s*" + _NUM + r"\s*per\s+(?:ordinary\s+)?(?:share|security|unit)",
                         text, re.I):
        pre = text[max(0, m.start() - 150):m.start()]
        near = pre[-90:]
        if NTA_HEAD.search(pre) and not NOT_NTA.search(near):
            out = {"stated_raw": float(m.group(1)), "unit": "dollars"}
            # some funds (BEL, KAT) headline the after-tax figure only:
            # keep it, tagged, rather than surface a worse match
            if re.search(r"after[- ]tax", near, re.I) and not ROW_PRETAX.search(near):
                out["basis"] = "post_tax"
            add(1, m.start(), out)

    # score 2: "NAV per unit ... as at <date> was $1.96701" / "NTA) per
    # share after tax ... was $0.858" - basis tagged when after-tax
    for m in re.finditer(r"(?:NAV|NTA)\)?\s+per\s+(?:share|security|unit)(.{0,140}?)"
                         r"was\s+\$\s*" + _NUM, text, re.I | re.S):
        out = {"stated_raw": float(m.group(2)), "unit": "dollars"}
        if ROW_POSTTAX.search(m.group(1)):
            out["basis"] = "post_tax"
        add(2, m.start(), out)

    # score 2: "NTA backing per share ... 255.1 c 233.5 c" (cents,
    # before-tax column first) - lazy to the first cents-suffixed value
    for m in re.finditer(r"(?:NAV|NTA)\)?\s+(?:backing\s+)?per\s+(?:ordinary\s+)?"
                         r"(?:share|security|unit)[^%$]{0,220}?" + _NUM +
                         r"\s*(?:cents|cps|c)\b", text, re.I | re.S):
        add(2, m.start(), {"stated_raw": float(m.group(1)), "unit": "cents"})

    # strict adjacency patterns: pre-tax labels score 3, per-share labels 2
    for pi, pat in enumerate(NTA_PATTERNS):
        score = 3 if pi < 2 else 2
        for m in pat.finditer(text):
            dollar, num, unit = m.group(1), m.group(2), m.group(3)
            tail = text[m.end():m.end() + 20]
            if MILLIONS.match(tail):    # a $-total, not a per-share figure
                continue
            if re.match(r"\s*(?:\*\s*\d|running yield)", tail, re.I):
                continue        # "1.28 cents * 4 quarters" = a dividend note
            if re.match(r"\s*%", tail):  # "% Change" column, not a level
                # the level itself usually follows: "+12.44% $0.1663"
                m2 = re.match(r"\s*%\s*\$\s*" + _NUM, tail)
                if m2:
                    add(score, m.start(), {"stated_raw": float(m2.group(1)),
                                           "unit": "dollars"})
                continue
            # bare integers ("Top 25 Investments") are never NTA quotes -
            # real ones carry decimals, $, or cents
            if "." not in num and not dollar and not unit:
                continue
            add(score, m.start(), _classify_value(dollar, num, unit))

    # score 1: before-tax label, lazy scan to the FIRST $-prefixed value;
    # a % or an intervening bare $ means a returns/holdings table
    for m in re.finditer(r"(?:pre|before)[- ]tax[^%$]{0,300}?\$\s*" + _NUM, text, re.I | re.S):
        pre = text[max(0, m.start() - 200):m.start() + 12]
        tail = text[m.end():m.end() + 20]
        if NTA_HEAD.search(pre) and not MILLIONS.match(tail):
            add(1, m.start(), {"stated_raw": float(m.group(1)), "unit": "dollars"})

    if not cands:
        return {"stated_raw": None, "unit": None}
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][2]


def derive_stated(extract: dict) -> dict:
    """Stated NTA from a cached extraction: table rows first, text second."""
    if extract.get("status") != "extracted":
        return extract
    got = parse_nta_rows(extract.get("rows") or [])
    if got is None:
        # mega-cell tables (whole layout in one cell) only surface via text,
        # so append the row text to the page text before the text pass
        rows_text = " ".join(" ".join(c for c in r if c)
                             for r in (extract.get("rows") or []))
        got = parse_nta_text((extract.get("text") or "") + " " + rows_text)
    status = "parsed" if got and got.get("stated_raw") is not None else "no_nta_in_pdf"
    return {"status": status, **(got or {})}


def parse_pdf(s: requests.Session, ann_id: str, url: str, counters: dict) -> dict:
    import pdfplumber

    cf = PARSE_DIR / f"{ann_id}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    if counters["pdf_calls"] >= PDF_BUDGET:
        counters["pdf_budget_hit"] = True
        return {"status": "budget_deferred"}
    if time.time() - START > DEADLINE_MIN * 60:
        counters["deadline_hit"] = True
        return {"status": "budget_deferred"}
    counters["pdf_calls"] += 1
    try:
        r = throttled_get(s, url)
    except Exception as exc:  # noqa: BLE001
        return {"status": f"pdf_error:{exc}"}    # transient - do not cache
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        res = {"status": f"pdf_http_{r.status_code}"}
        cf.write_text(json.dumps(res))
        return res
    if len(r.content) > 15_000_000:
        res = {"status": "pdf_too_large"}
        cf.write_text(json.dumps(res))
        return res
    # pdfplumber can crawl on pathological PDFs; hard-cap each parse
    import signal

    def _timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError("pdf parse timeout")

    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(60)
    try:
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            # A MONTHLY REPORT PUTS ITS NTA BEHIND THE COVER LETTER.
            # Underwood Capital's July statement is 7 pages: page 1 is a
            # covering note, pages 2-3 are the disclaimer, and the NTA per
            # share first appears on page 4. Reading two pages found
            # nothing, every month, for the whole monthly-report family -
            # Metrics, WAM, Future Generation, Cadence and the rest.
            # Bounded by NTA_PDF_PAGES so a long prospectus cannot turn one
            # document into a crawl.
            pages = pdf.pages[:PDF_PAGES]
            text = re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pages))
            rows = []
            for p in pages:
                try:
                    for tbl in p.extract_tables() or []:
                        for row in tbl:
                            joined = " ".join(str(c) for c in row if c)
                            if NTA_HEAD.search(joined):
                                rows.append([str(c) if c is not None else "" for c in row])
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        res = {"status": f"pdf_parse:{exc}"}
        cf.write_text(json.dumps(res))
        return res
    finally:
        signal.alarm(0)
    if len(text) < 50:                # scanned image, no text layer - honest gap
        res = {"status": "no_text_layer"}
        cf.write_text(json.dumps(res))
        return res
    res = {"status": "extracted", "text": text[:PDF_TEXT_CHARS],
           "rows": rows[:PDF_TABLE_ROWS]}
    cf.write_text(json.dumps(res))
    return res


def main() -> int:
    # The code list comes from the REGISTRY, which is committed and always
    # present, falling back to the panel. Reading the panel first meant a
    # workflow that does not rebuild it - the daily index top-up - died on
    # FileNotFoundError before making a single call. Same failure as the idea
    # scan, same cause: a job assuming an artefact another job happens to
    # build. The registry is also the correct source: it is the universe, and
    # a fund the panel never priced still files announcements.
    codes: set[str] = set()
    rp = Path("data/universe/registry.parquet")
    if rp.exists():
        reg = pd.read_parquet(rp)
        au = reg[reg["market"] == "AU"]
        codes = set(au["security_id"].astype(str)
                    .str.replace("^ASX:", "", regex=True).str.upper())
        print(f"registry AU codes: {len(codes)}")
    # The PDF cross-check compares a PDF's stated NTA against the panel's
    # derived one, so it needs the panel - but the SWEEP does not, and
    # making the whole job depend on the panel is what killed three earlier
    # runs. The panel stays optional: absent, the sweep still runs and the
    # cross-check is skipped rather than crashing after the crawl is done.
    have_nta = None
    pp = Path("data/au_processed/au_monthly_panel.parquet")
    if pp.exists():
        panel = pd.read_parquet(pp)
        panel["code"] = panel["security_id"].str.replace("ASX:", "", regex=False)
        codes |= set(panel.loc[panel["nta_derived"].notna(), "code"].unique())
        have_nta = panel[panel["nta_derived"].notna()]
    if not codes:
        print("no AU codes from registry or panel - nothing to sweep")
        return 0
    print(f"codes to keep from the sweep: {len(codes)}")

    s = requests.Session()
    s.headers["User-Agent"] = UA
    counters = {"index_calls": 0, "pdf_calls": 0}

    idx = sweep_index(s, codes, counters)
    print(f"index rows for our codes: {len(idx)} "
          f"(hist_done={counters.get('hist_done')}, calls={counters['index_calls']})")
    cands = pick_candidates(idx)

    if have_nta is None:
        # the crawl is the valuable part and it has already succeeded and
        # been persisted; exiting 0 here keeps the workflow green so the
        # commit step is not treated as cleanup after a failure
        print("no AU monthly panel - skipping the PDF cross-check "
              "(the sweep above is complete and saved)")
        return 0

    rows = []
    for code, grp in cands.groupby("code"):
        panel_months = set(have_nta.loc[have_nta["code"] == code, "obs_month"].astype(str))
        usable = sorted(m for m in grp["month"] if m in panel_months)
        if not usable:
            continue
        by_month = grp.set_index("month")
        for month in sample_months(usable):
            cand = by_month.loc[month]
            res = derive_stated(parse_pdf(s, str(cand["id"]), cand["url"], counters))
            prow = have_nta[(have_nta["code"] == code) & (have_nta["obs_month"] == month)]
            derived = float(prow["nta_derived"].iloc[0]) if len(prow) else None
            rec = {"code": code, "month": month, "ann_id": str(cand["id"]),
                   "headline": str(cand["headline"])[:120],
                   "derived_nta": round(derived, 4) if derived is not None else None,
                   "status": res.get("status")}
            stated, unit = res.get("stated_raw"), res.get("unit")
            if stated is not None and derived is not None:
                if unit == "ambiguous":
                    # decide nothing: report both readings for the audit file
                    rec.update({"stated_nta": stated, "stated_unit": "ambiguous",
                                "status": "unit_ambiguous"})
                else:
                    stated_dollars = stated / 100.0 if unit == "cents" else stated
                    rec.update({"stated_nta": stated_dollars, "stated_unit": unit,
                                "stated_basis": res.get("basis", "pre_tax")})
                    diff = abs(derived / stated_dollars - 1)
                    # ~100x gap = unresolved units statement, not a numeric
                    # disagreement - flag, never silently correct
                    if diff > 0.5 and (abs(derived / (stated / 100.0) - 1) < 0.05
                                       or abs(derived / (stated * 100.0) - 1) < 0.05):
                        rec["status"] = "unit_ambiguous"
                    else:
                        rec["abs_pct_diff"] = diff
            rows.append(rec)

    out = pd.DataFrame(rows)
    Path("outputs/au").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/au/au_nta_pdf_check.csv", index=False)

    # commit the raw evidence for every disagreement/ambiguity so parser
    # iteration works from actual document text, not guesses
    debug = []
    for rec in rows:
        bad = rec.get("status") == "unit_ambiguous" or \
            (rec.get("abs_pct_diff") is not None and rec["abs_pct_diff"] > 0.05)
        if not bad or len(debug) >= 150:
            continue
        cf = PARSE_DIR / f"{rec.get('ann_id')}.json"
        if cf.exists():
            ext = json.loads(cf.read_text())
            debug.append({**{k: rec.get(k) for k in
                             ("code", "month", "derived_nta", "stated_nta", "status")},
                          "text_head": (ext.get("text") or "")[:1500],
                          "rows": ext.get("rows")})
    Path("outputs/au/au_nta_parse_debug.json").write_text(
        json.dumps(debug, indent=1, default=str))
    if "abs_pct_diff" in out.columns:
        ok = out[(out["status"] == "parsed") & out["abs_pct_diff"].notna()]
    else:
        ok = out.iloc[0:0]
    summary = {
        "panel_codes": len(codes),
        "codes_compared": int(ok["code"].nunique()) if len(ok) else 0,
        "comparisons_parsed": int(len(ok)),
        "years_covered": sorted(ok["month"].str[:4].unique().tolist()) if len(ok) else [],
        "median_abs_pct_diff": float(ok["abs_pct_diff"].median()) if len(ok) else None,
        "p90_abs_pct_diff": float(ok["abs_pct_diff"].quantile(0.9)) if len(ok) else None,
        "within_1pct": float((ok["abs_pct_diff"] < 0.01).mean()) if len(ok) else None,
        "within_2pct": float((ok["abs_pct_diff"] < 0.02).mean()) if len(ok) else None,
        "status_counts": out["status"].value_counts().to_dict() if len(out) else {},
        "index_rows": counters.get("index_rows"),
        "index_calls": counters["index_calls"],
        # the question every sweep run exists to answer
        "index_span": counters.get("index_span"),
        "index_rows_by_year": counters.get("index_rows_by_year"),
        "contiguous_frontier": counters.get("contiguous_frontier"),
        "top_pass_resuming_at": counters.get("top_pass_resuming_at"),
        "gap_days_remaining": counters.get("gap_days_remaining"),
        "history_sweep_complete": counters.get("hist_done", False),
        "pdf_fetches": counters["pdf_calls"],
        "sweep_budget_hit": counters.get("sweep_budget_hit", False),
        "index_ok": counters.get("index_ok", 0),
        "index_timeouts": counters.get("index_timeouts", 0),
        "index_deadline_hit": counters.get("index_deadline_hit", False),
        "days_per_call": counters.get("days_per_call", [])[:40],
        "median_days_per_call": (
            sorted(counters["days_per_call"])[len(counters["days_per_call"]) // 2]
            if counters.get("days_per_call") else None),
        "index_error": counters.get("index_error"),
        "pdf_budget_hit": counters.get("pdf_budget_hit", False),
        "deadline_hit": counters.get("deadline_hit", False),
        "note": "historical sample: up to one month-end NTA PDF per code per year "
                "vs panel-derived NTA; announcement index from the public "
                "unauthenticated market-wide listing (probe 7/8), delisted "
                "issuers included; per-share pre-tax parse, unit ambiguity "
                "flagged rather than corrected",
    }
    Path("outputs/au/au_nta_pdf_check_summary.json").write_text(json.dumps(summary, indent=2))
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
