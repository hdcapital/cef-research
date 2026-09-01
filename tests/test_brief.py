"""The HTML brief must render real verdict rows - NaN round-trips included."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cef_live import brief


def _verdicts() -> pd.DataFrame:
    # a CSV round-trip turns evaluate()'s Nones into NaN - the shape the
    # renderer actually receives in production
    return pd.DataFrame([
        {"security_id": "A", "name": "Fund & Trust <plc>", "market": "UK",
         "verdict": "WATCH", "gate1_dislocation": True,
         "gate2_catalyst": False, "gate3_return": True, "z_adj": -2.5,
         "discount_est": -0.30, "irr_central": 0.18,
         "catalyst_class": np.nan, "catalyst_date": np.nan,
         "catalyst_headline": np.nan},
        {"security_id": "B", "name": "No-data fund", "market": "AU",
         "verdict": "WATCH", "gate1_dislocation": False,
         "gate2_catalyst": True, "gate3_return": False, "z_adj": np.nan,
         "discount_est": np.nan, "irr_central": np.nan,
         "catalyst_class": "tender_offer", "catalyst_date": "2026-09-01",
         "catalyst_headline": "Off-market tender"},
    ])


def test_the_brief_renders_nan_as_a_dash_and_escapes_names():
    v = _verdicts()
    html = brief.render_html(
        "pre-LSE open", "2026-09-01", evaluated=399,
        opps=v.iloc[0:0], disl=v[v["gate1_dislocation"]],
        irr_led=v.iloc[0:0], cat_led=v[~v["gate1_dislocation"]],
        z_threshold=-1.5, min_irr=0.15,
        wb_summary={"rows": 1116, "live": 399, "with_live_nav": 713,
                    "with_irr": 546, "catalysts": 117},
        wb_error=None, n_delist=2, n_watch=2)
    assert "nan" not in html.lower().replace("nan-", "")  # no leaked NaN text
    assert "Fund &amp; Trust &lt;plc&gt;" in html          # names escaped
    assert "Pre-LSE open brief" in html                    # label case kept
    assert "&#8805;" not in html and "&amp;#" not in html  # entities literal
    assert "tender_offer" in html
    assert "Off-market tender" in html


def test_an_empty_day_still_renders():
    v = _verdicts().iloc[0:0]
    html = brief.render_html(
        "pre-ASX open", "2026-09-01", evaluated=399, opps=v, disl=v,
        irr_led=v, cat_led=v, z_threshold=-1.5, min_irr=0.15,
        wb_summary={}, wb_error="boom", n_delist=0, n_watch=0)
    assert "No fund clears all three gates" in html
    assert "boom" in html
