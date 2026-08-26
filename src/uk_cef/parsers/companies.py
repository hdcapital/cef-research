"""Parser for the AIC company-universe Excel files in the Keyfacts bundles.

Observed variants (all verified against real files):

- 2007-2012: "AIC Companies end <Mon> <YY>.xls" - sheet1, columns:
  Company / Share, Sector, Manager, Total Assets (£m), Market Cap (£m),
  Domicile. AIC *members* only.
- 2013-2018: "AICAllCompanies<date>.xlsx" - 'All companies' sheet, columns:
  Company, Manager, Sector, Total assets (£m), Market cap (£m), Domicile,
  Member, Section 1158, Fund of funds, Listing. Members AND non-members.
- 2019+: adds ISIN and TIDM columns; later renamed
  "AICIndustryOverview<date>.xlsx" with extra sheets.

Header row is located by finding the row whose first cell is "Company" or
"Company / Share". Aggregate rows ("Total", "Investment company industry",
"AIC membership") are skipped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_SKIP_FIRST_CELLS = {
    "total", "totals", "investment company industry", "aic membership",
}

_COLMAP = {
    "company": "company_name",
    "company / share": "company_name",
    "manager": "manager",
    "management group": "manager",
    "sector": "sector",
    "aic sector": "sector",
    "isin": "isin",
    "tidm": "ticker",
    "total assets (£m)": "total_assets_m",
    "total assets (£m)": "total_assets_m",
    "market cap (£m)": "market_cap_m",
    "market cap (£m)": "market_cap_m",
    "domicile": "domicile",
    "member": "member",
    "section 1158": "s1158",
    "investment trust status": "s1158",
    "fund of funds": "fund_of_funds",
    "listing": "listing",
    "ftse 100 / 250": "ftse_index",
}


def parse_companies_excel(path: str | Path) -> list[dict]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheet = None
    for name in xl.sheet_names:
        if name.lower().strip() in ("all companies", "sheet1"):
            sheet = name
            break
    sheet = sheet or xl.sheet_names[0]
    raw = xl.parse(sheet, header=None)
    header_idx = col_offset = None
    for i in range(min(10, len(raw))):
        for j in range(min(4, raw.shape[1])):
            cell = str(raw.iat[i, j]).strip().lower()
            if cell in ("company", "company / share"):
                header_idx, col_offset = i, j
                break
        if header_idx is not None:
            break
    if header_idx is None:
        log.warning("%s: no company header row found", path.name)
        return []
    if col_offset:
        raw = raw.iloc[:, col_offset:]

    headers = [str(c).strip() for c in raw.iloc[header_idx]]
    mapped = [_COLMAP.get(h.lower().rstrip(), _COLMAP.get(h.lower(), None)) for h in headers]
    # normalize trailing-space header variants (e.g. "AIC sector ")
    for j, h in enumerate(headers):
        if mapped[j] is None:
            mapped[j] = _COLMAP.get(h.lower().strip())

    out: list[dict] = []
    for i in range(header_idx + 1, len(raw)):
        row = raw.iloc[i]
        first = str(row.iloc[0]).strip()
        if not first or first.lower() == "nan":
            continue
        if first.lower() in _SKIP_FIRST_CELLS or first.lower().startswith("produced by"):
            continue
        rec: dict = {"source_file": path.name}
        for j, key in enumerate(mapped):
            if key is None or j >= len(row):
                continue
            val = row.iloc[j]
            if pd.isna(val):
                continue
            if key in ("total_assets_m", "market_cap_m"):
                try:
                    rec[key] = float(val)
                except (TypeError, ValueError):
                    pass
            else:
                sval = str(val).strip()
                if sval:
                    rec[key] = sval
        if rec.get("company_name"):
            # aggregate rows in 2026-era files put a count in col 2
            if re.match(r"^\d+ companies$", str(rec.get("manager", ""))):
                continue
            out.append(rec)
    return out
