from __future__ import annotations

from datetime import UTC, datetime


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

    def now(self):
        return self.value

    def sleep(self, seconds):
        raise AssertionError(f"测试不应等待：{seconds}")


class PageApi:
    def __init__(self):
        self.less_values = []

    def get_pro_matches(self, less_than_match_id=None):
        self.less_values.append(less_than_match_id)
        base = 9000 if less_than_match_id is None else less_than_match_id - 1
        return [{
            "match_id": base,
            "start_time": 1_795_000_000,
            "radiant_team_id": 1,
            "radiant_name": "A",
            "dire_team_id": 2,
            "dire_name": "B",
            "leagueid": 3,
            "league_name": "L",
        }]


class FailingPageWorkingDetailApi:
    def get_pro_matches(self, less_than_match_id=None):
        from ingest.opendota import UpstreamError

        raise UpstreamError("HTTP 500")

    def get_match(self, match_id):
        return {
            "match_id": match_id,
            "start_time": 1_795_000_000,
            "game_mode": 1,
            "picks_bans": None,
            "players": [],
        }


class InvalidDetailApi:
    def get_match(self, match_id):
        return {
            "match_id": match_id,
            "start_time": 1_795_000_000,
            "game_mode": 2,
            "picks_bans": [
                {"order": 0, "is_pick": False, "team": 0, "hero_id": 999}
            ],
            "players": [],
        }


class TargetApi:
    def __init__(self):
        self.match_ids = []

    def get_match(self, match_id):
        self.match_ids.append(match_id)
        return {
            "match_id": match_id,
            "start_time": 1_795_000_000,
            "game_mode": 1,
            "picks_bans": None,
            "players": [],
        }


def test_page_budget_persists_next_page_and_restart_does_not_skip_it(db):
    from ingest.collector import Collector
    from ingest.collector_state import CollectorState

    clock = FixedClock()
    api1 = PageApi()
    first = Collector(db, api1, CollectorState(db, clock=clock), clock=clock).run_once(
        max_pages=1, max_details=0
    )
    assert first["pages"] == 1
    assert api1.less_values == [None]

    api2 = PageApi()
    second = Collector(db, api2, CollectorState(db, clock=clock), clock=clock).run_once(
        max_pages=1, max_details=0
    )
    assert second["pages"] == 1
    assert api2.less_values == [9000]
    assert db.execute("SELECT count(*) FROM collector_match_queue").fetchone()[0] == 2


def test_cli_help_lists_bounded_once_controls(capsys):
    from ingest.collector import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    text = capsys.readouterr().out
    for flag in ("--once", "--max-pages", "--max-details", "--daily-limit", "--match-id"):
        assert flag in text


def test_list_failure_keeps_cursor_and_still_processes_existing_detail_queue(db):
    from ingest.collector import Collector
    from ingest.collector_state import CollectorState
    from ingest.live_store import discover_public_match

    clock = FixedClock()
    state = CollectorState(db, clock=clock)
    discover_public_match(db, {
        "match_id": 77,
        "start_time": 1_795_000_000,
        "radiant_name": "A",
        "dire_name": "B",
    })
    state.enqueue(77)
    result = Collector(
        db, FailingPageWorkingDetailApi(), state, clock=clock
    ).run_once(max_pages=1, max_details=1)
    assert result["status"] == "degraded"
    assert result["errors"] == 1
    assert result["details"] == 1
    assert result["non_cm"] == 1
    assert db.execute(
        "SELECT next_less_than_match_id FROM collector_pro_scans WHERE source='pro_matches'"
    ).fetchone()[0] is None


def test_failed_once_result_maps_to_nonzero_exit_code():
    from ingest.collector import result_exit_code

    assert result_exit_code({"status": "ok"}) == 0
    assert result_exit_code({"status": "degraded"}) != 0
    assert result_exit_code({"status": "blocked"}) != 0


def test_detail_storage_error_rolls_back_and_keeps_daemon_batch_alive(db):
    from ingest.collector import Collector
    from ingest.collector_state import CollectorState
    from ingest.live_store import discover_public_match

    clock = FixedClock()
    state = CollectorState(db, clock=clock)
    discover_public_match(db, {
        "match_id": 88,
        "start_time": 1_795_000_000,
        "radiant_name": "A",
        "dire_name": "B",
    })
    state.enqueue(88)
    state.record_attempt("match_details", match_id=88, outcome="ok")
    result = Collector(db, InvalidDetailApi(), state, clock=clock).run_once(
        max_pages=0, max_details=1
    )
    assert result["status"] == "degraded"
    assert result["errors"] == 1
    assert db.execute(
        "SELECT draft_state FROM matches WHERE match_id=88"
    ).fetchone()[0] == "pending"
    assert db.execute(
        "SELECT status,last_error FROM collector_match_queue WHERE match_id=88"
    ).fetchone() == ("error", "ForeignKeyViolation")
    assert db.execute(
        "SELECT count(*),min(outcome),max(detail) FROM collector_attempts WHERE match_id=88"
    ).fetchone() == (1, "error", "ForeignKeyViolation")


def test_explicit_match_id_forces_and_processes_only_that_target(db):
    from ingest.collector import Collector
    from ingest.collector_state import CollectorState

    clock = FixedClock()
    state = CollectorState(db, clock=clock)
    state.enqueue(1)
    state.mark_error(1, "old")
    state.enqueue(2)
    state.mark_non_cm(2)
    api = TargetApi()
    result = Collector(db, api, state, clock=clock).run_once(
        max_pages=0, max_details=20, match_id=2
    )
    assert result["details"] == 1
    assert api.match_ids == [2]
    assert db.execute(
        "SELECT status FROM collector_match_queue WHERE match_id=1"
    ).fetchone()[0] == "error"


def test_explicit_match_is_not_enqueued_when_single_instance_lock_is_held(db):
    from ingest.collector import AlreadyRunning, Collector
    from ingest.collector_state import CollectorState

    clock = FixedClock()
    state = CollectorState(db, clock=clock)
    state.try_lock = lambda: False
    try:
        Collector(db, TargetApi(), state, clock=clock).run_once(
            max_pages=0, max_details=20, match_id=3
        )
    except AlreadyRunning:
        pass
    else:
        raise AssertionError("锁被占用时必须拒绝")
    assert db.execute(
        "SELECT count(*) FROM collector_match_queue WHERE match_id=3"
    ).fetchone()[0] == 0
