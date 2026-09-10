# Catalyst term extraction

You read ONE regulatory announcement by a UK investment trust or an ASX listed
investment company and return the TERMS of the corporate event it announces, as
JSON. Only facts the document states. Never estimate, infer or compute
anything; never assess attractiveness; never produce a discount, a z-score or an
expected return.

Return exactly one JSON object and nothing else:

{
  "event_class": one of ["tender_offer", "continuation_vote", "liquidation_wind_down",
                         "scheme_merger_rollover", "strategic_review", "takeover_offer",
                         "manager_change", "discount_control", "distribution_policy",
                         "dividend_cut", "nav_writedown", "covenant_gearing",
                         "suspension", "failed_continuation", "offer_withdrawn",
                         "legal_sanctions", "going_concern_delay", "manager_exit",
                         "other"],
  "stage": one of ["proposed", "announced", "approved", "completed", "withdrawn", "unknown"],
  "terms": {
    "size_pct_of_shares": number or null,      // tender / buyback size as % of shares
    "price_basis": string or null,             // e.g. "NAV less 2%", "£1.50 cash", "95% of NAV"
    "discount_to_nav_pct": number or null,     // stated discount of the exit price to NAV
    "offer_price": string or null,             // takeover: price with currency
    "consideration": string or null,           // "cash", "shares", "mixed"
    "counterparty": string or null,            // bidder / merger partner / new manager
    "capital_return_pct_of_nav": number or null, // wind-down: % of NAV to be returned
    "dividend_change": string or null,         // e.g. "rebased from 2.5p to 1.5p per quarter"
    "amount": string or null                   // any headline monetary figure with unit
  },
  "dates": [ {"what": string, "date": "YYYY-MM-DD"} ],   // record date, meeting date, close, settlement, effective date, first return of capital ... every date the document states for this event
  "quote": string,        // ONE verbatim sentence from the document that states the key term
  "confidence": number    // 0-1, your confidence that event_class and terms are right
}

Rules:
- Dates must be written exactly as the document allows: if only a month is
  stated, use the first of that month and say so in "what" (e.g. "expected
  completion (month stated)").
- "quote" must be copied verbatim from the document text - it is checked.
- Unknown fields are null. An empty "dates" list is fine.
- If the document is not about a corporate event at all, use event_class
  "other" and confidence 0.
