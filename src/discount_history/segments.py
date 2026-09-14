"""Stable sub-segment taxonomy for UK AIC sectors and ASX product sectors.

Why this exists: the AIC has renamed its sectors repeatedly over 2007-2026
("Sector Specialist: Debt" -> "Debt - Loans & Bonds", "China / Greater
China" -> "China - Greater China", "Biotechnology/Life Sciences" ->
"Biotechnology & Healthcare", ...). Grouping a 19-year discount series on
the *raw* label therefore snaps each series in half at every rename and
invents a new sub-segment that starts mid-sample. Both levels below are
rebuilt from the label text on every run, so a sector the AIC introduces
later still lands in the right bucket without a code change:

  sector_canon  the market's own sector, normalised for punctuation and
                known renames - fine-grained, comparable through time.
  segment       a coarse cross-market asset-class bucket, so a UK
                sub-segment can be read against its ASX counterpart.

Rules are ordered and the FIRST match wins, so narrow patterns
("Property Securities", "Renewable Energy Infrastructure") must precede
the broad ones they contain ("Property", "Infrastructure").
"""

from __future__ import annotations

import re

import pandas as pd

UNCLASSIFIED = "Unclassified"

# --------------------------------------------------------- canonical sector
# Prefixes the AIC bolted on (and later dropped) that carry no information.
_STRIP_PREFIXES = (
    "sector specialists:",
    "sector specialist:",
    "country specialists:",
    "country specialist:",
)

# Renames where the later label is adopted as canonical. Keys are compared
# after _normalise_label (lowercased, punctuation folded, prefix stripped).
_ALIASES = {
    "biotechnology/life sciences": "Biotechnology & Healthcare",
    "biotechnology & healthcare": "Biotechnology & Healthcare",
    "china / greater china": "China / Greater China",
    "china - greater china": "China / Greater China",
    "india / indian subcontinent": "India / Indian Subcontinent",
    "india/indian subcontinent": "India / Indian Subcontinent",
    "india": "India / Indian Subcontinent",
    "commodities and natural resources": "Commodities & Natural Resources",
    "commodities & natural resources": "Commodities & Natural Resources",
    "small media comms & it cos": "Technology & Media",
    "small media, comms & it cos": "Technology & Media",
    "tech media & telecomm": "Technology & Media",
    "technology & media": "Technology & Media",
    "technology & technology innovation": "Technology & Media",
    "debt": "Debt - Loans & Bonds",
    "debt - loans & bonds": "Debt - Loans & Bonds",
    "securitised debt": "Debt - Structured Finance",
    "financials": "Financials",
    "financials & financial innovation": "Financials",
    "environmental": "Environmental",
    "liquidity funds": "Liquidity Funds",
    "asia pacific - excluding japan": "Asia Pacific - ex Japan",
    "asia pacific ex japan": "Asia Pacific - ex Japan",
    "asia pacific - including japan": "Asia Pacific - inc Japan",
    "asia pacific income": "Asia Pacific Equity Income",
    "asia pacific equity income": "Asia Pacific Equity Income",
    "uk high income": "UK Equity Income",
    "uk equity income": "UK Equity Income",
    "uk growth & income": "UK Equity Income",
    "uk equity & bond income": "UK Equity & Bond Income",
    "uk growth": "UK All Companies",
    "uk all companies": "UK All Companies",
    "global growth": "Global",
    "global": "Global",
    "overseas growth": "Global",
    "global growth & income": "Global Equity Income",
    "global equity income": "Global Equity Income",
    "global high income": "Global Equity Income",
    "property direct - uk": "Property - UK Commercial",
    "property - uk commercial": "Property - UK Commercial",
}

