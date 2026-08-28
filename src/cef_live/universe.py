"""The universe registry: every listed vehicle, priced or not.

Rebuild principle (agreed 2026-08-28): the AIC and ASX files are used as a
REGISTRY - who exists, in what sector, from when to when, with what ISIN -
not as the source of market values. Evidence for the split: the July 2026
AIC MIR lists 282 funds but publishes price/NAV for only 137; Guernsey and
Jersey registered trusts are 50-of-56 name-only, and 90 GB-registered rows
are name-only too. Those funds exist, trade, and publish their own NAVs by
announcement - they were simply invisible to a pipeline that required the
aggregator to price them.

So this module keeps every row the registry lists and records, per fund:
identity, domicile, point-in-time span, delisting status, vehicle flags,
and whether the source ever priced it (``source_priced_months``). Nothing
is filtered here; downstream layers decide what they need, and the reason
a fund lacks values is always visible rather than implied by its absence.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# ISIN prefix -> where the vehicle is registered. London-listed trusts are
# routinely Guernsey/Jersey registered; that is a domicile fact, not a
# reason to exclude them.
OFFSHORE_PREFIXES = {"GG", "JE", "IM", "IE", "BM", "KY", "LU", "NL"}


def _domicile(isin: str | None) -> str | None:
    if not isin or not isinstance(isin, str) or len(isin) < 2:
        return None
    pre = isin[:2].upper()
    return pre if pre.isalpha() else None


def build_uk(mir: pd.DataFrame, entities: pd.DataFrame | None = None) -> pd.DataFrame:
    """Registry rows for the UK market from ALL parsed MIR rows.

    mir: output of uk_cef.panel.parse_all_mir - one row per fund-month as
    published, including the name-only rows the AIC does not price.
    """
    if mir.empty:
        return pd.DataFrame()
    df = mir.copy()
    df["priced"] = df["price"].notna() & df["nav"].notna()

    agg = df.groupby("security_id").agg(
        name=("company_name", "last"),
        sector=("sector", "last"),
        share_type=("share_type", "last"),
        isin=("code", "last"),
        currency=("currency", "last"),
        first_seen=("obs_month", "min"),
        last_seen=("obs_month", "max"),
        months_listed=("obs_month", "nunique"),
        source_priced_months=("priced", "sum"),
    ).reset_index()
    agg["market"] = "UK"
    agg["source_priced_months"] = agg["source_priced_months"].astype(int)
    return agg


def build_au(panel: pd.DataFrame) -> pd.DataFrame:
    """Registry rows for the AU market from the ASX monthly-report panel."""
    if panel.empty:
        return pd.DataFrame()
    df = panel.copy()
    price_col = "share_price" if "share_price" in df.columns else None
    nav_col = "nta_derived" if "nta_derived" in df.columns else None
    df["priced"] = (df[price_col].notna() & df[nav_col].notna()) \
        if price_col and nav_col else False

    agg = df.groupby("security_id").agg(
        name=("company_name", "last"),
        sector=("sector", "last") if "sector" in df.columns else ("security_id", "last"),
        first_seen=("obs_month", "min"),
        last_seen=("obs_month", "max"),
        months_listed=("obs_month", "nunique"),
        source_priced_months=("priced", "sum"),
    ).reset_index()
    agg["market"] = "AU"
    agg["share_type"] = "Ordinary"
    agg["isin"] = None
    agg["currency"] = "AUD"
    agg["source_priced_months"] = agg["source_priced_months"].astype(int)
    return agg


def finalise(frames: list[pd.DataFrame], as_of: str | None = None) -> pd.DataFrame:
    """Combine market registries and derive status + vehicle flags."""
    reg = pd.concat([f for f in frames if f is not None and len(f)], ignore_index=True)
    reg["domicile"] = reg["isin"].map(_domicile)
    reg["offshore"] = reg["domicile"].isin(OFFSHORE_PREFIXES)

    sec = reg["sector"].fillna("").str.lower()
    reg["is_vct"] = sec.str.contains("vct")
    reg["is_split"] = sec.str.contains("split capital")
    reg["is_ordinary"] = reg["share_type"].fillna("").str.lower().str.contains("ordinary")

    # a fund is live if the registry still listed it in the latest month that
    # market published; anything older is delisted as at its last_seen
    reg["status"] = "delisted"
    for mkt, g in reg.groupby("market"):
        latest = as_of or g["last_seen"].max()
        reg.loc[g.index, "market_latest_month"] = latest
        reg.loc[g[g["last_seen"] >= latest].index, "status"] = "live"

    reg["source_prices_it"] = reg["source_priced_months"] > 0
    # what we expect to carry this fund's NAV: the registry, or its own
    # announcements (the offshore/unpriced cohort the rebuild exists for)
    reg["nav_route"] = "registry"
    reg.loc[~reg["source_prices_it"], "nav_route"] = "announcements_only"
    return reg.sort_values(["market", "name"]).reset_index(drop=True)


def build(cfg_uk: dict | None = None) -> pd.DataFrame:
    """Build and persist the combined registry."""
    frames = []
    if cfg_uk is not None:
        from uk_cef.entities import EntityRegistry
        from uk_cef.panel import parse_all_corporate_activity, parse_all_mir

        raw = Path(cfg_uk["download"]["raw_dir"])
        mir = parse_all_mir(raw)
        if not mir.empty:
            registry = EntityRegistry(cfg_uk["paths"].get("entity_overrides"))
            registry.load_name_changes(parse_all_corporate_activity(raw))
            mir = mir.sort_values(["obs_month", "company_name"])
            mir["security_id"] = [
                registry.resolve(n, c, t)
                for n, c, t in zip(mir["company_name"], mir["code"], mir["share_type"])
            ]
            frames.append(build_uk(mir))

    au_path = Path("data/au_processed/au_monthly_panel.parquet")
    if au_path.exists():
        frames.append(build_au(pd.read_parquet(au_path)))

    if not frames:
        raise RuntimeError("no registry sources available")
    reg = finalise(frames)

    out = Path("data/universe")
    out.mkdir(parents=True, exist_ok=True)
    reg.to_parquet(out / "registry.parquet", index=False)
    reg.to_csv(out / "registry.csv", index=False)
    log.info("universe registry: %d vehicles (%d live, %d priced by source)",
             len(reg), int((reg["status"] == "live").sum()),
             int(reg["source_prices_it"].sum()))
    return reg
