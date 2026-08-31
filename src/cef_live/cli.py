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
    cand = reg[reg["status"] == liveness.STATUS_CANDIDATE]
    summary = {
        "delist_candidates": int(len(cand)),
        "vehicles": int(len(reg)),
        "by_market": reg.groupby("market").size().to_dict(),
        "live": int((reg["status"] == "live").sum()),
        "live_stale_nav": int((reg["status"] == "live_stale_nav").sum()),
        "status_before_evidence": before,
        "evidence_rows": int(len(ev)),
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



def _registry_for(market: str) -> pd.DataFrame:
    """Live funds for one market - the universe, from identity alone."""
    rp = Path("data/universe/registry.parquet")
    if not rp.exists():
        return pd.DataFrame()
    reg = pd.read_parquet(rp)
    return reg[(reg["market"] == market)
               & (reg["status"].isin(["live", "delist_candidate"]))].copy()


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
            frames.append(pd.DataFrame({
                "ticker": h["ticker"].astype(str).str.upper(),
                "nav_date": h.get("nav_date", h.get("ann_date")),
                "nav_value": pd.to_numeric(h["nav_cum_pence"], errors="coerce") / 100.0,
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
            ["security_id", "nav_date", "nav_value"]]

    for f in sorted(Path("data/asx_extract").glob("facts_det_*.parquet")):
        try:
            h = pd.read_parquet(f)
        except Exception:  # noqa: BLE001
            continue
        h = h[h["section"] == "nav_observations"]
        if not len(h):
            continue
        frames.append(pd.DataFrame({
            "security_id": "ASX:" + h["ticker"].astype(str).str.upper(),
            "nav_date": h["valuation_date"],
            "nav_value": pd.to_numeric(h["nav_per_share"], errors="coerce"),
        }))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).dropna(subset=["nav_value"])


def resolve_tickers(budget: int = 400) -> int:
    """Resolve Investegate/Yahoo tickers for live registry funds with none."""
    reg = pd.read_parquet("data/universe/registry.parquet")
    cfg_uk = None
    p = Path("config/default.yaml")
    if p.exists():
        cfg_uk = yaml.safe_load(p.read_text())
    tickers.seed_known(reg, cfg_uk)      # free: already-verified identifiers
    out = tickers.resolve(reg, budget=budget)
    live_uk = reg[(reg["status"] == "live") & (reg["market"] == "UK")]
    got = out[out["status"] == "verified"]
    # the cohort that actually matters: funds with NO other NAV source
    ao = reg[(reg["status"] == "live") & (reg["market"] == "UK")
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
    hist = None
    hp = Path("data/processed/monthly_panel.parquet")
    if hp.exists():
        hist = pd.read_parquet(hp)
        if "discount" not in hist.columns and {"share_price", "nav_per_share"} <= set(hist.columns):
            hist["discount"] = hist["share_price"] / hist["nav_per_share"] - 1.0
    irr = None
    if hist is not None:
        try:
            irr = forward_irr.build(live, hist, _params())
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

    hurdle_base = None
    hp = Path("data/processed/monthly_panel.parquet")
    if hp.exists():
        hurdle_base = opportunities.universe_trailing_tr(pd.read_parquet(hp))

    # a fund that may no longer exist must not generate an idea
    rp = Path("data/universe/registry.parquet")
    if rp.exists():
        reg = pd.read_parquet(rp)
        keep = set(reg.loc[reg["status"] == "live", "security_id"])
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
    summary = {"evaluated": int(len(live)), "opportunities": int(len(opps)),
               "watch": int(len(watch)), "ledger_rows": rows,
               "hurdle_base": hurdle_base,
               "hurdle": None if hurdle_base is None else round(
                   hurdle_base + params["opportunity"]["irr_hurdle_excess_pp"] / 100.0, 4)}
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
        body.append(f"\n\nHurdle: trailing universe return "
                    f"{'n/a' if hurdle_base is None else f'{hurdle_base:.1%}'} + "
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
        panel = pd.read_parquet("data/au_processed/au_monthly_panel.parquet")
        au_reg = _registry_for("AU")
        # every live fund is priced, not just the ones the aggregator covered
        codes = set(panel["security_id"].str.replace("ASX:", "", regex=False)) | \
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
                    need = rg[(rg["status"] == "live") & (rg["market"] == "UK")]
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
    Path("data/nta_live").mkdir(parents=True, exist_ok=True)
    out.to_parquet("data/nta_live/latest.parquet", index=False)
    Path("outputs/live").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/live/nta_live_latest.csv", index=False)
    notes["snapshot"] = _snapshot_s3(Path("data/nta_live/latest.parquet"),
                                     f"nta_live/{today.isoformat()}.parquet")

    # ---- Phase 1 acceptance metrics ----
    basis_counts = out["basis"].value_counts().to_dict()
    covered = float((out["basis"] <= 3).mean())
    sig_ok = out["sigma_1m"].notna().mean()
    accept = {
        "rows": int(len(out)),
        "basis_counts": {str(k): int(v) for k, v in sorted(basis_counts.items())},
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
