"""The announcement window before each ending, and what its headlines say.

Two things come out of the headline indices alone, before any document is
read:

1. the document window - the corporate, meeting and narrative announcements
   in the `before` months up to the fund's end month, capped per fund and
   ordered newest first, which is what the extractor reads;
2. headline features - deterministic, point-in-time counts per
   (security_id, obs_month) that need no model: buyback executions in the
   trailing quarter, holder filings, months since a strategic review or a
   continuation vote first appeared, whether a wind-up headline has been
   seen. They are the free baseline every extracted feature has to beat.

UK headlines come from the Investegate listing cache (S3 group
uk_announcements, data/investegate_cache/listings/<TICKER>.csv), AU from the
committed market index. Nothing is fetched here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from au_lic.extract import router
from cef_live import catalysts

UK_LISTINGS = Path("data/investegate_cache/listings")
AU_INDEX = Path("data/asx_ann_cache/asx1/lic_announcement_index.parquet")

# UK announcement families worth reading for the ten features. The catalyst
# taxonomy already names the event classes; these add the narrative
# documents (results, chairman's statement, circulars, meetings) where a
# board states its stance before any event is announced.
UK_NARRATIVE = re.compile(
    r"final results|annual (?:financial )?report|half[\s-]?year(?:ly)? (?:report|results)|"
    r"interim (?:results|report|management statement)|chairman'?s statement|"
    r"agm statement|result of (?:agm|egm|general meeting|meeting)|notice of (?:agm|egm|"
    r"general meeting|meeting)|circular|proposals?\b|recommended|strategic review|"
    r"continuation|tender offer|winding[\s-]?up|wind[\s-]?down|liquidation|"
    r"scheme of (?:arrangement|reconstruction)|reconstruction|managed wind|"
    r"discount (?:control|management)|requisition|board (?:changes?|statement)|"
    r"change of (?:investment )?manager|management (?:arrangements|agreement|fee)|"
    r"fee (?:reduction|change)|going concern|dividend policy|"
    r"realisation|return of (?:cash|capital)|capital return", re.I)
UK_HOLDER = re.compile(r"holding\(s\) in company|holdings? in company|tr-?1\b", re.I)
UK_BUYBACK_EXEC = re.compile(r"transaction in own shares|share buy-?back|purchase of own", re.I)
AU_BUYBACK_EXEC = re.compile(r"appendix 3[ce]\b|daily share buy-?back|buy-?back notification", re.I)
AU_HOLDER = re.compile(r"substantial (?:holder|holding)|form 60[345]|ceasing to be a substantial", re.I)

STRATEGIC = re.compile(r"strategic (?:review|options|alternatives)", re.I)
CONTINUATION = re.compile(r"continuation", re.I)
WINDUP = re.compile(r"wind(?:ing)?[\s-]?(?:up|down)|liquidat|managed wind|scheme of "
                    r"(?:arrangement|reconstruction)|return of (?:cash|capital) to shareholders|"
                    r"proposals? for (?:the )?(?:reconstruction|voluntary)", re.I)

DOC_COLUMNS = ["security_id", "market", "ticker", "ann_id", "date", "headline", "url",
               "family", "obs_month", "end_month"]


def _family_uk(headline: str) -> str | None:
    h = headline or ""
    if UK_HOLDER.search(h) or UK_BUYBACK_EXEC.search(h):
        return None                      # counted as headline features, not read
    got = catalysts.classify_signed(h)
    if got and got.get("weight", 0) != 0:
        return "catalyst"
    if UK_NARRATIVE.search(h):
        return "narrative"
    return None


def _family_au(headline: str) -> str | None:
    fam, route = router.classify(headline or "")
    if route == "llm" and fam in ("corporate_action", "meeting", "narrative_report"):
        return fam
    return None


def uk_listing(ticker: str, listings_dir: Path = UK_LISTINGS) -> pd.DataFrame:
    p = listings_dir / f"{ticker}.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(p, dtype=str)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if not {"ann_id", "date", "headline", "url"} <= set(df.columns):
        return pd.DataFrame()
    df["date"] = df["date"].fillna("").str[:10]
    return df


def au_index(path: Path = AU_INDEX) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    idx = pd.read_parquet(path)
    idx["date"] = pd.to_datetime(idx["release_date"], utc=True,
                                 errors="coerce").dt.strftime("%Y-%m-%d")
    return idx.rename(columns={"id": "ann_id", "code": "ticker"})[
        ["ann_id", "ticker", "date", "headline", "url"]]


def _window(rows: pd.DataFrame, end_month: str, before: int, after: int) -> pd.DataFrame:
    lo = str(pd.Period(end_month, freq="M") - before)
    hi = str(pd.Period(end_month, freq="M") + after)
    m = rows["date"].str[:7]
    return rows[(m >= lo) & (m <= hi)]


def select_documents(episodes: pd.DataFrame, before: int = 18, after: int = 1,
                     per_fund_cap: int = 40, listings_dir: Path = UK_LISTINGS,
                     au: pd.DataFrame | None = None) -> pd.DataFrame:
    """The documents the extractor reads: one frame over every episode with
    a ticker, newest first within each fund, capped."""
    if au is None:
        au = au_index()
    out = []
    for ep in episodes.itertuples(index=False):
        if not isinstance(ep.ticker, str) or not ep.ticker:
            continue
        if ep.market == "AU":
            rows = au[au["ticker"].eq(ep.ticker)] if len(au) else pd.DataFrame()
            fam = _family_au
        else:
            rows = uk_listing(ep.ticker, listings_dir)
            fam = _family_uk
        if not len(rows):
            continue
        rows = _window(rows, ep.end_month, before, after).copy()
        rows["family"] = rows["headline"].map(fam)
        rows = rows[rows["family"].notna()].sort_values("date", ascending=False)
        rows = rows.head(per_fund_cap)
        for r in rows.itertuples(index=False):
            out.append({"security_id": ep.security_id, "market": ep.market,
                        "ticker": ep.ticker, "ann_id": str(r.ann_id), "date": r.date,
                        "headline": r.headline, "url": r.url, "family": r.family,
                        "obs_month": str(r.date)[:7], "end_month": ep.end_month})
    return pd.DataFrame(out, columns=DOC_COLUMNS)


def headline_features(episodes: pd.DataFrame, before: int = 18,
                      listings_dir: Path = UK_LISTINGS,
                      au: pd.DataFrame | None = None) -> pd.DataFrame:
    """Deterministic point-in-time features per (security_id, obs_month)
    from headlines dated within the month or earlier. No document is read."""
    if au is None:
        au = au_index()
    out = []
    for ep in episodes.itertuples(index=False):
        if not isinstance(ep.ticker, str) or not ep.ticker:
            continue
        if ep.market == "AU":
            rows = au[au["ticker"].eq(ep.ticker)] if len(au) else pd.DataFrame()
            bb, hold = AU_BUYBACK_EXEC, AU_HOLDER
        else:
            rows = uk_listing(ep.ticker, listings_dir)
            bb, hold = UK_BUYBACK_EXEC, UK_HOLDER
        if not len(rows):
            continue
        rows = rows[rows["date"].str.match(r"\d{4}-\d{2}")]
        h = rows["headline"].fillna("")
        month = rows["date"].str[:7]
        flags = pd.DataFrame({
            "month": month,
            "buyback": h.str.contains(bb), "holder": h.str.contains(hold),
            "strategic": h.str.contains(STRATEGIC),
            "continuation": h.str.contains(CONTINUATION),
            "windup": h.str.contains(WINDUP)})
        for m in _months(ep.end_month, before):
            upto = flags[flags["month"] <= m]
            last3 = upto[upto["month"] > str(pd.Period(m, freq="M") - 3)]
            out.append({
                "security_id": ep.security_id, "obs_month": m,
                "buyback_execs_3m": int(last3["buyback"].sum()),
                "holder_filings_3m": int(last3["holder"].sum()),
                "months_since_strategic_review": _since(upto, "strategic", m),
                "months_since_continuation": _since(upto, "continuation", m),
                "windup_headline_seen": int(upto["windup"].any()),
                "announcements_3m": int(len(last3))})
    return pd.DataFrame(out)


def _months(end_month: str, before: int) -> list[str]:
    p = pd.Period(end_month, freq="M")
    return [str(p - k) for k in range(before, -1, -1)]


def _since(upto: pd.DataFrame, col: str, m: str):
    hit = upto[upto[col]]
    if not len(hit):
        return None
    first = pd.Period(hit["month"].min(), freq="M")
    return int((pd.Period(m, freq="M") - first).n)
