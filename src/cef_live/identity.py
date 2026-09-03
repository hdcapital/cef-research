"""Which security a ticker currently belongs to - and when it does not.

A ticker is a LEASE, not an identity. The London and ASX exchanges both
recycle them, and this registry has the receipts: CIP was CIP Merchant
Capital until 2022 and is Channel Islands Property now; VEIL was a Ventus
VCT share class and is Vietnam Enterprise Investments now. Neither pair is
the same company in any sense.

The hazard is specific and one-directional. The price layer maps
security_id -> "{ticker}.L" or "{code}.AX" and asks a quote provider for
the last close. The provider answers for whoever holds the ticker TODAY. So
a delisted fund whose security_id still carries that ticker receives the
live price of an unrelated company, computes a discount against its own
final NAV, and can produce a spectacular false dislocation - which is
exactly the shape of an idea the opportunity gates are built to find.

Nothing downstream can detect it: the price is real, the NAV is real, the
join is clean, and the answer is nonsense.

So the ticker's holder is resolved explicitly, from the registry's own
point-in-time spans:

  sole_claimant  one security_id claims this ticker. Nothing to resolve.
  incumbent      several claim it; this one was listed most recently, so
                 the live quote is its quote.
  superseded     several claim it; another was listed more recently. This
                 security must NEVER be priced from the live feed - its
                 own history stands, its live price does not.
  conflict       several claim it and MORE THAN ONE is alive. We cannot
                 say from the registry alone which the quote belongs to,
                 so neither may carry a live signal until a human says
                 which is which.

`conflict` is deliberately not resolved by a heuristic. Guessing here
produces a wrong price that looks right, and the whole point of the queue
is that a case we cannot settle is visible rather than settled badly.
"""

from __future__ import annotations

import re

import pandas as pd

STATUS_SOLE = "sole_claimant"
STATUS_INCUMBENT = "incumbent"
STATUS_SUPERSEDED = "superseded"
STATUS_CONFLICT = "conflict"
STATUS_NO_TICKER = "no_ticker"

# statuses whose security may be priced from a live quote and may carry a
# live signal. Everything else is excluded from signals by construction.
PRICEABLE = (STATUS_SOLE, STATUS_INCUMBENT)

# A ticker resolution grounded in the ISIN (OpenFIGI's exchange mapping, an
# AIC key-facts ISIN match) or stated by hand outranks a NAME match: the
# AIC lists a company's share classes and SEDOL vintages under one name,
# so a name match hands the same ticker to every one of them, and eleven
# trading funds (Rights & Issues, Vietnam Enterprise, EJF, Neuberger,
# Regional REIT among them) went unpriced as "conflicts" between their
# own line and their own ZDP or old SEDOL.
GROUNDED_METHOD = re.compile(r"^(?:openfigi_isin|aic_keyfacts_isin|manual_override)", re.I)


