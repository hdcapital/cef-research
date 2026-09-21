"""Which pre-open brief a firing belongs to - shared by the scan and the
workflow gate, and dependency-free so the gate can import it on a bare
runner.

The scheduler fires at 06:20 UTC (pre-LSE) and 23:10 UTC (pre-ASX) and
is late by minutes to hours; a firing still labels itself by the window
it falls in, and the gate refuses a second brief for a window that has
already gone. The window is an identity (label plus the UTC date it
opened on), not an age: 2026-09-07 a six-hour-late firing beat a four-hour
test, and 2026-09-21 a 12:38 firing beat the six-hour test that replaced
it, each sending a third email.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

def label_for(hour: int) -> str:
    """The brief label for a UTC hour: 15:00-02:59 is the pre-ASX window
    (23:10 firing, late runs included), 03:00-14:59 the pre-LSE window."""
    return "pre-ASX open" if (hour >= 15 or hour < 3) else "pre-LSE open"


def window_key(t: datetime) -> tuple[str, str]:
    """The window a UTC instant belongs to: its label and the UTC date the
    window opened on. A pre-ASX window opens at 15:00 and runs past
    midnight, so its 00:00-02:59 tail keys to the previous date."""
    label = label_for(t.hour)
    day = t.date()
    if label == "pre-ASX open" and t.hour < 3:
        day = (t - timedelta(days=1)).date()
    return label, day.isoformat()


def already_sent(last: dict | None, now: datetime | None = None) -> tuple[bool, str]:
    """Whether the brief this firing would send has already gone.

    `last` is the ideas.json of the previous run. True when it was emailed
    from the same window this firing falls in - the window is identity,
    not an age: 2026-09-21 the 06:20 cron fired at 12:38, six hours after
    the 06:35 send, past the old six-hour test, and a third email went.
    """
    now = now or datetime.now(timezone.utc)
    want, day = window_key(now)
    if not last:
        return False, f"no previous brief; {want} runs"
    try:
        t = datetime.fromisoformat(str(last.get("generated_at")))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return False, f"previous brief undated; {want} runs"
    age_h = (now - t).total_seconds() / 3600.0
    same = str(last.get("brief")) == want and window_key(t) == (want, day)
    sent = bool(last.get("emailed"))
    if sent and same and age_h >= 0:
        return True, f"{want} of {day} already went {age_h:.1f}h ago"
    return False, f"last brief {last.get('brief')!r} {age_h:.1f}h ago; {want} of {day} runs"
