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
import re
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from . import (catalysts, forward_irr, harvest_nav, liveness, nta_live,
               opportunities, prices, tickers, universe, universe_report)


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
NAV_HEAD = re.compile(
    r"net tangible asset|\bNTA\b|net asset value|\bNAV\b|fund update", re.I)


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
        from .notify import notify
        notify(f"{len(cand)} fund(s) awaiting delisting review",
               "These funds have stopped appearing in their registry source. "
               "They are already excluded from idea generation, and will be "
               "treated as delisted after the review grace period.\n\n"
               "To KEEP tracking one, add it to universe/manual.yaml — manual "
               "entries are never flagged.\n\n" + "\n".join(lines)
               + "\n\nFull list attached; nothing is ever deleted from the "
                 "registry, so the history stays intact either way.",
               attachments=["outputs/live/delist_review.csv"])
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


def _registry_for(market: str) -> pd.DataFrame:
    """Live funds for one market - the universe, from identity alone."""
    rp = Path("data/universe/registry.parquet")
    if not rp.exists():
        return pd.DataFrame()
    reg = pd.read_parquet(rp)
    # `live_stale_nav` is alive - it means "trading, NAV not fresh yet".
    # Once liveness is persisted, omitting it here would drop 100+ funds
    # out of the priced universe for a data-coverage reason.
    return reg[(reg["market"] == market)
               & (reg["status"].isin(liveness.TRACKED_STATUSES))].copy()


def _own_nav_history(market: str) -> pd.DataFrame:
    """NAV observations WE extracted, from the funds' own announcements.

    UK: the Investegate NAV archive. AU: the deterministic pass over the
    archived ASX PDFs. This is what replaces the aggregator as the source of
    value - the aggregator only ever says who exists.
    """
    frames = []
    if market == "UK":
        for f in sorted(Path("data").glob("uk_nav_history*.parquet")):
            try:
                h = pd.read_parquet(f)
            except Exception:  # noqa: BLE001
                continue
            h = h[h.get("status").eq("parsed")] if "status" in h.columns else h
            if not {"ticker", "nav_cum_pence"} <= set(h.columns):
                continue
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


def universe_sheet() -> int:
    """Build the universe spreadsheet and email it."""
    reg = pd.read_parquet("data/universe/registry.parquet")
    live = pd.read_parquet("data/nta_live/latest.parquet")
    panels = panels_by_market()
    # the sheet's historical discount stats are per market; concatenating
    # keeps every security joined to its OWN market's history
    hist = pd.concat(panels.values(), ignore_index=True) if panels else None
    irr = None
    if panels:
        try:
            irr = forward_irr.build_by_market(live, panels, _params())
            Path("data/forward_irr").mkdir(parents=True, exist_ok=True)
            irr.to_parquet("data/forward_irr/latest.parquet", index=False)
        except Exception as exc:  # noqa: BLE001
            print(f"forward IRR failed ({exc}); sheet will omit it")

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

    from .notify import notify
    body = (f"CEF universe spreadsheet, {stamp}.\n\n"
            f"Funds tracked: {summary['rows']} ({summary['live']} live)\n"
            f"With a market price: {summary['with_price']}\n"
            f"With a live NAV estimate: {summary['with_live_nav']}\n"
            f"With a forward IRR: {summary['with_irr']}\n"
            f"Catalysts announced (30d): {summary.get('catalysts', 0)}\n\n"
            "Sheets: Universe (all), Live only, Most dislocated (lowest z), "
            "Catalysts.\n"
            "Published NAVs and modelled estimates are separate columns; a "
            "blank means we hold no value, never a filled-in one.")
    sent = notify(f"universe spreadsheet {stamp}", body, attachments=[str(path)])
    print("emailed:" , sent)
    Path("reports/build/universe_sheet.json").write_text(
        json.dumps({**summary, "emailed": sent, "file": str(path)}, indent=2, default=str))
    return 0


