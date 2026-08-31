# Live fund coverage audit

Generated 2026-08-31T07:58:27+00:00 (as at 2026-08-31).

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
Valid current discount:          0 / 0.0%
Valid z-score history:         157 / 68.3%
Fully signal-ready:              0 / 0.0%

GREEN 0   AMBER 1   RED 229
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
| ...of which the parser ran and failed | 38 | 16.5% |

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
Valid current discount:         87 / 25.7%
Valid z-score history:         248 / 73.4%
Fully signal-ready:             46 / 13.6%

GREEN 46   AMBER 43   RED 249
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
| ...of which the parser ran and failed | 38 | 11.2% |

## Why coverage is missing

| Issue | UK | ASX | Total |
|---|--:|--:|--:|
| Suspected unit mismatch | 159 | 6 | 165 |
| NAV older than the amber window | 37 | 17 | 54 |
| Somewhat stale NAV | 0 | 32 | 32 |
| ASX monthly report NTA (no announcement route) | 0 | 31 | 31 |
| Stale historical panel price only | 22 | 9 | 31 |
| NAV announcement found but unparsed | 26 | 1 | 27 |
| Rolled-forward (modelled) NAV | 0 | 25 | 25 |
| No usable NAV | 20 | 0 | 20 |
| Insufficient z-score history | 0 | 12 | 12 |
| Extreme discount/premium | 1 | 5 | 6 |
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
| UK | AGT | AVI Global Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AJOT | AVI Japan Opportunity Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AAS | Aberdeen Asia Focus | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AAIF | Aberdeen Asian Income Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AEI | Aberdeen Equity Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ANII | Aberdeen New India Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AUSC | Aberdeen UK Smaller Companies Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AGVI | Aberforth Geared Value & Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ASL | Aberforth Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AIC | Achilles Investment Company | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ALW | Alliance Witan | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ATT | Allianz Technology Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AA4 | Amedeo Air Four Plus | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ATS | Artemis Alpha Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | AFL | Artemis UK Future Leaders | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AIE | Ashoka India Equity | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AWEM | Ashoka WhiteOak Emerging Markets | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ATY | Athelney Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | AUGM | Augmentum Fintech | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | ARR | Aurora UK Alpha | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BHMU | BH Macro | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BGCG | Baillie Gifford China Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BGEU | Baillie Gifford European Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BGFD | Baillie Gifford Japan Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BGS | Baillie Gifford Shin Nippon | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BGUK | Baillie Gifford UK Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | USA | Baillie Gifford US Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BSRT | Baker Steel Resources Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | BNKR | Bankers Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BEMO | Barings Emerging EMEA Opportunities | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BIOG | Biotech Growth Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRAI | BlackRock American Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BERI | BlackRock Energy and Resources Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRFI | BlackRock Frontiers | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRGE | BlackRock Greater Europe | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRIG | BlackRock Income & Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRLA | BlackRock Latin American | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BRSC | BlackRock Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | THRG | BlackRock Throgmorton Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BRWM | BlackRock World Mining | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BSIF | Bluefield Solar Income Fund | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | BAF | British & American | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BASC | Brown Advisory US Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BUT | Brunner Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CCJI | CC Japan Income & Growth Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CYN | CQS Natural Resources Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | NCYF | CQS New City High Yield Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CMPG | CT Global Managed Portfolio Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CMPI | CT Global Managed Portfolio Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CTHT | CT Healthcare Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CTPE | CT Private Equity Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | CTUK | CT UK Capital and Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CHI | CT UK High Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CHIB | CT UK High Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CVCE | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CVCG | CVC Income & Growth | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CLDN | Caledonia Investments | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CGI | Canadian General Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CGT | Capital Gearing Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CGL | Castelnau Group | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | CBA | Ceiba Investments | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | SDV | Chelverton UK Dividend Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CHRY | Chrysalis Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | CTY | City of London Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | CRS | Crystal Amber Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | DPA | DP Aircraft I | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DVNO | Develop North | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | DGI9 | Digital 9 Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | DIVI | Diverse Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | DIG | Dunedin Income Growth | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | EJFI | EJF Investments | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | EGL | Ecofin Global Utilities and Infrastructure | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | EDIN | Edinburgh Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | EWI | Edinburgh Worldwide | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | EAT | European Assets Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EOT | European Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FCIT | F&C Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FAS | Fidelity Asian Values | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FCSS | Fidelity China Special Situations | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FEML | Fidelity Emerging Markets | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FEV | Fidelity European Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FJV | Fidelity Japan Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FSV | Fidelity Special Values | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FGT | Finsbury Growth & Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FGEN | Foresight Environmental Infrastructure | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | FSFL | Foresight Solar Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | FRGT | Franklin Global Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GABI | GCP Asset Backed Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GCP | GCP Infrastructure Investments | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GRIT | GRIT Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GCL | Geiger Counter | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GOT | Global Opportunities Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GPM | Golden Prospect Precious Metals | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GSF | Gore Street Energy Storage | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | UKW | Greencoat UK Wind | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GRID | Gresham House Energy Storage | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HANA | Hansa Investment Company | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HAN | Hansa Investment Company | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HVPE | HarbourVest Global Private Equity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | HET | Henderson European Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HFEL | Henderson Far East Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HHI | Henderson High Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HINT | Henderson International Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOT | Henderson Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HSL | Henderson Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HRI | Herald Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | HGT | HgCapital Trust | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HOME | Home REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | HGEN | Hydrogen Capital Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ICGT | ICG Enterprise Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LBOW | ICG-Longbow Senior Secured UK Property Debt Invest | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | IEM | Impax Environmental Markets | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | IGC | India Capital Growth Fund | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | IBT | International Biotechnology | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | INPP | International Public Partnerships | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | IAD | Invesco Asia Dragon Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BIPS | Invesco Bond Income Plus | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | IGET | Invesco Global Equity Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JAM | JPMorgan American | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JAGI | JPMorgan Asia Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JCGI | JPMorgan China Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JCH | JPMorgan Claverhouse | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JEMA | JPMorgan Emerging Europe, Middle East & Africa | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JEMI | JPMorgan Emerging Markets Dividend Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JMGI | JPMorgan Emerging Markets Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JEDT | JPMorgan European Discovery | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JEGI | JPMorgan European Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JGGI | JPMorgan Global Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JIGI | JPMorgan India Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JFJ | JPMorgan Japanese | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JUGI | JPMorgan UK Small Cap Growth & Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JUSC | JPMorgan US Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | JGC | Jupiter Green Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | KPC | Keystone Positive Change | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | LMS | LMS Capital | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LWDB | Law Debenture Corporation | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | LABS | Life Science REIT | No price returned | check the fund still trades under this ticker; re-run the price layer for it and record the provider error |
| UK | LIVE | Living REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | LWI | Lowland Investment Company | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MGCI | M&G Credit Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MIGO | MIGO Opportunities Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MAJE | Majedie Investments | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MVI | Marwyn Value Investors | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | MHN | Menhaden Resource Efficiency | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MRC | Mercantile Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MRCH | Merchants Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MWY | Mid Wynd International | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MINI | Miton UK Microcap Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MMIT | Mobius Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GROW | Molten Ventures | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | MNKS | Monks Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MTE | Montanaro European Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MTU | Montanaro UK Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MUT | Murray Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | MYI | Murray International Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | NBPE | Neuberger Private Equity Partners | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NESF | NextEnergy Solar Fund | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | NAVF | Nippon Active Value Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | NAIT | North American Income Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | NAS | North Atlantic Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ORIT | Octopus Renewables Infrastructure Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | OIT | Odyssean Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ONWD | Onward Opportunities | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PAC | Pacific Assets Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PHI | Pacific Horizon Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PINT | Pantheon Infrastructure | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | PIN | Pantheon International | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AEET | Parvus Energy Efficiency Trust | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PPET | Patria Private Equity | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PSH | Pershing Square Holdings | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | PNL | Personal Assets Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PCFT | Polar Capital Global Financials | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PCGH | Polar Capital Global Healthcare | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PCT | Polar Capital Technology | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | PMGR | Premier Miton Global Renewables | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | RCP | RIT Capital Partners | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | RECI | Real Estate Credit Investments | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RGL | Regional REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TRIG | Renewables Infrastructure Group | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RESI | Residential Secure Income REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RIII | Rights & Issues Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RMMC | River UK Micro Cap | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | RSE | Riverstone Energy | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | RKW | Rockwood Strategic | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | RICA | Ruffer Investment Company | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | STS | STS Global Income & Growth Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SVM | SVM UK Emerging Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SDP | Schroder AsiaPacific Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ATR | Schroder Asian Total Return | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SBO | Schroder British Opportunities | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SERE | Schroder European Real Estate | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SCF | Schroder Income Growth Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SJG | Schroder Japan Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SOI | Schroder Oriental Income | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SREI | Schroder Real Estate | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | SCP | Schroder UK Mid Cap Fund | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | INOV | Schroders Capital Global Innovation Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SAIN | Scottish American | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SMT | Scottish Mortgage Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SST | Scottish Oriental Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SEQI | Sequoia Economic Infrastructure Income | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | SHRS | Shires Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SSON | Smithson Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SEC | Strategic Equity Capital | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | SYNC | Syncona | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | TRY | TR Property Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | THRL | Target Healthcare REIT | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | TMPL | Temple Bar Investment Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | TEM | Templeton Emerging Markets | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ESCT | The European Smaller Companies Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | GSCT | The Global Smaller Companies Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | INV | The Investment Company | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | BBOX | Tritax Big Box REIT | No usable NAV | no NAV from any source - confirm the fund publishes one, then add its announcement route (Tier 0 harvest / ASX index) |
| UK | TFIF | TwentyFour Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | SMIF | TwentyFour Select Monthly Income | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | UTL | UIL | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | UEM | Utilico Emerging Markets Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | ENRG | VH Global Energy Infrastructure | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VSL | VPC Specialty Lending Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VIP | Value and Indexed Property Income Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VNH | VietNam Holding | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | VEIL | Vietnam Enterprise Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | VOF | VinaCapital Vietnam Opportunity | NAV announcement found but unparsed | a NAV announcement exists but the parser produced no value - add a rule for its layout in harvest_nav.UK_RULES / the ASX parser |
| UK | WWH | Worldwide Healthcare Trust | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
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