# -------------------------------------------------------------- segment map
# (compiled pattern, segment). First match wins - order is significant.
_SEGMENT_RULES: list[tuple[str, str]] = [
    # --- specific structures, before any geography or asset class ---
    (r"venture capital|^vct\b", "VCT"),
    (r"zero dividend|split capital", "Split Capital & ZDP"),
    (r"liquidity|money market|cash", "Liquidity & Cash"),
    (r"growth capital", "Growth Capital"),
    (r"private equity", "Private Equity"),
    (r"hedge fund", "Hedge & Multi-Asset"),
    (r"flexible investment|multi[- ]asset|balanced", "Hedge & Multi-Asset"),
    (r"leasing", "Leasing"),
    (r"endowment", "Specialist - Other"),
    (r"forestry|timber", "Specialist - Other"),
    (r"insurance|reinsurance|royalt", "Specialist - Other"),
    # --- real assets ---
    (r"renewable|energy efficiency", "Infrastructure & Renewables"),
    (r"infrastructure", "Infrastructure & Renewables"),
    (r"property|real estate", "Property"),
    # --- credit ---
    # Hybrid equity-and-bond income mandates are equity vehicles; keep them
    # out of the credit bucket the bare \bbond\b rule below would claim.
    (r"equity & bond", "UK Equity"),
    (r"securitised debt|structured finance", "Debt & Fixed Income"),
    (r"\bdebt\b|loans? & bonds|fixed income|\bbond\b|credit", "Debt & Fixed Income"),
    # --- sector-specialist equity ---
    (r"biotech|healthcare|life science|pharma", "Specialist - Healthcare"),
    (r"technolog|media|telecom|comms", "Specialist - Technology & Media"),
    (r"commodit|natural resource|mining|gold|energy", "Specialist - Commodities"),
    (r"financial", "Specialist - Financials"),
    (r"environmental|climate|sustainab", "Specialist - Environmental"),
    (r"utilit", "Specialist - Other"),
    # --- geographic equity (ASX labels are 'Equity - X') ---
    (r"\buk\b|united kingdom|british", "UK Equity"),
    (r"australia|australian", "Australia Equity"),
    (r"north america|\bus\b|usa|united states", "North America Equity"),
    (r"latin america|brazil", "Latin America Equity"),
    (r"emerging|frontier", "Emerging & Frontier Equity"),
    (r"japan", "Japan Equity"),
    (r"china|greater china|india|indian subcontinent|vietnam|korea", "China & India Equity"),
    (r"asia|pacific", "Asia Pacific Equity"),
    (r"europe|european", "Europe Equity"),
    (r"country specialist|overseas", "Specialist - Other"),
    (r"global|international|world", "Global Equity"),
]

_COMPILED = [(re.compile(p), seg) for p, seg in _SEGMENT_RULES]


def _normalise_label(raw: str) -> str:
    """Lowercase, fold punctuation and drop the uninformative AIC prefixes."""
    s = str(raw).strip().lower()
    for pref in _STRIP_PREFIXES:
        if s.startswith(pref):
            s = s[len(pref):].strip()
            break
    s = s.replace(" and ", " & ")
    s = re.sub(r"\s*[-–—]\s*", " - ", s)
    s = re.sub(r"\s*/\s*", " / ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_alias_index() -> dict[str, str]:
    """Alias keys are written readably above; index them under the SAME
    normal form lookups use, so punctuation spacing can never desync the
    two (it did: "biotechnology/life sciences" normalises to
    "biotechnology / life sciences" and silently missed its alias)."""
    return {_normalise_label(k): v for k, v in _ALIASES.items()}


_ALIAS_INDEX = _build_alias_index()


def canonical_sector(raw: object) -> str:
    """The market's own sector label, normalised for punctuation + renames."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return UNCLASSIFIED
    norm = _normalise_label(raw)
    if norm in _ALIAS_INDEX:
        return _ALIAS_INDEX[norm]
    # Title-case the normalised form but keep short connectors lowercase.
    small = {"of", "the", "to", "ex", "inc", "&", "/", "-"}
    parts = [p if p in small else (p.upper() if len(p) <= 3 and p.isalpha() and p in {"uk", "us", "it"}
                                   else p.capitalize())
             for p in norm.split(" ")]
    return " ".join(parts)


def segment(raw: object) -> str:
    """Coarse cross-market asset-class bucket for a raw sector label."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return UNCLASSIFIED
    norm = _normalise_label(raw)
    for pattern, seg in _COMPILED:
        if pattern.search(norm):
            return seg
    return UNCLASSIFIED


def annotate(df: pd.DataFrame, sector_col: str = "sector") -> pd.DataFrame:
    """Add `sector_canon` and `segment` columns derived from `sector_col`.

    Mapping is done over the *distinct* labels rather than row-by-row: the
    panel has ~500k rows and ~100 labels.
    """
    out = df.copy()
    labels = out[sector_col].astype("object").where(out[sector_col].notna(), None)
    uniq = {lab for lab in labels.unique()}
    canon_map = {lab: canonical_sector(lab) for lab in uniq}
    seg_map = {lab: segment(lab) for lab in uniq}
    out["sector_canon"] = labels.map(canon_map)
    out["segment"] = labels.map(seg_map)
    return out
