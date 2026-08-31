# Live fund coverage audit

Generated 2026-08-31T08:04:37+00:00 (as at 2026-08-31).

Run on demand only - this report has no schedule.

## Denominators

| Denominator | UK | ASX | Combined |
|---|--:|--:|--:|
| Registry universe (all vehicles ever listed) | 944 | 172 | 1116 |
| Registry-labelled live (aggregator) | 281 | 95 | 376 |
| Liveness-adjusted live (own filings) | 297 | 112 | 409 |
| Research eligible | 586 | 168 | 754 |
| Monitoring eligible (audit denominator) | 230 | 108 | 338 |
| Excluded | 714 | 64 | 778 |

## UK

```
Monitoring-eligible funds:     230
Fresh price:                   205 / 89.1%
Usable price:                  205 / 89.1%
Usable NAV:                    147 / 63.9%
Valid current discount:        142 / 61.7%
Valid z-score history:         157 / 68.3%
Fully signal-ready:            126 / 54.8%

GREEN 126   AMBER 16   RED 88
```

| Price | n | % |
|---|--:|--:|
| Fresh | 205 | 89.1% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 22 | 9.6% |
| No price | 3 | 1.3% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 138 | 60.0% |
| Modelled / rolled forward | 26 | 11.3% |
| Stale | 43 | 18.7% |
| No usable NAV | 46 | 20.0% |
| NAV announcement held but no value parsed | 38 | 16.5% |
| ...of which the parser ran and failed | 39 | 17.0% |

## ASX

```
Monitoring-eligible funds:     108
Fresh price:                    99 / 91.7%
Usable price:                   99 / 91.7%
Usable NAV:                     90 / 83.3%
Valid current discount:         87 / 80.6%
Valid z-score history:          91 / 84.3%
Fully signal-ready:             46 / 42.6%

GREEN 46   AMBER 42   RED 20
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
Monitoring-eligible funds:     338
Fresh price:                   304 / 89.9%
Usable price:                  304 / 89.9%
Usable NAV:                    237 / 70.1%
Valid current discount:        229 / 67.8%
Valid z-score history:         248 / 73.4%
Fully signal-ready:            172 / 50.9%

GREEN 172   AMBER 58   RED 108
```

| Price | n | % |
|---|--:|--:|
| Fresh | 304 | 89.9% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 31 | 9.2% |
| No price | 3 | 0.9% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 196 | 58.0% |
| Modelled / rolled forward | 61 | 18.0% |
| Stale | 92 | 27.2% |
| No usable NAV | 47 | 13.9% |
| NAV announcement held but no value parsed | 114 | 33.7% |
| ...of which the parser ran and failed | 39 | 11.5% |

## Why coverage is missing

| Issue | UK | ASX | Total |
|---|--:|--:|--:|
| NAV older than the amber window | 37 | 17 | 54 |
| Somewhat stale NAV | 4 | 32 | 36 |
| ASX monthly report NTA (no announcement route) | 0 | 31 | 31 |
| Stale historical panel price only | 22 | 9 | 31 |
| Rolled-forward (modelled) NAV | 5 | 25 | 30 |
| NAV announcement found but unparsed | 26 | 1 | 27 |
| Insufficient z-score history | 9 | 12 | 21 |
| No usable NAV | 20 | 0 | 20 |
| Suspected unit mismatch | 7 | 6 | 13 |
| Extreme discount/premium | 1 | 5 | 6 |
| Aggregator NAV only (no own route) | 4 | 0 | 4 |
| No price returned | 3 | 0 | 3 |

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
| ASX | MHH | Magellan High Conviction Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PAF | PM Capital Asian Opportunities Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | PAI | Platinum Asia Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PMC | Platinum Capital Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | QRI | Qualitas Real Estate Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | TEK | Thorney Technologies Ltd | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| ASX | VG1 | VGI Partners Global Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | 3IN | 3i Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AEWU | AEW UK REIT | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AA4 | Amedeo Air Four Plus | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ATS | Artemis Alpha Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | AUGM | Augmentum Fintech | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | BHMU | BH Macro | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BSRT | Baker Steel Resources Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | THRG | BlackRock Throgmorton Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BSIF | Bluefield Solar Income Fund | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | CTPE | CT Private Equity Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | CVCE | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CVCG | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGI | Canadian General Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGL | Castelnau Group | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CBA | Ceiba Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CHRY | Chrysalis Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | CRS | Crystal Amber Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | DPA | DP Aircraft I | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DGI9 | Digital 9 Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DIVI | Diverse Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EAT | European Assets Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EOT | European Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FJV | Fidelity Japan Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FSFL | Foresight Solar Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | FRGT | Franklin Global Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GABI | GCP Asset Backed Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GRIT | GRIT Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GSF | Gore Street Energy Storage | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | GRID | Gresham House Energy Storage | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HVPE | HarbourVest Global Private Equity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | HET | Henderson European Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HINT | Henderson International Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOT | Henderson Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HGT | HgCapital Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HOME | Home REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HGEN | Hydrogen Capital Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ICGT | ICG Enterprise Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LBOW | ICG-Longbow Senior Secured UK Property Debt Invest | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | IGC | India Capital Growth Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | INPP | International Public Partnerships | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | JGC | Jupiter Green Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | KPC | Keystone Positive Change | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | LMS | LMS Capital | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LABS | Life Science REIT | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | LTI | Lindsell Train Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | LIVE | Living REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | MVI | Marwyn Value Investors | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | MHN | Menhaden Resource Efficiency | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MINI | Miton UK Microcap Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GROW | Molten Ventures | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NBPE | Neuberger Private Equity Partners | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NESF | NextEnergy Solar Fund | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | ORIT | Octopus Renewables Infrastructure Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PINT | Pantheon Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | PIN | Pantheon International | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AEET | Parvus Energy Efficiency Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PPET | Patria Private Equity | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PSH | Pershing Square Holdings | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PMGR | Premier Miton Global Renewables | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | RCP | RIT Capital Partners | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | RECI | Real Estate Credit Investments | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RGL | Regional REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TRIG | Renewables Infrastructure Group | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RESI | Residential Secure Income REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RMMC | River UK Micro Cap | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RSE | Riverstone Energy | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RICA | Ruffer Investment Company | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SVM | SVM UK Emerging Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SERE | Schroder European Real Estate | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SREI | Schroder Real Estate | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | INOV | Schroders Capital Global Innovation Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SEQI | Sequoia Economic Infrastructure Income | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SHRS | Shires Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SSON | Smithson Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SYNC | Syncona | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | THRL | Target Healthcare REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | BBOX | Tritax Big Box REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TFIF | TwentyFour Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SMIF | TwentyFour Select Monthly Income | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | ENRG | VH Global Energy Infrastructure | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VSL | VPC Specialty Lending Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VIP | Value and Indexed Property Income Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VNH | VietNam Holding | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VEIL | Vietnam Enterprise Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VOF | VinaCapital Vietnam Opportunity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | WINV | Worsley Investors | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | ADIG | abrdn Diversified Income and Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ASLI | abrdn European Logistics Income | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |

## Excluded vehicles

778 vehicles are outside the monitoring universe. They keep their rows in the Excluded tab with the reason; none is deleted.

| Reason | n |
|---|--:|
| not_live:delisted | 356 |
| vct_excluded_by_research_policy | 235 |
| split_capital_excluded_by_research_policy|non_ordinary_share_class | 68 |
| not_live:delist_candidate | 60 |
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
