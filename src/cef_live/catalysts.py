"""Daily catalyst scan - the reason to read announcements beyond NAV.

The research phase classified catalysts only over the historical backfill.
This reads the SAME announcement pages the NAV harvester already fetches
each night and classifies them against the fixed taxonomy from the build
brief, so a wind-down, tender or continuation vote is seen the day it is
announced rather than at the next monthly rebuild.

Taxonomy is fixed and pre-specified: adding a class is a config change
with a dated rationale, never a reaction to something we just missed.
Headlines that match nothing are ignored, not bucketed into "other" -
a catalyst we cannot name is not a catalyst we can act on.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pandas as pd

# (class, weight, pattern). Weight orders the digest: structural events that
# force a discount to close outrank soft signals.
CATALYST_CLASSES: list[tuple[str, int, re.Pattern]] = [
    ("liquidation_wind_down", 5, re.compile(
        r"winding[\s\-]?up|wind[\s\-]?down|liquidat|members'? voluntary|"
        r"managed\s+wind|realisation\s+(?:policy|opportunity)|orderly\s+realisation", re.I)),
    ("scheme_merger_rollover", 5, re.compile(
        r"scheme\s+of\s+arrangement|proposed\s+merger|recommended\s+(?:cash\s+)?(?:offer|merger)"
        r"|combination\s+with|rollover\s+option|reconstruction", re.I)),
    ("tender_offer", 4, re.compile(
        r"tender\s+offer|off[\s\-]market\s+buy[\s\-]?back|redemption\s+(?:offer|facility)"
        r"|exit\s+opportunity|return\s+of\s+capital|capital\s+return", re.I)),
    ("continuation_vote", 4, re.compile(
        r"continuation\s+(?:vote|resolution)|discontinuation\s+(?:vote|resolution)", re.I)),
    ("strategic_review", 3, re.compile(
        r"strategic\s+review|review\s+of\s+(?:strategy|options|the\s+company)"
        r"|formal\s+sale\s+process", re.I)),
    ("manager_change", 3, re.compile(
        r"change\s+of\s+(?:investment\s+)?manager|manager\s+(?:change|transition|appointment)"
        r"|termination\s+of\s+(?:the\s+)?(?:investment\s+)?management\s+agreement"
        r"|appointment\s+of\s+(?:new\s+)?investment\s+manager", re.I)),
    ("discount_control", 3, re.compile(
        r"discount\s+control|buy[\s\-]?back\s+(?:programme|program|authority|of\s+shares)"
        r"|share\s+buy[\s\-]?back|repurchase\s+of\s+(?:ordinary\s+)?shares", re.I)),
    ("substantial_holder", 2, re.compile(
        r"holding\(s\)\s+in\s+company|substantial\s+(?:holder|shareholder|holding)"
        r"|notification\s+of\s+major\s+(?:holdings|interest)|becoming\s+a\s+substantial", re.I)),
    # a disposal, sale or realisation of an investment: the fund's own NAV
    # tested against a price (Phase 3a layer 3, NAV credibility). Weight 1:
    # it never alerts on its own, the fund file and the why-line read it.
    ("realisation", 1, re.compile(
        r"\b(?:disposal|sale|realisation)\s+of\s+(?!own\s+shares|treasury"
        r"|(?:ordinary\s+|new\s+)?shares\s+(?:from|held\s+in|out\s+of)\s+treasury)"
        r"|completion\s+of\s+(?:the\s+)?(?:sale|disposal)\b|\bexit\s+from\b"
        r"|\bsells?\s+(?:its\s+)?(?:stake|interest|holding|investment)"
        r"|\bportfolio\s+(?:disposal|realisation)s?\b|\brealisation\s+of\b", re.I)),
    ("distribution_policy", 2, re.compile(
        r"dividend\s+policy|distribution\s+policy|revised\s+(?:dividend|distribution)"
        r"|target\s+dividend", re.I)),
]

# A takeover approach is the strongest discount-closing event there is;
# "no intention to bid" is its withdrawal and sits in the negative list.
TAKEOVER = ("takeover_offer", 5, re.compile(
    r"possible\s+offer|firm\s+intention|recommended\s+(?:cash\s+)?(?:offer|acquisition|"
    r"final\s+offer)|\boffer\s+for\s+(?!subscription)|rule\s+2\.7|statement\s+re(?:garding)?\s+"
    r"(?:possible\s+)?offer|offer\s+period|approach\s+from", re.I))

# NEGATIVE catalysts (Phase 3a, layer 2): the events that make a widening
# rational information repricing rather than a dislocation to buy. Weight
# is signed; magnitude orders the digest as for the positives. Evaluated
# BEFORE the positive list so "no intention to bid" is not read as an
# offer and "continuation vote not passed" is not read as a continuation
# vote.
NEGATIVE_CLASSES: list[tuple[str, int, re.Pattern]] = [
    ("suspension", -4, re.compile(
        r"^\s*suspension\s*[-\u2013:]|suspension\s+of\s+(?:trading|listing|shares|dealings)"
        r"|trading\s+suspended|shares?\s+suspended|temporary\s+suspension", re.I)),
    ("failed_continuation", -4, re.compile(
        r"continuation\s+(?:vote|resolution)\s+(?:not\s+passed|rejected|defeated|fails?|failed)"
        r"|resolution\s+not\s+passed|not\s+to\s+continue", re.I)),
    ("offer_withdrawn", -3, re.compile(
        r"no\s+intention\s+to\s+(?:bid|make\s+an\s+offer)|offer\s+(?:lapse|withdrawn|lapsed)"
        r"|termination\s+of\s+(?:possible\s+|potential\s+)?(?:offer|(?:reverse\s+)?takeover)"
        r"|potential\s+(?:reverse\s+)?takeover\s+terminat", re.I)),
    ("covenant_gearing", -3, re.compile(
        r"covenant\s+(?:breach|waiver|test)|breach\s+of\s+(?:covenant|banking|facility)"
        r"|lender\s+(?:waiver|standstill)|standstill\s+agreement|event\s+of\s+default"
        r"|default\s+under\s+(?:the\s+)?(?:facility|loan)", re.I)),
    ("dividend_cut", -3, re.compile(
        r"dividend\s+(?:cut|reduction|reduced|suspended|suspension|rebase|rebased|cancell)"
        r"|(?:reduction|reduce|suspension|cancellation)\s+(?:in|of)\s+(?:the\s+)?"
        r"(?:quarterly\s+|interim\s+|final\s+)?dividend|no\s+(?:interim|final)\s+dividend", re.I)),
    ("nav_writedown", -3, re.compile(
        r"write[\s\-]?downs?|impairment|valuation\s+(?:reduction|decline|adjustment)"
        r"|downward\s+(?:valuation|revaluation)|reduction\s+in\s+(?:the\s+)?(?:nav|net\s+asset\s+value)"
        r"|material\s+(?:reduction|fall)\s+in\s+(?:nav|net\s+asset\s+value)", re.I)),
    ("legal_sanctions", -3, re.compile(
        r"sanctions?\s+designation|litigation|legal\s+proceedings|dispute\s+with"
        r"|order\s+of\s+the\s+(?:royal\s+)?court|court\s+order|arbitration", re.I)),
    ("going_concern_delay", -2, re.compile(
        r"delay\s+(?:in|to)\s+(?:the\s+)?(?:publication\s+of\s+)?(?:accounts|results|annual\s+report"
        r"|financial\s+statements)|going\s+concern|material\s+uncertainty"
        r"|resignation\s+of\s+(?:the\s+)?auditor|auditor\s+resign", re.I)),
    ("manager_exit", -2, re.compile(
        r"(?:resignation|departure|retirement)\s+of\s+(?:the\s+)?(?:portfolio|fund|investment|lead)\s+manager"
        r"|(?:portfolio|fund|lead)\s+manager\s+(?:to\s+)?(?:depart|leave|step\s+down|resign)"
        r"|board\s+(?:&|and)\s+management\s+resignations|resignation\s+of\s+administrator", re.I)),
]

# Informational events: kept in the fund file with weight 0, never a
# catalyst on their own (an issue of equity is a premium fund's routine).
NEUTRAL_CLASSES: list[tuple[str, int, re.Pattern]] = [
    ("issuance", 0, re.compile(
        r"\bplacing\b|open\s+offer|offer\s+for\s+subscription"
        r"|issue\s+of\s+(?:new\s+)?(?:ordinary\s+)?(?:shares|equity)"
        r"|subscription\s+shares|c\s+share\s+issue|tap\s+issue", re.I)),
    # ASX: "Change in substantial holding for XYZ" is the fund notifying
    # ITS stake in XYZ - portfolio activity, not a holder of the fund.
    # "... from XYZ" is a holder of the fund and stays substantial_holder.
    ("portfolio_holding", 0, re.compile(
        r"substantial\s+(?:holder|holding)\s+(?:for|in)\s+[A-Z0-9]{2,5}\b")),
    ("holdings_notification", 0, re.compile(
        r"holding\(s\)\s+in\s+company|notification\s+of\s+major\s+(?:holdings|interest)"
        r"|\bTR-?1\b|substantial\s+(?:holder|holding)\s+notice", re.I)),
]

# Neutral classes that must win over a positive pattern they also match.
PRE_POSITIVE_NEUTRAL = {"portfolio_holding"}

# Routine filings that match a pattern above but carry no information -
# excluded so the digest stays worth reading.
NOISE = re.compile(r"total\s+voting\s+rights|transaction\s+in\s+own\s+shares"
                   r"|holding\(s\)\s+in\s+company\s*$", re.I)


def classify(headline: str) -> tuple[str, int] | None:
    """Return (class, weight) for the first POSITIVE taxonomy match, else None.

    Kept for the callers that gate on weight >= standalone_catalyst_weight;
    negatives never reach them. classify_signed carries the whole taxonomy.
    """
    got = classify_signed(headline)
    if got is None or got["weight"] <= 0:
        return None
    return got["class"], got["weight"]


def classify_signed(headline: str) -> dict | None:
    """{class, weight, direction} for the first taxonomy match, else None.

    Order: negatives, the takeover class, then the positive list; a bare
    buyback/holdings housekeeping filing is not an event; a holdings
    notification IS kept (weight 0) because the fund file counts them for
    holder churn.
    """
    h = headline or ""
    if not h.strip():
        return None
    for name, weight, pat in NEGATIVE_CLASSES:
        if pat.search(h):
            return {"class": name, "weight": weight, "direction": "negative"}
    if TAKEOVER[2].search(h):
        return {"class": TAKEOVER[0], "weight": TAKEOVER[1], "direction": "positive"}
    for name, weight, pat in NEUTRAL_CLASSES:
        if name in PRE_POSITIVE_NEUTRAL and pat.search(h):
            return {"class": name, "weight": weight, "direction": "neutral"}
    for name, weight, pat in CATALYST_CLASSES:
        if pat.search(h):
            if weight <= 3 and NOISE.search(h):
                break
            return {"class": name, "weight": weight, "direction": "positive"}
    for name, weight, pat in NEUTRAL_CLASSES:
        if pat.search(h):
            return {"class": name, "weight": weight, "direction": "neutral"}
    return None


def scan_rows(rows: list[dict], days: int = 30) -> pd.DataFrame:
    """Classify announcement rows.

    rows: dicts with security_id, date (ISO), headline, url.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    out = []
    for r in rows:
        d = str(r.get("date") or "")[:10]
        if d < cutoff:
            continue
        got = classify_signed(r.get("headline", ""))
        if got is None or got["weight"] == 0:
            continue          # neutral events live in the fund file, not here
        out.append({"security_id": r.get("security_id"), "date": d,
                    "catalyst_class": got["class"], "weight": got["weight"],
                    "direction": got["direction"],
                    "headline": (r.get("headline") or "")[:160],
                    "url": r.get("url")})
    df = pd.DataFrame(out)
    if len(df):
        df = df.sort_values(["weight", "date"], ascending=[False, False])
    return df


def scan_au(index_path: str, codes: set[str] | None = None,
            days: int = 30) -> pd.DataFrame:
    """Classify recent AU announcements from the committed market index -
    zero extra fetches."""
    from pathlib import Path
    p = Path(index_path)
    if not p.exists():
        return pd.DataFrame()
    idx = pd.read_parquet(p)
    if codes:
        idx = idx[idx["code"].isin(codes)]
    idx = idx.copy()
    idx["date"] = pd.to_datetime(idx["release_date"], utc=True,
                                 errors="coerce").dt.strftime("%Y-%m-%d")
    rows = [{"security_id": f"ASX:{r.code}", "date": r.date,
             "headline": r.headline, "url": r.url}
            for r in idx.itertuples(index=False)]
    return scan_rows(rows, days=days)


def summarise(df: pd.DataFrame) -> dict:
    if df is None or not len(df):
        return {"catalysts": 0, "funds": 0, "by_class": {}}
    return {"catalysts": int(len(df)),
            "funds": int(df["security_id"].nunique()),
            "by_class": df["catalyst_class"].value_counts().to_dict()}
