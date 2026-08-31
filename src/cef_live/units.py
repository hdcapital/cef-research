"""Canonical price/NAV units, and the deterministic checks that catch a
unit error before it becomes a 5,649% premium.

The failure this module exists for is real and was live in the repo: UK
NAV announcements are published in PENCE (``nav_cum_pence``), Yahoo quotes
London shares in PENCE, and one loader divided the NAV by 100 on its way
into the live table. Every fund reached through that path showed a
premium of 80x-5,000x. Nothing about the numbers was wrong except the unit
they were carried in, and no downstream sanity check noticed.

So units are stated once, here, per market:

  UK   NAV and price are both GBX (pence)
  AU   NAV and price are both AUD (dollars)

Conversions are explicit and only ever applied when the SOURCE unit is
known. An unknown unit yields the value unchanged plus a flag - never a
guessed conversion, because a wrong guess is indistinguishable from the
bug it is meant to catch.

The diagnosis half is advisory by design: it reports ``unit_check_status``,
``unit_check_reason`` and ``suspected_scale_factor`` and never rewrites a
number. A 100x scale gap is evidence of an error somewhere, not evidence
of which side is wrong - the price could be right and the NAV a misparse,
as it is for the funds whose NAV text yields "2.0p".
"""

from __future__ import annotations

import math

import pandas as pd

# canonical unit per market, for both NAV and price
CANONICAL_UNIT = {"UK": "GBX", "AU": "AUD"}

# source-unit labels we recognise, mapped to the multiplier that takes them
# to the market's canonical unit
# EVERY STERLING CURRENCY LABEL MEANS PENCE HERE, and none of them causes a
# conversion. That is not laziness, it is the only safe reading:
#
#   * Yahoo labels London lines "GBp" - lowercase p, pence. Case is the ONLY
#     thing separating it from "GBP", and a case-insensitive lookup collapses
#     them. This module did exactly that and multiplied 271 correctly-quoted
#     UK prices by 100 the first night real currency metadata reached it:
#     AVI Global went in at 265.5p and came out at 26,550, a 9,265% premium.
#     The module written to prevent unit errors committed one, in the one
#     direction it had no check for - because it TRUSTED a label instead of
#     flagging it.
#   * uk_cef/panel.py records the same hazard from the other side: "sterling
#     labels vary by vintage: GBX (pence), GBP/GBp/STG (same sterling quotes
#     - some 2012/2023 files label pence prices 'GBP')". So in this system's
#     own sources, a GBP label on a UK quote is a pence figure.
#
# A value genuinely in pounds is therefore caught by scale_diagnosis as a
# ~100x gap and REPORTED, never silently rescaled. Only unambiguous words -
# "pounds", "£" - convert, because those are descriptions rather than
# currency codes that two conventions disagree about.
_UK_UNITS = {
    "gbx": 1.0, "gbp": 1.0, "gbp_pence": 1.0, "stg": 1.0,
    "pence": 1.0, "p": 1.0, "pennies": 1.0,
    "pounds": 100.0, "£": 100.0,
}
_AU_UNITS = {
    "aud": 1.0, "dollars": 1.0, "a$": 1.0, "$": 1.0, "aux": 1.0,
    "cents": 0.01, "auc": 0.01, "c": 0.01,
}
_UNITS = {"UK": _UK_UNITS, "AU": _AU_UNITS}

# Plausible price/NAV ratio for a live closed-end fund. 0.20 is an 80%
# discount, 1.80 an 80% premium - the bounds the brief asks to flag.
PLAUSIBLE_LOW, PLAUSIBLE_HIGH = 0.20, 1.80
# Beyond these a ratio is no longer "an extreme valuation", it is a
# different unit: a 10x gap cannot be a market price.
SCALE_LOW, SCALE_HIGH = 0.10, 6.0
_SCALE_FACTORS = (0.001, 0.01, 0.1, 10.0, 100.0, 1000.0)


