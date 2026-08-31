"""Learn WHERE a fund's NTA sits on its own announcement, from the exchange.

The extractor reads numbers correctly - the validation run found zero
dollars-vs-cents errors and a median ratio of exactly 1.0 - but it picks the
wrong number off the page for a third of Australian fund-months. The errors
repeat per fund, because each fund publishes the same layout every month:
HCF reads 4.120 in August, September AND October while the exchange's NTA
moves 0.011 to 0.012, and TEK reads exactly 2.000 across four months. A
figure that does not move is not a NAV.

The ASX publishes a monthly NTA per fund. Where we hold both that figure and
the fund's own announcement for the same month, the correct answer is known,
so the LABEL that carries it can be discovered rather than guessed.

Three rules make this supervision rather than overfitting:

1. LEARN THE LABEL, NEVER THE VALUE. The output is "for this fund, the NTA
   is the figure labelled 'NTA after tax'" - a statement about page layout,
   which is stationary and which a human can check. Storing the value would
   build a lookup table that memorises the past and knows nothing about next
   month.

2. REQUIRE REPETITION. A monthly NTA announcement carries dozens of numbers,
   so searching for "the one closest to 1.71" always finds something - a
   spurious match is near certain, not unlikely. A label becomes a rule only
   when it wins across several months independently, and a month where many
   labels match is weak evidence, recorded as such.

3. THE EXCHANGE IS A SECOND OPINION, NOT GROUND TRUTH. The two legitimately
   differ where the exchange publishes post-tax and the fund reports pre-tax.
   Training the parser to agree would teach it to reproduce the EXCHANGE's
   basis rather than what the fund stated, and agreement would rise while the
   number quietly changed meaning. So a discovered label carries the basis
   words that sit in it, and a fund publishing both bases keeps both.

Nothing here rewrites a parser. It emits an auditable table of candidate
rules for review; applying them is a separate, deliberate step.
"""

from __future__ import annotations

import re
import zlib
from collections import defaultdict

import pandas as pd

# A NAV rounds to the cent, and the exchange and the fund may round
# differently, so an exact equality test would reject correct matches. This
# is tight enough that two genuinely different figures on a page do not both
# match: NTAs of $1.71 and $1.72 are 0.6% apart.
MATCH_TOL = 0.002

# A label must win in this many DISTINCT months before it is a rule. One
# match is noise; three independent months is a layout.
MIN_SUPPORTING_MONTHS = 3

# A month in which this many labels match the exchange figure tells us little
# about which one is meaningful, so it counts as weak evidence.
AMBIGUOUS_MATCH_COUNT = 3

_NUM = re.compile(r"(?<![\w.])(\$?)\s*([0-9][0-9,]*\.?[0-9]*)\s*(c|cents|¢)?(?![\w])",
                  re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z/&-]*")
# a label is the words immediately before the figure; more than this and it
# starts swallowing the previous sentence
LABEL_WORDS = 8


def normalise_label(s: str) -> str:
    """Collapse a label to its comparable form.

    Case, punctuation, footnote markers and the month name all vary between
    issues of the SAME layout, and treating those as different labels would
    stop any label ever reaching three supporting months.
    """
    t = (s or "").lower()
    t = re.sub(r"\(.*?\)", " ", t)                       # "(pre-tax)" varies
    t = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
               r"[a-z]*\b", " ", t)
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"[^a-z ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _values(raw: str, cents_marker: str | None, dollar: str) -> list[tuple[float, str]]:
    """The value(s) this token could denote, each with its unit.

    A bare number on a LIC announcement is genuinely ambiguous - per-share
    NTAs are quoted both as dollars (1.23) and cents (123.45) - so both
    readings are offered and the exchange figure decides which was meant.
    That is the one thing the exchange may legitimately settle, because it
    is a fact about notation rather than about value.
    """
    try:
        v = float(raw.replace(",", ""))
    except ValueError:
        return []
    if v <= 0:
        return []
    if cents_marker:
        return [(v / 100.0, "cents")]
    if dollar:
        return [(v, "dollars")]
    return [(v, "dollars"), (v / 100.0, "cents")]


