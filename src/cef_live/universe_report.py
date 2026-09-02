"""The universe spreadsheet: one row per fund we track, emailed from CI.

Columns are deliberately explicit about provenance, because the whole
point of the rebuild is that "no value" and "a modelled value" are
different things:

  last_price / price_date / price_source   market price, as fetched
  last_published_nav / nav_date / nav_source
                                           a REAL published figure
  live_nav_estimate / nav_basis / staleness_days / est_error
                                           our model's number, never
                                           stored in the published column
  discount_now, discount_median_5y, discount_mean_36m, z_score
  forward_irr + the three sensitivities

Funds we track but cannot value keep their row with the reason visible
(nav_route, status), rather than being dropped from the sheet.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

BASIS_LABEL = {0: "0 - published NAV (issuer announcement)",
               1: "1 - factor roll-forward from last NAV",
               2: "2 - holdings-based",
               3: "3 - stale (last published carried)"}


def build(registry: pd.DataFrame, live: pd.DataFrame,
          irr: pd.DataFrame | None, hist: pd.DataFrame | None,
          out_path: Path, cats: pd.DataFrame | None = None) -> tuple[Path, dict]:
    df = registry.merge(live, on="security_id", how="left",
                        suffixes=("", "_live"))
    if cats is not None and len(cats):
        latest = (cats.sort_values(["weight", "date"], ascending=[False, False])
                      .groupby("security_id").head(1)
                      .rename(columns={"catalyst_class": "catalyst_latest",
                                       "date": "catalyst_date",
                                       "headline": "catalyst_headline"}))
        df = df.merge(latest[["security_id", "catalyst_latest", "catalyst_date",
                              "catalyst_headline"]], on="security_id", how="left")
        counts = cats.groupby("security_id").size().rename("catalysts_30d").reset_index()
        df = df.merge(counts, on="security_id", how="left")
    if irr is not None and len(irr):
        df = df.merge(irr, on="security_id", how="left")

    # historical discount stats from the monthly panel
    if hist is not None and len(hist) and "discount" in hist.columns:
        h = hist.sort_values("obs_month")
        g = h.groupby("security_id")["discount"]
        stats = pd.DataFrame({
            "discount_median_5y": h.groupby("security_id").tail(60)
                                   .groupby("security_id")["discount"].median(),
            "discount_mean_36m": h.groupby("security_id").tail(36)
                                  .groupby("security_id")["discount"].mean(),
            "discount_min_all": g.min(), "discount_max_all": g.max(),
            "discount_months": g.count(),
        }).reset_index()
        df = df.merge(stats, on="security_id", how="left")

    df["nav_basis_label"] = df["basis"].map(BASIS_LABEL)
    cols = [
        ("security_id", "Security ID"), ("ticker", "Ticker"),
        ("name", "Fund"), ("market", "Market"), ("sector", "Sector"),
        ("status", "Listing status"), ("domicile", "Domicile"),
        ("is_vct", "VCT"), ("offshore", "Offshore"),
        ("first_seen", "First seen"), ("last_seen", "Last seen"),
        ("price", "Last price"), ("price_asof", "Price source/date"),
        ("nav_anchor", "Last published NAV"), ("anchor_date", "NAV date"),
        ("anchor_source", "NAV source"),
        ("nta_est", "Live NAV estimate"), ("nav_basis_label", "NAV basis"),
        ("staleness_days", "Staleness (days)"), ("est_error", "Est. error"),
        ("discount_est", "Discount now"),
        ("discount_median_5y", "Discount median 5y"),
        ("discount_mean_36m", "Discount mean 36m"),
        ("disc_sigma_36m", "Discount stdev 36m"),
        ("z_adj", "Z-score (own history)"),
        ("discount_min_all", "Discount min"), ("discount_max_all", "Discount max"),
        ("discount_months", "Discount months"),
        ("irr_central", "Forward IRR (central)"),
        ("irr_discount_only", "IRR from discount normalisation"),
        ("g_used", "NAV growth used (p.a.)"),
        ("g_source", "NAV growth source"),
        ("irr_own_median", "IRR @ own median disc"),
        ("irr_wider_5pp", "IRR @ 5pp wider"),
        ("irr_zero_discount", "IRR @ zero discount"),
        ("g_used", "NAV growth used"),
        ("terminal_discount_own", "Terminal discount"),
        ("dist_yield_used", "Distribution yield"),
        ("catalyst_latest", "Latest catalyst"), ("catalyst_date", "Catalyst date"),
        ("catalyst_headline", "Catalyst headline"), ("catalysts_30d", "Catalysts (30d)"),
        ("nav_route", "NAV route"), ("source_priced_months", "Months priced by registry"),
        ("alert_eligible", "Alert eligible"), ("updated_at", "Updated"),
    ]
    present = [(c, lbl) for c, lbl in cols if c in df.columns]
    sheet = df[[c for c, _ in present]].rename(columns=dict(present))
    sheet = sheet.sort_values(["Market", "Fund"], na_position="last")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as xl:
        sheet.to_excel(xl, sheet_name="Universe", index=False)
        live_only = sheet[sheet["Listing status"].isin(("live", "live_stale_nav"))] \
            if "Listing status" in sheet.columns else sheet
        live_only.to_excel(xl, sheet_name="Live only", index=False)
        if cats is not None and len(cats):
            named = cats.merge(
                registry[["security_id", "name", "market", "sector"]],
                on="security_id", how="left")
            named[["date", "name", "market", "sector", "catalyst_class",
                   "headline", "url"]].rename(columns={
                       "date": "Date", "name": "Fund", "market": "Market",
                       "sector": "Sector", "catalyst_class": "Catalyst",
                       "headline": "Headline", "url": "Link"}).to_excel(
                xl, sheet_name="Catalysts", index=False)
        if "Z-score (own history)" in sheet.columns:
            disl = live_only[live_only["Z-score (own history)"].notna()] \
                .nsmallest(40, "Z-score (own history)")
            disl.to_excel(xl, sheet_name="Most dislocated", index=False)
        # freeze headers + sane widths
        for ws in xl.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col[:60] if c.value), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(38, max(11, width + 2))

    summary = {
        "rows": int(len(sheet)),
        "live": int(sheet["Listing status"].isin(("live", "live_stale_nav")).sum())
                if "Listing status" in sheet.columns else None,
        "with_price": int(sheet["Last price"].notna().sum())
                      if "Last price" in sheet.columns else 0,
        "with_live_nav": int(sheet["Live NAV estimate"].notna().sum())
                         if "Live NAV estimate" in sheet.columns else 0,
        "with_irr": int(sheet["Forward IRR (central)"].notna().sum())
                    if "Forward IRR (central)" in sheet.columns else 0,
        "catalysts": int(len(cats)) if cats is not None else 0,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    return out_path, summary
