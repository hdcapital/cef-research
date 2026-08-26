"""Parser for AIC Corporate Activity annual workbooks (2007-present).

Layout facts (verified against 2007/2017/2026 files):

- 2007-2025 archive files: sheets 'Conventional & Split' and 'VCT'.
- Current-era files add 'Corporate activity', 'Issuance', 'Buybacks',
  'Summary', 'Formulas', 'Definitions' sheets; event rows live in
  'Corporate activity'/'Issuance'/'Buybacks' (plus 'VCT').
- Header row starts with 'Year' | 'Month' | 'Event' | company column.
- Some Year cells read 'Await <year>' - entries taken from company
  announcements that have not yet completed. In archived (final) annual
  files completed events carry the month they took effect.

Timing honesty: the archived files record events by their effective month.
The original public announcement generally PRECEDES that month. We therefore
treat an event as knowable no earlier than the END of its recorded month
('await' rows: the month the announcement was logged). Announcement-day
precision is not available from this source and is never invented.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Normalised action categories (Stage 10)
EVENT_CATEGORY = [
    (r"^tender", "tender"),
    (r"^buyback", "buyback"),
    (r"^capital redemption", "redemption"),
    (r"^capital distribution", "capital_return"),
    (r"^capital reorganisation/depart", "reorganisation"),
    (r"^capital reorganisation", "reorganisation"),
    (r"^capital change", "capital_change"),      # splits/consolidations
    (r"^depart/liquidate", "liquidation"),
    (r"^depart", "depart_other"),
    (r"^merge/depart", "merger_departing"),
    (r"^merge", "merger_continuing"),
    (r"^reconstruct", "reconstruction"),
    (r"^restructure", "reconstruction"),
    (r"^realisation policy", "realisation_policy"),
    (r"^wind", "liquidation"),
    (r"^new issue|^issue/rollover|^new issue/rollover", "ipo_or_rollover"),
    (r"^issue", "issuance"),
    (r"^convert", "conversion"),
    (r"^name change|^new name", "name_change"),
    (r"^listing change", "listing_change"),
    (r"^domicile change", "domicile_change"),
    (r"^trading status", "trading_status"),
    (r"^policy change", "policy_change"),
    (r"^fee change", "fee_change"),
    (r"^(mg |management|new management)", "manager_change"),
    (r"^(aic sector|sector change|new sector|new aic sector)", "sector_change"),
    (r"^tax status", "tax_status"),
    (r"^(open|closed|pending) offer", "vct_offer"),
]


def categorize_event(event: str) -> str:
    e = (event or "").strip().lower()
    for pat, cat in EVENT_CATEGORY:
        if re.match(pat, e):
            return cat
    return "other"


def _find_header(df: pd.DataFrame) -> int | None:
    for i in range(min(10, len(df))):
        if str(df.iat[i, 0]).strip().lower() == "year":
            return i
    return None


def parse_corporate_activity(path: str | Path) -> list[dict]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheets = [
        s for s in xl.sheet_names
        if s.strip().lower() in
        ("conventional & split", "conventional", "corporate activity", "issuance", "buybacks", "vct")
    ]
    out: list[dict] = []
    for sheet in sheets:
        raw = xl.parse(sheet, header=None)
        if raw.empty:
            continue
        h = _find_header(raw)
        if h is None:
            log.warning("%s[%s]: no Year header row", path.name, sheet)
            continue
        headers = [str(c).strip().lower() for c in raw.iloc[h]]

        def col_idx(*names: str) -> int | None:
            for n in names:
                if n in headers:
                    return headers.index(n)
            return None

        i_year, i_month, i_event = 0, 1, 2
        i_company = col_idx("company", "company name")
        if i_company is None:
            i_company = 3
        i_sector = col_idx("aic sector", "aic sector")
        i_structure = col_idx("structure")
        i_additional = col_idx("additional")
        i_assets = col_idx("assets (£m)", "total assets (£m)", "launch assets")

        for i in range(h + 1, len(raw)):
            row = raw.iloc[i]
            year_raw = str(row.iloc[i_year]).strip()
            if not year_raw or year_raw.lower() == "nan":
                continue
            is_await = year_raw.lower().startswith("await")
            m = re.search(r"(\d{4})", year_raw)
            if not m:
                continue
            year = int(m.group(1))
            month_raw = str(row.iloc[i_month]).strip().lower()[:3]
            month = MONTH_MAP.get(month_raw)
            if month is None:
                continue
            event = str(row.iloc[i_event]).strip()
            if not event or event.lower() == "nan":
                continue
            company = str(row.iloc[i_company]).strip() if i_company < len(row) else ""
            if not company or company.lower() == "nan":
                continue

            def val(idx):
                if idx is None or idx >= len(row):
                    return None
                v = row.iloc[idx]
                if pd.isna(v):
                    return None
                return v

            out.append(
                {
                    "event_year": year,
                    "event_month": f"{year:04d}-{month:02d}",
                    "event": event,
                    "category": categorize_event(event),
                    "company_name": company,
                    "sector": val(i_sector),
                    "structure": val(i_structure),
                    "detail": str(val(i_additional) or "")[:500] or None,
                    "assets_m": _to_float(val(i_assets)),
                    "is_await": is_await,
                    "sheet": sheet,
                    "source_file": path.name,
                }
            )
    return out


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
