# Live fund coverage audit

Generated 2026-08-31T08:41:22+00:00 (as at 2026-08-31).

Run on demand only - this report has no schedule.

## Denominators

| Denominator | UK | ASX | Combined |
|---|--:|--:|--:|
| Registry universe (all vehicles ever listed) | 944 | 172 | 1116 |
| Registry-labelled live (aggregator) | 281 | 95 | 376 |
| Liveness-adjusted live (own filings) | 290 | 112 | 402 |
| Research eligible | 586 | 168 | 754 |
| Monitoring eligible (audit denominator) | 216 | 108 | 324 |
| Excluded | 728 | 64 | 792 |

## UK

```
Monitoring-eligible funds:     216
Fresh price:                   195 / 90.3%
Usable price:                  195 / 90.3%
Usable NAV:                    145 / 67.1%
Valid current discount:        140 / 64.8%
Discount history sufficient:   154 / 71.3%
CURRENT z-score:               116 / 53.7%
  (of which none needed:         0 within the estimate's own error band)
Identity unresolved:             8
Fully signal-ready:            102 / 47.2%

GREEN 102   AMBER 32   RED 82
```

| Price | n | % |
|---|--:|--:|
| Fresh | 195 | 90.3% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 20 | 9.3% |
| No price | 1 | 0.5% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 137 | 63.4% |
| Modelled / rolled forward | 23 | 10.6% |
| Stale | 37 | 17.1% |
| No usable NAV | 40 | 18.5% |
| NAV announcement held but no value parsed | 59 | 27.3% |
| ...of which the parser ran and failed | 59 | 27.3% |

## ASX

```
Monitoring-eligible funds:     108
Fresh price:                    99 / 91.7%
Usable price:                   99 / 91.7%
Usable NAV:                     89 / 82.4%
Valid current discount:         87 / 80.6%
Discount history sufficient:    91 / 84.3%
CURRENT z-score:                41 / 38.0%
  (of which none needed:         0 within the estimate's own error band)
Identity unresolved:             0
Fully signal-ready:             26 / 24.1%

GREEN 26   AMBER 61   RED 21
```

| Price | n | % |
|---|--:|--:|
| Fresh | 99 | 91.7% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 9 | 8.3% |
| No price | 0 | 0.0% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 58 | 53.7% |
| Modelled / rolled forward | 35 | 32.4% |
| Stale | 49 | 45.4% |
| No usable NAV | 1 | 0.9% |
| NAV announcement held but no value parsed | 76 | 70.4% |
| ...of which the parser ran and failed | 0 | 0.0% |

## COMBINED

```
Monitoring-eligible funds:     324
Fresh price:                   294 / 90.7%
Usable price:                  294 / 90.7%
Usable NAV:                    234 / 72.2%
Valid current discount:        227 / 70.1%
Discount history sufficient:   245 / 75.6%
CURRENT z-score:               157 / 48.5%
  (of which none needed:         0 within the estimate's own error band)
Identity unresolved:             8
Fully signal-ready:            128 / 39.5%

GREEN 128   AMBER 93   RED 103
```

| Price | n | % |
|---|--:|--:|
| Fresh | 294 | 90.7% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 29 | 9.0% |
| No price | 1 | 0.3% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 195 | 60.2% |
| Modelled / rolled forward | 58 | 17.9% |
| Stale | 86 | 26.5% |
| No usable NAV | 41 | 12.7% |
| NAV announcement held but no value parsed | 135 | 41.7% |
| ...of which the parser ran and failed | 59 | 18.2% |

## Why coverage is missing

| Issue | UK | ASX | Total |
|---|--:|--:|--:|
| No current z-score | 22 | 39 | 61 |
| NAV older than the amber window | 31 | 17 | 48 |
| Somewhat stale NAV | 4 | 32 | 36 |
| ASX monthly report NTA (no announcement route) | 0 | 31 | 31 |
| Stale historical panel price only | 20 | 9 | 29 |
| Rolled-forward (modelled) NAV | 1 | 25 | 26 |
| No usable NAV | 21 | 0 | 21 |
| NAV announcement found but unparsed | 19 | 1 | 20 |
| Insufficient z-score history | 7 | 12 | 19 |
| Suspected unit mismatch | 8 | 6 | 14 |
| Ticker identity unresolved | 8 | 0 | 8 |
| Extreme discount/premium | 1 | 5 | 6 |
| Aggregator NAV only (no own route) | 2 | 0 | 2 |
| NAV is zero or negative | 0 | 1 | 1 |
| No price returned | 1 | 0 | 1 |

## RED funds

