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


def listing_coverage(episodes: pd.DataFrame, before: int = 18,
                     listings_dir: Path = UK_LISTINGS,
                     au: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per episode with a ticker: does a headline index exist, how far does
    it reach, and does it reach the window? The reason a fund's window is
    empty must be known - no listing, a listing that starts after the
    ending, or a listing with nothing to read - before the extractor's
    silence is read as the fund's."""
    if au is None:
        au = au_index()
    out = []
    for ep in episodes.itertuples(index=False):
        if not isinstance(ep.ticker, str) or not ep.ticker:
            continue
        rows = (au[au["ticker"].eq(ep.ticker)] if (ep.market == "AU" and len(au))
                else uk_listing(ep.ticker, listings_dir) if ep.market != "AU"
                else pd.DataFrame())
        start = str(pd.Period(ep.end_month, freq="M") - before)
        rec = {"security_id": ep.security_id, "market": ep.market, "ticker": ep.ticker,
               "end_month": ep.end_month, "window_start": start,
               "listing_rows": int(len(rows)), "listing_first": None, "listing_last": None,
               "reaches_window": False, "window_rows": 0}
        if len(rows):
            d = rows["date"].fillna("")
            d = d[d.str.match(r"\d{4}-\d{2}")]
            if len(d):
                rec["listing_first"], rec["listing_last"] = d.min()[:10], d.max()[:10]
                rec["reaches_window"] = d.min()[:7] <= ep.end_month
                rec["window_rows"] = int(((d.str[:7] >= start) & (d.str[:7] <= ep.end_month)).sum())
        out.append(rec)
    return pd.DataFrame(out)


# ------------------------------------------------------------ the control cohort
def _alive_through(reg: pd.DataFrame, start: str, end_plus: str) -> pd.DataFrame:
    """Registry rows listed before `start` and still listed after `end_plus`
    (or live today): funds whose window is not the run-up to an ending."""
    first = reg["first_seen"].astype(str).str[:7]
    last = reg["last_seen"].astype(str).str[:7]
    live = reg["status"].astype(str).isin(("live", "live_stale_nav"))
    return reg[(first <= start) & ((last >= end_plus) | live)]


def select_controls(episodes: pd.DataFrame, registry: pd.DataFrame, before: int = 18,
                    clearance: int = 12, per_case: int = 1,
                    tickers: dict[str, str] | None = None) -> pd.DataFrame:
    """For each episode, `per_case` funds of the same market that were listed
    throughout the same calendar window and for at least `clearance` months
    after the ending, chosen deterministically (crc32 of the episode id over
    the sorted candidates). Their documents in the same window are read
    under the same rules, so a feature's rate among endings is compared
    with its rate among survivors over the same months - the first
    evaluation read documents only for funds that ended, and every
    non-silent feature was by construction within 18 months of an ending."""
    import zlib
    from learning import episodes as E
    if tickers is None:
        tickers = E._tickers()
    ended = set(episodes["security_id"])
    reg = registry[registry["market"].isin(set(episodes["market"]))]
    # a control is a fund: never a benchmark row the registry carries
    if "research_eligible" in reg.columns:
        reg = reg[reg["research_eligible"].fillna(False).astype(bool)
                  | reg["status"].astype(str).isin(("live", "live_stale_nav"))]
    reg = reg[~reg["name"].astype(str).str.contains(r"S&P|\bindex\b|accumulation", case=False, regex=True)]
    out = []
    for ep in episodes.itertuples(index=False):
        if not isinstance(ep.ticker, str) or not ep.ticker:
            continue
        start = str(pd.Period(ep.end_month, freq="M") - before)
        end_plus = str(pd.Period(ep.end_month, freq="M") + clearance)
        cands = _alive_through(reg[reg["market"].eq(ep.market)], start, end_plus)
        cands = cands[~cands["security_id"].isin(ended)]
        ids = sorted(cands["security_id"])
        if not ids:
            continue
        h = zlib.crc32(str(ep.security_id).encode())
        for k in range(per_case):
            sid = ids[(h + k) % len(ids)]
            t = sid[4:] if sid.startswith("ASX:") else tickers.get(sid)
            if not t:
                continue
            out.append({"security_id": sid, "market": ep.market, "ticker": str(t).upper(),
                        "end_month": ep.end_month, "case_id": ep.security_id,
                        "name": cands.set_index("security_id")["name"].get(sid)})
    cols = ["security_id", "market", "ticker", "end_month", "case_id", "name"]
    return pd.DataFrame(out, columns=cols)


# ------------------------------------------------------ universe headline features
def headline_features_universe(registry: pd.DataFrame, listings_dir: Path = UK_LISTINGS,
                               au: pd.DataFrame | None = None,
                               tickers: dict[str, str] | None = None,
                               start_month: str = "2007-01") -> pd.DataFrame:
    """The headline features for EVERY registry fund with a headline index,
    every month it was listed - the model-free baseline over the whole
    universe, endings and survivors alike. Vectorised per fund: monthly
    counts, a trailing three-month sum, and months since the first
    strategic-review / continuation headline."""
    from learning import episodes as E
    if au is None:
        au = au_index()
    if tickers is None:
        tickers = E._tickers()
    out = []
    for r in registry.itertuples(index=False):
        sid = r.security_id
        ticker = sid[4:] if str(sid).startswith("ASX:") else tickers.get(sid)
        if not ticker:
            continue
        if r.market == "AU":
            rows = au[au["ticker"].eq(ticker)] if len(au) else pd.DataFrame()
            bb, hold = AU_BUYBACK_EXEC, AU_HOLDER
        else:
            rows = uk_listing(str(ticker).upper(), listings_dir)
            bb, hold = UK_BUYBACK_EXEC, UK_HOLDER
        if not len(rows):
            continue
        rows = rows[rows["date"].fillna("").str.match(r"\d{4}-\d{2}")]
        if not len(rows):
            continue
        h = rows["headline"].fillna("")
        m = rows["date"].str[:7]
        flags = pd.DataFrame({"buyback": h.str.contains(bb), "holder": h.str.contains(hold),
                              "strategic": h.str.contains(STRATEGIC),
                              "continuation": h.str.contains(CONTINUATION),
                              "windup": h.str.contains(WINDUP), "n": 1}).groupby(m.values).sum()
        first = max(start_month, str(r.first_seen)[:7]) if isinstance(r.first_seen, str) else start_month
        last = str(r.last_seen)[:7] if isinstance(r.last_seen, str) else m.max()
        if first > last:
            continue
        idx = pd.period_range(first, last, freq="M").astype(str)
        flags = flags.reindex(idx, fill_value=0)
        roll = flags[["buyback", "holder", "n"]].rolling(3, min_periods=1).sum()
        cum = flags[["windup"]].cumsum()
        first_strat = flags["strategic"].gt(0).idxmax() if flags["strategic"].gt(0).any() else None
        first_cont = flags["continuation"].gt(0).idxmax() if flags["continuation"].gt(0).any() else None
        pos = {mo: i for i, mo in enumerate(idx)}
        df = pd.DataFrame({
            "security_id": sid, "obs_month": idx,
            "buyback_execs_3m": roll["buyback"].astype(int).values,
            "holder_filings_3m": roll["holder"].astype(int).values,
            "months_since_strategic_review": [(i - pos[first_strat]) if first_strat and i >= pos[first_strat] else None
                                              for i in range(len(idx))],
            "months_since_continuation": [(i - pos[first_cont]) if first_cont and i >= pos[first_cont] else None
                                          for i in range(len(idx))],
            "windup_headline_seen": cum["windup"].gt(0).astype(int).values,
            "announcements_3m": roll["n"].astype(int).values})
        out.append(df)
    if not out:
        return pd.DataFrame(columns=["security_id", "obs_month", "buyback_execs_3m", "holder_filings_3m",
                                     "months_since_strategic_review", "months_since_continuation",
                                     "windup_headline_seen", "announcements_3m"])
    return pd.concat([o for o in out if len(o)], ignore_index=True)


def listing_targets(episodes: pd.DataFrame, listings_dir: Path = UK_LISTINGS,
                    before: int = 18) -> pd.DataFrame:
    """UK episodes with a ticker whose headline index is missing or starts
    after the window: the funds the survivorship-free corpus is FOR, and
    the ones the listing crawl (seeded from the aggregator's priced
    universe) never reached. 113 of 366 had an index on 2026-09-10 and 66
    reached their window; pre-2022 the universe headline features were
    therefore survivors' features, and read as the opposite of a signal."""
    cov = listing_coverage(episodes[episodes["market"].eq("UK")], before=before,
                           listings_dir=listings_dir, au=pd.DataFrame())
    if not len(cov):
        return cov
    want = cov[(cov["listing_rows"] == 0) | ~cov["reaches_window"]].copy()
    names = episodes.drop_duplicates("security_id").set_index("security_id")["name"]
    want["name"] = want["security_id"].map(names)
    return want.sort_values("end_month", ascending=False)


# ------------------------------------------------ AIC corporate-activity features
AIC_CATEGORIES = ("tender", "buyback", "realisation_policy", "reconstruction",
                  "manager_change", "fee_change", "policy_change", "liquidation",
                  "redemption", "capital_return")


def aic_activity_features(ca: pd.DataFrame, months: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time features from the AIC corporate-activity record, for
    every (security_id, obs_month) in `months` (which carries company_name).

    The AIC archive records an action by its effective month, so a feature
    at month t reads only records dated t or earlier: months since the
    first record of each category, and the count in the trailing twelve
    months. It covers 2007 onward for every fund the MIR ever listed, which
    is what the Investegate index cannot give the older endings."""
    from uk_cef.entities import normalize_name
    cols = ["security_id", "obs_month"] + [f"aic_{c}_since" for c in AIC_CATEGORIES] \
        + [f"aic_{c}_12m" for c in AIC_CATEGORIES]
    if ca is None or not len(ca) or months is None or not len(months):
        return pd.DataFrame(columns=cols)
    ca = ca[ca["category"].isin(AIC_CATEGORIES)].copy()
    ca["key"] = ca["company_name"].map(normalize_name)
    ca["m"] = ca["event_month"].astype(str).str[:7]
    by_key = {k: g for k, g in ca.groupby("key")}
    out = []
    for (sid, name), g in months.groupby(["security_id", "company_name"]):
        ev = by_key.get(normalize_name(name))
        idx = sorted(set(g["obs_month"].astype(str)))
        if ev is None or not idx:
            continue
        span = pd.period_range(min(idx[0], ev["m"].min()), idx[-1], freq="M").astype(str)
        counts = pd.crosstab(ev["m"], ev["category"]).reindex(span, fill_value=0)
        for c in AIC_CATEGORIES:
            if c not in counts.columns:
                counts[c] = 0
        roll = counts[list(AIC_CATEGORIES)].rolling(12, min_periods=1).sum()
        pos = {m: i for i, m in enumerate(span)}
        first = {c: (counts[c].gt(0).idxmax() if counts[c].gt(0).any() else None) for c in AIC_CATEGORIES}
        rows = []
        for m in idx:
            i = pos.get(m)
            if i is None:
                continue
            r = {"security_id": sid, "obs_month": m}
            for c in AIC_CATEGORIES:
                f = first[c]
                r[f"aic_{c}_since"] = (i - pos[f]) if f is not None and i >= pos[f] else None
                r[f"aic_{c}_12m"] = int(roll[c].iloc[i])
            rows.append(r)
        out.append(pd.DataFrame(rows))
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True)[cols]
