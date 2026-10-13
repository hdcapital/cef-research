from datetime import datetime, timezone

from cef_live.brief_window import (already_sent, firing_window, label_for,
                                   premature, sent_window, window_key)


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
    assert "firing_window(" in inspect.getsource(cli.ideas)
    wf = open(".github/workflows/ideas.yml").read()
    assert "from cef_live.brief_window import already_sent, firing_window" in wf
    # the gate's window reaches the scoring step
    assert "BRIEF_LABEL: ${{ needs.gate.outputs.label }}" in wf
    assert "BRIEF_DAY: ${{ needs.gate.outputs.day }}" in wf


def test_the_gate_reads_the_branch_tip_not_the_pinned_checkout():
    wf = open(".github/workflows/ideas.yml").read()
    assert 'git show "FETCH_HEAD:reports/build/ideas.json"' in wf
    assert 'pathlib.Path("/tmp/ideas_tip.json")' in wf


def test_a_firing_any_time_later_in_a_sent_window_is_skipped():
    """2026-09-21: brief sent 06:35Z, the 06:20 cron fired at 12:38Z (6.05h)."""
    skip, why = already_sent(_last("pre-LSE open", "2026-09-21T06:35:06+00:00"),
                             now=datetime(2026, 9, 21, 12, 38, tzinfo=timezone.utc))
    assert skip, why
    skip, why = already_sent(_last("pre-LSE open", "2026-09-21T06:35:06+00:00"),
                             now=datetime(2026, 9, 21, 14, 59, tzinfo=timezone.utc))
    assert skip, why


def test_the_same_label_on_the_next_day_is_a_new_window():
    skip, _ = already_sent(_last("pre-LSE open", "2026-09-21T06:35:06+00:00"),
                           now=datetime(2026, 9, 22, 5, 41, tzinfo=timezone.utc))
    assert not skip


def test_the_pre_asx_window_spans_midnight():
    assert window_key(datetime(2026, 9, 21, 22, 46, tzinfo=timezone.utc)) == ("pre-ASX open", "2026-09-21")
    assert window_key(datetime(2026, 9, 22, 1, 30, tzinfo=timezone.utc)) == ("pre-ASX open", "2026-09-21")
    assert window_key(datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)) == ("pre-LSE open", "2026-09-22")
    skip, _ = already_sent(_last("pre-ASX open", "2026-09-21T23:36:00+00:00"),
                           now=datetime(2026, 9, 22, 1, 40, tzinfo=timezone.utc))
    assert skip


def test_a_late_morning_cron_landing_in_the_afternoon_does_not_send_the_evening_brief():
    """2026-10-05: the 06:50 cron landed at 15:12Z, labelled itself pre-ASX
    and emailed at 16:04Z, eight hours before the ASX open and before the
    nightly refreshes. Nothing had gone for that window, so only an
    earliest-hour rule can hold it back."""
    last = _last("pre-LSE open", "2026-10-05T06:34:22+00:00")
    skip, why = already_sent(last, now=datetime(2026, 10, 5, 15, 12, tzinfo=timezone.utc))
    assert skip and "not sent before 22:00Z" in why
    skip, why = already_sent(last, now=datetime(2026, 10, 5, 21, 59, tzinfo=timezone.utc))
    assert skip
    skip, why = already_sent(last, now=datetime(2026, 10, 5, 22, 46, tzinfo=timezone.utc))
    assert not skip, why


def test_the_pre_lse_window_is_not_sent_before_five():
    last = _last("pre-ASX open", "2026-10-05T23:36:00+00:00")
    skip, why = already_sent(last, now=datetime(2026, 10, 6, 3, 30, tzinfo=timezone.utc))
    assert skip and "not sent before 05:00Z" in why
    skip, why = already_sent(last, now=datetime(2026, 10, 6, 5, 41, tzinfo=timezone.utc))
    assert not skip, why


def test_the_midnight_tail_of_the_pre_asx_window_is_never_early():
    assert premature(datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc)) == ""
    assert premature(datetime(2026, 10, 6, 2, 59, tzinfo=timezone.utc)) == ""
    assert premature(datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)) != ""
    skip, _ = already_sent(None, now=datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc))
    assert not skip


def test_the_window_is_fixed_at_firing_not_when_scoring_finishes():
    """2026-10-09: a 02:17Z pre-ASX firing reached scoring at 03:09Z and
    labelled its brief Friday's pre-LSE one; Friday's real pre-LSE firings
    (12:07Z onward) were then skipped as already sent."""
    env = {"BRIEF_LABEL": "pre-ASX open", "BRIEF_DAY": "2026-10-08"}
    assert firing_window(datetime(2026, 10, 9, 3, 9, tzinfo=timezone.utc), env=env) \
        == ("pre-ASX open", "2026-10-08")
    # without the gate's export (a run outside the workflow) the clock decides
    assert firing_window(datetime(2026, 10, 9, 3, 9, tzinfo=timezone.utc), env={}) \
        == ("pre-LSE open", "2026-10-09")
    assert firing_window(datetime(2026, 10, 9, 2, 17, tzinfo=timezone.utc), env={}) \
        == ("pre-ASX open", "2026-10-08")
    # a malformed export is ignored, not trusted
    assert firing_window(datetime(2026, 10, 9, 2, 17, tzinfo=timezone.utc),
                         env={"BRIEF_LABEL": "x", "BRIEF_DAY": "2026-10-08"}) \
        == ("pre-ASX open", "2026-10-08")


def test_a_brief_that_finished_past_the_window_edge_does_not_block_the_next_window():
    last = {"brief": "pre-ASX open", "window_day": "2026-10-08",
            "generated_at": "2026-10-09T03:08:59+00:00", "emailed": True}
    assert sent_window(last) == ("pre-ASX open", "2026-10-08")
    skip, why = already_sent(last, now=datetime(2026, 10, 9, 12, 7, tzinfo=timezone.utc))
    assert not skip, why
    # and it still blocks a later firing of its own window
    last = {"brief": "pre-ASX open", "window_day": "2026-10-11",
            "generated_at": "2026-10-12T02:19:16+00:00", "emailed": True}
    skip, why = already_sent(last, now=datetime(2026, 10, 12, 2, 37, tzinfo=timezone.utc))
    assert skip, why


def test_an_ideas_json_without_a_window_day_keys_on_its_generated_at():
    last = _last("pre-ASX open", "2026-10-12T02:19:16+00:00")
    assert sent_window(last) == ("pre-ASX open", "2026-10-11")
    # the 2026-10-09 failure, as the old record reads: the mislabelled
    # brief blocks the morning, which is why the label is now fixed at firing
    last = _last("pre-LSE open", "2026-10-09T03:08:59+00:00")
    assert already_sent(last, now=datetime(2026, 10, 9, 12, 7, tzinfo=timezone.utc))[0]
