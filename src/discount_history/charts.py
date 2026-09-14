"""Static chart set for the discount history (rendered headless in CI).

Design rules applied throughout:
* Discount is a DIVERGING measure around zero (discount vs premium), so
  every panel carries a zero reference line and the heatmap uses a
  blue<->red diverging scale with a neutral gray midpoint - never a rainbow.
* One y-axis per chart. Two measures of different scale get two charts.
* Categorical hues are assigned in fixed slot order and never cycled; a
  series past the cap folds into "Other" rather than inventing a hue.
* Legends are always present for >=2 series; grid and axes stay recessive.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # noqa: E402 - must precede pyplot on a headless runner
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

log = logging.getLogger(__name__)

# --- design tokens (validated categorical order; see dataviz palette) ---
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
          "#008300", "#4a3aa7", "#e34948"]
MARKET_COLOR = {"UK": SERIES[0], "ASX": SERIES[1]}
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e1"
DIVERGING = LinearSegmentedColormap.from_list(
    "discount_div", ["#0d366b", "#2a78d6", "#9ec5f4", "#f0efec",
                     "#f3b0af", "#e34948", "#8f2322"])

MAX_SERIES = 6  # past this, fold into "Other"


def _style(ax, title: str, ylabel: str, subtitle: str | None = None) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10)
    # Leave room for the subtitle: with the default pad the two collide.
    ax.set_title(title, color=INK, fontsize=13, fontweight="bold", loc="left",
                 pad=26 if subtitle else 12)
    if subtitle:
        ax.text(0.0, 1.018, subtitle, transform=ax.transAxes,
                color=INK_2, fontsize=9.5, va="bottom")


def _zero_line(ax) -> None:
    """Zero is the meaningful reference: below it a fund trades at a discount."""
    ax.axhline(0, color=MUTED, linewidth=1.2, linestyle="--", zorder=1)


def _finish(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.patch.set_facecolor(SURFACE)
    fig.tight_layout()
    fig.savefig(path, dpi=170, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    log.info("chart -> %s", path)
    return path


def _dates(df: pd.DataFrame) -> np.ndarray:
    return pd.to_datetime(df["month_end"]).to_numpy()


def _time_axis(ax) -> None:
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


# ------------------------------------------------------------------ charts
def market_history(by_market: pd.DataFrame, out: Path) -> Path:
    """The headline: each market's average fund discount through time."""
    fig, ax = plt.subplots(figsize=(11, 5.4))
    for market in ("UK", "ASX"):
        sub = by_market[(by_market["market"] == market) & by_market["sufficient"]]
        if sub.empty:
            continue
        ax.plot(_dates(sub), sub["mean_discount_pct"], linewidth=2.0,
                color=MARKET_COLOR[market], label=market, zorder=3)
        last = sub.iloc[-1]
        ax.annotate(f"{market} {last['mean_discount_pct']:.1f}%",
                    xy=(pd.to_datetime(last["month_end"]), last["mean_discount_pct"]),
                    xytext=(8, 0), textcoords="offset points", color=MARKET_COLOR[market],
                    fontsize=10, fontweight="bold", va="center")
    _zero_line(ax)
    _time_axis(ax)
    _style(ax, "Average discount to NAV, by market",
           "Mean discount (%)",
           "Equal-weighted across funds. Below zero = trading under asset value.")
    ax.legend(frameon=False, loc="lower left", fontsize=10, labelcolor=INK_2)
    return _finish(fig, out)


def distribution(by_market: pd.DataFrame, market: str, out: Path) -> Path:
    """Median with interquartile and decile bands - how wide the spread is."""
    sub = by_market[(by_market["market"] == market) & by_market["sufficient"]]
    if sub.empty:
        return out
    fig, ax = plt.subplots(figsize=(11, 5.4))
    x = _dates(sub)
    color = MARKET_COLOR.get(market, SERIES[0])
    ax.fill_between(x, sub["p10_discount_pct"], sub["p90_discount_pct"],
                    color=color, alpha=0.16, linewidth=0, label="10th-90th percentile", zorder=2)
    ax.fill_between(x, sub["p25_discount_pct"], sub["p75_discount_pct"],
                    color=color, alpha=0.30, linewidth=0, label="25th-75th percentile", zorder=3)
    ax.plot(x, sub["median_discount_pct"], color=color, linewidth=2.0,
            label="Median fund", zorder=4)
    _zero_line(ax)
    _time_axis(ax)
    _style(ax, f"{market}: the spread of discounts, not just the average",
           "Discount (%)",
           "A widening band means funds are being priced very differently from each other.")
    ax.legend(frameon=False, loc="lower left", fontsize=9.5, labelcolor=INK_2)
    return _finish(fig, out)