def ideas() -> int:
    """Pre-open idea scan: catalysts + dislocation + return, emailed.

    Reads what the nightly produced (live table, catalysts, IRR), applies
    the three pre-specified gates, writes every verdict to the point-in-time
    ledger, and emails only what clears. Run once per market before its
    open - the horizon is months to years, so nothing here needs to repeat
    during a session.
    """
    params = _params()
    live = pd.read_parquet("data/nta_live/latest.parquet")
    cats = None
    cp = Path("outputs/live/catalysts_recent.csv")
    if cp.exists():
        cats = pd.read_csv(cp)
    irr = None
    ip = Path("data/forward_irr/latest.parquet")
    if ip.exists():
        irr = pd.read_parquet(ip)

    # one hurdle PER MARKET, from that market's own panel
    hurdle_base = {m: opportunities.universe_trailing_tr(pan)
                   for m, pan in panels_by_market().items()}
    hurdle_base = {m: v for m, v in hurdle_base.items() if v is not None} or None

    # a fund that may no longer exist must not generate an idea
    rp = Path("data/universe/registry.parquet")
    if rp.exists():
        reg = pd.read_parquet(rp)
        keep = set(reg.loc[reg["status"].isin(liveness.LIVE_STATUSES),
                           "security_id"])
        before = len(live)
        live = live[live["security_id"].isin(keep)]
        print(f"universe filter: {before} -> {len(live)} live funds evaluated")

    verdicts = opportunities.evaluate(live, cats, irr, params, hurdle_base)
    # ledger write and signal emission are one step: the email is rendered
    # FROM the ledger rows, so an idea cannot be sent without being recorded
    rows = opportunities.append_ledger(verdicts, "data/ledger/signals.parquet")
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    if len(verdicts):
        verdicts.to_csv("outputs/live/ideas_latest.csv", index=False)

    opps = verdicts[verdicts["verdict"] == "OPPORTUNITY"] if len(verdicts) else verdicts
    watch = verdicts[verdicts["verdict"] == "WATCH"] if len(verdicts) else verdicts
    excess = params["opportunity"]["irr_hurdle_excess_pp"] / 100.0
    summary = {"evaluated": int(len(live)), "opportunities": int(len(opps)),
               "watch": int(len(watch)), "ledger_rows": rows,
               "hurdle_base_by_market": hurdle_base,
               "hurdle_by_market": None if not hurdle_base else
               {m: round(v + excess, 4) for m, v in hurdle_base.items()}}
    Path("reports/build").mkdir(parents=True, exist_ok=True)
    Path("reports/build/ideas.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))

    def _fmt(df, head):
        out = [head]
        for r in df.itertuples(index=False):
            out.append(f"\n  {r.name} ({r.market})")
            out.append(f"    discount {r.discount_est:+.1%}  z {r.z_adj:+.2f}  "
                       f"IRR {('n/a' if r.irr_central is None else f'{r.irr_central:+.1%}')}"
                       f"  vs hurdle {('n/a' if r.hurdle is None else f'{r.hurdle:+.1%}')}")
            if r.catalyst_class:
                out.append(f"    catalyst: {r.catalyst_class} ({r.catalyst_date}) "
                           f"- {(r.catalyst_headline or '')[:80]}")
            gates = [g for g, ok in (("dislocation", r.gate1_dislocation),
                                     ("catalyst", r.gate2_catalyst),
                                     ("return", r.gate3_return)) if ok]
            out.append(f"    gates passed: {', '.join(gates)}")
        return "\n".join(out)

    from .notify import notify
    if len(opps) or len(watch):
        body = []
        if len(opps):
            body.append(_fmt(opps, f"{len(opps)} OPPORTUNITY - all three gates:"))
        if len(watch):
            body.append(_fmt(watch.head(12), f"\n{len(watch)} WATCH - two of three:"))
        hb = ("n/a" if not hurdle_base else
              ", ".join(f"{m} {v:.1%}" for m, v in sorted(hurdle_base.items())))
        body.append(f"\n\nHurdle: trailing universe return per market "
                    f"({hb}) + "
                    f"{params['opportunity']['irr_hurdle_excess_pp']:.0f}pp.")
        body.append("Every verdict above is recorded in the paper-trade ledger "
                    "at signal time, whether or not you act on it.")
        notify(f"{len(opps)} opportunity, {len(watch)} watch",
               "\n".join(body),
               priority="critical" if len(opps) else "normal")
    else:
        notify("no ideas today",
               f"Scanned {len(live)} funds. Nothing cleared two gates.\n"
               "Silence here means the scan ran and found nothing, not that "
               "it failed to run.", priority="heartbeat")
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
        syms = {f"ASX:{c}": f"{c}.AX" for c in sorted(codes)}
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
                alive = set(uk_reg["security_id"].astype(str))
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

            t = nta_live.build_table(
                panel, "UK", ret_col="nav_total_return", nav_col=nav_col,
                price_col=price_col, params=params, tier0=uk_tier0,
                market_factors=mf, daily_factors=df, live_prices=live_px,
                registry=_registry_for("UK"),
                own_nav_history=_own_nav_history("UK"))
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

    # daily heartbeat - silence must be distinguishable from failure
    from .notify import notify
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
    notify(f"nightly OK - {len(out)} funds, {int(out['alert_eligible'].sum())} eligible"
           + (f", {catalysts.summarise(cats)['catalysts']} catalysts" if len(cats) else ""),
           "Nightly live NTA table built.\n"
           f"Basis counts: {basis_counts}\nSnapshot: {notes['snapshot']}\n"
           f"Deepest eligible dislocations:\n" + "\n".join(lines)
           + ("\n\nCatalysts announced (last 30 days):\n" + "\n".join(cat_lines)
              if cat_lines else "\n\nNo catalysts in the last 30 days."),
           priority="heartbeat")
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
    sub.add_parser("universe-sheet")
    sub.add_parser("ideas")
    ud = sub.add_parser("uk-daily", help="daily UK NAV/price/discount panel")
    ud.add_argument("--stages", default="nav,prices,discount",
                    help="comma list of nav,prices,discount")
    ud.add_argument("--deadline-min", type=float, default=240.0,
                    help="wall-clock budget per stage")
    ud.add_argument("--full-prices", action="store_true",
                    help="refetch every fund's whole price history")
    ud.add_argument("--exclude-vct", action="store_true")
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
        return universe_sheet()
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
            shard=args.shard, shards=args.shards)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
