"""HTML rendering for the pre-open brief email.

Email HTML is written for email clients, not browsers: tables for layout,
every style inline, one column, no external assets, no scripts. The plain
text body remains the canonical fallback (`cli.ideas` sends both as a
multipart/alternative), so nothing here may carry information the text
does not.

Numbers are formatted defensively: verdict fields round-trip through a
DataFrame, so a None written by `opportunities.evaluate` can come back as
NaN and both must render as a dash rather than "nan".
"""

from __future__ import annotations

import html as _html

import pandas as pd

# palette: near-black ink, muted secondary, one accent, subtle rules
INK = "#1f2427"
MUTED = "#6b7075"
ACCENT = "#1f5c8b"
RULE = "#e4e2df"
CARD_BG = "#ffffff"
PAGE_BG = "#f4f3f1"
GOOD = "#1a7f4e"
BAD = "#b3382c"

_FONT = ("font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,"
         "sans-serif;")
_NUMFONT = ("font-family:'SF Mono',SFMono-Regular,Consolas,'Liberation Mono',"
            "Menlo,monospace;")


def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def _num(v, spec: str, dash: str = "–") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return dash
    try:
        return _esc(format(float(v), spec))
    except (TypeError, ValueError):
        return dash


def _txt(v) -> str:
    return v if isinstance(v, str) else ""


def _section_header(title: str, note: str = "") -> str:
    sub = (f'<div style="{_FONT}font-size:12px;color:{MUTED};'
           f'padding-top:2px;">{_esc(note)}</div>' if note else "")
    return (f'<tr><td style="padding:22px 24px 8px 24px;">'
            f'<div style="{_FONT}font-size:13px;font-weight:700;'
            f'letter-spacing:0.06em;text-transform:uppercase;'
            f'color:{ACCENT};">{_esc(title)}</div>{sub}</td></tr>')


def _fund_rows(df: pd.DataFrame) -> str:
    cells = []
    for r in df.itertuples(index=False):
        z = getattr(r, "z_adj", None)
        z_style = f"color:{ACCENT};font-weight:700;" \
            if isinstance(z, (int, float)) and pd.notna(z) and z <= -2 else ""
        cat = ""
        if isinstance(getattr(r, "catalyst_class", None), str) and r.catalyst_class:
            head = _txt(getattr(r, "catalyst_headline", ""))[:90]
            cat = (f'<div style="{_FONT}font-size:12px;color:{MUTED};'
                   f'padding-top:3px;">▸ {_esc(r.catalyst_class)} '
                   f'({_esc(getattr(r, "catalyst_date", ""))})'
                   f'{" — " + _esc(head) if head else ""}</div>')
        num_td = (f'style="{_NUMFONT}font-size:13px;color:{INK};'
                  f'padding:9px 6px;border-bottom:1px solid {RULE};'
                  f'text-align:right;white-space:nowrap;vertical-align:top;"')
        cells.append(
            f'<tr>'
            f'<td style="{_FONT}font-size:14px;color:{INK};padding:9px 6px 9px 0;'
            f'border-bottom:1px solid {RULE};vertical-align:top;">'
            f'{_esc(getattr(r, "name", ""))}'
            f'<span style="color:{MUTED};font-size:12px;">'
            f'&nbsp;{_esc(getattr(r, "market", ""))}</span>{cat}</td>'
            f'<td {num_td}>{_num(getattr(r, "discount_est", None), "+.1%")}</td>'
            f'<td {num_td}><span style="{z_style}">'
            f'{_num(z, "+.2f")}</span></td>'
            f'<td {num_td}>{_num(getattr(r, "irr_central", None), "+.1%")}</td>'
            f'</tr>')
    return "".join(cells)


def _fund_table(df: pd.DataFrame) -> str:
    th = (f'style="{_FONT}font-size:11px;font-weight:600;color:{MUTED};'
          f'letter-spacing:0.04em;text-transform:uppercase;text-align:right;'
          f'padding:0 6px 6px 6px;border-bottom:1px solid {RULE};"')
    return (f'<tr><td style="padding:0 24px;">'
            f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" style="border-collapse:collapse;">'
            f'<tr><th style="{_FONT}font-size:11px;font-weight:600;'
            f'color:{MUTED};letter-spacing:0.04em;text-transform:uppercase;'
            f'text-align:left;padding:0 6px 6px 0;'
            f'border-bottom:1px solid {RULE};">Fund</th>'
            f'<th {th}>Discount</th><th {th}>z</th><th {th}>Fwd IRR</th></tr>'
            f'{_fund_rows(df)}</table></td></tr>')


def _stat(value: str, label: str) -> str:
    return (f'<td style="padding:0 18px 0 0;">'
            f'<div style="{_NUMFONT}font-size:20px;font-weight:700;'
            f'color:{INK};">{value}</div>'
            f'<div style="{_FONT}font-size:11px;color:{MUTED};'
            f'letter-spacing:0.03em;text-transform:uppercase;'
            f'padding-top:2px;">{_esc(label)}</div></td>')


