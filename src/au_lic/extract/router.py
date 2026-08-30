"""Decide which announcements actually need a model.

Measured on the real index (86,692 announcements): the corpus is dominated by
a small number of highly regular document types. 9,772 rows are
"Daily share buy-back notice - Appendix 3E"; 6,744 are "Net Tangible Asset
Backing". These are prescribed ASX forms and standing report formats with
fixed fields - reading them with an LLM would be paying frontier-model prices
to parse a form we can already parse, 86,692 times.

So each announcement is routed by headline alone (free - the index carries
headlines, no PDF is fetched to decide):

  skip           no backtest value at any price. A director's interest notice
                 does not inform a discount or catalyst study.
  deterministic  a prescribed form or standing format. Parsed in Python; the
                 NTA family already has an evidence-tested parser.
  llm            genuinely narrative: corporate actions, scheme booklets,
                 manager changes, meeting results. This is where the value is
                 and where rules do not reach.
  llm_audit      a random sample of the deterministic route, sent to the model
                 anyway so the cheap path's error rate is MEASURED rather than
                 assumed. Without this the saving is unquantified faith.

A deterministic parse that fails or returns an ambiguous value is escalated to
the llm route at extraction time - the router sets the intent, the parser's
result decides the outcome. Absence still has to mean "published nothing
parseable", never "we routed it away and never looked".
"""

from __future__ import annotations

import re
import zlib

import pandas as pd

# Order matters: the first family matching a headline wins, so specific
# corporate-action language is tested before the generic report formats it
# often appears inside.
FAMILIES: list[tuple[str, str, re.Pattern]] = [
    # -- narrative corporate actions: the reason this dataset exists --------
    ("corporate_action", "llm", re.compile(
        r"scheme of arrangement|schemebooklet|scheme booklet|takeover|bidder'?s "
        r"statement|target'?s statement|off-?market bid|merger|wind[\s-]?up|"
        r"winding[\s-]?up|liquidat|delist|removal from (?:the )?official list|"
        r"strategic review|strategic alternat|restructure|restructuring|"
        r"internalis|externalis|management agreement|investment management "
        r"agreement|change of (?:responsible entity|manager|investment manager)|"
        r"new investment manager|manager transition|fee (?:reduction|change|waiver)|"
        r"continuation (?:vote|resolution)|conversion to (?:an? )?(?:etf|open)|"
        r"open[\s-]?end|discount (?:control|management)|capital return|"
        r"return of capital|tender offer|equal access|selective buy-?back|"
        r"section 249d|s249d|requisition|board spill|proportional takeover",
        re.I)),
    # meeting results can carry a continuation vote or a wind-up resolution
    ("meeting", "llm", re.compile(
        r"results of (?:the )?(?:annual |extraordinary |general )*meeting|"
        r"meeting results|notice of (?:general|"
        r"extraordinary|annual general) meeting|notice of meeting|"
        r"explanatory (?:memorandum|statement)|proxy form|scheme meeting", re.I)),
    # narrative periodic reporting - performance commentary, manager letters
    ("narrative_report", "llm", re.compile(
        r"annual report|half[\s-]?year(?:ly)? (?:report|result)|"
        r"chairman'?s address|ceo'?s address|manager'?s (?:report|letter)|"
        r"investor (?:presentation|letter|update)|agm presentation|"
        r"quarterly (?:report|update|letter)|appendix 4[cde]\b|"
        r"preliminary final report|statutory accounts|half year accounts|"
        r"financial report|director appointment|board appointment", re.I)),

    # -- prescribed forms and standing formats ------------------------------
    ("nta", "deterministic", re.compile(
        r"net tangible asset|\bnta\b|net asset value|\bnav\b|fund update|"
        r"investment update|portfolio value|performance (?:update|estimate)|"
        r"month to date performance|month-to-date performance|"
        r"investment return|weekly estimate of performance", re.I)),
    ("buyback_daily", "deterministic", re.compile(
        r"appendix 3[cde]\b|daily share buy-?back|share buy-?back notice|"
        r"buy-?back notification|notification of buy-?back|"
        r"announcement of buy-?back|changes relating to buy-?back|"
        r"on-?market buy-?back", re.I)),
    # Form 484 / cancellations: prescribed ASIC form, and the share count is
    # needed to keep per-share history valid
    ("share_cancellation", "deterministic", re.compile(
        r"form 484|cancellation of (?:shares|units|buy-?back)|"
        r"share cancellation|notification of cancellation|"
        r"cancellation of securities", re.I)),
    ("dividend", "deterministic", re.compile(
        r"appendix 3a|dividend/?distribution|distribution announcement|"
        r"notification of dividend|dividend announcement|drp |"
        r"dividend reinvestment|fund payment notice|"
        r"notification of estimated distribution|distribution estimate", re.I)),
    ("issue", "deterministic", re.compile(
        r"appendix 3b|appendix 2a|application for quotation|"
        r"proposed issue of securities|cleansing (?:notice|statement)", re.I)),
    ("substantial_holder", "deterministic", re.compile(
        r"substantial (?:holding|shareholder|holder)|form 60[345]|"
        r"becoming a substantial|ceasing to be a substantial|"
        r"change in substantial", re.I)),

    # -- no backtest value at any price -------------------------------------
    ("director_interest", "skip", re.compile(
        r"director'?s? interest|appendix 3[xyz]\b|initial director", re.I)),
    ("governance_admin", "skip", re.compile(
        r"appendix 4g|corporate governance|annual report to shareholders - "
        r"corporate governance|constitution|securities trading policy|"
        r"company secretary|registered office|change of (?:address|auditor)|"
        r"annual general meeting date|top 20|distribution schedule|"
        r"annual tax statement|tax component|amit |attribution", re.I)),
    ("marketing_admin", "skip", re.compile(
        r"investor (?:forum|conference|webinar|briefing)|webinar|"
        r"product disclosure statement|\bpds\b|prospectus|"
        r"share trading policy|securities trading policy|key dates|"
        r"units on issue|monthly redemption|mfund|"
        r"change of (?:unit |share )?registry|invitation to", re.I)),
    ("index_admin", "skip", re.compile(
        r"trading halt|suspension from (?:official )?quotation|reinstatement to "
        r"official quotation|pause in trading|cessation of securities|"
        r"appendix 3g|notification regarding unquoted|change of share registry",
        re.I)),
]

