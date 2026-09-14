"""Multi-sheet XLSX deliverable: every series plus the underlying panel.

Sheet plan
----------
Notes                 what each sheet is, sign convention, caveats
Market-wide           one row per month, both markets pooled
By market             one row per month per market
By segment            long form, month x market x segment
By sector             long form, month x market x native sector
Segment summary       whole-sample stats per market x segment
UK segments (wide)    month rows x segment columns - pivot/chart ready
ASX segments (wide)   same for the ASX
Panel                 the full security-month panel behind every number
Excluded rows         fund-months dropped on the declared quality bounds
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Excel's hard limit is 1,048,576 rows; stay clear of it and spill to CSV.
MAX_SHEET_ROWS = 900_000

NOTES = [
    ("What this is",
     "Monthly average discount to NAV/NTA for listed closed-end funds: UK "
     "investment trusts (AIC monthly data, from 2007-01) and ASX LICs/LITs "
     "(ASX investment-products monthly reports, from 2017-01)."),
    ("Sign convention",
     "Discounts are decimals on the convention price/NAV - 1. NEGATIVE means "
     "trading BELOW asset value. -0.15 = a 15% discount; +0.05 = a 5% premium. "
     "A FALLING line is a WIDENING discount. Columns ending _pct are the same "
     "number in percentage points."),
    ("mean vs cap_weighted",
     "mean_discount gives every fund one vote (the average FUND's discount). "
     "cap_weighted_discount weights by market cap (the average DOLLAR's "
     "discount). They diverge when large trusts trade differently from small "
     "ones; that gap is a finding, not an error."),
    ("mean_discount_balanced",
     "Market-wide sheet only. Pooling every fund lets the UK (roughly 4x the "
     "ASX fund count) dominate. The balanced mean gives each MARKET one vote."),
    ("Composition break",
     "The ASX panel starts 2017-01, the UK 2007-01. The markets_included "
     "column on the Market-wide sheet records which markets are in each "
     "month; a pooled series compared across 2017 is comparing different "
     "universes. Use the By market sheet, or 2017+ only, for like-for-like."),
    ("Thin groups",
     "Distribution stats are blanked when a group has fewer than the minimum "
     "fund count for that month (sufficient = FALSE); n_funds is always shown "
     "so the thinness is visible. Do not read a sub-segment line where "
     "n_funds is low."),
    ("Quality bounds",
     "Both config files declare a -85% discount floor and a +100% premium "
     "ceiling, but only the ASX panel applies the ceiling upstream. This "
     "layer applies each market's own declared bounds symmetrically, without "
     "which the UK mean is not comparable with the ASX mean. Every dropped "
     "fund-month is listed in full on the Excluded rows sheet - including "
     "genuine extreme premiums, which the bound removes by rule rather than "
     "by judgement."),
    ("Eligibility",
     "Rows are the upstream panels' eligible universe: VCTs, split-capital "
     "share classes and ZDPs excluded, and residual data errors outside "
     "-85%/+100% dropped. Nothing is interpolated and no missing observation "
     "has been filled."),
    ("Market cap currency",
     "market_cap_local is GBP millions for the UK and AUD millions for the "
     "ASX. It is used only as a within-market weight; the two are never "
     "summed and no FX conversion is applied."),
]


def _autosize(ws, df: pd.DataFrame, max_width: int = 42) -> None:
    """Approximate column widths so the sheet is readable on open."""
    from openpyxl.utils import get_column_letter

    for i, col in enumerate(df.columns, start=1):
        header = len(str(col))
        try:
            sample = df[col].head(200).astype(str).str.len().max()
            sample = 0 if pd.isna(sample) else int(sample)
        except Exception:  # noqa: BLE001 - width is cosmetic, never fatal
            sample = 0
        ws.column_dimensions[get_column_letter(i)].width = min(
            max(10, header + 2, sample + 2), max_width)


def _write(writer: pd.ExcelWriter, name: str, df: pd.DataFrame,
           freeze: str = "A2") -> None:
    if df is None or df.empty:
        df = pd.DataFrame({"note": [f"no rows for '{name}' in this run"]})
    df.to_excel(writer, sheet_name=name[:31], index=False)
    ws = writer.sheets[name[:31]]
    ws.freeze_panes = freeze
    _autosize(ws, df)


def _wide(seg_monthly: pd.DataFrame, market: str, value: str = "mean_discount_pct") -> pd.DataFrame:
    """month rows x segment columns, ready to chart or pivot."""
    sub = seg_monthly[(seg_monthly["market"] == market) & seg_monthly["sufficient"]]
    if sub.empty:
        return pd.DataFrame()
    wide = sub.pivot_table(index="month", columns="segment", values=value, aggfunc="first")
    return wide.reset_index()


def write(path: Path, *, market_wide: pd.DataFrame, by_market: pd.DataFrame,
          by_segment: pd.DataFrame, by_sector: pd.DataFrame,
          segment_summary: pd.DataFrame, panel: pd.DataFrame,
          excluded: pd.DataFrame | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    notes = pd.DataFrame(NOTES, columns=["Topic", "Detail"])
    panel_out = panel.drop(columns=["month_end"], errors="ignore")
    spilled: Path | None = None
    if len(panel_out) > MAX_SHEET_ROWS:
        spilled = path.with_name(path.stem + "_panel.csv")
        panel_out.to_csv(spilled, index=False)
        log.warning("panel has %d rows (> %d); written to %s instead of a sheet",
                    len(panel_out), MAX_SHEET_ROWS, spilled)
        panel_sheet = pd.DataFrame({"note": [
            f"The full panel has {len(panel_out):,} rows, beyond what one sheet "
            f"holds comfortably. It is written alongside this workbook as "
            f"{spilled.name}."]})
    else:
        panel_sheet = panel_out

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        _write(writer, "Notes", notes)
        _write(writer, "Market-wide", market_wide)
        _write(writer, "By market", by_market)
        _write(writer, "By segment", by_segment)
        _write(writer, "By sector", by_sector)
        _write(writer, "Segment summary", segment_summary)
        _write(writer, "UK segments (wide)", _wide(by_segment, "UK"))
        _write(writer, "ASX segments (wide)", _wide(by_segment, "ASX"))
        _write(writer, "Panel", panel_sheet)
        _write(writer, "Excluded rows",
               (excluded.drop(columns=["month_end"], errors="ignore")
                if excluded is not None else None))
    log.info("workbook -> %s (%d sheets)", path, 10)
    return path
