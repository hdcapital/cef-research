"""Parser for the 'Spotlight LIC List' sheet of ASX investment-products
monthly reports (verified against Jan-2017 and Apr-2026 vintages).

Layout facts:
- header row: first cell containing 'Code'; a group row sits above it.
- columns are identified BY NAME (era-robust): Fund Name, Type
  (Shares=LIC / Units=LIT), MER, Mkt Cap ($m), Prem/Disc % NTA (pre-tax)
  [a fraction, e.g. -0.1366], NTA Date, NTA Price (later era only),
  Last / Last Close, Historical Distribution Yield, 1 Month Total Return
  [a fraction - the month's TOTAL return including distributions],
  1/3/5 Year Total Return.
- sector header rows ('Equity - Australia', ...) interleave the data and
  apply to following rows.
- the report named month M carries month-end M-1 data; each row's NTA Date
  is the authoritative valuation date (staleness is measured, not fixed).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_COLMAP = {
    "asx code": "code",
    "type": "product_type",
    "type*": "product_type",
    "fund name": "company_name",
    "mer (% p.a)": "mer",
    "mkt cap ($m)#": "market_cap",
    "mkt cap ($m)": "market_cap",
    "prem/disc % nta (pre-tax)": "published_discount",
    "prem/disc % nta (pre-tax) at nta date": "published_discount",
    "nta date": "nta_date",
    "nta price": "nta_price",
    "last": "share_price",
    "last close": "share_price",
    "historical distribution yield": "dividend_yield",
    "1 month total return": "tr_1m",
    "1 year total return": "tr_1y",
    "3 year total return (ann.)": "tr_3y",
    "5 year total return (ann.)": "tr_5y",
}


def _norm_header(h) -> str:
    s = re.sub(r"\s+", " ", str(h)).strip().lower()
    return s


def parse_ipr_lic_sheet(path: str | Path) -> list[dict]:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheet = next((s for s in xl.sheet_names if "LIC" in s.upper()), None)
    if sheet is None:
        log.warning("%s: no LIC sheet (sheets=%s)", path.name, xl.sheet_names)
        return []
    raw = xl.parse(sheet, header=None)

    hdr_i = None
    for i in range(min(20, len(raw))):
        if "code" in _norm_header(raw.iat[i, 0]):
            hdr_i = i
            break
    if hdr_i is None:
        log.warning("%s: no header row in %s", path.name, sheet)
        return []

    colmap: dict[int, str] = {}
    for c in range(raw.shape[1]):
        key = _COLMAP.get(_norm_header(raw.iat[hdr_i, c]))
        if key and key not in colmap.values():
            colmap[c] = key
    required = {"code", "company_name", "published_discount"}
    if not required.issubset(set(colmap.values())):
        log.warning("%s: missing required columns; found %s", path.name, sorted(colmap.values()))
        return []

    out: list[dict] = []
    sector = None
    for i in range(hdr_i + 1, len(raw)):
        row = raw.iloc[i]
        c0 = str(row.iloc[0]).strip()
        has_type = pd.notna(row.iloc[1]) and str(row.iloc[1]).strip() != ""
        if c0 and c0.lower() != "nan" and not has_type and len(c0) > 3 and not c0.startswith(("*", "#", "Source", "Note")):
            sector = c0
            continue
        if not has_type or c0.lower() == "nan" or not re.match(r"^[A-Z0-9]{3,6}$", c0):
            continue
        rec: dict = {"code": c0, "sector": sector, "source_file": path.name}
        for c, key in colmap.items():
            v = row.iloc[c]
            if pd.isna(v):
                continue
            if key in ("company_name", "product_type"):
                rec[key] = str(v).strip()
            elif key == "nta_date":
                d = pd.to_datetime(v, errors="coerce")
                rec[key] = d.date().isoformat() if pd.notna(d) else None
            else:
                try:
                    rec[key] = float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        out.append(rec)
    return out


def report_observation_month(path: str | Path, rows: list[dict]) -> str | None:
    """The report named month M carries month-end M-1 data: observation
    month = filename month - 1, deterministically (the downloader prefixes
    local names 'YYYY-MM_'). Per-row NTA dates measure staleness but are
    NOT used for month assignment - modal-NTA inference proved unreliable
    on vintages with many stale NTAs (it collided and dropped months)."""
    m = re.match(r"^(\d{4})-(\d{2})_", Path(path).name)
    if m:
        return str(pd.Period(f"{m.group(1)}-{m.group(2)}", freq="M") - 1)
    dates = pd.to_datetime(pd.Series([r.get("nta_date") for r in rows]), errors="coerce").dropna()
    if len(dates):
        modal = dates.dt.to_period("M").mode()
        if len(modal):
            return str(modal.iloc[0])
    return None
