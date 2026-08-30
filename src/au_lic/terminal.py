"""What a delisted fund was actually worth at the end.

A fund that leaves the exchange did not go to zero. It was schemed for cash,
taken over for scrip, or wound up and paid out - usually in several
instalments over months. Treating a delisting as -100% is the single largest
distortion available to this study, and it points the wrong way twice: it
punishes the wide-discount cohort hardest (those are the funds that get
wound up or bid for) and it makes the cheap strategy look far worse than it
was, so the error hides behind a plausible-looking result.

Treating it as 0% is no better - it silently drops the payoff that a
discount-narrowing thesis actually predicts.

So terminal value is EXTRACTED from the fund's own final announcements, and
where it cannot be recovered the observation is marked unrecoverable and the
final holding period is excluded from returns. Excluded, not zeroed, not
imputed: an unknown terminal value is missing data, and this project does not
fill missing data with guesses.

Exit types, in the order they are searched:

  scheme_cash        cash consideration per share, stated in the scheme
  takeover_cash      cash offer per share, declared unconditional
  scheme_scrip       acquirer shares - flagged, valued only if the document
                     states an implied value, never inferred from a price we
                     would have to look up later
  wind_up            liquidator distributions, summed WITH their dates
  final_nta          last published NTA, a proxy of last resort and labelled
                     as one
  unrecoverable      nothing stated; excluded from returns, never -100%
"""

from __future__ import annotations

import re

import pandas as pd

MONEY = r"\$\s*([0-9]+(?:\.[0-9]+)?)"
CENTS = r"([0-9]+(?:\.[0-9]+)?)\s*(?:cents|cps)"
# The gap between a label and its number. Two traps, both of which silently
# understate a terminal value - the direction that makes a wound-up fund look
# like a disaster:
#   - a plain [^.$] class blocks sentence ends AND decimal points, so
#     "62.5 cents per share" matched only the tail "5 cents" -> 0.05.
#   - greedy matching eats "62." and leaves "5 cents" for the same result.
# So: allow a dot only when a digit follows, and match lazily.
GAP = r"(?:[^.$]|\.(?=[0-9])){0,80}?"

SCHEME_CASH = re.compile(
    r"(?:scheme consideration|cash consideration|consideration of|"
    r"entitled to receive|will receive)" + GAP + r"(?:" + MONEY + r"|" + CENTS +
    r")\s*(?:in cash\s*)?(?:per|for each)\s*(?:share|security|unit)", re.I)
TAKEOVER_CASH = re.compile(
    r"(?:offer price|offer of|bid of|offering)" + GAP + r"(?:" + MONEY + r"|" +
    CENTS + r")\s*(?:cash\s*)?(?:per|for each)\s*(?:share|security|unit)", re.I)
SCRIP = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:new\s+)?(?:[A-Z]{2,6}\s+)?(?:shares?|securities)"
    r"\s*(?:in|of)?\s*[^.]{0,40}for (?:each|every)\s*(?:[0-9.]+\s*)?"
    r"(?:share|security|unit)", re.I)
CAPITAL_RETURN = re.compile(
    r"(?:capital return|return of capital|liquidat(?:or|ion)'?s? distribution|"
    r"distribution to (?:share|unit)holders|initial distribution|"
    r"final distribution|interim distribution)" + GAP + r"(?:" + MONEY + r"|" +
    CENTS + r")\s*(?:per|for each)\s*(?:share|security|unit)", re.I)
IMPLIED_VALUE = re.compile(
    r"implied value" + GAP + r"(?:" + MONEY + r"|" + CENTS + r")", re.I)


def _dollars(m: re.Match | None) -> float | None:
    """Group 1 is dollars, group 2 is cents - normalise to dollars."""
    if not m:
        return None
    if m.group(1):
        try:
            return float(m.group(1))
        except ValueError:
            return None
    if m.lastindex and m.group(2):
        try:
            return float(m.group(2)) / 100.0
        except ValueError:
            return None
    return None


