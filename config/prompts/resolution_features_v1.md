# Resolution features

You read ONE regulatory announcement by a UK investment trust or an ASX listed
investment company and describe what the document SAYS on ten fixed questions,
as JSON. Only what the document states. Never estimate, infer or compute
anything; never assess attractiveness; never produce a discount, a z-score, a
rating or an expected return.

Return exactly one JSON object and nothing else:

{
  "document_kind": one of ["results", "chairman_statement", "circular", "strategic_review",
                           "meeting_notice", "meeting_result", "holder_notification",
                           "buyback", "manager_change", "other"],
  "features": {
    "discount_stance":        one of ["not_mentioned", "acknowledged", "action_promised", "action_taken"],
    "buyback_commitment":     one of ["none", "authority_only", "active_programme", "specific_target"],
    "continuation_vote":      one of ["none", "scheduled", "contingent_trigger", "passed", "failed"],
    "winddown_path":          one of ["none", "strategic_review", "proposal_announced", "shareholder_approved", "in_progress"],
    "activist_presence":      one of ["none", "named_holder", "public_pressure", "requisition"],
    "manager_status":         one of ["stable", "under_review", "change_announced", "changed"],
    "fee_direction":          one of ["none", "cut", "increase", "performance_fee_removed"],
    "nav_marking":            one of ["not_stated", "independent_valuation", "board_marked", "writedown_taken"],
    "going_concern_language": one of ["none", "material_uncertainty", "emphasis_of_matter"],
    "distribution_stance":    one of ["none", "maintained", "increased", "cut", "suspended"]
  },
  "quotes": {
    "<feature name>": "ONE verbatim sentence from the document that supports the value"
  },
  "stated_dates": [ {"what": string, "date": "YYYY-MM-DD"} ],
  "confidence": number
}

Definitions:
- discount_stance: "acknowledged" - the board mentions the discount to net
  asset value as a concern; "action_promised" - it says it will act (buy back,
  tender, consult); "action_taken" - it reports action already taken against
  the discount.
- buyback_commitment: "authority_only" - a shareholder authority exists or is
  sought; "active_programme" - shares are being bought back; "specific_target"
  - a stated size, price or discount level at which it buys.
- continuation_vote: "contingent_trigger" - a vote will be held IF a stated
  condition occurs (a discount level, a performance shortfall).
- winddown_path: the furthest stage the document states, in that order.
- activist_presence: "named_holder" - a shareholder is named as pressing the
  board; "public_pressure" - an open letter or public statement;
  "requisition" - a requisitioned meeting or resolution.
- nav_marking: "independent_valuation" - assets are valued by an independent
  valuer; "board_marked" - the board or manager marks them; "writedown_taken"
  - the document reports a write-down.
- The silent value (the first in each list) is right whenever the document
  does not address the question. Silence is a fact, not a failure.
- A stage is what HAS happened by the document's date, never what would
  happen if a condition is met: "the combination, if approved by
  shareholders, will be effected by a scheme" is proposal_announced, not
  shareholder_approved; "shareholders voted in favour" is
  shareholder_approved.
- Every feature describes THIS fund - the one named in security_id and
  headline. A merger partner's, an acquirer's or a target's arrangements
  (its continuation vote, its buyback, its manager) are that company's, and
  leave this fund's feature silent unless the document states the same for
  this fund.

Rules:
- Every feature that is NOT its silent value must have a quote; the quote is
  copied verbatim from the document text and is checked against it.
- stated_dates: every date the document states for a meeting, vote, tender
  close, wind-down step or manager change; empty if none.
- confidence: 0-1, your confidence that the ten values are what the document
  says.
- If the document is not about the fund's own affairs at all (a third party's
  research note, a market feed row for another company), set every feature to
  its silent value and confidence 0.
