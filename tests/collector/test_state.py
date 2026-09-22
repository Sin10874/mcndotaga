from __future__ import annotations

from datetime import UTC, datetime, timedelta
import threading


def test_missing_cm_draft_uses_absolute_retry_ladder_and_reaches_unavailable():
    from ingest.collector_state import missing_draft_transition

    discovered = datetime(2026, 9, 1, tzinfo=UTC)
    state, due, final = missing_draft_transition(discovered, discovered)
    assert (state, due, final) == ("pending", discovered + timedelta(hours=2), False)

    state, due, final = missing_draft_transition(
        discovered, discovered + timedelta(hours=168)
    )
    assert (state, due, final) == (
        "unavailable",
        discovered + timedelta(hours=720),
        False,
    )

    state, due, final = missing_draft_transition(
        discovered, discovered + timedelta(hours=720)
    )
    assert (state, due, final) == ("unavailable", None, True)


def test_non_cm_is_terminal_and_does_not_wait_for_twenty_four_actions():
    from ingest.collector_state import classify_detail

    assert classify_detail({"game_mode": 1, "picks_bans": None}) == "non_cm"
    assert classify_detail({"game_mode": 2, "picks_bans": None}) == "not_ready"
    assert classify_detail({"game_mode": 2, "picks_bans": []}) == "not_ready"
    assert classify_detail({"game_mode": 2, "picks_bans": [{"order": 0}]}) == "complete"
    assert classify_detail({"picks_bans": None}) == "unknown"


def test_system_clock_stop_interrupts_a_long_wait():
    from ingest.collector_state import StopRequested, SystemClock

    clock = SystemClock()
    stopped = []

    def wait():
        try:
            clock.sleep(60)
        except StopRequested:
            stopped.append(True)

    thread = threading.Thread(target=wait)
    thread.start()
    clock.stop()
    thread.join(timeout=1)
    assert stopped == [True]
    assert not thread.is_alive()
