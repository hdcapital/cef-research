# Live fund coverage audit

Generated 2026-08-31T22:30:09+00:00 (as at 2026-08-31).

Run on demand only - this report has no schedule.

## Denominators

| Denominator | UK | ASX | Combined |
|---|--:|--:|--:|
| Registry universe (all vehicles ever listed) | 944 | 172 | 1116 |
| Registry-labelled live (aggregator) | 281 | 95 | 376 |
| Liveness-adjusted live (own filings) | 298 | 102 | 400 |
| Research eligible | 586 | 168 | 754 |
| Monitoring eligible (audit denominator) | 230 | 98 | 328 |
| Excluded | 714 | 74 | 788 |

## UK

```
Monitoring-eligible funds:     230
Fresh price:                   196 / 85.2%
Usable price:                  196 / 85.2%
Usable NAV:                    158 / 68.7%
Valid current discount:        145 / 63.0%
Discount history sufficient:   157 / 68.3%
CURRENT z-score:               132 / 57.4%
  (of which none needed:        25 within the estimate's own error band)
Identity unresolved:            11
Fully signal-ready:            109 / 47.4%

GREEN 109   AMBER 36   RED 85
```

| Price | n | % |
|---|--:|--:|
| Fresh | 196 | 85.2% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 26 | 11.3% |
| No price | 8 | 3.5% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 146 | 63.5% |
| Modelled / rolled forward | 25 | 10.9% |
| Stale | 63 | 27.4% |
| No usable NAV | 19 | 8.3% |
| NAV announcement held but no value parsed | 31 | 13.5% |
| ...of which the parser ran and failed | 31 | 13.5% |

## ASX

```
Monitoring-eligible funds:      98
Fresh price:                    95 / 96.9%
Usable price:                   95 / 96.9%
Usable NAV:                     89 / 90.8%
Valid current discount:         86 / 87.8%
Discount history sufficient:    85 / 86.7%
CURRENT z-score:                42 / 42.9%
  (of which none needed:        42 within the estimate's own error band)
Identity unresolved:             0
Fully signal-ready:             14 / 14.3%

GREEN 14   AMBER 72   RED 12
```

| Price | n | % |
|---|--:|--:|
| Fresh | 95 | 96.9% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 3 | 3.1% |
| No price | 0 | 0.0% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 56 | 57.1% |
| Modelled / rolled forward | 46 | 46.9% |
| Stale | 41 | 41.8% |
| No usable NAV | 1 | 1.0% |
| NAV announcement held but no value parsed | 66 | 67.3% |
| ...of which the parser ran and failed | 0 | 0.0% |

## COMBINED

```
Monitoring-eligible funds:     328
Fresh price:                   291 / 88.7%
Usable price:                  291 / 88.7%
Usable NAV:                    247 / 75.3%
Valid current discount:        231 / 70.4%
Discount history sufficient:   242 / 73.8%
CURRENT z-score:               174 / 53.0%
  (of which none needed:        67 within the estimate's own error band)
Identity unresolved:            11
Fully signal-ready:            123 / 37.5%

GREEN 123   AMBER 108   RED 97
```

| Price | n | % |
|---|--:|--:|
| Fresh | 291 | 88.7% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 29 | 8.8% |
| No price | 8 | 2.4% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 202 | 61.6% |
| Modelled / rolled forward | 71 | 21.6% |
| Stale | 104 | 31.7% |
| No usable NAV | 20 | 6.1% |
| NAV announcement held but no value parsed | 97 | 29.6% |
| ...of which the parser ran and failed | 31 | 9.5% |

## Why coverage is missing

| Issue | UK | ASX | Total |
|---|--:|--:|--:|
| NAV older than the amber window | 53 | 7 | 60 |
| No current z-score (within the error band) | 17 | 37 | 54 |
| Rolled-forward (modelled) NAV | 2 | 40 | 42 |
| Somewhat stale NAV | 8 | 34 | 42 |
| ASX monthly report NTA (no announcement route) | 0 | 31 | 31 |
| Stale historical panel price only | 26 | 3 | 29 |
| Insufficient z-score history | 16 | 12 | 28 |
| Suspected unit mismatch | 16 | 3 | 19 |
| No usable NAV | 10 | 1 | 11 |
| Ticker identity unresolved | 11 | 0 | 11 |
| NAV announcement found but unparsed | 9 | 0 | 9 |
| No price returned | 8 | 0 | 8 |
| Extreme discount/premium | 1 | 5 | 6 |
| Aggregator NAV only (no own route) | 3 | 0 | 3 |
| NAV is zero or negative | 0 | 1 | 1 |

## RED funds

