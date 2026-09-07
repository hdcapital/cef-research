"""Which pre-open brief a firing belongs to - shared by the scan and the
workflow gate, and dependency-free so the gate can import it on a bare
runner.

The scheduler fires at 06:20 UTC (pre-LSE) and 23:10 UTC (pre-ASX) and
is late by minutes to hours; a firing still labels itself by the window
it falls in, and the gate refuses a second brief for a window that has
already gone (2026-09-07: the 06:20 firing arrived at 12:40, six hours
late and past a four-hour "recently sent" test, and a third email went).
"""
from __future__ import annotations

from datetime import datetime, timezone

# a window's brief may be sent once; a firing this many hours after the
# window's brief went is still the same window (the two windows sit ~7h
# and ~17h apart, so 6h is inside the shorter gap with margin)
WINDOW_HOURS = 6


def label_for(hour: int) -> str:
    """The brief label for a UTC hour: 15:00-02:59 is the pre-ASX window
    (23:10 firing, late runs included), 03:00-14:59 the pre-LSE window."""
    return "pre-ASX open" if (hour >= 15 or hour < 3) else "pre-LSE open"


def already_sent(last: dict | None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the brief this firing would send has already gone.

    `last` is the ideas.json of the previous run. True when it was emailed,
    carries this firing's label, and went within WINDOW_HOURS - a later
    firing for the same window, however delayed, is a duplicate.
    """
    now = now or datetime.now(timezone.utc)
    want = label_for(now.hour)
    if not last:
        return False, f"no previous brief; {want} runs"
    try:
        t = datetime.fromisoformat(str(last.get("generated_at")))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return False, f"previous brief undated; {want} runs"
    age_h = (now - t).total_seconds() / 3600.0
    same = str(last.get("brief")) == want
    sent = bool(last.get("emailed"))
    if sent and same and 0 <= age_h < WINDOW_HOURS:
        return True, f"{want} already went {age_h:.1f}h ago"
    return False, f"last brief {last.get('brief')!r} {age_h:.1f}h ago; {want} runs"