def candidates_from_text(text: str) -> list[dict]:
    """Every (label, value) pair the flowing text offers - not just a winner."""
    out: list[dict] = []
    for m in _NUM.finditer(text or ""):
        before = (text or "")[: m.start()]
        words = _WORD.findall(before)[-LABEL_WORDS:]
        label = normalise_label(" ".join(words))
        if not label:
            continue
        for val, unit in _values(m.group(2), m.group(3), m.group(1)):
            out.append({"label": label, "value": val, "unit": unit,
                        "pos": m.start(), "src": "text"})
    return out


def candidates_from_rows(rows: list[list[str]] | None) -> list[dict]:
    """Table rows: the label cells, then every numeric cell after them."""
    out: list[dict] = []
    for row in rows or []:
        cells = [str(c) if c is not None else "" for c in row]
        label_parts, started = [], False
        for c in cells:
            has_num = bool(re.search(r"[0-9]", c))
            if not has_num and not started:
                label_parts.append(c)
                continue
            started = True
            if "%" in c:
                continue
            for m in _NUM.finditer(c):
                label = normalise_label(" ".join(label_parts))
                if not label:
                    continue
                for val, unit in _values(m.group(2), m.group(3), m.group(1)):
                    out.append({"label": label, "value": val, "unit": unit,
                                "pos": 0, "src": "row"})
    return out


def candidates(text: str, rows: list[list[str]] | None = None) -> list[dict]:
    return candidates_from_rows(rows) + candidates_from_text(text)


def matches(cands: list[dict], truth: float, tol: float = MATCH_TOL) -> list[dict]:
    """Candidates equal to the exchange's figure, within tolerance."""
    if truth is None or not pd.notna(truth) or truth <= 0:
        return []
    return [c for c in cands
            if abs(c["value"] - float(truth)) / float(truth) <= tol]


def observe(ticker: str, month: str, text: str, rows, truth: float) -> list[dict]:
    """One fund-month's evidence: which labels carried the right figure.

    A month where many labels match is recorded with that count, so the
    aggregation can discount it rather than treat a coincidence as a layout.
    """
    cands = candidates(text, rows)
    hit = matches(cands, truth)
    uniq = {(c["label"], c["unit"]) for c in hit}
    return [{"ticker": str(ticker).upper(), "month": str(month),
             "label": lab, "unit": unit, "truth": float(truth),
             "n_matching_labels": len(uniq)}
            for lab, unit in uniq]


def discover(observations: pd.DataFrame,
             min_months: int = MIN_SUPPORTING_MONTHS) -> pd.DataFrame:
    """Aggregate per-fund evidence into candidate label rules.

    Emits every label with its support so a rejected one can be inspected;
    `is_rule` marks those that cleared the repetition bar on evidence that
    was not ambiguous.
    """
    cols = ["ticker", "label", "unit", "months_supporting", "months_clean",
            "first_month", "last_month", "is_rule"]
    if observations is None or not len(observations):
        return pd.DataFrame(columns=cols)
    agg: dict[tuple, dict] = defaultdict(
        lambda: {"months": set(), "clean": set()})
    for r in observations.itertuples(index=False):
        k = (r.ticker, r.label, r.unit)
        agg[k]["months"].add(r.month)
        if r.n_matching_labels < AMBIGUOUS_MATCH_COUNT:
            agg[k]["clean"].add(r.month)
    rows = []
    for (tick, lab, unit), v in agg.items():
        months = sorted(v["months"])
        rows.append({
            "ticker": tick, "label": lab, "unit": unit,
            "months_supporting": len(months),
            "months_clean": len(v["clean"]),
            "first_month": months[0], "last_month": months[-1],
            # the bar is on UNAMBIGUOUS months: three coincidences in three
            # crowded documents is not a layout
            "is_rule": len(v["clean"]) >= min_months,
        })
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["ticker", "months_clean", "months_supporting"],
                           ascending=[True, False, False]).reset_index(drop=True)


def holdout_split(tickers, fraction_held: float = 0.3) -> dict[str, str]:
    """Assign each fund to learn or holdout, stably.

    Learning labels from the exchange and then reporting agreement against
    the exchange is circular - it would take the rate from 68% to whatever we
    like and mean nothing. The held-out funds are never used to discover a
    rule, so their agreement rate is the only honest measure of whether this
    worked. Hashing the ticker keeps membership stable as funds are added.
    """
    cut = int(fraction_held * 10_000)
    return {str(t).upper():
            ("holdout" if zlib.crc32(str(t).upper().encode()) % 10_000 < cut
             else "learn")
            for t in dict.fromkeys(tickers)}