AUDIT_RATE = 0.02          # 2% of the deterministic route, for error measurement


def classify(headline: str) -> tuple[str, str]:
    """(family, route) for one headline."""
    h = (headline or "").strip()
    if not h:
        return "unknown", "llm"       # unlabelled is not the same as worthless
    for family, route, pat in FAMILIES:
        if pat.search(h):
            return family, route
    return "unclassified", "llm"      # a rule that does not match must not skip


def route_index(idx: pd.DataFrame, audit_rate: float = AUDIT_RATE) -> pd.DataFrame:
    """Add family/route columns to the announcement index.

    The audit sample is chosen by a hash of the announcement id, so it is
    stable across runs: the same documents are audited every time, which is
    what makes the measured error rate comparable run to run.
    """
    out = idx.copy()
    fam_route = [classify(h) for h in out["headline"].fillna("")]
    out["family"] = [f for f, _ in fam_route]
    out["route"] = [r for _, r in fam_route]
    if audit_rate > 0:
        ids = out["id"].astype(str)
        pick = ids.map(lambda i: (zlib.crc32(b"audit:" + i.encode()) % 10000)
                       < audit_rate * 10000)
        out.loc[pick & (out["route"] == "deterministic"), "route"] = "llm_audit"
    return out


def summarise(routed: pd.DataFrame) -> dict:
    total = int(len(routed))
    by_route = routed["route"].value_counts().to_dict()
    llm = int(by_route.get("llm", 0) + by_route.get("llm_audit", 0))
    return {
        "total": total,
        "by_route": {k: int(v) for k, v in by_route.items()},
        "by_family": {k: int(v) for k, v in
                      routed["family"].value_counts().items()},
        "llm_documents": llm,
        "llm_share": round(llm / max(1, total), 4),
        "deterministic_share": round(
            int(by_route.get("deterministic", 0)) / max(1, total), 4),
        "skip_share": round(int(by_route.get("skip", 0)) / max(1, total), 4),
        "unclassified_examples": routed.loc[routed["family"] == "unclassified",
                                            "headline"].head(25).tolist(),
    }
