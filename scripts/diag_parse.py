"""Diagnostics over the cached raw AIC files (runs in CI where the cache
lives). Writes small CSV/text reports under data/probe/diag/ so parser
issues can be inspected offline:

1. per-MIR-file parse stats (rows, price/nav coverage) - finds months whose
   layout broke the parser;
2. header dumps + sample lines for the broken months;
3. every month-on-month price ratio > 4x or < 0.25x with its raw
   price/nav/shares/currency components - the unit-error forensics.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from uk_cef.parsers.mir import classify_mir_file, parse_mir_csv  # noqa: E402

RAW = Path("data/raw/aic")
OUT = Path("data/probe/diag")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stats = []
    all_rows = []
    for path in sorted(RAW.glob("*_mir_*")):
        inner = path.name.split("_mir_", 1)[1]
        kind = classify_mir_file(inner)
        if kind not in ("main",):
            continue
        if path.suffix.lower() not in (".csv",):
            continue
        try:
            rows = parse_mir_csv(path)
        except Exception as exc:  # noqa: BLE001
            stats.append({"file": path.name, "error": str(exc)})
            continue
        n_price = sum(1 for r in rows if r["price"] is not None)
        n_nav = sum(1 for r in rows if r["nav"] is not None)
        n_both = sum(1 for r in rows if r["price"] is not None and r["nav"] is not None)
        n_ord = sum(
            1 for r in rows
            if r["price"] is not None and r["nav"] is not None
            and (r["share_type"] or "").lower().startswith("ordinary")
            and "venture" not in (r["sector"] or "").lower()
        )
        stats.append(
            {"file": path.name, "rows": len(rows), "n_price": n_price,
             "n_nav": n_nav, "n_both": n_both, "n_ord_conv": n_ord}
        )
        for r in rows:
            all_rows.append(
                {"file": path.name, "obs_month": r["obs_month"], "name": r["company_name"],
                 "share_type": r["share_type"], "code": r["code"], "price": r["price"],
                 "nav": r["nav"], "shares": r["shares"], "currency": r["currency"],
                 "sector": r["sector"]}
            )

    with open(OUT / "mir_parse_stats.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "rows", "n_price", "n_nav", "n_both", "n_ord_conv", "error"])
        w.writeheader()
        for s in stats:
            w.writerow(s)

    # dump headers + samples of the worst files
    bad = [s for s in stats if s.get("n_ord_conv", 0) < 60 and "error" not in s]
    with open(OUT / "bad_file_headers.txt", "w", encoding="utf-8") as fh:
        for s in bad:
            p = RAW / s["file"]
            fh.write(f"===== {s['file']} (stats: {json.dumps(s)}) =====\n")
            try:
                with open(p, encoding="latin-1") as src:
                    for i, line in enumerate(src):
                        if i > 8:
                            break
                        fh.write(line[:2000] + "\n")
            except Exception as exc:  # noqa: BLE001
                fh.write(f"read error: {exc}\n")
            fh.write("\n")

    # extreme price moves with raw components
    import collections

    by_key = collections.defaultdict(dict)
    for r in all_rows:
        key = r["code"] or (r["name"], r["share_type"])
        by_key[(key if isinstance(key, tuple) else (key,))][r["obs_month"]] = r
    extremes = []
    for key, months in by_key.items():
        ordered = sorted(months)
        for a, b in zip(ordered, ordered[1:]):
            ra, rb = months[a], months[b]
            pa, pb = ra["price"], rb["price"]
            if not pa or not pb:
                continue
            # only adjacent calendar months
            ya, ma = map(int, a.split("-"))
            yb, mb = map(int, b.split("-"))
            if (yb * 12 + mb) - (ya * 12 + ma) != 1:
                continue
            ratio = pb / pa
            if ratio > 4 or ratio < 0.25:
                extremes.append(
                    {"name": rb["name"], "code": rb["code"], "month_prev": a, "month": b,
                     "price_prev": pa, "price": pb, "ratio": round(ratio, 4),
                     "nav_prev": ra["nav"], "nav": rb["nav"],
                     "shares_prev": ra["shares"], "shares": rb["shares"],
                     "ccy_prev": ra["currency"], "ccy": rb["currency"],
                     "share_type": rb["share_type"], "sector": rb["sector"]}
                )
    with open(OUT / "extreme_moves.csv", "w", newline="", encoding="utf-8") as fh:
        if extremes:
            w = csv.DictWriter(fh, fieldnames=list(extremes[0].keys()))
            w.writeheader()
            w.writerows(extremes)
    print(f"diag: {len(stats)} files, {len(bad)} bad, {len(extremes)} extreme moves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
