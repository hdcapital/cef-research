"""UK NAV/NTA observation panel for the funds that are still listed today.

Where the values come from
--------------------------
S3, which is this project's system of record for anything expensive to
rebuild (docs/RUNBOOK.md). Three stores are read, in descending order of
authority, and every observation keeps the source that produced it:

  1. ``uk/nav_announcements/{TICKER}/{date}_{annid}.json.gz`` - the archived
     RNS text of the fund's own "Net Asset Value(s)" announcement. The text
     is RE-PARSED here rather than trusting the stored fields, because the
     parser has improved since the earliest objects were written and a
     stored ``no_nav_parsed`` is a parser verdict, not a property of the
     announcement.
  2. ``nta_live/{YYYY-MM-DD}.parquet`` - the nightly live snapshots. These
     carry a published NAV (``anchor_source`` beginning ``investegate:``)
     for funds that the announcement archive never queued, which is how the
     alternatives cohort - HICL, Foresight, Gresham House and the other
     ``announcements_only`` names - gets any history at all.
  3. the committed ``data/uk_nav_history*.parquet`` shards, already derived
     from store 1 by earlier runs. Kept as a seed so a fresh clone with no
     bucket credentials still holds the history that was extracted before.

No Investegate request is made by this module. It re-derives observations
from text already collected, so it adds nothing to the publisher's load.

What a row means
----------------
One published NAV per share, in pence, on the cum-income basis where the
announcement states one (the same basis the research panel uses), carrying
BOTH the valuation date (``nav_date``, as-at) and the publication date
(``published_at``, when the market could first have known it). Those are
different dates and conflating them is the single largest look-ahead
available in this dataset, so they are stored separately and the discount
builder joins on the publication date.

Frequency is measured, never assumed: each fund publishes on its own
cadence - daily for most conventional trusts, monthly or quarterly for much
of the infrastructure and property cohort - and ``publication_frequency``
reports the observed cadence per fund so a stale-NAV rule can be relative
to what that fund actually publishes.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .harvest_nav import parse_uk_nav_text, uk_row_matches_ticker

NAV_PREFIX = "uk/nav_announcements/"
SNAPSHOT_PREFIX = "nta_live/"
NAV_DIR = Path("data/uk/nav")
LEGACY_GLOB = "uk_nav_history*.parquet"

# A NAV per share outside this band is a parse artefact, not a trust: the
# widest real range on the LSE runs from sub-penny wind-down residues to
# Alliance Witan's ~1,500p. Values outside are kept but marked, never
# silently deleted and never silently used.
NAV_MIN_PENCE = 0.01
NAV_MAX_PENCE = 100_000.0

COLUMNS = ["ticker", "ann_id", "published_at", "nav_date", "nav_pence",
           "nav_ex_pence", "cum_assumed", "nav_ccy", "nav_source", "quality"]


# --------------------------------------------------------------- universe
def live_universe(registry_path: str | Path = "data/universe/registry.parquet",
                  tickers_path: str | Path = "config/resolved_tickers.csv",
                  include_vct: bool = True) -> pd.DataFrame:
    """UK funds still listed today, with the ticker each one is addressed by.

    "Still alive today" is the registry's own liveness verdict (``live`` or
    ``delist_candidate``), which is decided from the funds' filings rather
    than from an aggregator's coverage. VCTs are included by default and
    flagged rather than dropped - they are listed funds that publish a NAV,
    and a caller that wants the conventional cohort filters on the flag.

    Where a ZDP or preference line shares its ordinary's ticker, only the
    ordinary is kept: the announcement parser excludes ZDP entitlements, so
    the NAV harvested under that ticker is the ordinary share's and giving
    it to both lines would state the same number about two different claims.
    """
    reg = pd.read_parquet(registry_path)
    uk = reg[(reg["market"] == "UK")
             & (reg["status"].isin(["live", "delist_candidate"]))].copy()
    tk = pd.read_csv(tickers_path)
    tk = tk[tk["status"] == "verified"][["security_id", "ticker"]]
    tk["ticker"] = tk["ticker"].astype(str).str.strip().str.upper()
    out = uk.merge(tk, on="security_id", how="left")
    out = out[out["ticker"].notna() & (out["ticker"] != "")]

    st = out["share_type"].fillna("").str.lower()
    out["is_ordinary_line"] = ~st.str.contains("zdp|zero dividend|preference")
    out = out.sort_values(["ticker", "is_ordinary_line"], ascending=[True, False])
    out = out.drop_duplicates("ticker", keep="first")

    if not include_vct:
        out = out[~out["is_vct"].astype(bool)]
    cols = ["security_id", "ticker", "name", "sector", "share_type", "currency",
            "isin", "is_vct", "nav_route", "status", "first_seen", "last_seen"]
    return out[[c for c in cols if c in out.columns]].reset_index(drop=True)


# ------------------------------------------------------------------- S3 io
def _s3():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION"))


def ann_id_from_key(key: str) -> str | None:
    """Recover the ann_id from an archive key.

    Keys are ``{TICKER}/{date}_{annid}.json.gz``; the date prefix is stripped
    on the FIRST underscore only, because ann_ids themselves contain them.
    """
    if "manifest" in key or not key.endswith(".json.gz"):
        return None
    stem = key.rsplit("/", 1)[-1][: -len(".json.gz")]
    if "_" not in stem:
        return None
    return stem.split("_", 1)[1] or None


def known_ann_ids(nav_dir: Path = NAV_DIR) -> set[str]:
    """Every ann_id already extracted into the panel, across year files."""
    ids: set[str] = set()
    for f in sorted(Path(nav_dir).glob("*.parquet")):
        try:
            ids |= set(pd.read_parquet(f, columns=["ann_id"])["ann_id"].astype(str))
        except Exception:  # noqa: BLE001
            continue
    return ids


def extract_from_archive(bucket: str, tickers: set[str] | None = None,
                         skip_ann_ids: set[str] | None = None,
                         deadline_min: float = 240.0, workers: int = 24,
                         shard: int = 0, shards: int = 1,
                         progress_every: int = 20_000) -> tuple[pd.DataFrame, dict]:
    """Re-parse archived NAV announcements out of S3 into panel rows.

    Incremental by construction: the keys carry the ann_id, so the set of
    objects still to read is computable from a LISTING alone and only the
    bodies of genuinely new announcements are fetched. A run that hits its
    deadline returns what it has - the next run resumes from the same
    subtraction, with no cursor to corrupt.
    """
    s3 = _s3()
    skip = skip_ann_ids or set()
    started = time.time()
    todo: list[tuple[str, str]] = []
    seen = 0
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=NAV_PREFIX):
        for o in page.get("Contents", []):
            key = o["Key"]
            ann_id = ann_id_from_key(key)
            if ann_id is None:
                continue
            seen += 1
            if ann_id in skip:
                continue
            if tickers is not None:
                tk = key[len(NAV_PREFIX):].split("/", 1)[0].upper()
                if tk not in tickers:
                    continue
            if shards > 1 and zlib.crc32(ann_id.encode()) % shards != shard:
                continue
            todo.append((key, ann_id))

    stats = {"archive_objects": seen, "queued": len(todo), "read": 0,
             "parsed": 0, "unparsed": 0, "read_failed": 0,
             "deadline_hit": False}
    if not todo:
        return pd.DataFrame(columns=COLUMNS), stats

    def load(item: tuple[str, str]) -> dict | None:
        key, ann_id = item
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            rec = json.loads(gzip.decompress(body))
        except Exception:  # noqa: BLE001
            return None
        ticker = str(rec.get("ticker") or key[len(NAV_PREFIX):].split("/", 1)[0]).upper()
        # a market-feed leak archived under the wrong fund must not become
        # that fund's NAV observation (see harvest_nav.uk_row_matches_ticker)
        if rec.get("url") and not uk_row_matches_ticker(rec["url"], ticker):
            return None
        published = rec.get("ann_date") or key.rsplit("/", 1)[-1].split("_", 1)[0]
        # Re-parse the stored text rather than trust the stored verdict: the
        # rule list has grown since these were archived, so a row written as
        # no_nav_parsed may well be a real observation under today's parser.
        got = parse_uk_nav_text(rec["text"]) if rec.get("text") else {}
        cum = got.get("nav_cum_pence", rec.get("nav_cum_pence"))
        if cum is None:
            return {"ticker": ticker, "ann_id": str(ann_id),
                    "published_at": published, "nav_date": None,
                    "nav_pence": None, "nav_ex_pence": None,
                    "cum_assumed": False, "nav_ccy": "GBX",
                    "nav_source": "s3_archive", "quality": "no_nav_parsed"}
        return {"ticker": ticker, "ann_id": str(ann_id),
                "published_at": published,
                "nav_date": got.get("asat") or rec.get("nav_date") or published,
                "nav_pence": float(cum),
                "nav_ex_pence": got.get("nav_ex_pence", rec.get("nav_ex_pence")),
                "cum_assumed": bool(got.get("cum_assumed",
                                            rec.get("cum_assumed", False))),
                # the unit the announcement stated. A fund quoting USD or CAD
                # cannot be divided into a pence price, and saying so is the
                # difference between no discount and an FX rate.
                "nav_ccy": got.get("nav_ccy", "GBX"),
                "nav_source": "s3_archive", "quality": "parsed"}

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, rec in enumerate(ex.map(load, todo), start=1):
            if rec is None:
                stats["read_failed"] += 1
            else:
                rows.append(rec)
                stats["read"] += 1
                stats["parsed" if rec["quality"] == "parsed" else "unparsed"] += 1
            if progress_every and i % progress_every == 0:
                print(f"  {i:,}/{len(todo):,} objects read, "
                      f"{stats['parsed']:,} parsed")
            if (time.time() - started) > deadline_min * 60:
                stats["deadline_hit"] = True
                print("deadline reached - keeping what has been read")
                break
    return pd.DataFrame(rows, columns=COLUMNS), stats


def extract_from_snapshots(bucket: str, tickers: set[str] | None = None,
                           sid_to_ticker: dict[str, str] | None = None,
                           since: str | None = None,
                           workers: int = 16) -> tuple[pd.DataFrame, dict]:
    """Published NAVs recorded in the nightly ``nta_live`` snapshots.

    The nightly job polls every addressable fund's own RNS page and stores
    what it found. For the ~100 live funds the announcement archive never
    queued, these snapshots are the only NAV history that exists, so they
    are read here rather than left on the shelf. Only rows whose anchor is a
    published announcement are taken - a modelled estimate never enters a
    field reserved for a published figure (RUNBOOK rule 3).
    """
    s3 = _s3()
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=SNAPSHOT_PREFIX):
        for o in page.get("Contents", []):
            k = o["Key"]
            if not k.endswith(".parquet"):
                continue
            if since and k.rsplit("/", 1)[-1][:10] < since:
                continue
            keys.append(k)

    stats = {"snapshots": len(keys), "rows": 0, "read_failed": 0}
    if not keys:
        return pd.DataFrame(columns=COLUMNS), stats

    def load(key: str) -> pd.DataFrame | None:
        import io
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return pd.read_parquet(io.BytesIO(body))
        except Exception:  # noqa: BLE001
            return None

    frames = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for key, d in zip(keys, ex.map(load, keys)):
            if d is None:
                stats["read_failed"] += 1
                continue
            if "anchor_source" not in d.columns or "nav_anchor" not in d.columns:
                continue
            pub = d[d["anchor_source"].astype(str).str.startswith("investegate:")]
            if not len(pub):
                continue
            frames.append(pd.DataFrame({
                "security_id": pub["security_id"].astype(str),
                "published_at": key.rsplit("/", 1)[-1][:10],
                "nav_date": pub["anchor_date"].astype(str),
                # The live table stores UK NAV in PENCE (confirmed against
                # the AIC anchor: HGEN 96.00 vs our 96.00 pence), so this is
                # a rename, not a conversion. Multiplying by 100 here would
                # have made every snapshot-derived observation 100x its
                # true value - and snapshots are the ONLY history the
                # announcements_only cohort has, so it would have been wrong
                # exactly where nothing else could contradict it.
                "nav_pence": pd.to_numeric(pub["nav_anchor"], errors="coerce"),
                "ann_id": pub["anchor_source"].astype(str).str.split(":").str[-1],
            }))
    if not frames:
        return pd.DataFrame(columns=COLUMNS), stats

    out = pd.concat(frames, ignore_index=True)
    mapping = sid_to_ticker or {}
    out["ticker"] = out["security_id"].map(mapping)
    out = out[out["ticker"].notna()]
    if tickers is not None:
        out = out[out["ticker"].isin(tickers)]
    out["nav_ex_pence"] = pd.NA
    out["cum_assumed"] = False
    out["nav_ccy"] = "GBX"
    out["nav_source"] = "s3_snapshot"
    out["quality"] = "parsed"
    out["ann_id"] = "snap:" + out["ann_id"].astype(str)
    out = out.dropna(subset=["nav_pence"]).drop_duplicates(["ticker", "ann_id"])
    stats["rows"] = int(len(out))
    return out[COLUMNS], stats


# ------------------------------------------------------------ legacy seed
def unparsed_ann_ids(data_dir: str | Path = "data",
                     tickers: set[str] | None = None) -> set[str]:
    """Announcements held in the seed that no parser has yet read a NAV from.

    These are the rows worth re-reading when the rule list grows. The list
    went from ~40 rules to 68 on 2026-08-31, and a stored `no_nav_parsed` is
    a verdict of the parser that ran at archive time, not a property of the
    announcement - so 167,347 rows that failed under the old rules may well
    be real observations under the new ones. Nothing is re-fetched from
    Investegate: the text is already in the bucket.
    """
    df = extract_from_committed(data_dir, tickers)
    if not len(df):
        return set()
    return set(df.loc[df["quality"] != "parsed", "ann_id"].astype(str))


def ann_ids_for(tickers: set[str], data_dir: str | Path = "data") -> set[str]:
    """Every announcement held for these funds, whatever its stored verdict.

    The complement of `unparsed_ann_ids`, and needed for the same reason
    from the other side. A parser improvement reaches a `no_nav_parsed` row
    by re-reading it; it never reaches a row the OLD parser answered
    WRONGLY, because that row is stored `parsed` and is skipped forever.

    Measured on 2026-08-31: the reparse recovered 75,819 observations, and
    seven funds came out with a median change between consecutive
    publications above 15% - VNH, VOF, CGI, IGC, RMMC among them. Their
    announcements quote GBP per share and Canadian dollars, the fallback rule
    took whatever number sat nearest the label, and every one of those rows
    is stored `parsed`. So the reliability measurement is used as the trigger
    to re-read them: the panel says which funds the parser got wrong, and
    those are exactly the funds worth asking again about.
    """
    df = extract_from_committed(data_dir)
    if not len(df):
        return set()
    return set(df.loc[df["ticker"].isin(tickers), "ann_id"].astype(str))


def extract_from_committed(data_dir: str | Path = "data",
                           tickers: set[str] | None = None) -> pd.DataFrame:
    """The already-extracted history committed to the repo.

    These rows came from the same archive objects on an earlier pass. They
    are the seed that lets a clone without bucket credentials still hold a
    real panel, and they are superseded by store 1 wherever both have the
    same ann_id.
    """
    frames = []
    for f in sorted(Path(data_dir).glob(LEGACY_GLOB)):
        try:
            d = pd.read_parquet(f)
        except Exception:  # noqa: BLE001
            continue
        if "ticker" not in d.columns or "ann_id" not in d.columns:
            continue
        frames.append(pd.DataFrame({
            "ticker": d["ticker"].astype(str).str.upper(),
            "ann_id": d["ann_id"].astype(str),
            "published_at": d.get("ann_date"),
            "nav_date": d.get("nav_date"),
            "nav_pence": pd.to_numeric(d.get("nav_cum_pence"), errors="coerce"),
            "nav_ex_pence": pd.to_numeric(d.get("nav_ex_pence"), errors="coerce"),
            "cum_assumed": d.get("cum_assumed", False),
            "nav_ccy": d["nav_ccy"] if "nav_ccy" in d.columns else "GBX",
            "nav_source": "committed",
            "quality": d.get("status", "parsed"),
        }))
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True).drop_duplicates("ann_id")
    if tickers is not None:
        out = out[out["ticker"].isin(tickers)]
    return out[COLUMNS]


# ------------------------------------------------------------- panel build
_SOURCE_RANK = {"s3_archive": 0, "committed": 1, "s3_snapshot": 2}


def normalise(rows: pd.DataFrame, start: str = "2007-01-01") -> pd.DataFrame:
    """One clean observation per (ticker, announcement), from 2007 onward.

    Rules, each of which exists because breaking it would corrupt a discount
    silently rather than loudly:

    * an unparsed announcement is dropped from the panel but its existence
      is not denied - it is counted in the run status, so a parser blind
      spot shows up as coverage rather than as absence;
    * a NAV outside the plausible pence band is marked ``implausible_nav``
      and excluded from the panel, because a mis-parsed order of magnitude
      produces a -99% discount that reads exactly like a real dislocation;
    * an as-at date later than the publication date is impossible and marks
      the row ``asat_after_publication``: the valuation date is then not
      trusted for staleness, but the observation itself still stands,
      because the join that matters is on the publication date.
    """
    if rows is None or not len(rows):
        return pd.DataFrame(columns=COLUMNS)
    df = rows.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df["ann_id"] = df["ann_id"].astype(str)
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce")
    df["nav_date"] = pd.to_datetime(df["nav_date"], errors="coerce")
    df["nav_pence"] = pd.to_numeric(df["nav_pence"], errors="coerce")
    df["nav_ex_pence"] = pd.to_numeric(df["nav_ex_pence"], errors="coerce")
    df["cum_assumed"] = df["cum_assumed"].fillna(False).astype(bool)
    df["nav_ccy"] = df.get("nav_ccy", pd.Series("GBX", index=df.index)).fillna("GBX")

    df = df.dropna(subset=["published_at", "nav_pence"])
    df = df[df["published_at"] >= pd.Timestamp(start)]

    bad = (df["nav_pence"] <= NAV_MIN_PENCE) | (df["nav_pence"] > NAV_MAX_PENCE)
    df = df[~bad]

    # a missing as-at date falls back to the publication date, which is the
    # conservative choice: it can only make a NAV look FRESHER than it is by
    # at most the publication lag, and it never moves the join.
    df["nav_date"] = df["nav_date"].fillna(df["published_at"])
    late = df["nav_date"] > df["published_at"]
    df.loc[late, "quality"] = "asat_after_publication"
    df.loc[late, "nav_date"] = df.loc[late, "published_at"]

    df["_rank"] = df["nav_source"].map(_SOURCE_RANK).fillna(9)
    df = (df.sort_values(["ticker", "ann_id", "_rank", "cum_assumed"])
            .drop_duplicates(["ticker", "ann_id"], keep="first"))
    # two announcements on one day (a correction, or a snapshot echoing an
    # archived announcement): keep the one with the later valuation date,
    # then the firmer basis, then the more authoritative store.
    df = (df.sort_values(["ticker", "published_at", "nav_date",
                          "cum_assumed", "_rank"],
                         ascending=[True, True, False, True, True])
            .drop_duplicates(["ticker", "published_at"], keep="first"))
    return df.drop(columns=["_rank"]).sort_values(
        ["ticker", "published_at"]).reset_index(drop=True)


def write_panel(panel: pd.DataFrame, nav_dir: Path = NAV_DIR) -> list[Path]:
    """Write the panel as one file per publication year.

    Year partitioning is what makes a DAILY job possible in a git repo: a
    run only rewrites the current year, so historic years keep the blob they
    already have instead of the whole panel being re-committed every night.
    """
    nav_dir = Path(nav_dir)
    nav_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for year, g in panel.groupby(panel["published_at"].dt.year):
        p = nav_dir / f"{int(year)}.parquet"
        g.sort_values(["ticker", "published_at"]).to_parquet(p, index=False)
        written.append(p)
    return written


def read_panel(nav_dir: Path = NAV_DIR) -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(Path(nav_dir).glob("*.parquet"))]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["published_at"] = pd.to_datetime(out["published_at"])
    out["nav_date"] = pd.to_datetime(out["nav_date"])
    return out.sort_values(["ticker", "published_at"]).reset_index(drop=True)


def publication_frequency(panel: pd.DataFrame) -> pd.DataFrame:
    """Each fund's OBSERVED publication cadence, measured over its last 2y.

    Frequency is a property of the fund, not of the universe: a conventional
    trust publishes a NAV every trading day, while much of the property and
    infrastructure cohort publishes quarterly. Measuring it per fund is what
    lets the discount builder apply a staleness rule relative to what that
    fund actually does, instead of blanking a quarterly publisher's entire
    history against a rule written for daily ones.

    The median gap is used rather than the mean because a single suspended
    year would otherwise reclassify a daily publisher as ad hoc.
    """
    if panel is None or not len(panel):
        return pd.DataFrame(columns=["ticker", "n_obs", "first_nav", "last_nav",
                                     "median_gap_days", "nav_frequency"])
    rows = []
    for tk, g in panel.groupby("ticker"):
        d = g["published_at"].sort_values()
        recent = d[d >= d.max() - pd.Timedelta(days=730)]
        gaps = recent.diff().dt.days.dropna()
        med = float(gaps.median()) if len(gaps) else float("nan")
        if pd.isna(med):
            freq = "single_observation"
        elif med <= 4:
            freq = "daily"
        elif med <= 10:
            freq = "weekly"
        elif med <= 45:
            freq = "monthly"
        elif med <= 130:
            freq = "quarterly"
        elif med <= 250:
            freq = "semiannual"
        else:
            freq = "adhoc"
        rows.append({"ticker": tk, "n_obs": int(len(g)),
                     "first_nav": d.min().date().isoformat(),
                     "last_nav": d.max().date().isoformat(),
                     "median_gap_days": med, "nav_frequency": freq})
    return pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)


def archive_readiness(universe: pd.DataFrame, panel: pd.DataFrame,
                      cache_dir: str | Path = "data/investegate_cache"
                      ) -> pd.DataFrame:
    """Why each fund's NAV history is as short as it is, and what would fix it.

    A short history has three quite different causes and only one of them is
    about the fund:

      ``no_listing_index``   its announcements were never INDEXED. The
                             listings crawl was seeded from the AIC panel's
                             eligible universe, which excludes most of the
                             ``announcements_only`` cohort - the
                             infrastructure, renewables and property trusts
                             the aggregator declines to price. Fixing it
                             means extending the listings crawl
                             (``uk-listings-refresh``) to these tickers;
                             nothing in S3 can supply what was never listed.
      ``indexed_not_archived`` indexed, but the announcement text was never
                             fetched into ``uk/nav_announcements/``. Running
                             ``uk-nav-archive`` closes it.
      ``archived``           the text is in the bucket and this panel has it;
                             a short history here is the fund's own (it
                             listed recently, or it genuinely publishes NAV
                             once a year).

    Distinguishing them is the difference between a queue to run and a
    limitation to write down, and guessing between the two is how a gap
    survives for months looking like the other one.
    """
    listings = Path(cache_dir) / "listings"
    # Absent cache means we cannot tell indexed from unindexed. Saying
    # "never indexed" because we did not pull the index would be a false
    # diagnosis pointing at the wrong queue, so it says so instead.
    have_cache = listings.exists() and any(listings.glob("*.csv"))
    have = (panel.groupby("ticker").size().rename("nav_rows")
            if panel is not None and len(panel) else pd.Series(dtype=int,
                                                               name="nav_rows"))
    pat = __import__("re").compile(r"net asset value", __import__("re").I)
    rows = []
    for tk in universe["ticker"]:
        f = listings / f"{tk}.csv"
        indexed = None
        if f.exists():
            try:
                d = pd.read_csv(f, dtype=str)
                indexed = int(d["headline"].fillna("").str.contains(pat).sum()) \
                    if "headline" in d.columns else 0
            except Exception:  # noqa: BLE001
                indexed = None
        extracted = int(have.get(tk, 0))
        if extracted > 0:
            status = "archived"
        elif not have_cache:
            status = "unknown_listing_index_not_pulled"
        elif indexed is None:
            status = "no_listing_index"
        elif indexed == 0:
            status = "indexed_no_nav_announcements"
        else:
            status = "indexed_not_archived"
        rows.append({"ticker": tk, "nav_rows_extracted": extracted,
                     "nav_announcements_indexed": indexed,
                     "readiness": status})
    out = pd.DataFrame(rows)
    keep = [c for c in ["ticker", "name", "sector", "nav_route", "is_vct"]
            if c in universe.columns]
    return universe[keep].merge(out, on="ticker", how="right")


# A NAV series whose value moves this much between CONSECUTIVE publications
# is not reporting a NAV. The panel's own median is 0.54% and its 90th
# percentile 1.8%; the funds this excludes sit at 26-33%, every one of them
# sourced 100% from the parser's unlabelled fallback. The threshold is set
# an order of magnitude above normal so that a genuinely volatile quarterly
# publisher is never caught by it.
UNRELIABLE_MEDIAN_CHANGE = 0.15
UNRELIABLE_MIN_OBS = 10


def unreliable_nav_series(quality: pd.DataFrame) -> pd.Series:
    """Funds whose NAV series cannot carry a discount, from measurement.

    This exists because of what the first full run produced. Every fund the
    unit reconciliation wanted to rescale by 100 - NCYF, LWDB, CHI, CMPI,
    PNL, SST, GCL, GPM - had cum_assumed_share 1.0 and a median change
    between consecutive publications of 26-33%. The plain fallback rule was
    matching whatever number sat nearest the words "net asset value", and it
    was a different number each time. Fitting a unit scale to that is fitting
    a scale to noise, and the resulting discount looked entirely ordinary:
    CQS New City High Yield priced at 5060p against an 8879p "NAV", for a
    trust that trades near 50p.
    """
    if quality is None or not len(quality):
        return pd.Series(dtype=bool)
    med = pd.to_numeric(quality.get("median_abs_change_all"), errors="coerce")
    obs = pd.to_numeric(quality.get("obs"), errors="coerce").fillna(0)
    return (med > UNRELIABLE_MEDIAN_CHANGE) & (obs >= UNRELIABLE_MIN_OBS)


def quality_report(panel: pd.DataFrame, jump: float = 0.25,
                   max_gap_days: int = 4) -> tuple[pd.DataFrame, dict]:
    """Per-fund evidence on how much each fund's NAV series can be trusted.

    A parser that picks the wrong number off a page does not fail loudly - it
    returns a number, and a NAV series full of plausible numbers is exactly
    what a discount panel cannot detect. But a mis-parse has a signature: a
    large move between consecutive publications that IMMEDIATELY REVERSES,
    because the next announcement goes back to reading the right line. A real
    NAV move does not come back the next day.

    Measured over the panel as committed (2026-08-31): the median change
    between consecutive publications is 0.54% and the 90th percentile 1.8% -
    which is what fund NAVs look like - and 0.065% of observations are
    jump-and-reverse.

    The finding that matters for a user: rows flagged ``cum_assumed`` (the
    parser's plain fallback, used where an announcement states no income
    basis) jump >25% at 3.55% versus 0.083% for rows matched by a labelled
    rule - a 43x higher rate. They are 23% of the panel, they are kept
    because they are real observations, and they are flagged on every row so
    an analysis that cannot tolerate them can drop them.
    """
    if panel is None or len(panel) < 2:
        return pd.DataFrame(), {}
    import numpy as np

    d = panel.sort_values(["ticker", "published_at"]).copy()
    d["chg"] = d.groupby("ticker")["nav_pence"].pct_change()
    d["gap_days"] = d.groupby("ticker")["published_at"].diff().dt.days
    d["next_chg"] = d.groupby("ticker")["chg"].shift(-1)
    con = d[(d["gap_days"] <= max_gap_days) & d["chg"].notna()]
    if not len(con):
        return pd.DataFrame(), {}
    big = con["chg"].abs() > jump
    reversed_ = (big & con["next_chg"].notna()
                 & (np.sign(con["next_chg"]) != np.sign(con["chg"]))
                 & (con["next_chg"].abs() > jump * 0.8))

    # Change between consecutive publications at ANY gap, not just the
    # daily ones: a monthly or quarterly publisher has no <=4-day pairs at
    # all, and judging its series by a statistic it cannot have would either
    # exempt it from every check or condemn it for having none.
    allpairs = d[d["chg"].notna()].groupby("ticker")["chg"].apply(
        lambda v: float(v.abs().median())).rename("median_abs_change_all")

    per = con.assign(_big=big, _rev=reversed_).groupby("ticker").agg(
        obs=("chg", "size"),
        median_abs_change=("chg", lambda v: float(v.abs().median())),
        p99_abs_change=("chg", lambda v: float(v.abs().quantile(0.99))),
        big_moves=("_big", "sum"),
        jump_reversals=("_rev", "sum"),
        cum_assumed_share=("cum_assumed", "mean"),
    ).reset_index()
    per = per.merge(allpairs, on="ticker", how="left")
    per["reliable"] = ~unreliable_nav_series(per)
    per["suspect_rate"] = per["jump_reversals"] / per["obs"].clip(lower=1)

    def _rate(mask):
        sub = con[mask]
        return float((sub["chg"].abs() > jump).mean()) if len(sub) else float("nan")

    summary = {
        "observations_compared": int(len(con)),
        "median_abs_change": round(float(con["chg"].abs().median()), 6),
        "p90_abs_change": round(float(con["chg"].abs().quantile(0.90)), 6),
        "big_move_rate": round(float(big.mean()), 6),
        "jump_reversal_rate": round(float(reversed_.mean()), 8),
        "big_move_rate_labelled_basis": round(_rate(~con["cum_assumed"]), 6),
        "big_move_rate_cum_assumed": round(_rate(con["cum_assumed"]), 6),
        "cum_assumed_share_of_panel": round(float(panel["cum_assumed"].mean()), 4),
        "funds_unreliable_nav_series": int((~per["reliable"]).sum()),
    }
    return per.sort_values("suspect_rate", ascending=False).reset_index(drop=True), summary