def extract_terminal(text: str, headline: str = "") -> dict | None:
    """Terminal value per share from one end-of-life announcement."""
    v = _dollars(SCHEME_CASH.search(text))
    if v is not None:
        return {"exit_type": "scheme_cash", "value_per_share": v,
                "basis": "stated_scheme_consideration"}
    v = _dollars(TAKEOVER_CASH.search(text))
    if v is not None:
        return {"exit_type": "takeover_cash", "value_per_share": v,
                "basis": "stated_offer_price"}
    v = _dollars(CAPITAL_RETURN.search(text))
    if v is not None:
        return {"exit_type": "wind_up", "value_per_share": v,
                "basis": "stated_distribution"}
    if SCRIP.search(text):
        # scrip is only valued when the document itself states the implied
        # value; deriving it from an acquirer price we look up afterwards
        # would import a later observation into a point-in-time record
        iv = _dollars(IMPLIED_VALUE.search(text))
        return {"exit_type": "scheme_scrip",
                "value_per_share": iv,
                "basis": "stated_implied_value" if iv is not None
                         else "scrip_unvalued",
                "requires_acquirer_price": iv is None}
    return None


def build_terminal_values(events: pd.DataFrame, final_nta: pd.DataFrame | None = None
                          ) -> pd.DataFrame:
    """One terminal record per delisted fund.

    Wind-ups pay in instalments, so every distribution is kept WITH its date
    rather than collapsed to a single number: three payments over eighteen
    months is a materially different return from one lump on the delisting
    date, and only the dated form can be discounted properly.
    """
    if events is None or events.empty:
        return pd.DataFrame()
    ev = events.copy()
    ev["published_at"] = pd.to_datetime(ev["published_at"], errors="coerce")
    out = []
    for ticker, g in ev.groupby("ticker"):
        g = g.sort_values("published_at")
        dists = g[g["exit_type"] == "wind_up"]
        decisive = g[g["exit_type"].isin(("scheme_cash", "takeover_cash"))]
        scrip = g[g["exit_type"] == "scheme_scrip"]
        if len(decisive):
            r = decisive.iloc[-1]
            out.append({"ticker": ticker, "exit_type": r["exit_type"],
                        "terminal_value_per_share": r["value_per_share"],
                        "payments": [{"date": str(r["published_at"].date()),
                                      "amount": r["value_per_share"]}],
                        "basis": r["basis"], "status": "recovered"})
        elif len(dists):
            payments = [{"date": str(d.date()), "amount": float(a)}
                        for d, a in zip(dists["published_at"], dists["value_per_share"])
                        if pd.notna(a)]
            out.append({"ticker": ticker, "exit_type": "wind_up",
                        "terminal_value_per_share": sum(p["amount"] for p in payments),
                        "payments": payments, "basis": "sum_of_stated_distributions",
                        "status": "recovered"})
        elif len(scrip):
            r = scrip.iloc[-1]
            recovered = pd.notna(r["value_per_share"])
            out.append({"ticker": ticker, "exit_type": "scheme_scrip",
                        "terminal_value_per_share": r["value_per_share"],
                        "payments": [], "basis": r["basis"],
                        "status": "recovered" if recovered else "scrip_unvalued"})
        else:
            out.append({"ticker": ticker, "exit_type": "unrecoverable",
                        "terminal_value_per_share": None, "payments": [],
                        "basis": None, "status": "unrecoverable"})

    df = pd.DataFrame(out)
    # last-resort proxy, always labelled as a proxy so a result can be re-run
    # without it
    if final_nta is not None and len(final_nta) and len(df):
        nta = final_nta.set_index("ticker")["nav_per_share"].to_dict()
        need = df["status"].isin(("unrecoverable", "scrip_unvalued"))
        df.loc[need & df["ticker"].isin(nta), "terminal_value_per_share"] = \
            df.loc[need & df["ticker"].isin(nta), "ticker"].map(nta)
        df.loc[need & df["ticker"].isin(nta), "basis"] = "final_published_nta_proxy"
        df.loc[need & df["ticker"].isin(nta), "status"] = "proxy_final_nta"
    return df


def apply_to_returns(terminal: pd.DataFrame) -> pd.DataFrame:
    """Mark which funds may contribute a final-period return.

    A fund whose terminal value could not be recovered is EXCLUDED from the
    final holding period - not carried at zero, not written down to -100%,
    and not filled with a guess. Missing stays missing; the exclusion is
    visible and countable, so its effect on any result can be bounded.
    """
    if terminal is None or terminal.empty:
        return terminal
    t = terminal.copy()
    t["usable_for_returns"] = t["status"].isin(("recovered", "proxy_final_nta"))
    t["exclusion_reason"] = None
    t.loc[~t["usable_for_returns"], "exclusion_reason"] = \
        t.loc[~t["usable_for_returns"], "status"]
    return t
