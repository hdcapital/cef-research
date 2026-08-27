"""Build the print/PDF edition of the research report.

Generates a print-styled HTML document from the narrative plus appendices
rendered directly from outputs/*.csv, prints it to PDF with headless
Chromium, and stamps page numbers. Rerunnable after any pipeline run:

    python scripts/make_pdf_report.py
"""

from __future__ import annotations

import base64
import html as html_mod
import subprocess
import sys
from pathlib import Path

import pandas as pd

OUT = Path("outputs")
CHARTS = OUT / "charts"
BUILD = Path("outputs/pdf")
BUILD.mkdir(parents=True, exist_ok=True)

INK, INK2, GILT, LINE, POS, NEG = "#16232e", "#4c5b66", "#a8721c", "#d9ddd8", "#1e6e4a", "#a83232"


def esc(s) -> str:
    return html_mod.escape(str(s))


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def pct(x, digits=1):
    try:
        v = float(x)
        if pd.isna(v):
            return "–"
        return f"{100 * v:.{digits}f}%"
    except (TypeError, ValueError):
        return "–"


def num(x, digits=2):
    try:
        v = float(x)
        if pd.isna(v):
            return "–"
        return f"{v:.{digits}f}"
    except (TypeError, ValueError):
        return "–"


def table(df: pd.DataFrame, cols: dict, title: str, note: str = "", small=False) -> str:
    """cols: {csv_col: (header, formatter)}"""
    rows = []
    for _, r in df.iterrows():
        tds = "".join(f"<td>{fmt(r.get(c))}</td>" for c, (_, fmt) in cols.items())
        rows.append(f"<tr>{tds}</tr>")
    ths = "".join(f"<th>{h}</th>" for _, (h, _) in cols.items())
    cls = "tbl small" if small else "tbl"
    note_html = f'<div class="tnote">{note}</div>' if note else ""
    return (f'<div class="tblock"><div class="ttitle">{title}</div>'
            f'<table class="{cls}"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>{note_html}</div>')