def render_html(label: str, date_str: str, evaluated: int,
                opps: pd.DataFrame, disl: pd.DataFrame,
                irr_led: pd.DataFrame, cat_led: pd.DataFrame,
                z_threshold: float, min_irr: float,
                wb_summary: dict, wb_error: str | None,
                n_delist: int, n_watch: int) -> str:
    """The complete HTML body for one pre-open brief."""
    parts: list[str] = []

    # header
    parts.append(
        f'<tr><td style="padding:26px 24px 0 24px;">'
        f'<div style="{_FONT}font-size:12px;font-weight:600;color:{MUTED};'
        f'letter-spacing:0.08em;text-transform:uppercase;">CEF Live</div>'
        f'<div style="{_FONT}font-size:21px;font-weight:700;color:{INK};'
        f'padding-top:3px;">{_esc(label[:1].upper() + label[1:])} brief'
        f'<span style="color:{MUTED};font-weight:400;"> · '
        f'{_esc(date_str)}</span></div></td></tr>')

    # stat strip
    parts.append(
        f'<tr><td style="padding:16px 24px 4px 24px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        + _stat(str(len(opps)), "actionable")
        + _stat(str(len(disl)), "dislocated")
        + _stat(str(len(irr_led)), f"IRR ≥ {min_irr:.0%}")
        + _stat(str(len(cat_led)), "catalyst-led")
        + _stat(str(evaluated), "funds scanned")
        + '</tr></table></td></tr>')

    if len(opps):
        parts.append(_section_header(
            f"Actionable today ({len(opps)})",
            "dislocated, catalyst live, IRR above hurdle, data fully sound"))
        parts.append(_fund_table(opps))
    else:
        parts.append(
            f'<tr><td style="padding:14px 24px 0 24px;">'
            f'<div style="{_FONT}font-size:13px;color:{MUTED};'
            f'border-top:1px solid {RULE};padding-top:12px;">No fund clears '
            f'all three gates on fully sound data today.</div></td></tr>')

    if len(disl):
        parts.append(_section_header(
            f"Dislocated vs own history ({len(disl)})",
            f"z ≤ {z_threshold:g} on the fund's own 36-month record"))
        parts.append(_fund_table(disl.head(20)))
        if len(disl) > 20:
            parts.append(_more_note(len(disl) - 20))

    if len(irr_led):
        parts.append(_section_header(
            f"Forward IRR ≥ {min_irr:.0%} ({len(irr_led)})",
            "not dislocated; central path, capped growth, fade to own "
            "median discount"))
        parts.append(_fund_table(irr_led.head(15)))
        if len(irr_led) > 15:
            parts.append(_more_note(len(irr_led) - 15))

    if len(cat_led):
        parts.append(_section_header(
            f"High-weight catalysts ({len(cat_led)})",
            "tender / scheme / continuation vote / wind-down"))
        parts.append(_fund_table(cat_led))

    # footer
    foot = []
    if wb_summary:
        foot.append(
            f"Universe workbook attached — {wb_summary.get('rows', 0)} "
            f"funds ({wb_summary.get('live', 0)} live), "
            f"{wb_summary.get('with_live_nav', 0)} with a live NAV, "
            f"{wb_summary.get('with_irr', 0)} with a forward IRR, "
            f"{wb_summary.get('catalysts', 0)} catalysts (30d).")
    if wb_error:
        foot.append(f'<span style="color:{BAD};font-weight:600;">Universe '
                    f'workbook FAILED to build: {_esc(wb_error)}</span>')
    if n_delist:
        foot.append(f"{n_delist} fund(s) awaiting delisting review "
                    "(delist_review.csv).")
    foot.append(f"{n_watch} verdict(s) recorded in the paper-trade ledger at "
                "signal time, whether or not acted on.")
    parts.append(
        f'<tr><td style="padding:22px 24px 24px 24px;">'
        f'<div style="{_FONT}font-size:12px;color:{MUTED};line-height:1.6;'
        f'border-top:1px solid {RULE};padding-top:12px;">'
        + "<br>".join(foot) + '</div></td></tr>')

    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;'
        f'background:{PAGE_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="background:{PAGE_BG};"><tr>'
        f'<td align="center" style="padding:18px 8px;">'
        f'<table role="presentation" width="640" cellpadding="0" '
        f'cellspacing="0" style="max-width:640px;width:100%;'
        f'background:{CARD_BG};border:1px solid {RULE};border-radius:6px;">'
        + "".join(parts) +
        '</table></td></tr></table></body></html>')


def _more_note(n: int) -> str:
    return (f'<tr><td style="padding:6px 24px 0 24px;">'
            f'<div style="{_FONT}font-size:12px;color:{MUTED};">'
            f'… and {n} more in the attached workbook.</div></td></tr>')
