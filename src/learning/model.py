"""Phase 3b step 3: a small model on the model-free anticipation flags.

A logistic regression, ridge-penalised, on binary flags - fitted on
observations up to `split_month` and scored on the months after it, never
the other way round and never a fund-based split. It reports what a
trader needs to judge it: the area under the ROC on the held-out months,
the resolution rate by decile of predicted probability against the base
rate, calibration per decile, and the coefficients. Newton's method in
numpy - a dozen binary flags need nothing more - so it runs wherever the
evaluation runs.

Nothing here reaches the live gates: a model that clears the holdout
still needs a dated CHANGELOG entry before any threshold moves.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from learning import schema as S


def design(frame: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Binary columns: 1 where the flag is not its silent value."""
    X = pd.DataFrame(index=frame.index)
    for c in feature_cols:
        if c in frame.columns:
            X[c] = (frame[c].notna() & frame[c].ne(S.SILENT.get(c, "none"))).astype(float)
    return X


def fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    """Ridge-penalised logistic regression by Newton's method; the
    intercept is not penalised. Returns [intercept, coef...]."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    b = np.zeros(k + 1)
    pen = np.full(k + 1, l2)
    pen[0] = 0.0
    for _ in range(iters):
        z = Xb @ b
        p = 1.0 / (1.0 + np.exp(-z))
        g = Xb.T @ (p - y) + pen * b
        w = p * (1.0 - p)
        H = (Xb * w[:, None]).T @ Xb + np.diag(pen)
        step = np.linalg.solve(H + 1e-9 * np.eye(k + 1), g)
        b = b - step
        if np.abs(step).max() < 1e-8:
            break
    return b


def predict(beta: np.ndarray, X: np.ndarray) -> np.ndarray:
    z = beta[0] + X @ beta[1:]
    return 1.0 / (1.0 + np.exp(-z))


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """Rank-based (Mann-Whitney) area under the ROC."""
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(p).rank().values
    return float((ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def deciles(y: np.ndarray, p: np.ndarray, n: int = 10) -> pd.DataFrame:
    """Resolution rate and mean predicted probability by decile of p (10 = highest)."""
    df = pd.DataFrame({"y": y, "p": p})
    try:
        df["decile"] = pd.qcut(df["p"].rank(method="first"), n, labels=range(1, n + 1))
    except ValueError:
        df["decile"] = 1
    base = df["y"].mean()
    g = df.groupby("decile", observed=True).agg(n=("y", "size"), resolved=("y", "sum"),
                                                rate=("y", "mean"), mean_p=("p", "mean"))
    g["lift"] = g["rate"] / base if base else np.nan
    return g.reset_index()


def run(frame: pd.DataFrame, feature_cols: list[str], label: str = "resolved_within_12m",
        split_month: str = "2021-12", l2: float = 1.0) -> dict:
    """Fit on obs_month <= split_month, score on later months."""
    f = frame[frame[label].notna()].copy()
    X = design(f, feature_cols)
    cols = [c for c in X.columns if X[c].sum() > 0]
    X = X[cols]
    train = f["obs_month"] <= split_month
    test = ~train
    if train.sum() < 100 or test.sum() < 100 or f.loc[train, label].sum() < 10:
        return {"error": "not enough observations on one side of the split",
                "train_rows": int(train.sum()), "test_rows": int(test.sum())}
    beta = fit_logit(X[train].values, f.loc[train, label].values.astype(float), l2=l2)
    p_test = predict(beta, X[test].values)
    y_test = f.loc[test, label].values.astype(float)
    p_train = predict(beta, X[train].values)
    y_train = f.loc[train, label].values.astype(float)
    coef = pd.DataFrame({"feature": ["intercept"] + cols, "coef": beta,
                         "odds_ratio": np.exp(beta)})
    dec = deciles(y_test, p_test)
    return {"label": label, "split_month": split_month, "features": cols,
            "train_rows": int(train.sum()), "test_rows": int(test.sum()),
            "train_base_rate": float(y_train.mean()), "test_base_rate": float(y_test.mean()),
            "auc_train": auc(y_train, p_train), "auc_test": auc(y_test, p_test),
            "top_decile_rate": float(dec["rate"].iloc[-1]) if len(dec) else None,
            "top_decile_lift": float(dec["lift"].iloc[-1]) if len(dec) else None,
            "coefficients": coef, "deciles": dec}
