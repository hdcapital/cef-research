"""Portfolio construction and backtest engine.

Timing model (look-ahead safe by construction):

- ``date`` on a panel row is the month-end the information refers to
  (signal_date).
- AIC publishes month-end data ~5-6 working days later, so a portfolio
  using month-t information is formed after publication and earns the
  return of calendar month t+1 (``fwd_return`` on the row, which the panel
  builder aligns as the security's total return over month t+1).
- holding_period_start = first trading day after formation;
  holding_period_end = end of month t+1.

Missing-return policy: a security selected into the portfolio whose next
month return is unobserved is NEVER assigned 0 or -100%. The month's
portfolio return is computed over the observed names (weights
renormalised) and the missing weight is recorded on the result so the
report can disclose it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

REBALANCE_MONTHS = {"monthly": 1, "quarterly": 3}


@dataclass
class BacktestResult:
    name: str
    monthly: pd.DataFrame = field(default_factory=pd.DataFrame)
    # columns: holding_month(Period), gross_return, buy_turnover,
    # sell_turnover, n_holdings, missing_return_weight, n_missing_return
    holdings: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def gross_returns(self) -> pd.Series:
        if self.monthly.empty:
            return pd.Series(dtype=float)
        s = self.monthly.set_index("holding_month")["gross_return"]
        s.index = pd.PeriodIndex(s.index, freq="M").to_timestamp(how="end").normalize()
        return s


def _weights(sel: pd.DataFrame, scheme: str, cap_mult: float = 3.0) -> pd.Series:
    n = len(sel)
    if n == 0:
        return pd.Series(dtype=float)
    if scheme == "equal":
        w = pd.Series(1.0 / n, index=sel.index)
    elif scheme == "market_cap":
        mc = sel["market_cap"]
        w = mc / mc.sum()
    elif scheme == "capped_inverse_rank":
        # rank 1 = best signal; weight proportional to (n + 1 - rank),
        # capped at cap_mult x equal weight, iteratively renormalised.
        ranks = sel["__signal"].rank(method="first")
        raw = (n + 1 - ranks).astype(float)
        w = raw / raw.sum()
        cap = cap_mult / n
        for _ in range(20):
            over = w > cap
            if not over.any():
                break
            excess = (w[over] - cap).sum()
            w[over] = cap
            under = ~over
            if w[under].sum() > 0:
                w[under] += excess * w[under] / w[under].sum()
            else:
                break
    else:
        raise ValueError(f"unknown weighting scheme {scheme!r}")
    return w / w.sum()


def _select(
    at_t: pd.DataFrame,
    signal_col: str,
    top_fraction: float,
    sector_neutral: bool,
    min_names: int,
) -> pd.DataFrame | None:
    """Pick the cheapest ``top_fraction`` by ascending signal. Returns None
    if the eligible cross-section is too small this month."""
    elig = at_t.dropna(subset=[signal_col])
    if len(elig) < min_names:
        return None
    if not sector_neutral:
        n_pick = max(1, int(round(top_fraction * len(elig))))
        sel = elig.nsmallest(n_pick, signal_col).copy()
        sel["__sector_w"] = np.nan
        return sel
    picks = []
    sectors = elig.groupby("sector")
    for sector, grp in sectors:
        if len(grp) < 3:
            continue  # a 1-2 name 'sector' cannot be ranked meaningfully
        k = max(1, int(round(top_fraction * len(grp))))
        p = grp.nsmallest(k, signal_col).copy()
        p["__sector_w"] = len(grp)  # sector weight proportional to its size
        picks.append(p)
    if not picks:
        return None
    sel = pd.concat(picks)
    if len(sel) < min_names:
        return None
    return sel


def run_strategy(
    panel: pd.DataFrame,
    signal_col: str,
    name: str,
    top_fraction: float = 0.10,
    weighting: str = "equal",
    rebalance: str = "monthly",
    sector_neutral: bool = False,
    min_market_cap: float | None = None,
    min_names: int = 5,
) -> BacktestResult:
    """Run a long-only, unlevered, ascending-signal decile-style strategy.

    ``panel`` needs: date, security_id, sector (if sector_neutral),
    market_cap (if cap filters/weighting), the signal column, fwd_return,
    and optionally fwd_return_status.
    """
    if rebalance not in REBALANCE_MONTHS:
        raise ValueError(f"unknown rebalance {rebalance!r}")
    step = REBALANCE_MONTHS[rebalance]

    panel = panel.copy()
    panel["__period"] = panel["date"].dt.to_period("M")
    dates = sorted(panel["__period"].unique())

    prev_weights = pd.Series(dtype=float)  # index: security_id, drifted
    current_sel: pd.DataFrame | None = None
    months_since_rebalance = step  # force selection at first date

    monthly_rows = []
    holdings_rows = []

    for t in dates:
        at_t = panel[panel["__period"] == t]
        if min_market_cap is not None:
            at_t = at_t[at_t["market_cap"].notna() & (at_t["market_cap"] >= min_market_cap)]
        if weighting == "market_cap":
            at_t = at_t[at_t["market_cap"].notna()]

        reselect = months_since_rebalance >= step
        if reselect:
            sel = _select(at_t, signal_col, top_fraction, sector_neutral, min_names)
            if sel is not None:
                sel = sel.copy()
                sel["__signal"] = sel[signal_col]
                current_sel = sel
                months_since_rebalance = 0
            else:
                # selection impossible this month (fewer than min_names
                # qualifiers): HOLD the previous book, but refresh to THIS
                # month's panel rows so fwd_return is the current holding
                # month's return - reusing last month's rows would relabel
                # stale returns (and double-count them).
                reselect = False
                if current_sel is not None:
                    held = at_t[at_t["security_id"].isin(prev_weights.index)]
                    current_sel = held.copy()
                    current_sel["__signal"] = current_sel.get(signal_col)
                    current_sel["__sector_w"] = np.nan
        else:
            # carry existing names forward at their drifted weights, using
            # this month's row for each held security (for fwd_return).
            if current_sel is not None:
                held = at_t[at_t["security_id"].isin(prev_weights.index)]
                current_sel = held.copy()
                current_sel["__signal"] = current_sel.get(signal_col)
                current_sel["__sector_w"] = np.nan

        months_since_rebalance += 1
        if current_sel is None or current_sel.empty:
            prev_weights = pd.Series(dtype=float)
            continue

        sel = current_sel
        if reselect:
            if sector_neutral and sel["__sector_w"].notna().any():
                w = pd.Series(0.0, index=sel.index)
                for sector, grp in sel.groupby("sector"):
                    sw = grp["__sector_w"].iloc[0]
                    w[grp.index] = sw / len(grp)
                w = w / w.sum()
            else:
                w = _weights(sel, weighting)
        else:
            # drifted previous weights mapped onto this month's rows
            w = sel["security_id"].map(prev_weights)
            w = w[w.notna()]
            if w.empty:
                prev_weights = pd.Series(dtype=float)
                continue
            sel = sel.loc[w.index]
            w = w / w.sum()

        new_by_sid = pd.Series(w.values, index=sel["security_id"].values)

        # turnover vs drifted previous book
        all_sids = new_by_sid.index.union(prev_weights.index)
        nw = new_by_sid.reindex(all_sids, fill_value=0.0)
        ow = prev_weights.reindex(all_sids, fill_value=0.0)
        buy_turnover = float((nw - ow).clip(lower=0).sum())
        sell_turnover = float((ow - nw).clip(lower=0).sum())

        # portfolio return over month t+1
        fr = sel["fwd_return"]
        observed = fr.notna()
        missing_weight = float(w[~observed].sum())
        n_missing = int((~observed).sum())
        if observed.any():
            w_obs = w[observed] / w[observed].sum()
            gross = float((w_obs * fr[observed]).sum())
        else:
            gross = np.nan

        holding_month = t + 1
        monthly_rows.append(
            {
                "signal_month": t,
                "holding_month": holding_month,
                "gross_return": gross,
                "buy_turnover": buy_turnover,
                "sell_turnover": sell_turnover,
                "n_holdings": len(sel),
                "missing_return_weight": missing_weight,
                "n_missing_return": n_missing,
                "rebalanced": bool(reselect),
            }
        )
        for idx, row in sel.iterrows():
            holdings_rows.append(
                {
                    "formation_date": t.to_timestamp(how="end").normalize(),
                    "holding_month": str(holding_month),
                    "security_id": row["security_id"],
                    "company_name": row.get("company_name"),
                    "sector": row.get("sector"),
                    "discount": row.get("discount"),
                    "discount_z": row.get("discount_z_36m"),
                    "discount_change_3m": row.get("discount_change_3m"),
                    "catalyst": row.get("catalyst_flag"),
                    "weight": float(w.loc[idx]),
                    "next_month_return": row.get("fwd_return"),
                    "return_status": row.get("fwd_return_status", "observed" if pd.notna(row.get("fwd_return")) else "missing"),
                    "strategy": name,
                }
            )

        # drift weights through month t+1 for next month's turnover calc;
        # names with missing returns drift at the portfolio's observed
        # return (neutral assumption for TURNOVER ACCOUNTING ONLY - never
        # enters the return series).
        drift_r = fr.copy()
        if observed.any():
            drift_r[~observed] = float((w[observed] / w[observed].sum() * fr[observed]).sum())
        else:
            drift_r[:] = 0.0
        drifted = w * (1 + drift_r)
        total = drifted.sum()
        prev_weights = (
            pd.Series(drifted.values / total, index=sel["security_id"].values)
            if total > 0
            else pd.Series(dtype=float)
        )

    monthly = pd.DataFrame(monthly_rows)
    holdings = pd.DataFrame(holdings_rows)
    return BacktestResult(name=name, monthly=monthly, holdings=holdings)


def benchmark_universe(
    panel: pd.DataFrame, weighting: str = "equal", name: str | None = None
) -> BacktestResult:
    """Benchmark: hold every eligible trust in the point-in-time universe."""
    return run_strategy(
        panel.assign(__all=0.0),
        signal_col="__all",
        name=name or f"benchmark_{weighting}",
        top_fraction=1.0,
        weighting=weighting,
        min_names=1,
    )
