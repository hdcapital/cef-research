# Live fund coverage audit

Generated 2026-08-31T06:28:52+00:00 (as at 2026-08-31).

Run on demand only - this report has no schedule.

## Denominators

| Denominator | UK | ASX | Combined |
|---|--:|--:|--:|
| Registry universe (all vehicles ever listed) | 944 | 172 | 1116 |
| Registry-labelled live (aggregator) | 281 | 95 | 376 |
| Liveness-adjusted live (own filings) | 309 | 112 | 421 |
| Research eligible | 586 | 168 | 754 |
| Monitoring eligible (audit denominator) | 239 | 108 | 347 |
| Excluded | 705 | 64 | 769 |

## UK

```
Monitoring-eligible funds:     239
Fresh price:                   144 / 60.3%
Usable price:                  144 / 60.3%
Usable NAV:                    138 / 57.7%
Valid current discount:        132 / 55.2%
Valid z-score history:         155 / 64.9%
Fully signal-ready:             99 / 41.4%

GREEN 99   AMBER 33   RED 107
```

| Price | n | % |
|---|--:|--:|
| Fresh | 144 | 60.3% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 24 | 10.0% |
| No price | 71 | 29.7% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 106 | 44.4% |
| Modelled / rolled forward | 51 | 21.3% |
| Stale | 34 | 14.2% |
| No usable NAV | 71 | 29.7% |
| NAV announcement held but no value parsed | 33 | 13.8% |
| ...of which the parser ran and failed | 33 | 13.8% |

## ASX

```
Monitoring-eligible funds:     108
Fresh price:                    98 / 90.7%
Usable price:                   98 / 90.7%
Usable NAV:                     89 / 82.4%
Valid current discount:         89 / 82.4%
Valid z-score history:          91 / 84.3%
Fully signal-ready:              5 / 4.6%

GREEN 5   AMBER 84   RED 19
```

| Price | n | % |
|---|--:|--:|
| Fresh | 98 | 90.7% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 9 | 8.3% |
| No price | 1 | 0.9% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 6 | 5.6% |
| Modelled / rolled forward | 82 | 75.9% |
| Stale | 101 | 93.5% |
| No usable NAV | 1 | 0.9% |
| NAV announcement held but no value parsed | 100 | 92.6% |
| ...of which the parser ran and failed | 0 | 0.0% |

## COMBINED

```
Monitoring-eligible funds:     347
Fresh price:                   242 / 69.7%
Usable price:                  242 / 69.7%
Usable NAV:                    227 / 65.4%
Valid current discount:        221 / 63.7%
Valid z-score history:         246 / 70.9%
Fully signal-ready:            104 / 30.0%

GREEN 104   AMBER 117   RED 126
```

| Price | n | % |
|---|--:|--:|
| Fresh | 242 | 69.7% |
| Stale | 0 | 0.0% |
| Historical panel fallback | 33 | 9.5% |
| No price | 72 | 20.7% |
| Unresolved ticker | 0 | |

NAV rows below are independent flags, not a partition: a NAV can be both modelled and stale, and a fund with a usable monthly NAV can still have an unparsed announcement.

| NAV | n | % |
|---|--:|--:|
| Fresh directly published | 112 | 32.3% |
| Modelled / rolled forward | 133 | 38.3% |
| Stale | 135 | 38.9% |
| No usable NAV | 72 | 20.7% |
| NAV announcement held but no value parsed | 133 | 38.3% |
| ...of which the parser ran and failed | 33 | 9.5% |

## Why coverage is missing

| Issue | UK | ASX | Total |
|---|--:|--:|--:|
| Rolled-forward (modelled) NAV | 27 | 71 | 98 |
| Somewhat stale NAV | 3 | 83 | 86 |
| ASX monthly report NTA (no announcement route) | 0 | 83 | 83 |
| Live but absent from the live table | 71 | 1 | 72 |
| No usable NAV | 71 | 0 | 71 |
| NAV older than the amber window | 30 | 18 | 48 |
| Stale historical panel price only | 24 | 9 | 33 |
| Aggregator NAV only (no own route) | 28 | 0 | 28 |
| Insufficient z-score history | 4 | 12 | 16 |
| Suspected unit mismatch | 2 | 4 | 6 |
| NAV announcement found but unparsed | 0 | 1 | 1 |

## RED funds

