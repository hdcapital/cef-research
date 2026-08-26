"""Transaction cost modelling.

Costs are applied to the monthly gross return series using the backtest's
own recorded one-way turnover:

    net_r = gross_r - buy_turnover * (one_way + stamp_duty_effective)
                    - sell_turnover * one_way

Stamp duty / SDRT: purchases of shares in UK-domiciled investment trusts
attract 0.5% stamp duty; companies domiciled offshore (Guernsey, Jersey,
etc.) are generally exempt. When per-holding domicile is available the
engine passes the duty-applicable fraction of each month's buys; otherwise
a conservative configurable default fraction is used and reported.
"""

from __future__ import annotations

import pandas as pd


def apply_costs(
    monthly: pd.DataFrame,
    one_way_bps: float,
    stamp_duty_bps: float = 0.0,
    duty_applicable_fraction: pd.Series | float = 1.0,
) -> pd.DataFrame:
    """Return a copy of the monthly frame with cost and net_return columns.

    duty_applicable_fraction: scalar or per-month Series (aligned on the
    monthly frame's index) giving the fraction of buy turnover subject to
    stamp duty.
    """
    out = monthly.copy()
    one_way = one_way_bps / 10_000.0
    duty = stamp_duty_bps / 10_000.0
    frac = duty_applicable_fraction
    if isinstance(frac, pd.Series):
        frac = frac.reindex(out.index).fillna(1.0)  # conservative: unknown => dutiable
    out["cost"] = out["buy_turnover"] * (one_way + duty * frac) + out["sell_turnover"] * one_way
    out["net_return"] = out["gross_return"] - out["cost"]
    return out


def cost_scenarios(
    monthly: pd.DataFrame,
    scenarios_bps: list[float],
    stamp_duty_bps: float,
    duty_applicable_fraction: pd.Series | float = 1.0,
) -> dict[str, pd.DataFrame]:
    """Standard scenario grid: each bps level without and (once) with duty."""
    out = {}
    for bps in scenarios_bps:
        out[f"{int(bps)}bps"] = apply_costs(monthly, bps, 0.0)
    out["headline"] = apply_costs(
        monthly, scenarios_bps[-1] if scenarios_bps else 50.0, stamp_duty_bps, duty_applicable_fraction
    )
    return out
