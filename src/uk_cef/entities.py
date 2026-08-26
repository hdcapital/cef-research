"""Point-in-time entity resolution (Stage 3).

Identity model: one entity = one listed security (share class). The primary
key is the security's SEDOL. Facts used (all real, observed):

- MIR "Code" column carries SEDOL (2007-~2013) then ISIN. UK/Channel-Island
  ISINs embed the SEDOL in characters 4..10 (e.g. GB00B0P6J834 -> B0P6J83),
  which chains the two identifier eras without guessing.
- Company renames keep the SEDOL, so SEDOL grouping survives name changes.
- AIC Corporate Activity "Name Change"/"New Name" pairs provide alias links
  for the minority of cases where an identifier is missing.
- config/entity_overrides.csv provides manually verified links; nothing
  else is ever merged on fuzzy similarity alone.

Rows with no usable identifier fall back to (normalised name, share type)
keys; these never silently merge with SEDOL-keyed entities unless the exact
normalised name matches an entity seen with that name.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_SEDOL_RE = re.compile(r"^[B-DF-HJ-NP-TV-Z0-9]{6}\d$", re.I)

_SUFFIX_WORDS = (
    "plc", "limited", "ltd", "inc", "the", "ord", "ordinary", "shares",
    "share", "fund", "company", "co",
)


def sedol_from_code(code: str | None) -> str | None:
    """Return the 7-char SEDOL embedded in a SEDOL or UK-style ISIN."""
    if code is None or (isinstance(code, float) and pd.isna(code)):
        return None
    code = str(code).strip().upper()
    if not code or code == "NAN":
        return None
    if _ISIN_RE.match(code):
        candidate = code[4:11]
        return candidate
    if len(code) == 7 and _SEDOL_RE.match(code):
        return code
    if len(code) == 6:  # early files sometimes drop the check digit
        return None if not code.isalnum() else code
    return None


def normalize_name(name: str) -> str:
    n = (name or "").lower()
    n = n.replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    words = [w for w in n.split() if w not in _SUFFIX_WORDS]
    return " ".join(words)


class EntityRegistry:
    """Assigns stable security_ids to observed (name, code, share_type)."""

    def __init__(self, overrides_path: str | Path | None = None):
        self.by_sedol: dict[str, str] = {}
        self.by_name_type: dict[tuple[str, str], str] = {}
        self.records: dict[str, dict] = {}  # security_id -> attributes
        self.alias_map: dict[str, str] = {}  # normalized old name -> new name
        self.overrides: list[dict] = []
        if overrides_path and Path(overrides_path).exists():
            with open(overrides_path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(
                    r for r in fh if not r.lstrip().startswith("#")
                ):
                    if row.get("raw_name"):
                        self.overrides.append(row)

    def load_name_changes(self, ca_events: pd.DataFrame) -> None:
        """Alias links from Corporate Activity name-change pairs.
        'Name Change' rows: company_name = old, detail = 'to <new>'."""
        if ca_events is None or ca_events.empty:
            return
        nc = ca_events[ca_events["category"] == "name_change"]
        for _, r in nc.iterrows():
            detail = str(r.get("detail") or "")
            old = new = None
            if str(r["event"]).strip().lower().startswith("name change"):
                m = re.match(r"^to\s+(.+)$", detail.strip(), re.I)
                if m:
                    old, new = r["company_name"], m.group(1)
            else:  # "New Name": company_name = new, detail = "from <old>"
                m = re.match(r"^from\s+(.+)$", detail.strip(), re.I)
                if m:
                    old, new = m.group(1), r["company_name"]
            if old and new:
                self.alias_map[normalize_name(old)] = normalize_name(new)

    def _canonical_name(self, name: str) -> str:
        n = normalize_name(name)
        seen = {n}
        while n in self.alias_map and self.alias_map[n] not in seen:
            n = self.alias_map[n]
            seen.add(n)
        return n

    def resolve(self, name: str, code: str | None, share_type: str | None) -> str:
        """Return the stable security_id for an observation, creating a new
        entity when nothing links it to an existing one."""
        if share_type is None or (isinstance(share_type, float) and pd.isna(share_type)):
            share_type = "Ordinary Share"
        share_type = str(share_type).strip() or "Ordinary Share"
        sedol = sedol_from_code(code)
        cname = self._canonical_name(name)
        key_nt = (cname, share_type.lower())

        for ov in self.overrides:
            if ov.get("match_type") == "alias" and normalize_name(ov["raw_name"]) == normalize_name(name):
                target = ov["canonical_id"]
                if sedol:
                    self.by_sedol.setdefault(sedol, target)
                self.by_name_type[key_nt] = target
                self._touch(target, name, sedol, share_type)
                return target

        sid = None
        if sedol and sedol in self.by_sedol:
            sid = self.by_sedol[sedol]
        elif key_nt in self.by_name_type:
            sid = self.by_name_type[key_nt]

        if sid is None:
            sid = f"SEDOL:{sedol}" if sedol else f"NAME:{cname}|{share_type.lower()}"
            self.records[sid] = {
                "security_id": sid,
                "first_name": name,
                "share_type": share_type,
                "sedols": set(),
                "names": set(),
                "codes": set(),
            }
        if sedol:
            self.by_sedol[sedol] = sid
        self.by_name_type[key_nt] = sid
        self._touch(sid, name, sedol, share_type)
        if code is not None and not (isinstance(code, float) and pd.isna(code)):
            code_s = str(code).strip().upper()
            if code_s and code_s != "NAN":
                self.records[sid]["codes"].add(code_s)
        return sid

    def _touch(self, sid: str, name: str, sedol: str | None, share_type: str) -> None:
        rec = self.records.setdefault(
            sid,
            {"security_id": sid, "first_name": name, "share_type": share_type,
             "sedols": set(), "names": set(), "codes": set()},
        )
        rec["names"].add(name)
        if sedol:
            rec["sedols"].add(sedol)

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for sid, rec in self.records.items():
            rows.append(
                {
                    "security_id": sid,
                    "share_type": rec["share_type"],
                    "primary_name": rec["first_name"],
                    "all_names": "; ".join(sorted(rec["names"])),
                    "sedols": "; ".join(sorted(rec["sedols"])),
                    "codes": "; ".join(sorted(rec["codes"])),
                }
            )
        return pd.DataFrame(rows)


def build_entities(cfg: dict) -> pd.DataFrame:
    """CLI entry: parse raw MIR + corporate activity, resolve entities, and
    write entities.parquet + a CSV summary."""
    from .panel import parse_all_mir, parse_all_corporate_activity

    raw_dir = Path(cfg["download"]["raw_dir"])
    processed = Path(cfg["paths"]["processed_dir"])
    processed.mkdir(parents=True, exist_ok=True)

    mir = parse_all_mir(raw_dir)
    if mir.empty:
        raise RuntimeError(
            f"no MIR files parsed from {raw_dir} - run download first (in CI: "
            "check the raw-data cache restored with the exact same path list it was saved with)"
        )
    ca = parse_all_corporate_activity(raw_dir)
    registry = EntityRegistry(cfg["paths"].get("entity_overrides"))
    registry.load_name_changes(ca)

    mir = mir.sort_values(["obs_month", "company_name"])
    mir["security_id"] = [
        registry.resolve(n, c, t)
        for n, c, t in zip(mir["company_name"], mir["code"], mir["share_type"])
    ]

    ent = registry.to_frame()
    spans = (
        mir.groupby("security_id")
        .agg(first_seen=("obs_month", "min"), last_seen=("obs_month", "max"),
             n_months=("obs_month", "nunique"),
             sector_last=("sector", "last"))
        .reset_index()
    )
    ent = ent.merge(spans, on="security_id", how="left")
    ent.to_parquet(processed / "entities.parquet", index=False)
    ent.to_csv(processed / "entities.csv", index=False)
    log.info("entities: %d securities, %d MIR rows", len(ent), len(mir))
    return ent