| Market | Ticker | Fund | Blocking issue | Recommended fix |
|---|---|---|---|---|
| ASX | AIX | AI Private Opportunities Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| ASX | APL | Antipodes Global Investment Company Ltd | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | ALF | Australian Leaders Fund Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | BST | Barrack St Investments Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | BHD | Benjamin Hornigold Limited | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| ASX | DUI | Diversified United Investment Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | HCF | H&G High Conviction Limited | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| ASX | IBC | Ironbark Capital Limited | NAV is zero or negative | the NAV parsed as zero or negative, which is not a valuation - fix the source reader rather than the output |
| ASX | PAF | PM Capital Asian Opportunities Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | PMC | Platinum Capital Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | QRI | Qualitas Real Estate Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | TEK | Thorney Technologies Ltd | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | 3IN | 3i Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AEWU | AEW UK REIT | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AGVI | Aberforth Geared Value & Income Trust | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | AA4 | Amedeo Air Four Plus | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ATS | Artemis Alpha Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | AUGM | Augmentum Fintech | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | BHMU | BH Macro | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BSRT | Baker Steel Resources Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | THRG | BlackRock Throgmorton Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BSIF | Bluefield Solar Income Fund | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | CTPE | CT Private Equity Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | CVCE | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CVCG | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGI | Canadian General Investments | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CGL | Castelnau Group | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CBA | Ceiba Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SDV | Chelverton UK Dividend Trust | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | CHRY | Chrysalis Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | CRS | Crystal Amber Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | DPA | DP Aircraft I | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DGI9 | Digital 9 Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | DIVI | Diverse Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EJFI | EJF Investments | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | EAT | European Assets Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EOT | European Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FJV | Fidelity Japan Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FRGT | Franklin Global Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GABI | GCP Asset Backed Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GRIT | GRIT Investment Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GSF | Gore Street Energy Storage | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GRID | Gresham House Energy Storage | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | HET | Henderson European Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HINT | Henderson International Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOT | Henderson Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HGT | HgCapital Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | HOME | Home REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HGEN | Hydrogen Capital Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ICGT | ICG Enterprise Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LBOW | ICG-Longbow Senior Secured UK Property Debt Invest | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | INPP | International Public Partnerships | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | JIGI | JPMorgan India Growth & Income | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | JGC | Jupiter Green Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | KPC | Keystone Positive Change | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | LMS | LMS Capital | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LABS | Life Science REIT | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | LTI | Lindsell Train Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | LIVE | Living REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | MVI | Marwyn Value Investors | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | MHN | Menhaden Resource Efficiency | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MINI | Miton UK Microcap Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GROW | Molten Ventures | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NBPE | Neuberger Private Equity Partners | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | NESF | NextEnergy Solar Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PINT | Pantheon Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PIN | Pantheon International | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AEET | Parvus Energy Efficiency Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PPET | Patria Private Equity | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PSH | Pershing Square Holdings | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PCGH | Polar Capital Global Healthcare | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | PMGR | Premier Miton Global Renewables | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | RECI | Real Estate Credit Investments | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RGL | Regional REIT | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | TRIG | Renewables Infrastructure Group | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RESI | Residential Secure Income REIT | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | RIII | Rights & Issues Investment Trust | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | RSE | Riverstone Energy | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RICA | Ruffer Investment Company | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SVM | SVM UK Emerging Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SERE | Schroder European Real Estate | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SREI | Schroder Real Estate | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | INOV | Schroders Capital Global Innovation Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SEQI | Sequoia Economic Infrastructure Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SHRS | Shires Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SSON | Smithson Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SYNC | Syncona | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | BBOX | Tritax Big Box REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TFIF | TwentyFour Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SMIF | TwentyFour Select Monthly Income | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | UTL | UIL | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | ENRG | VH Global Energy Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VIP | Value and Indexed Property Income Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VEIL | Vietnam Enterprise Investments | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | WINV | Worsley Investors | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | ADIG | abrdn Diversified Income and Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ASLI | abrdn European Logistics Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |

## Excluded vehicles

788 vehicles are outside the monitoring universe. They keep their rows in the Excluded tab with the reason; none is deleted.

| Reason | n |
|---|--:|
| not_live:delisted | 360 |
| vct_excluded_by_research_policy | 235 |
| split_capital_excluded_by_research_policy|non_ordinary_share_class | 68 |
| not_live:delist_candidate | 66 |
| non_ordinary_share_class | 26 |
| non_sterling_quote | 18 |
| split_capital_excluded_by_research_policy | 8 |
| benchmark_index_not_a_fund | 4 |
| vct_excluded_by_research_policy|non_ordinary_share_class | 3 |

## How to read the verdicts

- **GREEN** - fresh, credible price and a published NAV in consistent units, with enough of the fund's own discount history to z-score. A live signal can be produced.
- **AMBER** - monitorable with a stated qualification: a rolled-forward or somewhat stale NAV, a somewhat stale price, an extreme but not impossible discount, or too little history to z-score.
- **RED** - no reliable live signal is possible: no current price, an unresolved ticker, only a historical panel price, no usable NAV, an unparsed NAV announcement, or a suspected unit mismatch.
- **EXCLUDED** - not part of the intended monitoring universe (research-policy exclusions, or not currently live).
