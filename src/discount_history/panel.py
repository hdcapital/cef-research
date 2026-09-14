"""Load the UK and ASX monthly panels and harmonise them into one frame.

The two upstream panels already carry an audited, point-in-time
`discount` on the same convention (share price / NAV(NTA) per share - 1,
so -0.15 is a 15% discount and +0.05 a 5% premium) and an `eligible`
flag that drops VCTs, split-capital lines, ZDPs and residual data errors.
This module does not recompute either: it selects, renames and stacks.

Market caps arrive in different currencies (GBP m for the UK, AUD m for
the ASX). They are used ONLY as within-market weights and within-market
size buckets, never summed across markets, so no FX conversion is
applied - and none is invented.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import segments

log = logging.getLogger(__name__)

UK_PANEL = Path("data/processed/monthly_panel.parquet")
AU_PANEL = Path("data/au_processed/au_monthly_panel.parquet")

# Harmonised output schema.
COLUMNS = [
    "month", "market", "security_id", "company_name",
    "sector_raw", "sector_canon", "segment",
    "discount", "share_price", "nav_per_share",
    "market_cap_local", "currency", "eligible",
]


class PanelMissing(RuntimeError):
    """Raised when neither market's panel is present."""


def _pick(df: pd.DataFrame, *names: str) -> pd.Series:
    """First present column among `names`, else an all-NaN column.

    Upstream panels have grown columns over time and the AU panel does not
    carry every UK field; a missing optional column must degrade to NaN
    rather than kill a 19-year aggregation.
    """
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(np.nan, index=df.index)


def _load_one(path: Path, market: str, currency: str) -> pd.DataFrame:
    if not path.exists():
        log.warning("%s panel not found at %s - skipping that market", market, path)
        return pd.DataFrame(columns=COLUMNS)
    raw = pd.read_parquet(path)
    if raw.empty:
        log.warning("%s panel at %s is empty", market, path)
        return pd.DataFrame(columns=COLUMNS)

    out = pd.DataFrame({
        "month": raw["obs_month"].astype(str),
        "market": market,
        "security_id": _pick(raw, "security_id").astype(str),
        "company_name": _pick(raw, "company_name", "name"),
        "sector_raw": _pick(raw, "sector"),
        "discount": pd.to_numeric(_pick(raw, "discount"), errors="coerce"),
        "share_price": pd.to_numeric(_pick(raw, "share_price", "price"), errors="coerce"),
        "nav_per_share": pd.to_numeric(
            _pick(raw, "nav_per_share", "nta_derived", "nta_price", "nav"), errors="coerce"),
        "market_cap_local": pd.to_numeric(_pick(raw, "market_cap"), errors="coerce"),
        "currency": currency,
    })
    # `eligible` is the upstream quality gate. If a panel predates it, treat
    # every row with a discount as eligible rather than silently dropping the
    # whole market.
    if "eligible" in raw.columns:
        out["eligible"] = raw["eligible"].fillna(False).astype(bool)
    else:
        log.warning("%s panel has no `eligible` column; falling back to discount.notna()", market)
        out["eligible"] = out["discount"].notna()

    out = segments.annotate(out, sector_col="sector_raw")
    log.info("%s panel: %d rows, %d securities, %s..%s (%d eligible)",
             market, len(out), out["security_id"].nunique(),
             out["month"].min(), out["month"].max(), int(out["eligible"].sum()))
    return out[COLUMNS]


def build(uk_path: Path = UK_PANEL, au_path: Path = AU_PANEL) -> pd.DataFrame:
    """Stacked UK+ASX security-month discount panel, sorted and deduped."""
    frames = [
        _load_one(Path(uk_path), "UK", "GBP"),
        _load_one(Path(au_path), "ASX", "AUD"),
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise PanelMissing(
            f"neither {uk_path} nor {au_path} exists. Build them first:\n"
            "  python -m uk_cef.cli build-panel\n"
            "  python -m au_lic.cli build-panel")
    panel = pd.concat(frames, ignore_index=True)

    # A security-month must be unique; duplicates would double-weight a fund
    # in every average below.
    before = len(panel)
    panel = panel.drop_duplicates(subset=["market", "security_id", "month"], keep="last")
    if before != len(panel):
        log.warning("dropped %d duplicate security-months", before - len(panel))

    panel = panel.sort_values(["market", "month", "security_id"]).reset_index(drop=True)
    panel["month_end"] = (
        pd.PeriodIndex(panel["month"], freq="M").to_timestamp(how="end").normalize()
    )
    log.info("harmonised panel: %d rows, %s..%s, markets=%s",
             len(panel), panel["month"].min(), panel["month"].max(),
             sorted(panel["market"].unique()))
    return panel


def eligible_only(panel: pd.DataFrame) -> pd.DataFrame:
    """Rows that carry a usable discount and pass the upstream quality gate."""
    return panel[panel["eligible"] & panel["discount"].notna()].copy()
