"""The fixed feature vocabulary the learning layer reads from announcements.

Ten pre-specified judgments, each an enum, each carrying one verbatim
quote. The prompt (config/prompts/resolution_features_v1.md) tells the
model what is allowed; this module decides. An out-of-set value is a
rejected field, not a new category, so the vocabulary cannot drift across
years of corpus.

Nothing here is a signal. Discount, z-score, expected return and the like
are FORBIDDEN keys at any depth (reusing the ASX extraction's matcher):
the features describe what the fund said, and the honest framework decides
what that was worth.
"""

from __future__ import annotations

from au_lic.extract.schema import FORBIDDEN_KEY_SUBSTRINGS  # noqa: F401

FEATURES: dict[str, tuple[str, ...]] = {
    # the board's stated stance on its own discount
    "discount_stance": ("not_mentioned", "acknowledged", "action_promised",
                        "action_taken"),
    # buyback: an authority is not a programme, a programme is not a target
    "buyback_commitment": ("none", "authority_only", "active_programme",
                           "specific_target"),
    "continuation_vote": ("none", "scheduled", "contingent_trigger", "passed",
                          "failed"),
    # the path to an ending, in order
    "winddown_path": ("none", "strategic_review", "proposal_announced",
                      "shareholder_approved", "in_progress"),
    "activist_presence": ("none", "named_holder", "public_pressure",
                          "requisition"),
    "manager_status": ("stable", "under_review", "change_announced", "changed"),
    "fee_direction": ("none", "cut", "increase", "performance_fee_removed"),
    # how the NAV is marked: the credibility of the number the discount is
    # measured against
    "nav_marking": ("not_stated", "independent_valuation", "board_marked",
                    "writedown_taken"),
    "going_concern_language": ("none", "material_uncertainty",
                               "emphasis_of_matter"),
    "distribution_stance": ("none", "maintained", "increased", "cut",
                            "suspended"),
}

# the value each feature takes when a document says nothing about it
SILENT: dict[str, str] = {
    "discount_stance": "not_mentioned", "buyback_commitment": "none",
    "continuation_vote": "none", "winddown_path": "none",
    "activist_presence": "none", "manager_status": "stable",
    "fee_direction": "none", "nav_marking": "not_stated",
    "going_concern_language": "none", "distribution_stance": "none",
}

# an ordinal reading for the deterministic tests: "further along" is higher
ORDINAL: dict[str, dict[str, int]] = {
    k: {v: i for i, v in enumerate(vals)} for k, vals in FEATURES.items()}

MIN_CONFIDENCE = 0.6
MAX_DOC_CHARS = 60_000

DOCUMENT_KINDS = ("results", "chairman_statement", "circular", "strategic_review",
                  "meeting_notice", "meeting_result", "holder_notification",
                  "buyback", "manager_change", "other")