| Market | Ticker | Fund | Blocking issue | Recommended fix |
|---|---|---|---|---|
| ASX | AIX | AI Private Opportunities Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| ASX | ALR | Aberdeen Leaders Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | AEG | Absolute Equity Performance Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | APL | Antipodes Global Investment Company Ltd | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | ALF | Australian Leaders Fund Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | BST | Barrack St Investments Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | BHD | Benjamin Hornigold Limited | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| ASX | BSN | Bisan Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | CTN | Contango Microcap Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | DUI | Diversified United Investment Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | D2O | Duxton Water Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | EAF | Evans & Partners Asia Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | FPP | Fat Prophets Global Property Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | IBC | Ironbark Capital Limited | NAV is zero or negative | the NAV parsed as zero or negative, which is not a valuation - fix the source reader rather than the output |
| ASX | MHH | Magellan High Conviction Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PAF | PM Capital Asian Opportunities Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | PAI | Platinum Asia Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PMC | Platinum Capital Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | QRI | Qualitas Real Estate Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | TEK | Thorney Technologies Ltd | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| ASX | VG1 | VGI Partners Global Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | 3IN | 3i Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AGVI | Aberforth Geared Value & Income Trust | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | ATS | Artemis Alpha Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BHMU | BH Macro | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | THRG | BlackRock Throgmorton Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | CVCE | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGI | Canadian General Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGL | Castelnau Group | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CBA | Ceiba Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CIP | Channel Islands Property | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | SDV | Chelverton UK Dividend Trust | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | TORO | Chenavari Toro Income Fund | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | CORD | Cordiant Digital Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | CRS | Crystal Amber Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | DPA | DP Aircraft I | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DIVI | Diverse Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EJFI | EJF Investments | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | EAT | European Assets Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EOT | European Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FJV | Fidelity Japan Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FSFL | Foresight Solar Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | FRGT | Franklin Global Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GABI | GCP Asset Backed Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GMP | Gabelli Merchant Partners | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HVPE | HarbourVest Global Private Equity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | HET | Henderson European Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HINT | Henderson International Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOT | Henderson Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOME | Home REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HGEN | Hydrogen Capital Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ICGT | ICG Enterprise Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LBOW | ICG-Longbow Senior Secured UK Property Debt Invest | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | IGC | India Capital Growth Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | JARA | JPMorgan Global Core Real Assets | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | JIGI | JPMorgan India Growth & Income | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | JGC | Jupiter Green Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | KPC | Keystone Positive Change | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | LMS | LMS Capital | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LTI | Lindsell Train Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MVI | Marwyn Value Investors | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | MHN | Menhaden Resource Efficiency | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MINI | Miton UK Microcap Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GROW | Molten Ventures | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NBPE | Neuberger Private Equity Partners | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | NESF | NextEnergy Solar Fund | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | OCI | Oakley Capital Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | ORIT | Octopus Renewables Infrastructure Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PINT | Pantheon Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | PEY | Partners Group Private Equity | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | PPET | Patria Private Equity | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PSH | Pershing Square Holdings | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PSDL | Phoenix Spree Deutschland | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | PCGH | Polar Capital Global Healthcare | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | PMGR | Premier Miton Global Renewables | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | RCP | RIT Capital Partners | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | RTW | RTW Biotech Opportunities | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RECI | Real Estate Credit Investments | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RGL | Regional REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TRIG | Renewables Infrastructure Group | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RESI | Residential Secure Income REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RMMC | River UK Micro Cap | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RSE | Riverstone Energy | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RICA | Ruffer Investment Company | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SEIT | SDCL Efficiency Income | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SVM | SVM UK Emerging Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SHRS | Shires Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SSON | Smithson Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | THRL | Target Healthcare REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | BBOX | Tritax Big Box REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SHIP | Tufton Assets | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TFIF | TwentyFour Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SMIF | TwentyFour Select Monthly Income | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | UTL | UIL | Ticker identity unresolved | this ticker is claimed by more than one security, or now belongs to a different company - settle which security the quote belongs to (outputs/live_coverage/identity_conflicts.csv) before trusting it |
| UK | USF | US Solar Fund | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | ENRG | VH Global Energy Infrastructure | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VSL | VPC Specialty Lending Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VNH | VietNam Holding | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VEIL | Vietnam Enterprise Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VOF | VinaCapital Vietnam Opportunity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | WINV | Worsley Investors | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | ADIG | abrdn Diversified Income and Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ASLI | abrdn European Logistics Income | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |

## Excluded vehicles

792 vehicles are outside the monitoring universe. They keep their rows in the Excluded tab with the reason; none is deleted.

| Reason | n |
|---|--:|
| not_live:delisted | 398 |
| vct_excluded_by_research_policy | 235 |
| split_capital_excluded_by_research_policy|non_ordinary_share_class | 68 |
| not_live:delist_candidate | 32 |
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