def normalise(market: str, value, unit: str | None) -> tuple[float | None, str, str]:
    """(value in the market's canonical unit, unit label, note).

    A recognised source unit is converted; an unrecognised or missing one
    is passed through untouched and said so, so the caller can decide
    rather than inherit a guess.
    """
    canon = CANONICAL_UNIT.get(market, "")
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None, canon, "missing"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, canon, "not_numeric"
    key = str(unit).strip().lower() if unit is not None and str(unit).strip() else ""
    table = _UNITS.get(market, {})
    if not key:
        return v, canon, "unit_unstated_assumed_canonical"
    if key in table:
        mult = table[key]
        return v * mult, canon, ("unit_canonical" if mult == 1.0
                                 else f"converted_x{mult:g}_from_{key}")
    return v, canon, f"unit_unrecognised:{key}"


def unit_metadata_conflict(market: str, price_ccy: str | None) -> str | None:
    """Report a quote currency that contradicts the market's canonical unit.

    Yahoo labels London lines ``GBp``; a ``GBP`` label on a London quote
    means the number is pounds and is 100x out. This only ever RETURNS the
    disagreement - the conversion decision belongs to the caller.
    """
    if not price_ccy or not isinstance(price_ccy, str):
        return None
    c = price_ccy.strip()
    if market == "UK":
        if c.lower() in ("gbx", "gbp_pence", "stg") or c == "GBp":
            return None
        if c == "GBP":
            # ambiguous, not wrong: this repo's own sources use GBP for
            # pence quotes. Reported so a reader can check, never converted.
            return "quote_currency_GBP_is_ambiguous_read_as_pence"
        return f"quote_currency_{c}_not_sterling"
    if market == "AU":
        if c.upper() in ("AUD", "A$"):
            return None
        return f"quote_currency_{c}_not_AUD"
    return None


def scale_diagnosis(price, nav) -> dict:
    """Deterministic verdict on a price/NAV pair. Never rewrites either.

    status:
      no_data       one side absent or non-positive - nothing to check
      ok            0.20 <= price/nav <= 1.80
      extreme       outside that band but within a decade - a real, if
                    remarkable, discount/premium (flagged, not condemned)
      suspect_scale a gap no market produces: the nearest power of ten is
                    reported as ``suspected_scale_factor``
    """
    out = {"unit_check_status": "no_data", "unit_check_reason": "",
           "suspected_scale_factor": None, "price_nav_ratio": None,
           "extreme_discount_flag": False}
    if price is None or nav is None or pd.isna(price) or pd.isna(nav):
        out["unit_check_reason"] = "price_or_nav_missing"
        return out
    price, nav = float(price), float(nav)
    if nav <= 0 or price <= 0:
        out["unit_check_reason"] = "non_positive_price_or_nav"
        return out

    ratio = price / nav
    out["price_nav_ratio"] = round(ratio, 6)
    out["extreme_discount_flag"] = bool(ratio < PLAUSIBLE_LOW or ratio > PLAUSIBLE_HIGH)

    if PLAUSIBLE_LOW <= ratio <= PLAUSIBLE_HIGH:
        out["unit_check_status"] = "ok"
        return out
    if SCALE_LOW <= ratio <= SCALE_HIGH:
        out["unit_check_status"] = "extreme"
        out["unit_check_reason"] = (
            f"price/NAV {ratio:.3g} implies {(ratio - 1) * 100:+.0f}% "
            "discount/premium (>80%)")
        return out

    nearest = min(_SCALE_FACTORS, key=lambda f: abs(math.log10(ratio) - math.log10(f)))
    out["unit_check_status"] = "suspect_scale"
    out["suspected_scale_factor"] = nearest
    out["unit_check_reason"] = (
        f"price/NAV {ratio:.4g} is ~{nearest:g}x - a unit mismatch "
        "(pence/pounds, cents/dollars) or a misparsed NAV, not a market price")
    return out


def discount(price, nav) -> float | None:
    """price / NAV - 1, on values the caller has already normalised.

    Returns None rather than a number whenever the inputs cannot support
    one; a missing discount is missing, never zero.
    """
    if price is None or nav is None or pd.isna(price) or pd.isna(nav):
        return None
    price, nav = float(price), float(nav)
    if nav <= 0:
        return None
    return price / nav - 1.0
