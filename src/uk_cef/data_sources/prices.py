"""External free price sources - VALIDATION ONLY, disabled by default.

The backtest's return series comes entirely from AIC MIR month-end prices
(point-in-time, survivorship-free). This module exists to CROSS-CHECK a
sample of those prices against an independent public source, per the
project's price-validation requirement. It is NOT used to fill gaps and it
never contributes a return observation to the backtest.

Stooq (stooq.com) offers free daily OHLC CSVs for LSE-listed shares under
symbol "<ticker>.uk". Coverage is essentially live tickers only - delisted
trusts are absent, which is exactly why it cannot be a return source for a
survivorship-free backtest. Before any fetch the adapter checks robots.txt
and stays within a strict request budget; enable it explicitly with
`validate --external` (CI) after satisfying yourself that your use complies
with the source's terms (https://stooq.com - see their site policies).
"""

from __future__ import annotations

import io
import logging
import time
import urllib.parse

import pandas as pd
import requests

log = logging.getLogger(__name__)

STOOQ_DAILY = "https://stooq.com/q/d/l/?s={symbol}&i=m"
UA = "uk-cef-research/0.1 (price cross-validation; contact via repo)"
THROTTLE = 2.0
MAX_REQUESTS = 40  # strict budget: this is a spot-check, not a crawl


class StooqValidator:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self._used = 0
        self._last = 0.0
        self._robots_ok: bool | None = None

    def _allowed(self) -> bool:
        if self._robots_ok is None:
            try:
                r = self.session.get("https://stooq.com/robots.txt", timeout=30)
                disallow_all = False
                active = False
                for line in r.text.splitlines():
                    line = line.split("#", 1)[0].strip().lower()
                    if line.startswith("user-agent"):
                        active = line.split(":", 1)[1].strip() == "*"
                    elif active and line.startswith("disallow"):
                        path = line.split(":", 1)[1].strip()
                        if path in ("/", "/q/"):
                            disallow_all = True
                self._robots_ok = not disallow_all
            except Exception as exc:  # noqa: BLE001
                log.warning("robots.txt check failed (%s); external validation skipped", exc)
                self._robots_ok = False
        return self._robots_ok

    def monthly_closes(self, ticker: str) -> pd.Series | None:
        """Monthly close series for an LSE ticker (pence), or None."""
        if not self._allowed() or self._used >= MAX_REQUESTS:
            return None
        wait = THROTTLE - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self._used += 1
        symbol = urllib.parse.quote(f"{ticker.lower()}.uk")
        try:
            r = self.session.get(STOOQ_DAILY.format(symbol=symbol), timeout=60)
            if r.status_code != 200 or not r.text.startswith("Date"):
                return None
            df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"])
            s = df.set_index(df["Date"].dt.to_period("M"))["Close"]
            return s[~s.index.duplicated(keep="last")]
        except Exception as exc:  # noqa: BLE001
            log.warning("stooq fetch failed for %s: %s", ticker, exc)
            return None


def cross_validate(panel: pd.DataFrame, n_securities: int = 25) -> pd.DataFrame:
    """Compare MIR month-end prices against Stooq monthly closes for a
    sample of securities that carry a TIDM ticker. Returns a comparison
    frame with median absolute % difference per security."""
    if "ticker" not in panel.columns:
        return pd.DataFrame()
    tickers = (
        panel.dropna(subset=["ticker"])
        .groupby("security_id")["ticker"]
        .last()
        .head(n_securities)
    )
    validator = StooqValidator()
    rows = []
    for sid, ticker in tickers.items():
        ext = validator.monthly_closes(str(ticker))
        if ext is None or ext.empty:
            rows.append({"security_id": sid, "ticker": ticker, "status": "unavailable"})
            continue
        ours = panel[panel["security_id"] == sid].set_index(
            pd.PeriodIndex(panel[panel["security_id"] == sid]["obs_month"], freq="M")
        )["share_price"]
        joined = pd.concat({"mir": ours, "ext": ext}, axis=1).dropna()
        if joined.empty:
            rows.append({"security_id": sid, "ticker": ticker, "status": "no_overlap"})
            continue
        # Stooq LSE quotes are usually pence, matching MIR; a x100 ratio
        # indicates pounds - normalise before comparing.
        ratio = (joined["mir"] / joined["ext"]).median()
        scale = 100.0 if 50 < ratio < 200 else (0.01 if 0.005 < ratio < 0.02 else 1.0)
        diff = (joined["mir"] / (joined["ext"] * scale) - 1).abs()
        rows.append(
            {
                "security_id": sid,
                "ticker": ticker,
                "status": "ok",
                "months_compared": len(joined),
                "median_abs_pct_diff": float(diff.median()),
                "p90_abs_pct_diff": float(diff.quantile(0.9)),
                "scale_applied": scale,
            }
        )
    return pd.DataFrame(rows)