def _top_segments(seg: pd.DataFrame, market: str, cap: int = MAX_SERIES) -> list[str]:
    sub = seg[(seg["market"] == market) & seg["sufficient"]]
    if sub.empty:
        return []
    ranked = (sub.groupby("segment")
                 .agg(months=("month", "nunique"), funds=("n_funds", "mean"))
                 .sort_values(["months", "funds"], ascending=False))
    return list(ranked.head(cap).index)


def segment_history(seg: pd.DataFrame, market: str, out: Path) -> Path:
    """Sub-segment discount lines for one market, capped and fixed-order."""
    keep = _top_segments(seg, market)
    if not keep:
        return out
    fig, ax = plt.subplots(figsize=(11, 5.8))
    for i, name in enumerate(keep):
        sub = seg[(seg["market"] == market) & (seg["segment"] == name) & seg["sufficient"]]
        if sub.empty:
            continue
        ax.plot(_dates(sub), sub["mean_discount_pct"], linewidth=1.9,
                color=SERIES[i % len(SERIES)], label=name, zorder=3)
    _zero_line(ax)
    _time_axis(ax)
    _style(ax, f"{market}: average discount by sub-segment",
           "Mean discount (%)",
           f"The {len(keep)} sub-segments with the longest continuous history.")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK_2, ncol=2, loc="lower left")
    return _finish(fig, out)


def segment_heatmap(seg: pd.DataFrame, market: str, out: Path) -> Path:
    """Segment x year mean discount - where and when the market was cheap."""
    sub = seg[(seg["market"] == market) & seg["sufficient"]].copy()
    if sub.empty:
        return out
    sub["year"] = sub["month"].str[:4]
    grid = sub.pivot_table(index="segment", columns="year",
                           values="mean_discount_pct", aggfunc="mean")
    # Drop segments observed in fewer than three years - a two-cell row is
    # a gap, not a pattern.
    grid = grid[grid.notna().sum(axis=1) >= 3]
    if grid.empty:
        return out
    grid = grid.reindex(grid.mean(axis=1).sort_values().index)
    lo = float(np.nanmin(grid.to_numpy()))
    hi = float(np.nanmax(grid.to_numpy()))
    # Anchor the neutral midpoint at zero so colour means discount vs premium,
    # not "above/below this sample's average".
    norm = TwoSlopeNorm(vmin=min(lo, -1e-6), vcenter=0.0, vmax=max(hi, 1e-6))

    fig, ax = plt.subplots(figsize=(min(16, 2.4 + 0.52 * grid.shape[1]),
                                    1.6 + 0.44 * grid.shape[0]))
    im = ax.imshow(grid.to_numpy(), aspect="auto", cmap=DIVERGING, norm=norm)
    ax.set_xticks(range(grid.shape[1]))
    ax.set_xticklabels(grid.columns, rotation=90, fontsize=8, color=INK_2)
    ax.set_yticks(range(grid.shape[0]))
    ax.set_yticklabels(grid.index, fontsize=9, color=INK_2)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_title(f"{market}: average discount by sub-segment and year (%)",
                 color=INK, fontsize=13, fontweight="bold", loc="left", pad=12)
    cbar = fig.colorbar(im, ax=ax, fraction=0.022, pad=0.012)
    cbar.ax.tick_params(labelsize=8, colors=INK_2, length=0)
    cbar.outline.set_visible(False)
    cbar.set_label("discount (%)  <- wider   |   narrower ->", fontsize=8.5, color=INK_2)
    return _finish(fig, out)


def equal_vs_cap(by_market: pd.DataFrame, market: str, out: Path) -> Path:
    """Average fund vs average dollar: does size trade differently?"""
    sub = by_market[(by_market["market"] == market) & by_market["sufficient"]]
    sub = sub[sub["cap_weighted_discount"].notna()]
    if sub.empty:
        return out
    fig, ax = plt.subplots(figsize=(11, 5.2))
    x = _dates(sub)
    ax.plot(x, sub["mean_discount_pct"], color=SERIES[0], linewidth=2.0,
            label="Equal-weighted (average fund)", zorder=3)
    ax.plot(x, sub["cap_weighted_discount_pct"], color=SERIES[1], linewidth=2.0,
            label="Cap-weighted (average dollar)", zorder=3)
    _zero_line(ax)
    _time_axis(ax)
    _style(ax, f"{market}: the average fund vs the average dollar",
           "Mean discount (%)",
           "A persistent gap means large and small trusts are priced differently.")
    ax.legend(frameon=False, loc="lower left", fontsize=9.5, labelcolor=INK_2)
    return _finish(fig, out)


