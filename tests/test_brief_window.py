from datetime import datetime, timezone

from cef_live.brief_window import already_sent, label_for


def test_labels_follow_the_two_windows():
    assert label_for(6) == "pre-LSE open" and label_for(12) == "pre-LSE open"
    assert label_for(23) == "pre-ASX open" and label_for(0) == "pre-ASX open"
    assert label_for(2) == "pre-ASX open" and label_for(3) == "pre-LSE open"


def _last(label, at, emailed=True):
    return {"brief": label, "generated_at": at, "emailed": emailed}


def test_a_six_hour_late_firing_for_a_sent_window_is_skipped():
    """2026-09-07: brief sent 07:45Z, the 06:20 cron fired at 12:40Z."""
    skip, why = already_sent(_last("pre-LSE open", "2026-09-07T07:45:12+00:00"),
                             now=datetime(2026, 9, 7, 12, 40, tzinfo=timezone.utc))
    assert skip, why


def test_the_next_window_is_never_skipped():
    skip, _ = already_sent(_last("pre-LSE open", "2026-09-07T07:45:12+00:00"),
                           now=datetime(2026, 9, 7, 23, 10, tzinfo=timezone.utc))
    assert not skip
    skip, _ = already_sent(_last("pre-ASX open", "2026-09-08T00:05:00+00:00"),
                           now=datetime(2026, 9, 8, 6, 20, tzinfo=timezone.utc))
    assert not skip


def test_backup_firings_fifteen_minutes_later_are_skipped():
    skip, _ = already_sent(_last("pre-ASX open", "2026-09-08T00:02:00+00:00"),
                           now=datetime(2026, 9, 8, 0, 30, tzinfo=timezone.utc))
    assert skip


def test_a_brief_that_was_not_emailed_does_not_block():
    skip, _ = already_sent(_last("pre-LSE open", "2026-09-07T07:45:12+00:00", emailed=False),
                           now=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc))
    assert not skip
    assert not already_sent(None)[0]


def test_the_gate_and_the_scan_share_one_rule():
    import inspect
    from cef_live import cli
    assert "label_for(" in inspect.getsource(cli.ideas)
    wf = open(".github/workflows/ideas.yml").read()
    assert "from cef_live.brief_window import already_sent" in wf


def test_the_gate_reads_the_branch_tip_not_the_pinned_checkout():
    wf = open(".github/workflows/ideas.yml").read()
    assert 'git show "FETCH_HEAD:reports/build/ideas.json"' in wf
    assert 'pathlib.Path("/tmp/ideas_tip.json")' in wf
