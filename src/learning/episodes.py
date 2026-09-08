"""Resolution episodes: every fund that ended, how, and when.

One row per gone fund, built from what the repository already holds and
nothing else:

- UK: the research panel's terminal classification, materialised in
  outputs/return_data_coverage.csv (terminal_liquidated / merged /
  reconstructed / reorganised / redeemed / departed / unresolved) with the
  last observed month;
- AU and any UK fund the live registry marks delisted on the fund's own
  cancellation notice (data/universe/registry.parquet, delisting_notice);
- the Investegate ticker where one is verified (config/resolved_tickers.csv,
  config/investegate_tickers.csv), so the announcement window can be read.

The outcome vocabulary is fixed here. `value_realising` is True for an
ending that paid holders NAV-ish value (liquidation, merger, reconstruction,
redemption), False for a plain departure, and None when the exit type is
not yet known (an AU delisting whose own notice has not been read for its
terms). A learning label built on `resolved` counts every known ending;
one built on `value_realising` counts only the endings the discount closes
into.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

COVERAGE_CSV = Path("outputs/return_data_coverage.csv")
REGISTRY = Path("data/universe/registry.parquet")
TICKER_FILES = (Path("config/resolved_tickers.csv"),
                Path("config/investegate_tickers.csv"))
OUT_DIR = Path("data/learning")

OUTCOMES = ("liquidated", "merged", "reconstructed", "reorganised", "redeemed",
            "departed", "unresolved", "delisted")
VALUE_REALISING = {"liquidated": True, "merged": True, "reconstructed": True,
                   "redeemed": True, "reorganised": False, "departed": False,
                   "unresolved": None, "delisted": None}

COLUMNS = ["security_id", "market", "name", "ticker", "end_month", "outcome",
           "outcome_source", "value_realising", "first_month", "months_listed",
           "delisting_notice"]


def _tickers() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in TICKER_FILES:
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, dtype=str, comment="#")
        except Exception:  # noqa: BLE001
            continue
        if not {"security_id", "ticker"} <= set(df.columns):
            continue
        if "status" in df.columns:
            df = df[df["status"].fillna("verified").eq("verified")]
        for sid, t in zip(df["security_id"], df["ticker"]):
            if isinstance(sid, str) and isinstance(t, str) and sid not in out:
                out[sid] = t.strip().upper()
    return out


def _uk_terminal(cov: pd.DataFrame) -> pd.DataFrame:
    if cov is None or not len(cov):
        return pd.DataFrame(columns=COLUMNS)
    t = cov[cov["terminal_status"].fillna("").str.startswith("terminal_")].copy()
    t["outcome"] = t["terminal_status"].str.replace("terminal_", "", regex=False)
    t = t[t["outcome"].isin(OUTCOMES)]
    return pd.DataFrame({
        "security_id": t["security_id"], "market": "UK",
        "name": t.get("company_name"), "end_month": t["last_month"],
        "outcome": t["outcome"], "outcome_source": "panel_terminal",
        "first_month": t.get("first_month"),
        "months_listed": t.get("months_expected")})


def _registry_delisted(reg: pd.DataFrame) -> pd.DataFrame:
    if reg is None or not len(reg):
        return pd.DataFrame(columns=COLUMNS)
    d = reg[reg["status"].eq("delisted")].copy()
    return pd.DataFrame({
        "security_id": d["security_id"], "market": d["market"],
        "name": d["name"], "end_month": d["last_seen"],
        "outcome": "delisted", "outcome_source": "own_delisting_notice",
        "first_month": d.get("first_seen"), "months_listed": d.get("months_listed"),
        "delisting_notice": d.get("delisting_notice")})


def build(cov: pd.DataFrame | None = None, reg: pd.DataFrame | None = None,
          tickers: dict[str, str] | None = None) -> pd.DataFrame:
    """The episode table. A fund in both sources keeps the panel's named
    outcome and gains the registry's notice date."""
    if cov is None:
        cov = pd.read_csv(COVERAGE_CSV) if COVERAGE_CSV.exists() else None
    if reg is None:
        reg = pd.read_parquet(REGISTRY) if REGISTRY.exists() else None
    if tickers is None:
        tickers = _tickers()
    uk = _uk_terminal(cov)
    dl = _registry_delisted(reg)
    if len(uk) and len(dl):
        notice = dl.set_index("security_id")["delisting_notice"]
        uk["delisting_notice"] = uk["security_id"].map(notice)
        dl = dl[~dl["security_id"].isin(set(uk["security_id"]))]
    ep = pd.concat([uk, dl], ignore_index=True)
    if not len(ep):
        return pd.DataFrame(columns=COLUMNS)
    ep["ticker"] = ep["security_id"].map(tickers).map(
        lambda t: t.strip().upper() if isinstance(t, str) else None)
    asx = ep["security_id"].str.startswith("ASX:")
    ep.loc[asx, "ticker"] = ep.loc[asx, "security_id"].str[4:]
    if reg is not None and len(reg) and "name" in reg.columns:
        names = reg.set_index("security_id")["name"]
        ep["name"] = ep["name"].where(ep["name"].notna(), ep["security_id"].map(names))
    ep["value_realising"] = ep["outcome"].map(VALUE_REALISING)
    ep["end_month"] = ep["end_month"].astype(str).str[:7]
    ep = ep[ep["end_month"].str.match(r"\d{4}-\d{2}")]
    for c in COLUMNS:
        if c not in ep.columns:
            ep[c] = None
    ep = ep[COLUMNS].sort_values(["market", "end_month", "security_id"])
    return ep.reset_index(drop=True)


def write(ep: pd.DataFrame, out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    ep.to_parquet(out_dir / "episodes.parquet", index=False)
    ep.to_csv(out_dir / "episodes.csv", index=False)
    return summarise(ep)


def summarise(ep: pd.DataFrame) -> dict:
    if not len(ep):
        return {"episodes": 0}
    return {"episodes": int(len(ep)),
            "by_market": ep["market"].value_counts().to_dict(),
            "by_outcome": ep["outcome"].value_counts().to_dict(),
            "with_ticker": int(ep["ticker"].notna().sum()),
            "value_realising": int(ep["value_realising"].eq(True).sum()),
            "end_month_range": [ep["end_month"].min(), ep["end_month"].max()]}


def window_months(end_month: str, before: int = 18, after: int = 0) -> list[str]:
    """The months whose announcements precede the ending: [end-before, end+after]."""
    p = pd.Period(end_month, freq="M")
    return [str(p - k) for k in range(before, -after - 1, -1)]
