"""Performance metrics for monthly return series.

All inputs are pandas Series of simple monthly returns indexed by month-end
Timestamps (or monthly Periods). Missing months must be absent from the
index, never zero-filled — every function here treats NaN as "no
observation" and refuses to annualise inadequate samples.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MONTHS_PER_YEAR = 12
MIN_MONTHS_FOR_CAGR = 24  # refuse to annualise shorter samples


def cumulative_return(returns: pd.Series) -> float:
    r = returns.dropna()
    if r.empty:
        return np.nan
    return float((1 + r).prod() - 1)


def cagr(returns: pd.Series, min_months: int = MIN_MONTHS_FOR_CAGR) -> float:
    r = returns.dropna()
    if len(r) < min_months:
        return np.nan
    years = len(r) / MONTHS_PER_YEAR
    total = (1 + r).prod()
    if total <= 0:
        return np.nan
    return float(total ** (1 / years) - 1)


def annual_volatility(returns: pd.Series, min_months: int = 12) -> float:
    r = returns.dropna()
    if len(r) < min_months:
        return np.nan
    return float(r.std(ddof=1) * np.sqrt(MONTHS_PER_YEAR))


def sharpe(returns: pd.Series, rf_annual: float = 0.0, min_months: int = 24) -> float:
    """Sharpe on monthly excess returns vs a constant annual risk-free rate
    (default 0; documented in the report)."""
    r = returns.dropna()
    if len(r) < min_months:
        return np.nan
    rf_m = (1 + rf_annual) ** (1 / MONTHS_PER_YEAR) - 1
    ex = r - rf_m
    sd = ex.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return np.nan
    return float(ex.mean() / sd * np.sqrt(MONTHS_PER_YEAR))


def sortino(returns: pd.Series, rf_annual: float = 0.0, min_months: int = 24) -> float:
    r = returns.dropna()
    if len(r) < min_months:
        return np.nan
    rf_m = (1 + rf_annual) ** (1 / MONTHS_PER_YEAR) - 1
    ex = r - rf_m
    downside = ex[ex < 0]
    if len(downside) == 0:
        return np.nan
    dd = np.sqrt((downside**2).sum() / len(ex))
    if dd == 0:
        return np.nan
    return float(ex.mean() / dd * np.sqrt(MONTHS_PER_YEAR))


def drawdown_series(returns: pd.Series) -> pd.Series:
    r = returns.dropna()
    wealth = (1 + r).cumprod()
    return wealth / wealth.cummax() - 1


def max_drawdown(returns: pd.Series) -> float:
    dd = drawdown_series(returns)
    if dd.empty:
        return np.nan
    return float(dd.min())


def calmar(returns: pd.Series) -> float:
    g = cagr(returns)
    mdd = max_drawdown(returns)
    if np.isnan(g) or np.isnan(mdd) or mdd == 0:
        return np.nan
    return float(g / abs(mdd))


def alpha_beta(returns: pd.Series, benchmark: pd.Series, min_months: int = 24):
    """OLS monthly alpha (annualised, simple x12) and beta vs benchmark on
    the overlapping sample. Returns (alpha_annual, beta, t_alpha, n)."""
    df = pd.concat({"r": returns, "b": benchmark}, axis=1).dropna()
    if len(df) < min_months:
        return np.nan, np.nan, np.nan, len(df)
    x = df["b"].values
    y = df["r"].values
    X = np.column_stack([np.ones_like(x), x])
    coef, residuals, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = coef
    resid = y - X @ coef
    dof = len(y) - 2
    if dof <= 0:
        return float(a * 12), float(b), np.nan, len(df)
    s2 = (resid**2).sum() / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    t_a = a / np.sqrt(cov[0, 0]) if cov[0, 0] > 0 else np.nan
    return float(a * 12), float(b), float(t_a), len(df)


def information_ratio(returns: pd.Series, benchmark: pd.Series, min_months: int = 24) -> float:
    df = pd.concat({"r": returns, "b": benchmark}, axis=1).dropna()
    if len(df) < min_months:
        return np.nan
    active = df["r"] - df["b"]
    sd = active.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return np.nan
    return float(active.mean() / sd * np.sqrt(MONTHS_PER_YEAR))


def t_stat_mean(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return np.nan
    sd = r.std(ddof=1)
    if sd == 0:
        return np.nan
    return float(r.mean() / (sd / np.sqrt(len(r))))


def annual_returns(returns: pd.Series) -> pd.Series:
    """Calendar-year compounded returns. Years with fewer than 12 observed
    months are labelled partial via the index (e.g. '2007(11m)')."""
    r = returns.dropna()
    if r.empty:
        return pd.Series(dtype=float)
    idx = pd.PeriodIndex(r.index, freq="M") if not isinstance(r.index, pd.PeriodIndex) else r.index
    df = pd.DataFrame({"r": r.values, "year": idx.year})
    out = {}
    for year, grp in df.groupby("year"):
        label = str(year) if len(grp) == 12 else f"{year}({len(grp)}m)"
        out[label] = float((1 + grp["r"]).prod() - 1)
    return pd.Series(out)


def summarize(
    returns: pd.Series,
    benchmark: pd.Series | None = None,
    name: str = "strategy",
) -> dict:
    r = returns.dropna()
    stats = {
        "name": name,
        "months": len(r),
        "start": str(r.index.min()) if len(r) else None,
        "end": str(r.index.max()) if len(r) else None,
        "cumulative_return": cumulative_return(r),
        "cagr": cagr(r),
        "volatility": annual_volatility(r),
        "sharpe": sharpe(r),
        "sortino": sortino(r),
        "max_drawdown": max_drawdown(r),
        "calmar": calmar(r),
        "best_month": float(r.max()) if len(r) else np.nan,
        "worst_month": float(r.min()) if len(r) else np.nan,
        "pct_positive_months": float((r > 0).mean()) if len(r) else np.nan,
        "t_stat_mean": t_stat_mean(r),
    }
    if benchmark is not None:
        a, b, t_a, n = alpha_beta(r, benchmark)
        stats.update(
            {
                "alpha_annual_vs_benchmark": a,
                "beta_vs_benchmark": b,
                "alpha_t_stat": t_a,
                "information_ratio": information_ratio(r, benchmark),
                "overlap_months": n,
            }
        )
    return stats
