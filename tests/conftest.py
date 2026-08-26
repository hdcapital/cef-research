import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
import pytest

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "probe" / "samples"


@pytest.fixture
def samples_dir() -> Path:
    if not SAMPLES.exists():
        pytest.skip("probe samples not present")
    return SAMPLES


@pytest.fixture
def toy_panel() -> pd.DataFrame:
    """Small deterministic panel for engine tests (synthetic data is used in
    UNIT TESTS ONLY - never in backtest results)."""
    months = pd.period_range("2015-01", "2016-12", freq="M")
    rows = []
    rng = np.random.RandomState(7)
    for sid, base_disc, sector in [
        ("S1", -0.20, "Global"), ("S2", -0.10, "Global"), ("S3", -0.05, "Global"),
        ("S4", -0.15, "UK Equity Income"), ("S5", 0.02, "UK Equity Income"),
        ("S6", -0.30, "UK Equity Income"),
    ]:
        nav = 100.0
        for i, m in enumerate(months):
            nav *= 1 + rng.normal(0.004, 0.02)
            disc = base_disc + 0.03 * np.sin(i / 4 + hash(sid) % 5)
            price = nav * (1 + disc)
            rows.append(
                {
                    "date": m.to_timestamp(how="end").normalize(),
                    "obs_month": str(m),
                    "security_id": sid,
                    "company_name": sid,
                    "sector": sector,
                    "discount": disc,
                    "share_price": price,
                    "nav_per_share": nav,
                    "market_cap": 100 + 10 * i,
                    "shares": 1_000_000,
                }
            )
    df = pd.DataFrame(rows)
    df = df.sort_values(["security_id", "date"])
    df["fwd_price"] = df.groupby("security_id")["share_price"].shift(-1)
    df["fwd_return"] = df["fwd_price"] / df["share_price"] - 1
    df["fwd_return_month"] = (
        pd.PeriodIndex(df["obs_month"], freq="M") + 1
    ).astype(str)
    df.loc[df["fwd_price"].isna(), "fwd_return_month"] = None
    df = df.drop(columns=["fwd_price"])
    df["fwd_return_status"] = np.where(df["fwd_return"].notna(), "observed", "missing_next_price")
    df["catalyst_flag"] = False
    return df
