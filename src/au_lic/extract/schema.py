"""Controlled vocabularies for ASX announcement extraction.

Every enum here is copied from config/prompts/asx_extraction_v1.md. The prompt
tells the model what is allowed; this module is what actually decides. A value
outside these sets is a rejected field, not a new category - otherwise the
vocabulary drifts silently and the backtest ends up grouping on labels that
mean different things in different years.
"""

from __future__ import annotations

SECTIONS = ("nav_observations", "performance_observations", "distribution_events",
            "manager_events", "fee_events", "catalyst_events",
            "capital_structure_events", "fund_structure_events",
            "other_material_events")

PRIMARY_DOCUMENT_TYPE = {
    "nta_report", "monthly_report", "quarterly_report", "annual_report",
    "half_year_report", "performance_update", "dividend_announcement",
    "buyback_announcement", "capital_management", "strategic_review",
    "takeover_or_merger", "manager_change", "fee_change", "board_change",
    "shareholder_activism", "meeting_notice", "meeting_results", "capital_raising",
    "portfolio_update", "substantial_holder", "fund_restructure",
    "windup_or_liquidation", "other"}

NAV_BASIS = {"pre_tax", "post_tax", "after_realisation_tax", "before_realisation_tax",
             "net_asset_value", "net_tangible_assets", "other", "unknown"}

MEASUREMENT_PERIOD = {"1_month", "3_month", "6_month", "1_year", "2_year", "3_year",
                      "5_year", "10_year", "since_inception", "financial_year_to_date",
                      "calendar_year_to_date", "other"}

RETURN_BASIS = {"portfolio_before_fees", "portfolio_after_fees", "nav_total_return",
                "nta_total_return", "investment_portfolio", "shareholder_total_return",
                "unknown"}

DISTRIBUTION_TYPE = {"ordinary_dividend", "special_dividend", "distribution",
                     "capital_return", "other"}

MANAGER_EVENT = {"manager_appointed", "manager_terminated", "manager_resigned",
                 "manager_replaced", "management_internalised", "management_externalised",
                 "management_contract_extended", "management_contract_terminated", "other"}

FEE_EVENT = {"base_fee_reduction", "base_fee_increase", "performance_fee_reduction",
             "performance_fee_increase", "performance_fee_removed",
             "fee_structure_changed", "management_agreement_changed", "other"}

CATALYST_TYPE = {
    "on_market_buyback", "off_market_buyback", "tender_offer", "capital_return",
    "special_dividend", "strategic_review", "portfolio_realisation", "windup_proposal",
    "windup_approved", "liquidation", "takeover_offer", "scheme_of_arrangement",
    "merger", "fund_conversion", "open_end_conversion", "etf_conversion",
    "delisting_proposal", "manager_change", "manager_termination",
    "management_internalisation", "fee_reduction", "continuation_vote",
    "discount_control_mechanism", "shareholder_activism", "board_spill",
    "board_restructure", "asset_sale", "capital_raising", "rights_issue",
    "placement", "other"}

EVENT_STAGE = {"proposed", "announced", "under_review", "recommended", "approved",
               "commenced", "in_progress", "extended", "amended", "completed",
               "rejected", "withdrawn", "cancelled", "failed", "unknown"}

EVENT_STATUS = {"active", "completed", "cancelled", "failed", "superseded", "unknown"}

CAPITAL_STRUCTURE_EVENT = {"share_split", "share_consolidation", "bonus_issue",
                           "rights_issue", "entitlement_offer", "placement",
                           "share_purchase_plan", "capital_return", "share_cancellation",
                           "buyback_execution", "other"}

FUND_STRUCTURE_EVENT = {"lic_to_lit", "lit_to_lic", "closed_end_to_open_end",
                        "etf_conversion", "trust_restructure", "merger", "demerger",
                        "delisting", "windup", "mandate_change", "benchmark_change",
                        "other"}

PARSE_QUALITY = {"excellent", "good", "poor", "unusable"}

# field -> allowed set, per section
ENUM_FIELDS: dict[str, dict[str, set[str]]] = {
    "nav_observations": {"nav_basis": NAV_BASIS},
    "performance_observations": {"measurement_period": MEASUREMENT_PERIOD,
                                 "return_basis": RETURN_BASIS},
    "distribution_events": {"event_type": DISTRIBUTION_TYPE},
    "manager_events": {"event_type": MANAGER_EVENT},
    "fee_events": {"event_type": FEE_EVENT},
    "catalyst_events": {"catalyst_type": CATALYST_TYPE, "event_stage": EVENT_STAGE,
                        "event_status": EVENT_STATUS},
    "capital_structure_events": {"event_type": CAPITAL_STRUCTURE_EVENT},
    "fund_structure_events": {"event_type": FUND_STRUCTURE_EVENT},
    "other_material_events": {},
}

# Dates that record WHEN SOMETHING BECAME KNOWN. These may never postdate
# published_at - that would be lookahead. Dates recording when something takes
# EFFECT (effective_date, payment_date, ex_date, end_date...) are legitimately
# in the future and are deliberately absent from this set.
KNOWLEDGE_DATE_FIELDS = {"valuation_date", "period_end", "announcement_date",
                         "reporting_period_end"}

# The spec's ABSOLUTE RULE, as a matcher. Any key anywhere in the output whose
# name contains one of these is a computed signal the extractor must never emit;
# they are derived later, in Python, from the facts.
FORBIDDEN_KEY_SUBSTRINGS = (
    "discount_to_nav", "discount_pct", "discount_z", "z_score", "zscore",
    "manager_quality", "manager_score", "quality_score", "catalyst_score",
    "catalyst_strength", "expected_return", "attractive", "recommendation",
    "future_return", "forward_return", "signal", "rating", "rank", "premium_discount",
)

MIN_CONFIDENCE = 0.70