| Market | Ticker | Fund | Blocking issue | Recommended fix |
|---|---|---|---|---|
| ASX | AIX | AI Private Opportunities Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| ASX | ALR | Aberdeen Leaders Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | AEG | Absolute Equity Performance Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | APL | Antipodes Global Investment Company Ltd | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | ALF | Australian Leaders Fund Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | BST | Barrack St Investments Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | BSN | Bisan Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | CTN | Contango Microcap Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | DUI | Diversified United Investment Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | D2O | Duxton Water Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | EAF | Evans & Partners Asia Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | FPP | Fat Prophets Global Property Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | HCF | H&G High Conviction Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | MHH | Magellan High Conviction Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PAF | PM Capital Asian Opportunities Fund Limited | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | PAI | Platinum Asia Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | PMC | Platinum Capital Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| ASX | QRI | Qualitas Real Estate Income Fund | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| ASX | VG1 | VGI Partners Global Investments Limited | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | 3IN | 3i Infrastructure | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | AIC | Achilles Investment Company | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | ATS | Artemis Alpha Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BHMU | BH Macro | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | BSRT | Baker Steel Resources Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | BRLA | BlackRock Latin American | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | THRG | BlackRock Throgmorton Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | BSIF | Bluefield Solar Income Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | BAF | British & American | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CTPE | CT Private Equity Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CVCE | CVC Income & Growth | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CVCG | CVC Income & Growth | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CGI | Canadian General Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CGL | Castelnau Group | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CBA | Ceiba Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CIP | Channel Islands Property | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | TORO | Chenavari Toro Income Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CHRY | Chrysalis Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | CORD | Cordiant Digital Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CRS | Crystal Amber Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | CREI | Custodian Property Income REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | DPA | DP Aircraft I | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | DGI9 | Digital 9 Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | DIVI | Diverse Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EJFI | EJF Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | EAT | European Assets Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | EOT | European Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FJV | Fidelity Japan Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | FGEN | Foresight Environmental Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | FSFL | Foresight Solar Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | FRGT | Franklin Global Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GABI | GCP Asset Backed Income | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | GMP | Gabelli Merchant Partners | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | GSF | Gore Street Energy Storage | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | GRID | Gresham House Energy Storage | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | HICL | HICL Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | HVPE | HarbourVest Global Private Equity | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | HET | Henderson European Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HINT | Henderson International Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HOT | Henderson Opportunities Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | HGT | HgCapital Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | HOME | Home REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | HGEN | Hydrogen Capital Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ICGT | ICG Enterprise Trust | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | LBOW | ICG-Longbow Senior Secured UK Property Debt Invest | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | IGC | India Capital Growth Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | INPP | International Public Partnerships | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | JPEL | JPEL Private Equity | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | JEMA | JPMorgan Emerging Europe, Middle East & Africa | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | JARA | JPMorgan Global Core Real Assets | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | JGC | Jupiter Green Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | KPC | Keystone Positive Change | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | LMS | LMS Capital | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | BOOK | Literacy Capital | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | LIVE | Living REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | MVI | Marwyn Value Investors | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | MHN | Menhaden Resource Efficiency | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | MINI | Miton UK Microcap Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | GROW | Molten Ventures | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | MTU | Montanaro UK Smaller Companies | Suspected unit mismatch | do NOT rescale the output - find which side is wrong (NAV parse or quote currency) and fix the source reader |
| UK | NBPE | Neuberger Private Equity Partners | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | NESF | NextEnergy Solar Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | NAS | North Atlantic Smaller Companies | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | OCI | Oakley Capital Investments | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | ORIT | Octopus Renewables Infrastructure Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | PINT | Pantheon Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | PIN | Pantheon International | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PEY | Partners Group Private Equity | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | AEET | Parvus Energy Efficiency Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | PPET | Patria Private Equity | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | PSH | Pershing Square Holdings | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | PSDL | Phoenix Spree Deutschland | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | PMGR | Premier Miton Global Renewables | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | RCP | RIT Capital Partners | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | RTW | RTW Biotech Opportunities | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RECI | Real Estate Credit Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RGL | Regional REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | TRIG | Renewables Infrastructure Group | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RESI | Residential Secure Income REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RIII | Rights & Issues Investment Trust | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RMMC | River UK Micro Cap | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RSE | Riverstone Energy | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | RICA | Ruffer Investment Company | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SEIT | SDCL Efficiency Income | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SVM | SVM UK Emerging Fund | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SERE | Schroder European Real Estate | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SREI | Schroder Real Estate | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SEQI | Sequoia Economic Infrastructure Income | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SHRS | Shires Income | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SSON | Smithson Investment Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | SYNC | Syncona | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | THRL | Target Healthcare REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | INV | The Investment Company | NAV older than the amber window | the newest NAV we hold is older than the amber window - re-run the NAV harvest for this fund and check its publication frequency |
| UK | BBOX | Tritax Big Box REIT | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SHIP | Tufton Assets | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | TFIF | TwentyFour Income Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | SMIF | TwentyFour Select Monthly Income | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | USF | US Solar Fund | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | ENRG | VH Global Energy Infrastructure | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | VSL | VPC Specialty Lending Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | VIP | Value and Indexed Property Income Trust | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | VNH | VietNam Holding | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | VEIL | Vietnam Enterprise Investments | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | VOF | VinaCapital Vietnam Opportunity | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | WINV | Worsley Investors | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |
| UK | ADIG | abrdn Diversified Income and Growth | Stale historical panel price only | the live price feed returned nothing for this symbol - verify the Yahoo symbol and re-run; never present the panel price as today's |
| UK | ASLI | abrdn European Logistics Income | Live but absent from the live table | no row in data/nta_live/latest.parquet: the table was built when a fund with no NAV anchor was dropped outright, which discarded its price too. nta_live now keeps the row - re-run the nightly (or this audit with --refresh) and the price, if there is one, will appear |

## Excluded vehicles

769 vehicles are outside the monitoring universe. They keep their rows in the Excluded tab with the reason; none is deleted.

| Reason | n |
|---|--:|
| not_live:delisted | 398 |
| vct_excluded_by_research_policy | 235 |
| split_capital_excluded_by_research_policy|non_ordinary_share_class | 68 |
| non_ordinary_share_class | 26 |
| non_sterling_quote | 18 |
| not_live:delist_candidate | 9 |
| split_capital_excluded_by_research_policy | 8 |
| benchmark_index_not_a_fund | 4 |
| vct_excluded_by_research_policy|non_ordinary_share_class | 3 |

## How to read the verdicts

- **GREEN** - fresh, credible price and a published NAV in consistent units, with enough of the fund's own discount history to z-score. A live signal can be produced.
- **AMBER** - monitorable with a stated qualification: a rolled-forward or somewhat stale NAV, a somewhat stale price, an extreme but not impossible discount, or too little history to z-score.
- **RED** - no reliable live signal is possible: no current price, an unresolved ticker, only a historical panel price, no usable NAV, an unparsed NAV announcement, or a suspected unit mismatch.
- **EXCLUDED** - not part of the intended monitoring universe (research-policy exclusions, or not currently live).