def read(name: str) -> pd.DataFrame:
    p = OUT / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def main() -> int:
    css = f"""
    @page {{ size: A4; margin: 22mm 18mm 20mm 18mm; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: "DejaVu Serif", serif; color: {INK}; font-size: 10.2pt;
            line-height: 1.55; margin: 0; }}
    h1 {{ font-size: 27pt; line-height: 1.05; margin: 0 0 10pt; letter-spacing: -0.3pt; }}
    h2 {{ font-size: 15pt; margin: 20pt 0 7pt; page-break-after: avoid; }}
    h3 {{ font-size: 11.5pt; margin: 13pt 0 4pt; page-break-after: avoid; }}
    p {{ margin: 0 0 7pt; text-align: justify; }}
    .eyebrow {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7.2pt; letter-spacing: 2.2pt;
                text-transform: uppercase; color: {GILT}; margin-bottom: 10pt; }}
    .standfirst {{ font-size: 12pt; line-height: 1.5; color: {INK2}; margin-bottom: 12pt; }}
    .rule {{ border: none; border-top: 2.2pt double {GILT}; margin: 12pt 0; }}
    .verdict {{ border: 0.8pt solid {GILT}; background: #f8f2e5; padding: 9pt 12pt; margin: 12pt 0;
                page-break-inside: avoid; }}
    .verdict .k, .honesty .k {{ font-family: "DejaVu Sans Mono", monospace; font-size: 6.8pt;
                letter-spacing: 1.6pt; text-transform: uppercase; margin-bottom: 4pt; }}
    .verdict .k {{ color: {GILT}; }}
    .honesty {{ border: 0.8pt solid {NEG}; padding: 9pt 12pt; margin: 12pt 0; page-break-inside: avoid; }}
    .honesty .k {{ color: {NEG}; }}
    aside.term {{ border-left: 2.2pt solid {GILT}; background: #f5f6f4; padding: 6pt 9pt;
                  margin: 8pt 0; font-size: 9pt; color: {INK2}; page-break-inside: avoid; }}
    aside.term b {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7.4pt; letter-spacing: .8pt;
                    text-transform: uppercase; color: {GILT}; display: block; }}
    .fnum {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7.4pt; letter-spacing: 1.2pt;
             text-transform: uppercase; color: {GILT}; margin: 14pt 0 0; }}
    figure {{ margin: 10pt 0; page-break-inside: avoid; }}
    figure img {{ width: 100%; border: 0.6pt solid {LINE}; }}
    figcaption {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7pt; color: #7d8892;
                  margin-top: 3pt; line-height: 1.4; }}
    .tblock {{ margin: 10pt 0 12pt; page-break-inside: avoid; }}
    .ttitle {{ font-family: "DejaVu Sans", sans-serif; font-weight: bold; font-size: 9.4pt; margin-bottom: 4pt; }}
    .tnote {{ font-family: "DejaVu Sans Mono", monospace; font-size: 6.8pt; color: #7d8892; margin-top: 3pt; }}
    table.tbl {{ border-collapse: collapse; width: 100%; }}
    .tbl th {{ font-family: "DejaVu Sans Mono", monospace; font-size: 6.6pt; letter-spacing: .6pt;
               text-transform: uppercase; color: {INK2}; text-align: right; padding: 3pt 5pt;
               border-bottom: 1.4pt solid {GILT}; }}
    .tbl td {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7.6pt; text-align: right;
               padding: 2.6pt 5pt; border-bottom: 0.5pt solid {LINE}; }}
    .tbl.small td {{ font-size: 6.9pt; padding: 2pt 4pt; }}
    .tbl th:first-child, .tbl td:first-child {{ text-align: left; }}
    .pagebreak {{ page-break-before: always; }}
    .appx {{ font-family: "DejaVu Sans Mono", monospace; font-size: 7.4pt; letter-spacing: 1.6pt;
             text-transform: uppercase; color: {GILT}; }}
    .cover {{ margin-top: 60mm; }}
    .toc td {{ font-family: "DejaVu Serif", serif; font-size: 9.6pt; text-align: left; }}
    ul {{ margin: 0 0 7pt 14pt; padding: 0; }}
    li {{ margin-bottom: 3pt; }}
    """

    A = []  # document parts

    # ------------------------------------------------------------- cover
    A.append(f"""
    <div class="cover">
      <div class="eyebrow">Research note · UK closed-end funds · 2007–2026</div>
      <h1>The Discount Machine</h1>
      <p class="standfirst">What nineteen years of free public data say about buying UK investment
      trusts below asset value — with full technical appendices.</p>
      <hr class="rule">
      <p style="font-family:'DejaVu Sans Mono',monospace;font-size:8pt;color:{INK2}">
      Built from 1,003 archive files of the Association of Investment Companies and 4,632 dividend
      announcements from the Investegate regulatory news archive.<br>
      303 trusts, dead and alive · 41,241 fund-month observations · 235 months.<br><br>
      Historical backtests are research tools, not investment advice.<br>
      No missing financial data has been synthetically filled.</p>
    </div>
    <div class="pagebreak"></div>
    """)

    # ----------------------------------------------------------- narrative
    A.append(f"""
    <div class="verdict"><div class="k">The verdict in three sentences</div>
    <p><b>Yes, buying UK investment trusts at unusually wide discounts has been genuinely
    profitable</b> — the cheapest tenth of the market beat the average trust by roughly 7–15
    percentage points a year before costs, a result too consistent to be luck.</p>
    <p><b>But the profit is a sprint, not a marathon.</b> Nearly all of it arrives in the first
    month after a fund becomes abnormally cheap; an investor who reacts a month late captures almost
    nothing, and since 2022 even the fast version has paid roughly zero.</p>
    <p style="margin:0"><b>And 15–20% a year from this alone is not supported.</b> A disciplined,
    fast-moving investor could plausibly have earned low double digits before costs; the celebrated
    specialist returns must come from leverage, activism, or deal-level timing that monthly public
    data cannot capture.</p></div>

    <h2>What an investment trust is, and what we tested</h2>
    <p>An investment trust is a company whose only business is owning a portfolio of other
    investments. Because its own shares trade freely on the London Stock Exchange, the share price
    can drift away from the value of what it owns. When the shares sell for less than the portfolio
    per share, the gap is called the <b>discount</b> — paying 90p for £1.00 of assets is a 10%
    discount.</p>
    <aside class="term"><b>Discount &amp; NAV</b> NAV — net asset value — is what one share's slice
    of the portfolio is worth. Discount = share price ÷ NAV − 1. Negative means cheap: −20% means
    80p buys £1 of assets.</aside>
    <p>Specialist investors argue that buying at wide discounts and waiting for the gap to close is
    a durable source of profit. We tested that claim month by month from January 2007 to July 2026,
    using only what an investor could have known at the time, across 303 London-listed trusts
    including every one that later merged, liquidated or vanished. No missing number was invented;
    where data doesn't exist, the gap is reported, not filled.</p>

    <h2>How to read the numbers</h2>
    <aside class="term"><b>CAGR</b> Compound annual growth rate — the steady yearly return producing
    the same end result. £1 at 10% CAGR for 19 years becomes about £6.12.</aside>
    <aside class="term"><b>Alpha</b> Return above what simply owning every trust would have earned —
    the part the tested pattern added.</aside>
    <aside class="term"><b>t-statistic</b> A luck detector: how large a result is relative to its
    noise. Above ~2, chance is an unlikely explanation; near 0, it could easily be luck.</aside>
    <aside class="term"><b>z-score</b> How cheap a trust is versus its own normal. Always at −20%
    and there today: zero. Normally −5% and suddenly −20%: deeply negative — the dislocation the
    strategies hunt.</aside>
    <aside class="term"><b>Skip-month test</b> Re-run everything ignoring the first month after each
    signal. Data errors and profits only a lightning-fast trader could catch disappear; what
    survives is slow enough for a real investor.</aside>
    <p>Primary returns are built from month-end share prices excluding dividends (no free source
    records dividends for long-dead funds). Dividend histories were separately rebuilt for most of
    the universe from company announcements, and headline results are shown on both bases.</p>
    """)

    # findings
    fig1 = b64(CHARTS / "05_discount_decile_returns.png")
    fig2 = b64(CHARTS / "02_growth_primary_vs_benchmarks.png")
    fig3 = b64(CHARTS / "15_quality_value_growth.png")
    A.append(f"""
    <h2>The findings, in order of confidence</h2>
    <div class="fnum">Finding 1 · very high confidence</div>
    <h3>Cheap trusts really did beat expensive ones — in a near-perfect staircase</h3>
    <p>Sorted into ten buckets by discount each month, next-month returns descend almost perfectly
    from cheapest (+1.18%/month) to dearest (−0.45%). Strategies built on this earned 12.8–28.9% a
    year before costs against 5.1% for the average trust; with real dividends added back, 15.9% vs
    7.9% on the covered sample. Ranking each trust against its own history (z-score) beat raw
    cheapness: ~14.7 points of annual alpha versus ~8.</p>
    <figure><img src="data:image/png;base64,{fig1}">
    <figcaption>Fig. 1 — Average next-month return by discount decile, 2007–2026. Decile 1 = cheapest.</figcaption></figure>

    <div class="fnum">Finding 2 · very high confidence</div>
    <h3>The profit is a sprint: skip one month and it nearly all evaporates</h3>
    <p>Under the skip-month test annual alpha collapses: 8.0→0.9 points (raw discount), 14.7→1.4
    (z-score), 17.1→2.8 (composite). The snap-back happens within weeks of a trust becoming
    abnormally cheap. The discount machine pays the investor who monitors daily and acts at the turn
    of the month — and almost nothing to one who reads about the bargain later.</p>
    <figure><img src="data:image/png;base64,{fig2}">
    <figcaption>Fig. 2 — Growth of £1: cheapest-decile strategy vs whole-universe benchmark (price returns).</figcaption></figure>

    <div class="fnum">Finding 3 · high confidence</div>
    <h3>Since 2022, the machine has stalled</h3>
    <p>Strong 2007–2016, present 2017–2021, roughly zero from 2022 onward — precisely the years UK
    trust discounts widened to a generation's extreme and stayed there. Cheap stopped snapping back.
    Whether today's discounts are a coiled spring or a permanent repricing is the judgment the data
    cannot make.</p>

    <div class="fnum">Finding 4 · high confidence</div>
    <h3>The money came from the gap closing, not the assets growing</h3>
    <p>A trust's return decomposes exactly into portfolio growth (NAV), discount change, and
    dividends. Our best-quality portfolio's 15.3% annual return split as <b>5.6% NAV growth ×
    7.1% discount narrowing + 2.1% dividends</b>. Full year-by-year table: Appendix F.</p>

    <div class="fnum">Finding 5 · moderate confidence</div>
    <h3>"Catalysts" didn't help a monthly investor — the market moves first</h3>
    <p>Tested twice — industry records and real announcement dates from the regulatory news archive —
    cheap trusts with a recent tender/wind-up/merger announcement did no better next month (1.18% vs
    2.08% monthly; weakly significant the wrong way). The discount re-rates on announcement day,
    before a month-end screen can react.</p>

    <div class="fnum">Finding 6 · moderate confidence, corrected</div>
    <h3>"Great funds, temporarily cheap" helps — modestly, and only at speed</h3>
    <p>Buying only top-quartile five-year NAV compounders when wider than their own normal discount
    beat either ingredient alone — ~7 points of annual alpha in a 6–8 name portfolio — but the
    skip-month test flattens it to zero and it too has earned nothing since 2022.</p>
    <figure><img src="data:image/png;base64,{fig3}">
    <figcaption>Fig. 3 — Quality × value vs its ingredients. The skip-month line hugging the benchmark is Finding 2 in one picture.</figcaption></figure>
    <div class="honesty"><div class="k">A correction, on the record</div>
    <p style="margin:0">An earlier draft showed this strategy earning 24–28% a year and surviving the
    skip-month test. That was a software bug: in months with fewer than five qualifiers the simulator
    re-counted the previous month's returns. Our own accounting identity — the decomposition refusing
    to add up — exposed it. A backtest you can't audit is a backtest you shouldn't trust, including
    ours.</p></div>

    <div class="fnum">Finding 7 · contextual</div>
    <h3>In crashes it falls with everyone — then recovers roughly twice as fast</h3>
    <p>2008–09: cheapest decile −29% vs −39% for the average trust; the following twelve months +103%
    vs +58%. After COVID: +83% vs +50%. Mechanically, the strategy buys panic — painful to hold, well
    paid on the far side.</p>

    <h2>Costs, and the final verdict on 15–20%</h2>
    <p>At a realistic ½% each way plus stamp duty the cheapest-decile strategy falls from 13.7% to
    ~9.1% a year; at 1% each way, to 6.9% — barely ahead of the 5.1% benchmark (Appendix I). The
    honest ledger: the average trust returned ~5% in price terms (~8% with dividends); systematic
    discount buying added mid-single to low-double-digit alpha before costs for an investor fast
    enough to act within the month; costs claim a third or more; the engine has idled since 2022.
    A specialist earning 15–20% must be adding leverage, activist pressure, announcement-day dealing,
    or genuine selection inside portfolios. The discount is real fuel; it is not the whole engine.</p>
    """)

    # ----------------------------------------------------------- appendices
    A.append('<div class="pagebreak"></div><h2><span class="appx">Appendices</span></h2>'
             '<p>All tables regenerate from the repository (outputs/*.csv); every number traces to '
             'named AIC or Investegate source files via data/manifest.csv.</p>')

    # A: methodology
    A.append(f"""
    <h3><span class="appx">Appendix A</span> · Data &amp; methodology</h3>
    <p><b>Universe.</b> Point-in-time monthly membership from the AIC Monthly Information Release
    (MIR) CSV archive, Jan 2007–Jul 2026: ordinary shares of conventional London-listed investment
    companies; VCTs, split-capital classes, ZDPs and non-sterling quote lines excluded. Dead, merged
    and renamed trusts included through their final published month; entities keyed by SEDOL (UK
    ISINs embed the SEDOL, chaining identifier eras), renames linked via corporate-activity records.</p>
    <p><b>Returns.</b> Month-end MIR mid prices; splits/consolidations adjusted from shares-in-issue;
    pence/pounds unit switches corrected only where a ×100 rescale restores price/NAV consistency
    (26 rows) and returns invalidated — never repaired — where the implied one-month discount move
    exceeds 4× without NAV corroboration (33 rows). Dividends parsed from Investegate announcement
    pages (ex-date attach; 69% of 4,632 with exact ex-dates), validated per security-year against
    the AIC's independently published trailing yield; failing years excluded from total-return
    eligibility, never silently understated.</p>
    <p><b>Timing.</b> Month-end t information trades in month t+1 (AIC publishes ~6 working days
    after month-end); late-reported rows are never signal-eligible before publication. Skip-month
    variants earn month t+2. Development 2007–16 / validation 2017–21 / holdout 2022+ splits were
    fixed before results were inspected; the overshoot composite's weights (50/25/25) and z-window
    (36m) were pre-specified.</p>
    <p><b>Engine safeguards.</b> Missing returns never become 0 or −100%; delistings classified
    against corporate-activity outcomes and left unresolved where the payoff is unknowable; portfolio
    months with insufficient qualifiers hold the prior book at current-month prices (the corrected
    carry-forward); 74 automated tests including parser regressions on real archive files.</p>
    """)

    # B: performance summary
    s = read("performance_summary.csv")
    if not s.empty:
        cols = {"strategy": ("Strategy", esc), "period": ("Period", esc), "basis": ("Basis", esc),
                "months": ("Mo", lambda x: num(x, 0)), "cagr": ("CAGR", pct),
                "volatility": ("Vol", pct), "sharpe": ("Sharpe", num),
                "max_drawdown": ("MaxDD", pct),
                "alpha_annual_vs_benchmark": ("Alpha", pct),
                "alpha_t_stat": ("t", num)}
        full = s[(s.period == "full")]
        A.append('<div class="pagebreak"></div>')
        A.append(table(full, cols, "Appendix B1 · Performance summary — full period, all strategies and bases", small=True))
        sub = s[(s.period != "full") & (s.basis == "gross")]
        A.append(table(sub, cols, "Appendix B2 · Development / validation / holdout sub-periods (gross)",
                       "Splits fixed ex-ante: 2007–16 / 2017–21 / 2022+.", small=True))

    # C: deciles
    d = read("decile_summary.csv")
    if not d.empty:
        cols = {"signal": ("Signal", esc), "bucket": ("Bucket", esc),
                "avg_fwd_return_monthly": ("Avg fwd/mo", lambda x: pct(x, 2)),
                "t_stat": ("t", num), "sharpe": ("Sharpe", num),
                "win_rate": ("Win", pct), "months": ("Mo", lambda x: num(x, 0))}
        A.append('<div class="pagebreak"></div>')
        A.append(table(d, cols, "Appendix C · Decile tests — all four signals",
                       "Bucket 1 = cheapest/most dislocated. Long-short = bucket 1 minus 10.", small=True))

    # D: robustness
    r = read("robustness_grid.csv")
    if not r.empty:
        cols = {"variant": ("Variant", esc), "months": ("Mo", lambda x: num(x, 0)),
                "cagr": ("CAGR", pct), "sharpe": ("Sharpe", num),
                "alpha_annual": ("Alpha", pct), "alpha_t": ("t", num),
                "avg_holdings": ("Avg hold", lambda x: num(x, 0)),
                "max_drawdown": ("MaxDD", pct)}
        A.append(table(r, cols, "Appendix D · Robustness grid incl. skip-month variants", small=True))

    # E: quality x value grids
    g = read("quality_value_grid.csv")
    if not g.empty:
        cols = {"horizon": ("Horizon", esc), "nav_quartile": ("NAV Q", lambda x: num(x, 0)),
                "z_quartile": ("Z Q", lambda x: num(x, 0)),
                "mean_monthly_fwd_return": ("Avg fwd/mo", lambda x: pct(x, 2)),
                "t_stat": ("t", num), "months": ("Mo", lambda x: num(x, 0)),
                "avg_names_per_month": ("Names/mo", lambda x: num(x, 0))}
        A.append('<div class="pagebreak"></div>')
        A.append(table(g, cols, "Appendix E · Quality × value 4×4 double-sort (t+1 and skip-month)",
                       "NAV Q1 = best 5y compounders; Z Q1 = most dislocated vs own history.", small=True))

    # F: decomposition
    dec = read("f_annual_decomposition.csv")
    if not dec.empty:
        cols = {"year": ("Year", esc), "total_return": ("Total", pct),
                "nav_growth": ("NAV growth", pct), "discount_change": ("Discount", pct),
                "distributions": ("Dividends", pct), "residual": ("Residual", pct),
                "months": ("Mo", lambda x: num(x, 0)),
                "avg_coverage_weight": ("Coverage", pct)}
        A.append(table(dec, cols, "Appendix F · Annual return decomposition — quality × value strategy",
                       "(1+total) = (1+NAV)(1+discount) + dividends; residual = compounding cross-terms."))

    # G: regressions
    reg = read("regressions.csv")
    if not reg.empty and "variable" in reg.columns:
        reg = reg.dropna(subset=["variable"])
        cols = {"spec": ("Specification", esc), "variable": ("Variable", esc),
                "mean_coef": ("Mean coef", lambda x: num(x, 4)),
                "t_stat_nw": ("t (Newey-West)", num), "months": ("Mo", lambda x: num(x, 0))}
        A.append(table(reg, cols, "Appendix G · Fama-MacBeth cross-sectional regressions",
                       "Monthly cross-sections; NW(4) t-stats on coefficient time series. No causal claims."))

    # H: stress
    st = read("stress_episodes.csv")
    if not st.empty:
        cols = {"episode": ("Episode", esc), "strategy": ("Strategy", esc),
                "months": ("Mo", lambda x: num(x, 0)),
                "cumulative_return": ("Cum. return", pct), "worst_month": ("Worst mo", pct)}
        A.append('<div class="pagebreak"></div>')
        A.append(table(st, cols, "Appendix H · Market-stress episodes and +12-month recoveries", small=True))

    # I: costs
    c = read("cost_scenarios.csv")
    if not c.empty:
        cols = {"strategy": ("Strategy", esc), "one_way_bps": ("One-way bps", lambda x: num(x, 0)),
                "stamp_duty_bps": ("Duty bps", lambda x: num(x, 0)),
                "cagr_net": ("Net CAGR", pct), "sharpe_net": ("Net Sharpe", num)}
        A.append(table(c, cols, "Appendix I · Transaction-cost scenarios", small=True))

    # J: catalysts
    cat1, cat2 = read("catalyst_analysis.csv"), read("catalyst_analysis_announced.csv")
    for df, ttl in ((cat1, "Appendix J1 · Catalysts — AIC completion-month proxy"),
                    (cat2, "Appendix J2 · Catalysts — real announcement dates (Investegate)")):
        if not df.empty:
            cols = {"group": ("Group", esc), "n_obs": ("N", lambda x: num(x, 0)),
                    "mean_fwd_return": ("Mean fwd/mo", lambda x: pct(x, 2)),
                    "median_fwd_return": ("Median", lambda x: pct(x, 2)), "t_stat": ("t", num)}
            A.append(table(df, cols, ttl))

    # K: episodes & contributors
    ep, co = read("f_episodes.csv"), read("f_contributors.csv")
    if not ep.empty:
        cols = {"company_name": ("Trust", esc), "entry_month": ("Entry", esc),
                "months_held": ("Held", lambda x: num(x, 0)),
                "entry_discount": ("Entry disc", pct), "entry_z": ("Entry z", num),
                "spell_compound_return": ("Spell return", pct),
                "contribution": ("Contribution", lambda x: pct(x, 1))}
        A.append('<div class="pagebreak"></div>')
        A.append(table(ep.sort_values("contribution", ascending=False).head(20), cols,
                       "Appendix K1 · Twenty best holding episodes — quality × value strategy"))
        A.append(table(ep.sort_values("contribution").head(10), cols,
                       "Appendix K2 · Ten worst holding episodes"))
    if not co.empty:
        cols = {"company_name": ("Trust", esc),
                "total_contribution": ("Total contribution", lambda x: pct(x, 1)),
                "n_episodes": ("Episodes", lambda x: num(x, 0)),
                "months_held": ("Months held", lambda x: num(x, 0))}
        A.append(table(co.head(15), cols, "Appendix K3 · Top contributors (share of strategy profit)"))

    # L: coverage
    cov = read("return_data_coverage_by_year.csv")
    if not cov.empty:
        cols = {"year": ("Year", esc), "securities": ("Securities", lambda x: num(x, 0)),
                "rows": ("Rows", lambda x: num(x, 0)),
                "price_coverage_pct": ("Price cov.", pct),
                "return_coverage_pct": ("Return cov.", pct)}
        A.append(table(cov, cols, "Appendix L · Data coverage by year (all panel rows incl. VCT lines)",
                       "Eligible conventional universe coverage is near-complete; VCT rows lack monthly prices by design.", small=True))

    # M: chart gallery
    A.append('<div class="pagebreak"></div><h3><span class="appx">Appendix M</span> · Complete chart gallery</h3>')
    captions = {
        "01": "Growth of £1 — all strategies (gross)", "02": "Primary strategy vs benchmarks",
        "03": "Drawdowns", "04": "Calendar-year returns", "05": "Discount-decile forward returns",
        "06": "Z-score-decile forward returns", "07": "Rolling 3-year returns",
        "08": "Rolling 3-year active return", "09": "Portfolio turnover",
        "10": "Eligible universe size", "11": "Data coverage", "12": "Median universe discount",
        "13": "Holdings by AIC sector", "14": "Return decomposition by year",
        "15": "Quality × value growth (corrected)",
    }
    for png in sorted(CHARTS.glob("*.png")):
        key = png.name[:2]
        A.append(f'<figure><img src="data:image/png;base64,{b64(png)}">'
                 f'<figcaption>Chart {key} — {captions.get(key, png.name)}</figcaption></figure>')

    A.append(f"""
    <hr class="rule">
    <p style="font-family:'DejaVu Sans Mono',monospace;font-size:7.4pt;color:{INK2}">
    Historical backtests are research tools, not investment advice. No missing financial data has
    been synthetically filled. Sources: Association of Investment Companies data archive (MIR,
    industry overviews, corporate activity, 2007–2026); Investegate regulatory news archive.
    Repository: hdcapital/cef-research — the entire study rebuilds via
    python -m uk_cef.cli run-all.</p>
    """)

    html_doc = f"<meta charset='utf-8'><style>{css}</style>" + "\n".join(A)
    src = BUILD / "report_print.html"
    src.write_text(html_doc)

    pdf_path = BUILD / "The_Discount_Machine.pdf"
    subprocess.run([
        "/opt/pw-browsers/chromium", "--headless", "--disable-gpu", "--no-sandbox",
        f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer", str(src),
    ], check=True, capture_output=True)
    print(f"PDF written: {pdf_path} ({pdf_path.stat().st_size:,} bytes)")

    # stamp page numbers
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    reader = PdfReader(str(pdf_path))
    n = len(reader.pages)
    buf = io.BytesIO()
    cnv = canvas.Canvas(buf, pagesize=A4)
    for i in range(n):
        cnv.setFont("Helvetica", 7)
        cnv.setFillColorRGB(0.55, 0.58, 0.6)
        cnv.drawCentredString(A4[0] / 2, 24, f"{i + 1} / {n}")
        cnv.drawString(52, 24, "The Discount Machine · UK CEF Research 2007–2026")
        cnv.drawRightString(A4[0] - 52, 24, "hdcapital/cef-research")
        cnv.showPage()
    cnv.save()
    buf.seek(0)
    overlay = PdfReader(buf)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i > 0:  # keep the cover clean
            page.merge_page(overlay.pages[i])
        writer.add_page(page)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    print(f"page numbers stamped ({n} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
