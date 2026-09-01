"""CEF-LIVE CLI.

  python -m cef_live.cli nightly [--markets au,uk]

Builds the live NTA table for each market from the research panels,
harvests Tier 0 NAV announcements (AU via the open index; UK census from
the crawler cache), writes data/nta_live/latest.parquet + a readable CSV,
snapshots to S3 (append-only, never overwritten), and emits the Phase 1
acceptance report to reports/build/.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from . import (catalysts, forward_irr, harvest_nav, identity, liveness,
               nta_live, opportunities, prices, tickers, universe,
               universe_report)


log = logging.getLogger(__name__)


def _params() -> dict:
    raw = Path("config/params.yaml").read_text()
    raw = re.sub(r"\$\{[^}]+\}", "", raw)   # env placeholders not needed here
    return yaml.safe_load(raw)


def _snapshot_s3(path: Path, key: str) -> str:
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        return "s3_skipped_no_bucket"
    import boto3
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    # append-only: refuse to overwrite an existing snapshot
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return f"s3_exists_not_overwritten:{key}"
    except Exception:  # noqa: BLE001
        pass
    s3.upload_file(str(path), bucket, key)
    return f"s3_uploaded:{key}"



REPORT_HEAD = re.compile(
    r"annual report|half[\s-]?year(?:ly)? (?:report|result|account)|"
    r"preliminary final report|appendix 4[de]\b|financial report|"
    r"statutory accounts|annual financial", re.I)

# An ASX headline that IS a published NTA/NAV statement. Liveness asks
# whether the fund published a NAV, not whether we managed to parse the PDF
# it published it in - conflating the two made 106 currently-reporting LICs
# read as `live_stale_nav` (alive on an Appendix 4E) while they were filing
# daily NTA statements the whole time. The parse result stays a separate,
# and separately reported, fact.
#
# It is the harvester's pattern, not a second copy of it: two lists of
# "headlines that carry an NTA" would drift, and the fund that fell in the
# gap would be live-but-unpriceable for a reason no column explains.
NAV_HEAD = harvest_nav.AU_NAV_HEAD


def _liveness_evidence() -> pd.DataFrame:
    """When each fund last published a NAV, a periodic report, or anything.

    Read from what we already hold - the UK announcement archive, the ASX
    announcement index, and our own extracted NAV history - so liveness is
    decided by the funds' own filings rather than by an aggregator's
    editorial coverage.
    """
    rows: dict[str, dict] = {}

    def note(sid, key, when):
        if not sid or when is None or pd.isna(when):
            return
        d = rows.setdefault(str(sid), {"security_id": str(sid)})
        prev = d.get(key)
        w = str(pd.Timestamp(when).date())
        if prev is None or w > prev:
            d[key] = w

    for market in ("UK", "AU"):
        own = _own_nav_history(market)
        if own is not None and len(own):
            for sid, g in own.groupby("security_id"):
                note(sid, "last_nav", pd.to_datetime(g["nav_date"],
                                                     errors="coerce").max())

    # AU: the market-wide announcement index carries every filing with a date
    idx_f = Path("data/asx_ann_cache/asx1/lic_announcement_index.parquet")
    if idx_f.exists():
        idx = pd.read_parquet(idx_f)
        idx["d"] = pd.to_datetime(idx["release_date"], utc=True, errors="coerce")
        idx = idx.dropna(subset=["d"])
        for code, g in idx.groupby("code"):
            sid = f"ASX:{str(code).upper()}"
            note(sid, "last_announcement", g["d"].max())
            rep = g[g["headline"].fillna("").str.contains(REPORT_HEAD)]
            if len(rep):
                note(sid, "last_report", rep["d"].max())
            # a published NTA statement is a published NAV, whether or not
            # the PDF behind it parsed
            nav = g[g["headline"].fillna("").str.contains(NAV_HEAD)]
            if len(nav):
                note(sid, "last_nav", nav["d"].max())

    # UK: the committed NAV announcement archive. Every row in it is a
    # "Net Asset Value(s)" RNS (scripts/archive_uk_navs.py filters on that
    # headline), so its date is evidence the fund PUBLISHED a NAV - whether
    # or not our parser got a number out of the page. Counting only parsed
    # rows conflated "the fund went quiet" with "our regex missed", and put
    # Personal Assets, Law Debenture, Temple Bar and Fidelity China - all
    # filing NAVs within the last week - into the delisting review queue.
    # Parse success is a coverage fact and is reported as one, separately.
    # Unlike the crawler cache below this archive is in the repository, so
    # liveness does not silently degrade when an ephemeral CI cache misses.
    tp0 = Path("config/resolved_tickers.csv")
    if tp0.exists():
        t0 = pd.read_csv(tp0)
        t0 = t0[t0["status"] == "verified"]
        by_tk = {str(r.ticker).upper(): r.security_id
                 for r in t0.itertuples(index=False) if pd.notna(r.ticker)}
        for f in sorted(Path("data").glob("uk_nav_history*.parquet")):
            try:
                h = pd.read_parquet(f, columns=["ticker", "ann_date"])
            except Exception:  # noqa: BLE001
                continue
            h["ticker"] = h["ticker"].astype(str).str.upper()
            h["d"] = pd.to_datetime(h["ann_date"], errors="coerce")
            for tk, g in h.dropna(subset=["d"]).groupby("ticker"):
                sid = by_tk.get(tk)
                if sid:
                    note(sid, "last_announcement", g["d"].max())
                    note(sid, "last_nav", g["d"].max())

    # UK: the Investegate listing cache, keyed by ticker -> security_id
    cache = Path("data/investegate_cache/listings")
    tp = Path("config/resolved_tickers.csv")
    if cache.exists() and tp.exists():
        t = pd.read_csv(tp)
        t = t[t["status"] == "verified"]
        by_ticker = {str(r.ticker).upper(): r.security_id
                     for r in t.itertuples(index=False) if pd.notna(r.ticker)}
        for f in cache.glob("*.csv"):
            sid = by_ticker.get(f.stem.upper())
            if not sid:
                continue
            try:
                df_ = pd.read_csv(f, dtype=str)
            except Exception:  # noqa: BLE001
                continue
            if "date" not in df_.columns:
                continue
            d = pd.to_datetime(df_["date"], errors="coerce").dropna()
            if len(d):
                note(sid, "last_announcement", d.max())
            if "headline" in df_.columns:
                rep = df_[df_["headline"].fillna("").str.contains(REPORT_HEAD)]
                dr = pd.to_datetime(rep["date"], errors="coerce").dropna()
                if len(dr):
                    note(sid, "last_report", dr.max())
    return pd.DataFrame(list(rows.values())) if rows else pd.DataFrame(
        columns=["security_id", "last_nav", "last_report", "last_announcement"])


def build_universe() -> int:
    """Registry of every listed vehicle - priced or not, live or dead."""
    import yaml as _yaml
    cfg_uk = None
    p = Path("config/default.yaml")
    if p.exists():
        cfg_uk = _yaml.safe_load(p.read_text())
    reg = universe.build(cfg_uk, _params())
    # liveness from the funds' own filings; the aggregator's status is kept
    # alongside as `aggregator_status` so the disagreement is measurable
    ev = _liveness_evidence()
    before = reg["status"].value_counts().to_dict()
    reg = liveness.apply(reg, ev, params=_params())

    # PERSIST it. universe.build() writes the registry with the AGGREGATOR's
    # status; this function then computed a better one from the funds' own
    # filings and wrote only a summary JSON, so every downstream reader -
    # _registry_for, the NAV harvest target set, the idea scan's universe
    # filter, the spreadsheet - kept reading the aggregator's answer. The
    # improved liveness existed and was measured nightly, and nothing used
    # it. Writing it back is the whole point of computing it.
    out = Path("data/universe")
    out.mkdir(parents=True, exist_ok=True)
    reg.to_parquet(out / "registry.parquet", index=False)
    reg.to_csv(out / "registry.csv", index=False)

    cand = reg[reg["status"] == liveness.STATUS_CANDIDATE]
    summary = {
        "delist_candidates": int(len(cand)),
        "vehicles": int(len(reg)),
        "by_market": reg.groupby("market").size().to_dict(),
        "live": int((reg["status"] == "live").sum()),
        "live_stale_nav": int((reg["status"] == "live_stale_nav").sum()),
        "status_before_evidence": before,
        "evidence_rows": int(len(ev)),
        "persisted_to": "data/universe/registry.parquet",
        "live_status_source": reg["live_status_source"].value_counts().to_dict(),
        "revived_by_own_filings": int(((reg["aggregator_status"] == "delisted")
                                       & (reg["status"].isin(
                                           ["live", "live_stale_nav"]))).sum()),
        "aggregator_said_live_evidence_says_not": int(
            ((reg["aggregator_status"] == "live")
             & (reg["status"].isin(["delist_candidate", "delisted"]))).sum()),
        "by_liveness_reason": reg["liveness_reason"].str.replace(
            r"_\d+d.*", "", regex=True).value_counts().head(10).to_dict(),
        "delisted": int((reg["status"] == "delisted").sum()),
        "priced_by_source": int(reg["source_prices_it"].sum()),
        "announcements_only": int((reg["nav_route"] == "announcements_only").sum()),
        "live_announcements_only": int(((reg["status"] == "live") &
                                        (reg["nav_route"] == "announcements_only")).sum()),
        "offshore": int(reg["offshore"].sum()),
        "vct": int(reg["is_vct"].sum()),
        "live_by_domicile": reg[reg["status"] == "live"]["domicile"]
                            .value_counts(dropna=False).head(8).to_dict(),
        # the cohort the rebuild exists for: listed, live, never priced by
        # the registry source - these must come from their own announcements
        "live_announcements_only_funds": reg[
            (reg["status"] == "live") & (reg["nav_route"] == "announcements_only")
        ][["security_id", "name", "sector", "domicile", "isin", "first_seen",
           "months_listed"]].sort_values("name").to_dict("records"),
        "live_announcements_only_by_sector": reg[
            (reg["status"] == "live") & (reg["nav_route"] == "announcements_only")
        ]["sector"].value_counts().to_dict(),
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/universe_registry.json").write_text(
        json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))

    # review queue: warn BEFORE anything stops being tracked, so a fund is
    # never dropped without a chance to keep it
    if len(cand):
        cand.sort_values(["market", "name"])[
            ["security_id", "name", "market", "sector", "last_seen",
             "months_missing", "review_action"]
        ].to_csv("outputs/live/delist_review.csv", index=False)
        lines = [f"  {r['name']} ({r.get('market','')}) — last seen "
                 f"{r['last_seen']}, missing {int(r['months_missing'])} monthly "
                 f"release(s)" for _, r in cand.sort_values("months_missing",
                                                            ascending=False).head(30).iterrows()]
        # NOT emailed: the two daily pre-open briefs are the only scheduled
        # emails (owner instruction 2026-09-01, config/CHANGELOG.md). The
        # queue is committed as outputs/live/delist_review.csv and its count
        # is carried in the briefs.
        print(f"{len(cand)} fund(s) awaiting delisting review "
              "(outputs/live/delist_review.csv):\n" + "\n".join(lines))
    return 0



PANEL_PATHS = {"UK": "data/processed/monthly_panel.parquet",
               "AU": "data/au_processed/au_monthly_panel.parquet"}


def panel_for(market: str) -> pd.DataFrame | None:
    """The monthly research panel for ONE market, or None.

    UK securities carry UK history and AU securities carry AU history.
    Reading `data/processed/monthly_panel.parquet` and calling it "the
    panel" gave every ASX LIC a NaN growth input and judged it against a
    UK trailing-return hurdle - a silent cross-market join that no column
    in the output revealed.
    """
    f = Path(PANEL_PATHS.get(market, ""))
    if not f.exists():
        return None
    try:
        panel = pd.read_parquet(f)
    except Exception:  # noqa: BLE001
        return None
    if "discount" not in panel.columns:
        for px, nav in (("share_price", "nav_per_share"),
                        ("share_price", "nta_derived"), ("price", "nav")):
            if {px, nav} <= set(panel.columns):
                panel["discount"] = panel[px] / panel[nav] - 1.0
                break
    panel["market"] = market
    return panel


def panels_by_market(markets=("UK", "AU")) -> dict[str, pd.DataFrame]:
    got = {m: panel_for(m) for m in markets}
    return {m: p for m, p in got.items() if p is not None and len(p)}


def _verified_tickers() -> pd.DataFrame:
    frames = []
    for f, status in ((Path("config/resolved_tickers.csv"), "status"),
                      (Path("config/investegate_tickers.csv"), None)):
        if not f.exists():
            continue
        try:
            t = pd.read_csv(f, comment="#")
        except Exception:  # noqa: BLE001
            continue
        if status and status in t.columns:
            t = t[t[status] == "verified"]
        if {"security_id", "ticker"} <= set(t.columns):
            frames.append(t[["security_id", "ticker"]])
    if not frames:
        return pd.DataFrame(columns=["security_id", "ticker"])
    return pd.concat(frames, ignore_index=True).dropna(
        subset=["ticker"]).drop_duplicates("security_id", keep="first")


def _with_identity(reg: pd.DataFrame) -> pd.DataFrame:
    """Registry plus ticker identity - who a ticker's live quote belongs to.

    A ticker is a lease, not an identity: CIP was CIP Merchant Capital
    until 2022 and is Channel Islands Property now. Resolving this BEFORE
    the price layer is the only reliable place to stop a reused ticker,
    because a wrong price is indistinguishable from a right one once it has
    been fetched.
    """
    return identity.resolve(reg, _verified_tickers(),
                            live_statuses=tuple(liveness.LIVE_STATUSES))


def _registry_for(market: str) -> pd.DataFrame:
    """Live funds for one market - the universe, from identity alone."""
    rp = Path("data/universe/registry.parquet")
    if not rp.exists():
        return pd.DataFrame()
    reg = pd.read_parquet(rp)
    # `live_stale_nav` is alive - it means "trading, NAV not fresh yet".
    # Once liveness is persisted, omitting it here would drop 100+ funds
    # out of the priced universe for a data-coverage reason.
    out = reg[(reg["market"] == market)
              & (reg["status"].isin(liveness.TRACKED_STATUSES))].copy()
    return _with_identity(out)


def _own_nav_history(market: str) -> pd.DataFrame:
    """NAV observations WE extracted, from the funds' own announcements.

    UK: the Investegate NAV archive. AU: the deterministic pass over the
    archived ASX PDFs. This is what replaces the aggregator as the source of
    value - the aggregator only ever says who exists.
    """
    frames = []
    if market == "UK":
        # The daily NAV panel (data/uk/nav, cef_live.uk_nav_panel) is the
        # better UK store where it exists: normalised, deduplicated on
        # ann_id, plausibility-filtered, and re-parsed by
        # `uk-daily --stages nav --reparse-unparsed` whenever the rule list
        # grows. Reading only the legacy shards meant a NAV that a re-parse
        # had just recovered stayed invisible to the live table, so the same
        # fund could be fixed in one store and still RED in the audit.
        # Both are read: the panel starts in 2007 but need not cover every
        # ticker, and nta_live takes the newest anchor per fund either way.
        try:
            from . import uk_nav_panel as UKP
            panel = UKP.read_panel()
        except Exception:  # noqa: BLE001
            panel = None
        if panel is not None and len(panel):
            # A fund quoting USD or CAD cannot be divided into a pence price.
            # The panel records the unit each announcement stated; taking
            # nav_pence without reading nav_ccy would put a $1.91 NAV into a
            # pence column against a 200p price - the unit bug, arriving
            # through a column that exists precisely to prevent it.
            if "nav_ccy" in panel.columns:
                ccy = panel["nav_ccy"].fillna("GBX").astype(str).str.upper()
                foreign = int((~ccy.isin(("GBX", "GBP", "GBP_PENCE", "STG"))).sum())
                if foreign:
                    log.info("UK NAV panel: %d non-sterling observation(s) "
                             "excluded from the pence anchor", foreign)
                panel = panel[ccy.isin(("GBX", "GBP", "GBP_PENCE", "STG"))]
            frames.append(pd.DataFrame({
                "ticker": panel["ticker"].astype(str).str.upper(),
                "nav_date": panel["nav_date"],
                "nav_value": pd.to_numeric(panel["nav_pence"], errors="coerce"),
                "nav_unit": "GBX",
            }))
        for f in sorted(Path("data").glob("uk_nav_history*.parquet")):
            try:
                h = pd.read_parquet(f)
            except Exception:  # noqa: BLE001
                continue
            h = h[h.get("status").eq("parsed")] if "status" in h.columns else h
            if not {"ticker", "nav_cum_pence"} <= set(h.columns):
                continue
            if "nav_ccy" in h.columns:            # same rule for the shards
                h = h[h["nav_ccy"].fillna("GBX").astype(str).str.upper().isin(
                    ("GBX", "GBP", "GBP_PENCE", "STG"))]
            # PENCE, not pounds. The UK canonical NAV unit is GBX
            # (units.CANONICAL_UNIT["UK"]) - the unit Yahoo quotes London
            # shares in, and the unit the AIC panel's nav_col and the Tier 0
            # harvest already carry. Dividing by 100 here put this one anchor
            # in a different unit from the price it is divided by: the 20
            # funds anchored this way carried discount_est between +79 and
            # +5649 (premiums of 7,990% to 564,900%) in the committed table -
            # nonsense large enough to be obvious in isolation and easy to
            # miss in a 641-row file. The unit is fixed at the source and now
            # STATED on the frame, so units.normalise does any conversion
            # once and explicitly. tests/test_uk_daily_discount.py asserts
            # the unit stays shared.
            frames.append(pd.DataFrame({
                "ticker": h["ticker"].astype(str).str.upper(),
                "nav_date": h.get("nav_date", h.get("ann_date")),
                "nav_value": pd.to_numeric(h["nav_cum_pence"], errors="coerce"),
                "nav_unit": "GBX",
            }))
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True).dropna(subset=["nav_value"])
        tp = Path("config/resolved_tickers.csv")
        if not tp.exists():
            return pd.DataFrame()
        t = pd.read_csv(tp)
        t = t[t["status"] == "verified"][["security_id", "ticker"]]
        t["ticker"] = t["ticker"].astype(str).str.upper()
        return out.merge(t, on="ticker", how="inner")[
            ["security_id", "nav_date", "nav_value", "nav_unit"]]

    # AU: the deterministic pass over the archived ASX PDFs. Its output is
    # written to data/asx_extract AND uploaded to S3; a CI runner has only
    # the S3 copy, so the loader restores it when the local cache is empty.
    # Reading only the local directory meant this tier was always empty on a
    # runner and every ASX fund fell back to the aggregator's monthly print.
    from au_lic.extract import facts as AUF
    own = AUF.nav_observations()
    return own if len(own) else pd.DataFrame()


def _uk_aux_discount_history() -> pd.DataFrame | None:
    """Monthly UK discount history from the daily panel, keyed by security_id.

    The aggregator's monthly panel never priced the announcements-only
    cohort, so those funds could not z-score however good their own NAV
    series was. The daily panel holds exactly that series; this resamples
    it monthly (uk_discount.monthly_history) and joins it to security_ids
    through the VERIFIED ticker map. A ticker claimed by more than one
    fund is dropped rather than guessed - identity ambiguity must not leak
    a discount history across funds.
    """
    from . import uk_discount
    hist = uk_discount.monthly_history()
    if not len(hist):
        return None
    tp = Path("config/resolved_tickers.csv")
    if not tp.exists():
        return None
    rt = pd.read_csv(tp)
    rt = rt[(rt["status"] == "verified") & rt["ticker"].notna()]
    m = rt[["security_id", "ticker"]].copy()
    m["ticker"] = m["ticker"].astype(str).str.upper()
    m = m[~m["ticker"].duplicated(keep=False)]
    hist = hist.copy()
    hist["ticker"] = hist["ticker"].astype(str).str.upper()
    out = hist.merge(m, on="ticker", how="inner")
    return out[["security_id", "obs_month", "discount"]] if len(out) else None


def _signal_coverage(live: pd.DataFrame, irr: pd.DataFrame | None,
                     live_sids: set | None = None) -> dict:
    """The objective function: can we PRICE the universe?

    Share of live, research-eligible funds carrying all three of: a NAV
    that is live or a good estimate (basis <= 2, fresh by the fund's own
    cadence), a discount z against the fund's own history, and a growth
    input for the forward IRR. Written every run so progress toward the
    90% target is measured, never assumed. Per-fund blockers go to
    outputs/live/signal_coverage.csv - that file is the work list.
    """
    df = live.copy()
    if irr is not None and len(irr):
        df = df.merge(irr[["security_id", "irr_central", "g_used"]],
                      on="security_id", how="left")
    else:
        df["irr_central"] = np.nan
        df["g_used"] = np.nan
    df = df[df["research_eligible"].fillna(False)]
    # the denominator is LIVE funds only - the panel carries history for
    # delisted ones too, and counting the dead would flatter nothing but
    # would distort the target
    if live_sids is not None:
        df = df[df["security_id"].astype(str).isin(live_sids)]
    lim = df["staleness_limit_days"] if "staleness_limit_days" in df.columns \
        else 45.0
    nav_ok = df["basis"].le(2) & df["staleness_days"].le(lim)
    z_ok = df["z_adj"].notna()
    growth_ok = df["g_used"].notna()
    irr_ok = df["irr_central"].notna()
    complete = nav_ok & z_ok & growth_ok

    def _blk(r, n_ok, z_ok_, g_ok):
        b = []
        if not n_ok:
            b.append("nav")
        if not z_ok_:
            b.append(f"z:{r.get('z_status')}")
        if not g_ok:
            b.append("growth")
        return "+".join(b) or ""

    per_fund = df.assign(
        nav_ok=nav_ok, z_ok=z_ok, growth_ok=growth_ok, irr_ok=irr_ok,
        signal_complete=complete)
    per_fund["blockers"] = [
        _blk(r, n, z_, g_) for (_, r), n, z_, g_ in
        zip(df.iterrows(), nav_ok, z_ok, growth_ok)]
    cols = ["security_id", "name", "market", "basis", "staleness_days",
            "z_adj", "z_status", "z_source", "g_used", "irr_central",
            "nav_ok", "z_ok", "growth_ok", "irr_ok", "signal_complete",
            "blockers"]
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    per_fund[[c for c in cols if c in per_fund.columns]].to_csv(
        "outputs/live/signal_coverage.csv", index=False)

    summary: dict = {"generated_at": datetime.now(timezone.utc)
                     .isoformat(timespec="seconds"),
                     "target_pct": 90.0, "markets": {}}
    for mkt, g in per_fund.groupby("market"):
        summary["markets"][mkt] = {
            "denominator_live_research_eligible": int(len(g)),
            "nav_ok": int(g["nav_ok"].sum()),
            "z_ok": int(g["z_ok"].sum()),
            "growth_ok": int(g["growth_ok"].sum()),
            "irr_ok": int(g["irr_ok"].sum()),
            "signal_complete": int(g["signal_complete"].sum()),
            "signal_complete_pct": round(
                100.0 * g["signal_complete"].mean(), 1) if len(g) else 0.0,
        }
    n = len(per_fund)
    summary["total"] = {
        "denominator": n,
        "signal_complete": int(per_fund["signal_complete"].sum()),
        "signal_complete_pct": round(
            100.0 * per_fund["signal_complete"].mean(), 1) if n else 0.0,
        "top_blockers": per_fund.loc[~per_fund["signal_complete"], "blockers"]
        .value_counts().head(12).to_dict(),
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/signal_coverage.json").write_text(
        json.dumps(summary, indent=2, default=str))
    print("signal coverage:", json.dumps(summary["total"], default=str))
    return summary


def resolve_tickers(budget: int = 400) -> int:
    """Resolve Investegate/Yahoo tickers for live registry funds with none."""
    reg = pd.read_parquet("data/universe/registry.parquet")
    cfg_uk = None
    p = Path("config/default.yaml")
    if p.exists():
        cfg_uk = yaml.safe_load(p.read_text())
    tickers.seed_known(reg, cfg_uk)      # free: already-verified identifiers
    out = tickers.resolve(reg, budget=budget)
    alive = reg["status"].isin(liveness.LIVE_STATUSES)
    live_uk = reg[alive & (reg["market"] == "UK")]
    got = out[out["status"] == "verified"]
    # the cohort that actually matters: funds with NO other NAV source
    ao = reg[alive & (reg["market"] == "UK")
             & (reg["nav_route"] == "announcements_only")]
    ao_got = ao.merge(got[["security_id", "ticker"]], on="security_id", how="inner")
    ao_missing = ao[~ao["security_id"].isin(set(got["security_id"]))]
    summary = {"attempted_total": int(len(out)),
               "verified": int(len(got)),
               "unresolved": int((out["status"] != "verified").sum()),
               "live_uk_funds": int(len(live_uk)),
               "coverage": round(len(got) / max(1, len(live_uk)), 4),
               "announcements_only_total": int(len(ao)),
               "announcements_only_resolved": int(len(ao_got)),
               "announcements_only_coverage": round(len(ao_got) / max(1, len(ao)), 4),
               "announcements_only_resolved_names": ao_got[["name", "ticker"]]
                   .sort_values("name").to_dict("records"),
               "announcements_only_unresolved_names": ao_missing[["name", "sector", "domicile"]]
                   .sort_values("name").to_dict("records")}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/ticker_resolution.json").write_text(
        json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2))
    return 0


def build_universe_workbook() -> tuple[Path, dict]:
    """Build the universe spreadsheet (and refresh the forward IRR store).

    Emailing is the caller's decision: the two daily pre-open briefs attach
    this workbook, so nothing here sends anything.
    """
    reg = pd.read_parquet("data/universe/registry.parquet")
    live = pd.read_parquet("data/nta_live/latest.parquet")
    panels = panels_by_market()
    # the sheet's historical discount stats are per market; concatenating
    # keeps every security joined to its OWN market's history
    hist = pd.concat(panels.values(), ignore_index=True) if panels else None
    irr = None
    if panels:
        try:
            aux_h = {}
            try:
                a = _uk_aux_discount_history()
                if a is not None:
                    aux_h["UK"] = a
            except Exception as exc:  # noqa: BLE001
                print(f"UK aux discount history unavailable ({exc})")
            irr = forward_irr.build_by_market(
                live, panels, _params(),
                own_hist={m: _own_nav_history(m) for m in panels},
                aux_discount_hist=aux_h)
            Path("data/forward_irr").mkdir(parents=True, exist_ok=True)
            irr.to_parquet("data/forward_irr/latest.parquet", index=False)
        except Exception as exc:  # noqa: BLE001
            print(f"forward IRR failed ({exc}); sheet will omit it")

    # the objective function: can we price the universe? Measured every
    # run, per market, with the per-fund work list committed beside it.
    try:
        live_sids = set(reg.loc[reg["status"].isin(liveness.LIVE_STATUSES),
                                "security_id"].astype(str))
        _signal_coverage(live, irr, live_sids)
    except Exception as exc:  # noqa: BLE001
        print(f"signal coverage measurement failed ({exc})")

    # attach resolved tickers so the sheet is addressable
    tp = Path("config/resolved_tickers.csv")
    if tp.exists():
        tk = pd.read_csv(tp)
        tk = tk[tk["status"] == "verified"][["security_id", "ticker"]]
        reg = reg.merge(tk, on="security_id", how="left")

    cats = None
    cp = Path("outputs/live/catalysts_recent.csv")
    if cp.exists():
        cats = pd.read_csv(cp)

    stamp = datetime.now(timezone.utc).date().isoformat()
    path, summary = universe_report.build(
        reg, live, irr, hist, Path(f"outputs/live/cef_universe_{stamp}.xlsx"), cats)
    print(json.dumps(summary, indent=2, default=str))
    return path, summary


def universe_sheet(email: bool = False) -> int:
    """Build the universe spreadsheet; email only when explicitly asked.

    The two daily pre-open briefs attach this workbook, so the scheduled
    nightly build no longer sends its own email (owner instruction
    2026-09-01, config/CHANGELOG.md). `--email` remains for manual use.
    """
    path, summary = build_universe_workbook()
    sent = False
    if email:
        from .notify import notify
        stamp = datetime.now(timezone.utc).date().isoformat()
        body = (f"CEF universe spreadsheet, {stamp}.\n\n"
                f"Funds tracked: {summary['rows']} ({summary['live']} live)\n"
                f"With a market price: {summary['with_price']}\n"
                f"With a live NAV estimate: {summary['with_live_nav']}\n"
                f"With a forward IRR: {summary['with_irr']}\n"
                f"Catalysts announced (30d): {summary.get('catalysts', 0)}\n\n"
                "Sheets: Universe (all), Live only, Most dislocated (lowest "
                "z), Catalysts.\n"
                "Published NAVs and modelled estimates are separate columns; "
                "a blank means we hold no value, never a filled-in one.")
        sent = notify(f"universe spreadsheet {stamp}", body,
                      attachments=[str(path)])
        print("emailed:", sent)
    Path("reports/build/universe_sheet.json").write_text(
        json.dumps({**summary, "emailed": sent, "file": str(path)}, indent=2, default=str))
    return 0


def ideas() -> int:
    """Pre-open brief: the day's actionable ideas + the universe workbook.

    This is one of the only two scheduled emails of the day (pre-LSE and
    pre-ASX open; owner instruction 2026-09-01, config/CHANGELOG.md). It
    always sends - an empty day says so explicitly - and always attaches
    the full universe workbook, so the inbox carries everything: the
    OPPORTUNITY/WATCH verdicts, the per-trigger lists (dislocation z,
    forward IRR >= the fixed hurdle, high-weight catalysts), and the
    complete live universe behind them.
    """
    params = _params()
    live = pd.read_parquet("data/nta_live/latest.parquet")
    cats = None
    cp = Path("outputs/live/catalysts_recent.csv")
    if cp.exists():
        cats = pd.read_csv(cp)

    # Build the workbook FIRST: it refreshes data/forward_irr/latest.parquet,
    # so the IRR gate below judges today's NAVs, not yesterday's commit. A
    # workbook failure must not cost the ideas email - the brief still goes,
    # naming the failure instead of attaching silence.
    wb_path, wb_summary, wb_error = None, {}, None
    try:
        wb_path, wb_summary = build_universe_workbook()
    except Exception as exc:  # noqa: BLE001
        wb_error = str(exc)
        print(f"universe workbook failed ({exc}); brief will say so")

    irr = None
    ip = Path("data/forward_irr/latest.parquet")
    if ip.exists():
        irr = pd.read_parquet(ip)

    # a fund that may no longer exist must not generate an idea
    rp = Path("data/universe/registry.parquet")
    if rp.exists():
        reg = pd.read_parquet(rp)
        keep = set(reg.loc[reg["status"].isin(liveness.LIVE_STATUSES),
                           "security_id"])
        before = len(live)
        live = live[live["security_id"].isin(keep)]
        print(f"universe filter: {before} -> {len(live)} live funds evaluated")

    verdicts = opportunities.evaluate(live, cats, irr, params)
    # ledger write and signal emission are one step: the email is rendered
    # FROM the ledger rows, so an idea cannot be sent without being recorded
    rows = opportunities.append_ledger(verdicts, "data/ledger/signals.parquet")
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    if len(verdicts):
        verdicts.to_csv("outputs/live/ideas_latest.csv", index=False)

    empty = verdicts.iloc[0:0]
    opps = verdicts[verdicts["verdict"] == "OPPORTUNITY"] if len(verdicts) else empty
    watch = verdicts[verdicts["verdict"] == "WATCH"] if len(verdicts) else empty
    min_irr = float(params["opportunity"]["min_irr_central"])

    def _num(v, spec):
        # verdict fields round-trip through a DataFrame, so a None written
        # by evaluate() can come back as NaN - both must read "n/a"
        return "n/a" if v is None or pd.isna(v) else format(float(v), spec)

    def _fmt(df, head):
        out = [head]
        for r in df.itertuples(index=False):
            out.append(f"\n  {r.name} ({r.market})")
            out.append(f"    discount {_num(r.discount_est, '+.1%')}  "
                       f"z {_num(r.z_adj, '+.2f')}  "
                       f"IRR {_num(r.irr_central, '+.1%')}"
                       f"  (hurdle {min_irr:+.1%})")
            if isinstance(r.catalyst_class, str) and r.catalyst_class:
                head_txt = (r.catalyst_headline
                            if isinstance(r.catalyst_headline, str) else "")
                out.append(f"    catalyst: {r.catalyst_class} "
                           f"({r.catalyst_date}) - {head_txt[:80]}")
            gates = [g for g, ok in (("dislocation", r.gate1_dislocation),
                                     ("catalyst", r.gate2_catalyst),
                                     ("return", r.gate3_return)) if ok]
            out.append(f"    gates passed: {', '.join(gates)}")
        return "\n".join(out)

    # per-trigger lists: each WATCH row appears once, under its strongest basis
    if len(watch):
        disl = watch[watch["gate1_dislocation"]].sort_values("z_adj")
        irr_led = watch[~watch["gate1_dislocation"]
                        & watch["gate3_return"]].sort_values("irr_central",
                                                             ascending=False)
        cat_led = watch[~watch["gate1_dislocation"] & ~watch["gate3_return"]
                        & watch["gate2_catalyst"]]
    else:
        disl = irr_led = cat_led = empty

    body = []
    if len(opps):
        body.append(_fmt(opps, f"{len(opps)} ACTIONABLE - dislocated, "
                               f"catalyst live, IRR >= {min_irr:.0%}, data "
                               "fully sound:"))
    else:
        body.append("No fund clears all three gates on fully sound data "
                    "today.")
    if len(disl):
        body.append(_fmt(disl.head(20),
                         f"\n{len(disl)} dislocated vs own history "
                         f"(z <= {params['opportunity']['z_threshold']}):"))
    if len(irr_led):
        body.append(_fmt(irr_led.head(15),
                         f"\n{len(irr_led)} at forward IRR >= {min_irr:.0%} "
                         "(not dislocated):"))
    if len(cat_led):
        body.append(_fmt(cat_led,
                         f"\n{len(cat_led)} with a high-weight catalyst "
                         "(tender / scheme / continuation / wind-down):"))
    if not (len(opps) or len(watch)):
        body.append(f"\nScanned {len(live)} funds. Nothing is actionable on "
                    "z-score, forward IRR or catalyst today. This email is "
                    "the proof the scan ran; silence would mean failure.")

    dr = Path("outputs/live/delist_review.csv")
    n_delist = 0
    if dr.exists():
        try:
            n_delist = len(pd.read_csv(dr))
        except Exception:  # noqa: BLE001
            pass
    tail = [f"\n\nScanned {len(live)} live funds."]
    if wb_summary:
        tail.append(f"Universe workbook attached: {wb_summary['rows']} funds "
                    f"({wb_summary['live']} live), "
                    f"{wb_summary['with_live_nav']} with a live NAV, "
                    f"{wb_summary['with_irr']} with a forward IRR, "
                    f"{wb_summary.get('catalysts', 0)} catalysts (30d).")
    if wb_error:
        tail.append(f"Universe workbook FAILED to build: {wb_error}")
    if n_delist:
        tail.append(f"{n_delist} fund(s) awaiting delisting review "
                    "(delist_review.csv in the repo).")
    tail.append("Every verdict above is recorded in the paper-trade ledger "
                "at signal time, whether or not you act on it.")
    body.append("\n".join(tail))

    # which open is this brief for? The scheduler runs 06:20 UTC (pre-LSE)
    # and 23:10 UTC (pre-ASX); anything late still labels itself correctly.
    hour = datetime.now(timezone.utc).hour
    label = "pre-ASX open" if (hour >= 15 or hour < 3) else "pre-LSE open"
    subject = (f"{label}: {len(opps)} actionable, {len(watch)} watch"
               if len(verdicts) else f"{label}: no new ideas")

    from . import brief
    from .notify import notify
    html = None
    try:
        html = brief.render_html(
            label, datetime.now(timezone.utc).date().isoformat(),
            evaluated=len(live), opps=opps, disl=disl, irr_led=irr_led,
            cat_led=cat_led,
            z_threshold=float(params["opportunity"]["z_threshold"]),
            min_irr=min_irr, wb_summary=wb_summary, wb_error=wb_error,
            n_delist=n_delist, n_watch=len(watch))
    except Exception as exc:  # noqa: BLE001
        # the text body is canonical; a rendering bug must cost the styling,
        # never the brief
        print(f"HTML brief rendering failed ({exc}); sending text only")
    sent = notify(subject, "\n".join(body),
                  priority="critical" if len(opps) else "normal",
                  attachments=[str(wb_path)] if wb_path else None,
                  html=html)

    summary = {"generated_at": datetime.now(timezone.utc)
               .isoformat(timespec="seconds"),
               "brief": label,
               "evaluated": int(len(live)), "opportunities": int(len(opps)),
               "watch": int(len(watch)), "ledger_rows": rows,
               "min_irr_central": min_irr,
               "emailed": bool(sent),
               "workbook": None if wb_path is None else str(wb_path),
               "workbook_error": wb_error}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/ideas.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    return 0


def nightly(markets: list[str]) -> int:
    params = _params()
    today = datetime.now(timezone.utc).date()
    tables = []
    all_anns: list[dict] = []
    notes = {"date": today.isoformat(), "markets": {}}
    ysess = prices.session()

    if "au" in markets:
        # A missing panel must not raise before anything has been attempted.
        # The registry is the universe; the panel only ADDS history, so an
        # absent panel costs factor models and z-scores, not the whole run -
        # the same rule the index sweep already follows.
        ap = Path("data/au_processed/au_monthly_panel.parquet")
        if not ap.exists():
            notes["markets"]["au"] = "panel_missing_history_unavailable"
            panel = pd.DataFrame(columns=["security_id", "obs_month", "sector",
                                          "nta_total_return", "nta_derived",
                                          "share_price"])
        else:
            panel = pd.read_parquet(ap)
        au_reg = _registry_for("AU")
        # every live fund is priced, not just the ones the aggregator covered
        codes = set(panel["security_id"].astype(str)
                    .str.replace("ASX:", "", regex=False)) | \
            set(au_reg["security_id"].astype(str).str.replace("ASX:", "", regex=False))
        tier0 = harvest_nav.harvest_au(codes)
        mf = prices.monthly_factor_returns(ysess, "AU")
        df = prices.daily_factor_returns(ysess, "AU")
        # only ask for a quote where the code is unambiguously this
        # security's; a superseded or conflicted holder is never fetched
        syms = identity.priceable_symbols(au_reg, ".AX")
        for c in sorted(codes):
            syms.setdefault(f"ASX:{c}", f"{c}.AX")
        blocked = set(au_reg.loc[~au_reg["identity_ok"], "security_id"].astype(str))
        syms = {k: v for k, v in syms.items() if k not in blocked}
        notes["markets"]["au_identity_blocked"] = len(blocked)
        live_px = prices.latest_prices(ysess, syms)
        notes["markets"]["au"] = {"tier0_navs": int(len(tier0)),
                                  "factors_monthly": mf is not None,
                                  "factors_daily": df is not None,
                                  "live_prices": int(len(live_px))}
        t = nta_live.build_table(
            panel, "AU", ret_col="nta_total_return", nav_col="nta_derived",
            price_col="share_price", params=params, tier0=tier0,
            market_factors=mf, daily_factors=df, live_prices=live_px,
            registry=au_reg, own_nav_history=_own_nav_history("AU"))
        tables.append(t)
        if len(tier0):
            Path("data/nta_live").mkdir(parents=True, exist_ok=True)
            tier0.to_csv("data/nta_live/au_tier0_latest.csv", index=False)

    if "uk" in markets:
        p = Path("data/processed/monthly_panel.parquet")
        if p.exists():
            panel = pd.read_parquet(p)
            nav_col = next(c for c in ("nav", "nav_per_share") if c in panel.columns)
            price_col = next(c for c in ("price", "share_price") if c in panel.columns)
            mf = prices.monthly_factor_returns(ysess, "UK")
            df = prices.daily_factor_returns(ysess, "UK")
            live_px = pd.DataFrame()
            tmap = None
            try:
                import yaml as _yaml  # uk config for the SEDOL->TIDM map
                from uk_cef.data_sources.investegate import build_ticker_map
                ucfg = _yaml.safe_load(Path("config/default.yaml").read_text())
                tmap = build_ticker_map(ucfg)
                tmap = tmap[tmap["ticker"].notna()]
                # The live universe is the REGISTRY, not "whoever appeared in
                # the aggregator's last monthly file". Keying on the panel
                # meant the 108 funds the AIC never priced were never even
                # sent to the price feed, so they could not have had a
                # discount however good the NAV harvest got.
                uk_reg = _registry_for("UK")
                blocked_uk = set(
                    uk_reg.loc[~uk_reg["identity_ok"], "security_id"].astype(str))
                notes["markets"]["uk_identity_blocked"] = len(blocked_uk)
                alive = set(uk_reg["security_id"].astype(str)) - blocked_uk
                syms = {r.security_id: f"{r.ticker}.L"
                        for r in tmap.itertuples(index=False)
                        if not alive or r.security_id in alive}
                # every live fund with a verified ticker gets priced, including
                # those the MIR ticker map never covered
                tp = Path("config/resolved_tickers.csv")
                if tp.exists():
                    rt = pd.read_csv(tp)
                    rt = rt[(rt["status"] == "verified") & rt["ticker"].notna()]
                    for r in rt.itertuples(index=False):
                        if r.security_id in alive:
                            syms.setdefault(r.security_id,
                                            f"{str(r.ticker).upper()}.L")
                live_px = prices.latest_prices(ysess, syms)
            except Exception as exc:  # noqa: BLE001
                notes["markets"].setdefault("uk", {})
                notes["markets"]["uk_ticker_map_error"] = str(exc)

            # Tier 0 BEFORE the table so fresh published NAVs anchor it
            cache = Path("data/investegate_cache")
            uk_tier0 = None
            census = pd.DataFrame()
            if cache.exists():
                census = harvest_nav.uk_frequency_census(cache)
                Path("data/nta_live").mkdir(parents=True, exist_ok=True)
                census.to_csv("data/nta_live/uk_nav_frequency_census.csv", index=False)
                # funds the registry lists but never prices: their NAV
                # exists only in their own announcements
                extra = {}
                rp = Path("data/universe/registry.parquet")
                tp2 = Path("config/resolved_tickers.csv")
                if rp.exists() and tp2.exists():
                    rg = pd.read_parquet(rp)
                    rt = pd.read_csv(tp2)
                    rt = rt[rt["status"] == "verified"]
                    # EVERY live UK fund with a ticker - the registry gives
                    # identity, the fund's own announcements give the NAV
                    # every ALIVE fund is a NAV target - `live_stale_nav`
                    # most of all, since a stale NAV is precisely what a
                    # fresh harvest is meant to replace
                    need = rg[rg["status"].isin(liveness.LIVE_STATUSES)
                              & (rg["market"] == "UK")]
                    m = need.merge(rt[["security_id", "ticker"]],
                                   on="security_id", how="inner")
                    tmap = pd.concat([
                        tmap, m[["security_id", "ticker"]]], ignore_index=True
                    ).drop_duplicates("security_id", keep="last")
                    # priority: funds the registry never prices go first
                    ao = m[m["security_id"].isin(set(
                        rg.loc[rg["nav_route"] == "announcements_only",
                               "security_id"]))]
                    extra = dict(zip(ao["ticker"].astype(str).str.upper(),
                                     ao["security_id"]))
                    notes["markets"]["uk_nav_targets"] = int(len(m))
                    notes["markets"]["uk_registry_only_targets"] = len(extra)
                if tmap is not None and (len(census) or extra):
                    try:
                        uk_tier0, uk_anns = harvest_nav.harvest_uk(
                            tmap, census, extra_targets=extra)
                        all_anns.extend(uk_anns)
                        if len(uk_tier0):
                            uk_tier0.to_csv("data/nta_live/uk_tier0_latest.csv", index=False)
                    except Exception as exc:  # noqa: BLE001
                        notes["markets"]["uk_tier0_error"] = str(exc)

            aux_uk = None
            try:
                aux_uk = _uk_aux_discount_history()
                notes["markets"]["uk_aux_z_history_funds"] = (
                    int(aux_uk["security_id"].nunique())
                    if aux_uk is not None else 0)
            except Exception as exc:  # noqa: BLE001
                notes["markets"]["uk_aux_z_history_error"] = str(exc)
            t = nta_live.build_table(
                panel, "UK", ret_col="nav_total_return", nav_col=nav_col,
                price_col=price_col, params=params, tier0=uk_tier0,
                market_factors=mf, daily_factors=df, live_prices=live_px,
                registry=_registry_for("UK"),
                own_nav_history=_own_nav_history("UK"),
                aux_discount_history=aux_uk)
            tables.append(t)
            if len(census):
                notes["markets"]["uk"] = {
                    "tier0_navs": int(len(uk_tier0)) if uk_tier0 is not None else 0,
                    "nav_publishers_found": int(len(census)),
                    "daily_weekly": int((census["nav_frequency"].isin(["daily", "weekly"])).sum())
                    if len(census) else 0}
        else:
            notes["markets"]["uk"] = "panel_missing_skipped"

    # ---- catalyst scan: the reason to read announcements beyond NAV ----
    cat_frames = []
    if all_anns:
        cat_frames.append(catalysts.scan_rows(all_anns))
    au_idx = "data/asx_ann_cache/asx1/lic_announcement_index.parquet"
    au_cat = catalysts.scan_au(au_idx)
    if len(au_cat):
        cat_frames.append(au_cat)
    cats = pd.concat([c for c in cat_frames if len(c)], ignore_index=True) \
        if any(len(c) for c in cat_frames) else pd.DataFrame()
    if len(cats):
        Path("outputs/live").mkdir(parents=True, exist_ok=True)
        cats.to_csv("outputs/live/catalysts_recent.csv", index=False)
    notes["catalysts"] = catalysts.summarise(cats)

    if not tables:
        print("no market tables built"); return 1
    out = pd.concat(tables, ignore_index=True)

    # A run over ONE market must not delete the other market's rows.
    # `nightly --markets au` rewrote latest.parquet with AU alone, so every
    # UK fund vanished from the live table until the next full run - a
    # single-market refresh silently emptying half the monitoring universe.
    # Rows for markets this run did not rebuild are carried forward with
    # their ORIGINAL updated_at, so their age is visible and nothing is
    # presented as fresher than it is.
    prev_f = Path("data/nta_live/latest.parquet")
    carried = 0
    if prev_f.exists():
        try:
            prev = pd.read_parquet(prev_f)
            built = set(out["market"].unique())
            keep = prev[~prev["market"].isin(built)]
            if len(keep):
                out = pd.concat([out, keep], ignore_index=True)
                carried = int(len(keep))
                print(f"carried forward {carried} row(s) for markets not "
                      f"rebuilt this run: {sorted(set(keep['market']))}")
        except Exception as exc:  # noqa: BLE001
            print(f"could not read previous live table ({exc}); "
                  "writing this run's markets only")
    notes["carried_forward_rows"] = carried
    Path("data/nta_live").mkdir(parents=True, exist_ok=True)
    out.to_parquet("data/nta_live/latest.parquet", index=False)
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/live/nta_live_latest.csv", index=False)
    notes["snapshot"] = _snapshot_s3(Path("data/nta_live/latest.parquet"),
                                     f"nta_live/{today.isoformat()}.parquet")

    # ---- Phase 1 acceptance metrics ----
    basis_counts = out["basis"].value_counts(dropna=False).to_dict()
    # rows with NO NAV from any source are now kept (so their price and the
    # gap are both visible); the basis-coverage share is over the rows that
    # HAVE a NAV, which is what it always meant
    with_nav = out[out["basis"].notna()]
    covered = float((with_nav["basis"] <= 3).mean()) if len(with_nav) else 0.0
    sig_ok = out["sigma_1m"].notna().mean()
    accept = {
        "rows": int(len(out)),
        "rows_with_nav": int(len(with_nav)),
        "rows_without_nav": int(len(out) - len(with_nav)),
        "basis_counts": {str(k): int(v) for k, v in basis_counts.items()},
        "share_basis_le3_labelled": round(covered, 4),
        "share_with_sigma": round(float(sig_ok), 4),
        "alert_eligible": int(out["alert_eligible"].sum()),
        "z_extremes": out.loc[out["z_adj"].notna()]
                         .nsmallest(10, "z_adj")[["security_id", "z_adj",
                                                  "discount_est", "staleness_days"]]
                         .to_dict("records"),
        **notes,
    }
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/phase1_nightly.json").write_text(
        json.dumps(accept, indent=2, default=str))

    # NOT emailed: the two daily pre-open briefs are the only scheduled
    # emails (owner instruction 2026-09-01, config/CHANGELOG.md). The
    # always-sent briefs now carry the liveness signal; this summary goes
    # to the log and the acceptance report instead.
    top = out[out["alert_eligible"] & out["z_adj"].notna()].nsmallest(5, "z_adj")
    lines = [f"  {r.security_id:>16}  z={r.z_adj:+.2f}  disc={r.discount_est:+.1%}"
             f"  basis={r.basis} stale={r.staleness_days}d"
             for r in top.itertuples(index=False)]
    cat_lines = []
    if len(cats):
        top = cats.merge(out[["security_id", "name", "z_adj", "discount_est"]],
                         on="security_id", how="left").head(12)
        for r in top.itertuples(index=False):
            z = "" if pd.isna(getattr(r, "z_adj", None)) else f"  z={r.z_adj:+.2f}"
            # `x or y` is wrong for a possibly-NaN name: NaN is TRUTHY, so a
            # left-joined miss returns the float and slicing it raises. This
            # crashed the nightly AFTER a 24-minute harvest, losing the run.
            nm = getattr(r, "name", None)
            label = str(nm) if isinstance(nm, str) and nm.strip() else str(r.security_id)
            cat_lines.append(f"  {r.date}  {label[:34]:<34} "
                             f"{r.catalyst_class}{z}")
    print(f"nightly OK - {len(out)} funds, "
          f"{int(out['alert_eligible'].sum())} eligible"
          + (f", {catalysts.summarise(cats)['catalysts']} catalysts" if len(cats) else "")
          + f"\nBasis counts: {basis_counts}\nSnapshot: {notes['snapshot']}\n"
          f"Deepest eligible dislocations:\n" + "\n".join(lines)
          + ("\n\nCatalysts announced (last 30 days):\n" + "\n".join(cat_lines)
             if cat_lines else "\n\nNo catalysts in the last 30 days."))
    print(json.dumps({k: accept[k] for k in
                      ("rows", "basis_counts", "share_with_sigma",
                       "alert_eligible", "snapshot")}, default=str))
    # A harvester that CRASHED is not a market that published nothing. The
    # UK Tier 0 error sat in this report as a one-line note for days while
    # the NAV count read 0, which is indistinguishable from silence unless
    # the run itself goes red. Outputs are already written above, so other
    # markets keep their results; only the exit status changes.
    errs = {k: v for k, v in notes["markets"].items() if k.endswith("_error")}
    if errs:
        for k, v in errs.items():
            print(f"HARVEST FAILURE {k}: {v}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("nightly")
    n.add_argument("--markets", default="au,uk")
    sub.add_parser("universe")
    rt = sub.add_parser("resolve-tickers")
    rt.add_argument("--budget", type=int, default=400)
    us = sub.add_parser("universe-sheet")
    us.add_argument("--email", action="store_true",
                    help="also email the workbook (scheduled runs never do; "
                         "the two daily pre-open briefs attach it instead)")
    sub.add_parser("ideas")
    ud = sub.add_parser("uk-daily", help="daily UK NAV/price/discount panel")
    ud.add_argument("--stages", default="nav,prices,discount",
                    help="comma list of nav,prices,discount")
    ud.add_argument("--deadline-min", type=float, default=240.0,
                    help="wall-clock budget per stage")
    ud.add_argument("--full-prices", action="store_true",
                    help="refetch every fund's whole price history")
    ud.add_argument("--exclude-vct", action="store_true")
    ud.add_argument("--reparse-unparsed", action="store_true",
                    help="re-read announcements no parser has yet read a NAV "
                         "from, using today's rules (S3 text, no new fetches)")
    ud.add_argument("--shard", type=int, default=int(os.environ.get("SHARD_INDEX", "0")))
    ud.add_argument("--shards", type=int, default=int(os.environ.get("SHARD_COUNT", "1")))
    ig = sub.add_parser("uk-index-gap",
                        help="index live UK funds whose announcements were never listed")
    ig.add_argument("--budget-minutes", type=float, default=300.0)
    ig.add_argument("--include-vct", action="store_true")
    ig.add_argument("--limit", type=int, default=0)
    ig.add_argument("--shard", type=int, default=int(os.environ.get("SHARD_INDEX", "0")))
    ig.add_argument("--shards", type=int, default=int(os.environ.get("SHARD_COUNT", "1")))
    args = ap.parse_args()
    if args.cmd == "universe":
        return build_universe()
    if args.cmd == "resolve-tickers":
        return resolve_tickers(args.budget)
    if args.cmd == "universe-sheet":
        return universe_sheet(email=args.email)
    if args.cmd == "ideas":
        return ideas()
    if args.cmd == "nightly":
        return nightly([m.strip() for m in args.markets.split(",") if m.strip()])
    if args.cmd == "uk-index-gap":
        from . import uk_index_gap
        return uk_index_gap.run(budget_minutes=args.budget_minutes,
                                shard=args.shard, shards=args.shards,
                                include_vct=args.include_vct, limit=args.limit)
    if args.cmd == "uk-daily":
        from . import uk_daily
        return uk_daily.run(
            stages=tuple(s.strip() for s in args.stages.split(",") if s.strip()),
            deadline_min=args.deadline_min, full_prices=args.full_prices,
            include_vct=not args.exclude_vct,
            shard=args.shard, shards=args.shards,
            reparse_unparsed=args.reparse_unparsed)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