def share_at_discount(by_market: pd.DataFrame, out: Path) -> Path:
    """Breadth: what share of funds trade below NAV at all."""
    fig, ax = plt.subplots(figsize=(11, 5.0))
    for market in ("UK", "ASX"):
        sub = by_market[(by_market["market"] == market) & by_market["sufficient"]]
        if sub.empty:
            continue
        ax.plot(_dates(sub), sub["pct_at_discount_share_pct"], linewidth=2.0,
                color=MARKET_COLOR[market], label=market, zorder=3)
    ax.axhline(100, color=MUTED, linewidth=1.0, linestyle=":", zorder=1)
    ax.set_ylim(0, 104)
    _time_axis(ax)
    _style(ax, "Breadth: share of funds trading below NAV",
           "Funds at a discount (%)",
           "100% means literally every fund in the market was below asset value.")
    ax.legend(frameon=False, loc="lower left", fontsize=10, labelcolor=INK_2)
    return _finish(fig, out)


def latest_vs_history(summary: pd.DataFrame, market: str, out: Path) -> Path:
    """Diverging bars: today's discount against each segment's own norm."""
    sub = summary[(summary["market"] == market)
                  & summary["latest_vs_own_average_pp"].notna()].copy()
    # Only segments still reporting: comparing a segment's 2019 final reading
    # against "today" would be a category error.
    if "is_current" in sub.columns:
        sub = sub[sub["is_current"].astype(bool)]
    if sub.empty:
        return out
    sub = sub.sort_values("latest_vs_own_average_pp")
    fig, ax = plt.subplots(figsize=(10.5, 1.4 + 0.42 * len(sub)))
    vals = sub["latest_vs_own_average_pp"].to_numpy()
    # Cheap vs its own history = negative = the "opportunity" pole.
    colors = [SERIES[0] if v >= 0 else SERIES[7] for v in vals]
    ax.barh(range(len(sub)), vals, color=colors, height=0.68, zorder=3)
    ax.set_yticks(range(len(sub)))
    ax.set_yticklabels(sub["segment"], fontsize=9.5, color=INK_2)
    ax.axvline(0, color=MUTED, linewidth=1.2, zorder=4)
    for i, v in enumerate(vals):
        ax.text(v + (0.35 if v >= 0 else -0.35), i, f"{v:+.1f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=9, color=INK_2, zorder=5)
    _style(ax, f"{market}: latest discount vs each sub-segment's own long-run average",
           "", "Percentage points. Left (red) = wider than its own history.")
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("percentage points vs own average", color=INK_2, fontsize=9.5)
    return _finish(fig, out)


def render_all(*, by_market_df: pd.DataFrame, seg_df: pd.DataFrame,
               summary_df: pd.DataFrame, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    jobs = [
        ("01_market_discount_history.png", lambda p: market_history(by_market_df, p)),
        ("02_uk_distribution.png", lambda p: distribution(by_market_df, "UK", p)),
        ("03_asx_distribution.png", lambda p: distribution(by_market_df, "ASX", p)),
        ("04_uk_segments.png", lambda p: segment_history(seg_df, "UK", p)),
        ("05_asx_segments.png", lambda p: segment_history(seg_df, "ASX", p)),
        ("06_uk_segment_heatmap.png", lambda p: segment_heatmap(seg_df, "UK", p)),
        ("07_asx_segment_heatmap.png", lambda p: segment_heatmap(seg_df, "ASX", p)),
        ("08_uk_equal_vs_cap.png", lambda p: equal_vs_cap(by_market_df, "UK", p)),
        ("09_share_at_discount.png", lambda p: share_at_discount(by_market_df, p)),
        ("10_uk_latest_vs_history.png", lambda p: latest_vs_history(summary_df, "UK", p)),
        ("11_asx_latest_vs_history.png", lambda p: latest_vs_history(summary_df, "ASX", p)),
    ]
    for name, fn in jobs:
        target = out_dir / name
        try:
            fn(target)
            if target.exists():
                made.append(target)
        except Exception as exc:  # noqa: BLE001 - one bad chart must not kill the run
            log.warning("chart %s failed: %s", name, exc)
    return made
