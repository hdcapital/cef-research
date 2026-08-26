"""Parser for AIC Monthly Information Release (MIR) CSV files, 2007-present.

Layout facts (verified against real files across 2007/2010/2014/2018/2022/2026):

- Two header rows: row0 = group header, row1 = field header. The pair is
  REPEATED throughout the file between sector blocks and must be skipped.
- Column 0..4 are stable: Category, Fund/Name, Share/Type, MonthEnd Date,
  Code (SEDOL in early years, ISIN later).
- Price column header contains "Price" ("MonthendMidPrice" early,
  "MonthEnd Price" later); "Price for TS dilution" must be excluded.
- NAV-per-share bases appear under field headers PCNCWNE..FCCWETScum
  (P=par debt, F=fair-value debt; NC/C = capital-only/cum-income? no:
  NC = "not cum income", cum suffix = cum income; W E/NE = warrants
  exercised / not exercised; TS = treasury-share dilution). Values in the
  fund's reported currency, pence for GBX.
- PFY = trailing dividend yield %, where supplied.
- "Reported Currency" column exists in later years only; earlier files are
  GBX throughout (spot-checked against contemporaneous LSE prices).
- Errata files (…ERR / PostErr / ErrataAdditional) use the same layout and
  contain corrected or late-reported rows keyed by their own MonthEnd date.

The parser returns one dict per data row; it NEVER fabricates a value -
anything unparseable stays None.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

NAV_BASES = [
    # preference order for discount calculation: cum-income first, fair
    # (debt at fair value) before par, diluted before undiluted. The AIC's
    # published discounts use cum-income fair NAV where available.
    "FCCWETScum", "FCCWEcum", "FCNCWEcum", "FCNCWNEcum", "FCCWNEcum",
    "PCCWETScum", "PCCWEcum", "PCNCWEcum", "PCNCWNEcum", "PCCWNEcum",
    # ex-income fallbacks (flagged via nav_basis)
    "FCCWETS", "FCCWE", "FCNCWE", "FCNCWNE", "FCCWNE",
    "PCCWETS", "PCCWE", "PCNCWE", "PCNCWNE", "PCCWNE",
]

_NUM_RE = re.compile(r"^-?[\d,]*\.?\d+$")


def _num(val: str) -> float | None:
    val = (val or "").strip().replace('"', "")
    if not val or val.upper() in {"N/A", "NA", "-", "NIL"}:
        return None
    val = val.replace(",", "")
    if not _NUM_RE.match(val):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_date(val: str) -> datetime | None:
    val = (val or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _find_col(h1: list[str], h2: list[str], pred) -> int | None:
    for i, (a, b) in enumerate(zip(h1, h2)):
        if pred(a.strip(), b.strip()):
            return i
    return None


class MIRLayout:
    def __init__(self, h1: list[str], h2: list[str]):
        self.h1, self.h2 = h1, h2
        self.price = _find_col(
            h1, h2,
            lambda a, b: ("price" in a.lower() or "price" in b.lower())
            and "dilution" not in a.lower() and "dilution" not in b.lower(),
        )
        self.shares = _find_col(
            h1, h2,
            lambda a, b: (a in ("Shares",) and b in ("Number", "NumberShares"))
            or b == "NumberShares",
        )
        self.total_assets = _find_col(
            h1, h2, lambda a, b: b.lower().startswith("totalassets")
            or b.lower().startswith("total assets incl")
        )
        self.pfy = _find_col(h1, h2, lambda a, b: b == "PFY")
        self.currency = _find_col(h1, h2, lambda a, b: b == "Reported Currency")
        self.net_gearing = _find_col(h1, h2, lambda a, b: b in ("NetGearing", "Gearing") and a in ("NetGearing", "Gearing"))
        self.nav_cols: dict[str, int] = {}
        for i, b in enumerate(h2):
            base = b.strip()
            if base in NAV_BASES and base not in self.nav_cols:
                self.nav_cols[base] = i

    def ok(self) -> bool:
        return self.price is not None and bool(self.nav_cols)


def _is_header_row(row: list[str]) -> bool:
    if len(row) < 5:
        return True
    c0, c3 = row[0].strip(), row[3].strip()
    return c0 in ("AIC", "Category") or c3 in ("Date", "MonthEnd") or not row[1].strip()


def parse_mir_csv(path: str | Path, source_name: str | None = None) -> list[dict]:
    """Parse one MIR (or errata) CSV. Returns a list of row dicts."""
    path = Path(path)
    source_name = source_name or path.name
    with open(path, newline="", encoding="latin-1") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        return []

    # locate the first header pair (errata files may have a banner line)
    start = None
    for i in range(min(6, len(rows) - 1)):
        if rows[i] and rows[i][0].strip() == "AIC" and rows[i + 1][0].strip() == "Category":
            start = i
            break
    if start is None:
        log.warning("%s: no MIR header pair found", source_name)
        return []
    layout = MIRLayout(rows[start], rows[start + 1])
    if not layout.ok():
        log.warning("%s: header pair found but layout incomplete", source_name)
        return []

    out: list[dict] = []
    for row in rows[start + 2 :]:
        if _is_header_row(row):
            # layout can be re-stated mid-file; refresh if the pair differs
            continue
        sector = row[0].strip()
        name = row[1].strip()
        share_type = row[2].strip()
        dt = _parse_date(row[3])
        if not name or dt is None:
            continue
        code = row[4].strip() or None

        def col(idx: int | None) -> str:
            if idx is None or idx >= len(row):
                return ""
            return row[idx]

        navs = {}
        for base, idx in layout.nav_cols.items():
            v = _num(col(idx))
            if v is not None and v != 0:
                navs[base] = v
        nav, nav_basis = None, None
        for base in NAV_BASES:
            if base in navs:
                nav, nav_basis = navs[base], base
                break

        price = _num(col(layout.price))
        if price == 0:
            price = None  # zero is "not supplied" in MIR files, not a price

        out.append(
            {
                "sector": sector or None,
                "company_name": name,
                "share_type": share_type or None,
                "obs_date": dt.date(),
                "obs_month": f"{dt.year:04d}-{dt.month:02d}",
                "code": code,
                "price": price,
                "nav": nav,
                "nav_basis": nav_basis,
                "shares": _num(col(layout.shares)),
                "total_assets": _num(col(layout.total_assets)),
                "dividend_yield": _num(col(layout.pfy)),
                "net_gearing": _num(col(layout.net_gearing)),
                "currency": (col(layout.currency).strip() or None) if layout.currency else None,
                "source_file": source_name,
            }
        )
    return out


def classify_mir_file(name: str) -> str:
    """main | errata | post_errata | other-component (GEO/PC/WAR/CNV)."""
    n = name.upper()
    stem = Path(name).stem.upper()
    if "POSTERR" in n or "POSTMIR" in n:
        return "post_errata"
    if "ERR" in n:
        return "errata"
    if re.match(r"^(?:\d{4}-\d{2}_MIR_)?MIR\d{4}", stem) or stem.startswith("MIR"):
        return "main"
    return "component"
