from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest


class FakeClock:
    def __init__(self, now: datetime):
        self.value = now
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


def test_retry_after_is_respected_and_each_http_call_is_persisted(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"match_id": 9, "game_mode": 2, "picks_bans": None})

    clock = FakeClock(datetime(2026, 9, 22, 0, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)
    limiter = PersistentRateLimiter(state, daily_limit=100, clock=clock)
    client = OpenDotaClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test"),
        limiter,
        state,
        clock=clock,
    )

    assert client.get_match(9)["match_id"] == 9
    assert clock.sleeps == [7.0]
    assert db.execute(
        "SELECT outcome FROM collector_attempts ORDER BY attempt_id"
    ).fetchall() == [("ratelimited",), ("not_ready",)]


def test_daily_budget_survives_limiter_recreation_and_blocks_before_http(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import DailyBudgetExhausted, PersistentRateLimiter

    clock = FakeClock(datetime(2026, 9, 22, 1, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)
    state.record_attempt("pro_matches", outcome="ok")
    state.record_attempt("match_details", match_id=1, outcome="error")
    limiter = PersistentRateLimiter(CollectorState(db, clock=clock), daily_limit=2, clock=clock)

    try:
        limiter.acquire()
    except DailyBudgetExhausted as exc:
        assert exc.used == 2
    else:
        raise AssertionError("持久化日配额已经用尽，必须在发请求前拒绝")


def test_sixty_requests_in_last_minute_wait_until_window_opens(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import PersistentRateLimiter

    clock = FakeClock(datetime(2026, 9, 22, 1, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)
    for _ in range(60):
        state.record_attempt("pro_matches", outcome="ok", at=clock.now() - timedelta(seconds=30))

    PersistentRateLimiter(state, daily_limit=100, clock=clock).acquire()
    assert clock.sleeps == [30.001]


def test_attempt_is_reserved_before_transport_and_mismatched_payload_is_one_error_row(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter, UpstreamError

    clock = FakeClock(datetime(2026, 9, 22, 2, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)

    def handler(request: httpx.Request) -> httpx.Response:
        assert db.execute("SELECT count(*) FROM collector_attempts").fetchone()[0] == 1
        return httpx.Response(200, json={"match_id": 10, "game_mode": 2, "picks_bans": []})

    client = OpenDotaClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test"),
        PersistentRateLimiter(state, daily_limit=100, clock=clock),
        state,
        clock=clock,
    )
    with pytest.raises(UpstreamError, match="match_id"):
        client.get_match(9)
    assert db.execute(
        "SELECT outcome,detail FROM collector_attempts"
    ).fetchall() == [("error", "invalid_match_id")]


def test_http_200_error_object_and_missing_game_mode_are_rejected_without_raw_body(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter, UpstreamError

    clock = FakeClock(datetime(2026, 9, 22, 3, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)
    payloads = iter([{"error": "private upstream text"}, {"match_id": 9}])
    client = OpenDotaClient(
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(payloads))),
            base_url="https://example.test",
        ),
        PersistentRateLimiter(state, daily_limit=100, clock=clock),
        state,
        clock=clock,
    )
    with pytest.raises(UpstreamError):
        client.get_match(9)
    with pytest.raises(UpstreamError):
        client.get_match(9)
    rows = db.execute("SELECT outcome,detail FROM collector_attempts ORDER BY attempt_id").fetchall()
    assert rows == [("error", "upstream_error_object"), ("error", "missing_game_mode")]
    assert "private upstream text" not in repr(rows)


def test_final_429_persists_shared_retry_after_for_the_next_process(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter, UpstreamError

    start = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
    first_clock = FakeClock(start)
    first_state = CollectorState(db, clock=first_clock)
    first = OpenDotaClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "7"})
            ),
            base_url="https://example.test",
        ),
        PersistentRateLimiter(first_state, daily_limit=100, clock=first_clock),
        first_state,
        clock=first_clock,
        max_attempts=1,
    )
    with pytest.raises(UpstreamError):
        first.get_pro_matches()
    assert first_clock.sleeps == []

    restarted_clock = FakeClock(start)
    restarted_state = CollectorState(db, clock=restarted_clock)
    PersistentRateLimiter(
        restarted_state, daily_limit=100, clock=restarted_clock
    ).acquire()
    assert restarted_clock.sleeps == [7.0]


@pytest.mark.parametrize("header", [None, "broken", "nan", "inf", "-1", "Sat, 01 Jan 2000 00:00:00 GMT"])
def test_missing_or_unusable_retry_after_backs_off_by_whole_minutes(db, header):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter

    clock = FakeClock(datetime(2026, 9, 22, 5, 0, tzinfo=UTC))
    state = CollectorState(db, clock=clock)
    observed = []

    def handler(request):
        observed.append(clock.now())
        if len(observed) < 3:
            return httpx.Response(429, headers={} if header is None else {"Retry-After": header})
        return httpx.Response(200, json=[])

    client = OpenDotaClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test"),
        PersistentRateLimiter(state, daily_limit=100, clock=clock),
        state, clock=clock,
    )
    assert client.get_pro_matches() == []
    assert clock.sleeps == [60.0, 120.0]
    assert (observed[1] - observed[0]).total_seconds() == 60
    assert (observed[2] - observed[1]).total_seconds() == 120
    assert db.execute("SELECT count(*) FROM collector_attempts").fetchone()[0] == 3


def test_missing_retry_after_fallback_survives_process_restart(db):
    from ingest.collector_state import CollectorState
    from ingest.opendota import OpenDotaClient, PersistentRateLimiter, UpstreamError

    start = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)
    clock = FakeClock(start)
    state = CollectorState(db, clock=clock)
    client = OpenDotaClient(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)),
                     base_url="https://example.test"),
        PersistentRateLimiter(state, daily_limit=100, clock=clock),
        state, clock=clock, max_attempts=1,
    )
    with pytest.raises(UpstreamError):
        client.get_pro_matches()
    restarted_clock = FakeClock(start)
    restarted = CollectorState(db, clock=restarted_clock)
    PersistentRateLimiter(restarted, daily_limit=100, clock=restarted_clock).acquire()
    assert restarted_clock.sleeps == [60.0]