def _norm_ticker(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = str(v).strip().upper()
    return t or None


def resolve(registry: pd.DataFrame, tickers: pd.DataFrame,
            live_statuses: tuple[str, ...] = ("live", "live_stale_nav")
            ) -> pd.DataFrame:
    """Attach ticker identity to every registry row.

    registry: security_id, market, name, status, first_seen, last_seen.
    tickers:  security_id, ticker (verified mappings only).

    Adds: ticker, identity_status, identity_reason, identity_ok,
    identity_incumbent_id, identity_incumbent_name, identity_claimants.
    """
    reg = registry.copy()
    tmap = {}
    tmethod: dict[str, str] = {}
    if tickers is not None and len(tickers):
        for r in tickers.itertuples(index=False):
            t = _norm_ticker(getattr(r, "ticker", None))
            if t:
                tmap[str(r.security_id)] = t
                m_ = getattr(r, "method", None)
                if isinstance(m_, str) and m_:
                    tmethod[str(r.security_id)] = m_
    reg["ticker"] = reg["security_id"].astype(str).map(tmap).astype("object")
    # an ASX security IS its code - there is no separate mapping to get wrong
    is_au = reg.get("market", pd.Series("", index=reg.index)).eq("AU")
    reg.loc[is_au & reg["ticker"].isna(), "ticker"] = (
        reg.loc[is_au, "security_id"].astype(str).str.replace("ASX:", "", regex=False))

    # scope by MARKET as well as ticker. ASX:ALF (Australian Leaders Fund)
    # and the UK's Alternative Liquidity Fund both answer to "ALF", and
    # ASX:SEC and Strategic Equity Capital both to "SEC" - but the price
    # layer asks for ALF.AX and ALF.L, which are different instruments on
    # different exchanges. Grouping on the bare ticker reported four
    # conflicts that cannot happen and would have suppressed four live
    # funds' signals for a collision in a namespace nothing shares.
    reg["_mkt"] = reg.get("market", pd.Series("", index=reg.index)).fillna("")
    reg["_key"] = reg["_mkt"] + "|" + reg["ticker"].fillna("")

    alive = reg["status"].isin(live_statuses)
    reg["_alive"] = alive
    # last_seen is YYYY-MM; a missing one sorts oldest so it can never
    # displace a security with a real span
    reg["_last"] = reg.get("last_seen", pd.Series("", index=reg.index)).fillna("")

    status, reason, inc_id, inc_name, n_claim = [], [], [], [], []
    groups = {k: g for k, g in reg[reg["ticker"].notna()].groupby("_key")}

    def _name_key(v) -> str:
        return "".join(ch for ch in str(v or "").lower() if ch.isalnum())

    for r in reg.to_dict("records"):
        t = r.get("ticker")
        if t is None or (isinstance(t, float) and pd.isna(t)) or not str(t).strip():
            status.append(STATUS_NO_TICKER)
            reason.append("no verified ticker for this security")
            inc_id.append(None); inc_name.append(None); n_claim.append(0)
            continue
        g = groups[r["_key"]]
        n_claim.append(int(len(g)))
        if len(g) == 1:
            status.append(STATUS_SOLE); reason.append("")
            inc_id.append(r["security_id"]); inc_name.append(r.get("name"))
            continue
        live_claims = g[g["_alive"]]
        top = g.sort_values("_last").iloc[-1]
        # among several LIVE claimants, exactly one grounded in the ISIN
        # (or stated by hand) owns the ticker; the name matches are its
        # other share classes / SEDOL vintages
        grounded = [str(x) for x in live_claims["security_id"]
                    if GROUNDED_METHOD.match(tmethod.get(str(x), ""))]
        if len(live_claims) > 1 and len(grounded) == 1:
            win = grounded[0]
            inc_id.append(win)
            inc_name.append(live_claims.loc[live_claims["security_id"].astype(str) == win, "name"].iloc[0]
                            if "name" in live_claims.columns else None)
            if str(r["security_id"]) == win:
                status.append(STATUS_INCUMBENT)
                reason.append(f"ticker {t} also claimed by {len(live_claims) - 1} live "
                              f"name-matched security(ies); this one is the ISIN-grounded "
                              f"resolution ({tmethod.get(win)})")
            else:
                status.append(STATUS_SUPERSEDED)
                reason.append(f"ticker {t} belongs to {win} by ISIN-grounded resolution "
                              f"({tmethod.get(win)}); this row is a name match")
            continue
        inc_id.append(str(top["security_id"]))
        inc_name.append(top.get("name"))
        if len(live_claims) > 1:
            others = ", ".join(
                f"{x['security_id']} ({x.get('name')}, to {x['_last']})"
                for _, x in live_claims.iterrows()
                if str(x["security_id"]) != str(r["security_id"]))
            same = len({_name_key(x) for x in live_claims.get("name", [])}) == 1
            status.append(STATUS_CONFLICT)
            reason.append(
                (f"ticker {t} claimed by {len(live_claims)} LIVE securities "
                 + ("with the SAME name - probably one company's share-class "
                    "or SEDOL vintages, but which row the quote belongs to is "
                    "still unsettled" if same else
                    "with DIFFERENT names - a reused ticker; pricing the wrong "
                    "one staples another company's quote to this fund")
                 + f" - {others}"))
        elif str(top["security_id"]) == str(r["security_id"]):
            status.append(STATUS_INCUMBENT)
            reason.append(f"ticker {t} also claimed by {len(g) - 1} older "
                          "security(ies); this one was listed most recently")
        else:
            status.append(STATUS_SUPERSEDED)
            reason.append(
                f"ticker {t} now belongs to {top['security_id']} "
                f"({top.get('name')}, listed to {top['_last']}); a live quote "
                f"for {t} is that security's, not this one's")

    reg["identity_status"] = status
    reg["identity_reason"] = reason
    reg["identity_incumbent_id"] = inc_id
    reg["identity_incumbent_name"] = inc_name
    reg["identity_claimants"] = n_claim
    reg["identity_ok"] = reg["identity_status"].isin(PRICEABLE)
    reg["identity_same_name"] = reg["identity_reason"].str.contains(
        "SAME name", na=False)
    return reg.drop(columns=["_alive", "_last", "_mkt", "_key"])


def priceable_symbols(identity: pd.DataFrame, suffix: str) -> dict[str, str]:
    """{security_id: symbol} for securities whose ticker really is theirs.

    A superseded or conflicted security is simply not asked for - which is
    the only reliable place to stop a reused ticker, because once a price
    has been fetched nothing downstream can tell it apart from a real one.
    """
    out = {}
    for r in identity.itertuples(index=False):
        if getattr(r, "identity_ok", False) and getattr(r, "ticker", None):
            out[str(r.security_id)] = f"{r.ticker}{suffix}"
    return out


def conflict_queue(identity: pd.DataFrame) -> pd.DataFrame:
    """Every unresolved identity, for review. Nothing is auto-resolved."""
    cols = ["security_id", "name", "market", "ticker", "status",
            "first_seen", "last_seen", "identity_status", "identity_reason",
            "identity_incumbent_id", "identity_incumbent_name",
            "identity_claimants"]
    q = identity[identity["identity_status"].isin(
        (STATUS_CONFLICT, STATUS_SUPERSEDED))]
    have = [c for c in cols if c in q.columns]
    return q[have].sort_values(
        ["identity_status", "ticker"], na_position="last").reset_index(drop=True)
