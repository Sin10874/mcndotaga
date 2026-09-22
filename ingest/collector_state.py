"""采集器的持久状态、重试状态机与单实例锁。"""
from __future__ import annotations

import threading
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping

RETRY_HOURS = (2, 8, 20, 44, 68, 92, 116, 140, 164, 168, 720)
LOCK_KEY = 1_296_258_244
NOT_BEFORE_KEY = "collector_opendota_not_before"


class SystemClock:
    def __init__(self):
        self._stop_event = threading.Event()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        if self._stop_event.wait(seconds):
            raise StopRequested("采集器已收到停止信号")

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()


class StopRequested(RuntimeError):
    pass


def classify_detail(payload: Mapping[str, object]) -> str:
    if "game_mode" not in payload or payload.get("game_mode") is None:
        return "unknown"
    picks = payload.get("picks_bans")
    if int(payload["game_mode"]) != 2:
        return "non_cm"
    return "complete" if picks else "not_ready"


def missing_draft_transition(
    discovered_at: datetime, now: datetime
) -> tuple[str, datetime | None, bool]:
    """按首次发现的绝对时间计算状态，进程重启不会重置阶梯。"""
    age_h = max(0.0, (now - discovered_at).total_seconds() / 3600)
    if age_h >= 720:
        return "unavailable", None, True
    if age_h >= 168:
        return "unavailable", discovered_at + timedelta(hours=720), False
    for hours in RETRY_HOURS:
        if hours > age_h:
            return "pending", discovered_at + timedelta(hours=hours), False
    raise AssertionError("重试阶梯没有覆盖当前时间")


@dataclass(frozen=True)
class ScanState:
    mode: str
    cutoff_at: datetime
    next_less_than_match_id: int | None
    stop_at_match_id: int | None
    cycle_high_match_id: int | None


class CollectorState:
    def __init__(self, conn, *, clock=None):
        self.conn = conn
        self.clock = clock or SystemClock()

    def record_attempt(
        self,
        source: str,
        *,
        outcome: str,
        match_id: int | None = None,
        detail: str | None = None,
        at: datetime | None = None,
    ) -> None:
        attempt_id = self.begin_attempt(source, match_id=match_id, at=at)
        self.finish_attempt(attempt_id, outcome=outcome, detail=detail)

    def begin_attempt(
        self,
        source: str,
        *,
        match_id: int | None = None,
        at: datetime | None = None,
    ) -> int:
        """请求发出前先占一行，进程异常退出也不会把预算返还。"""
        at = at or self.clock.now()
        attempt_id = self.conn.execute(
            "INSERT INTO collector_attempts(source,match_id,outcome,detail,at) "
            "VALUES (%s,%s,'error','request_started',%s) RETURNING attempt_id",
            (source, match_id, at),
        ).fetchone()[0]
        if match_id is not None:
            self.conn.execute(
                "UPDATE matches SET attempt_count=attempt_count+1,last_attempt_at=%s "
                "WHERE match_id=%s AND data_source='pro_match'",
                (at, match_id),
            )
        return int(attempt_id)

    def finish_attempt(self, attempt_id: int, *, outcome: str, detail: str | None = None) -> None:
        self.conn.execute(
            "UPDATE collector_attempts SET outcome=%s,detail=%s WHERE attempt_id=%s",
            (outcome, detail, attempt_id),
        )

    def fail_latest_detail_attempt(self, match_id: int, error_type: str) -> None:
        """详情已成功返回但落库失败时，改写同一次 HTTP 尝试，不新增预算行。"""
        self.conn.execute(
            """UPDATE collector_attempts SET outcome='error',detail=%s
               WHERE attempt_id=(
                 SELECT attempt_id FROM collector_attempts
                 WHERE source='match_details' AND match_id=%s
                 ORDER BY attempt_id DESC LIMIT 1
               )""",
            (error_type[:1000], match_id),
        )

    def daily_used(self, now: datetime | None = None) -> int:
        now = now or self.clock.now()
        start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            self.conn.execute(
                "SELECT count(*) FROM collector_attempts WHERE at >= %s AND at < %s",
                (start, start + timedelta(days=1)),
            ).fetchone()[0]
        )

    def minute_window(self, now: datetime | None = None) -> tuple[int, datetime | None]:
        now = now or self.clock.now()
        row = self.conn.execute(
            "SELECT count(*),min(at) FROM collector_attempts WHERE at > %s AND at <= %s",
            (now - timedelta(seconds=60), now),
        ).fetchone()
        return int(row[0]), row[1]

    def opendota_not_before(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT (value->>'at')::timestamptz FROM app_config_kv WHERE key=%s",
            (NOT_BEFORE_KEY,),
        ).fetchone()
        return row[0] if row else None

    def set_opendota_not_before(self, value: datetime) -> None:
        payload = json.dumps({"at": value.astimezone(UTC).isoformat()})
        self.conn.execute(
            """INSERT INTO app_config_kv(key,value,note)
               VALUES (%s,%s::jsonb,'OpenDota Retry-After 全局冷却截止时间')
               ON CONFLICT(key) DO UPDATE SET
                 value=CASE
                   WHEN (app_config_kv.value->>'at')::timestamptz
                          >= (EXCLUDED.value->>'at')::timestamptz
                   THEN app_config_kv.value ELSE EXCLUDED.value END,
                 updated_at=now()""",
            (NOT_BEFORE_KEY, payload),
        )

    def enqueue(
        self,
        match_id: int,
        *,
        discovered_at: datetime | None = None,
        force: bool = False,
        reopen_complete: bool = False,
    ) -> None:
        discovered_at = discovered_at or self.clock.now()
        self.conn.execute(
            """INSERT INTO collector_match_queue
               (match_id,discovered_at,status,next_attempt_at,final_attempt_done)
               VALUES (%s,%s,'pending',%s,false)
               ON CONFLICT (match_id) DO UPDATE SET
                 status = CASE WHEN %s OR (%s AND collector_match_queue.status='complete')
                               THEN 'pending' ELSE collector_match_queue.status END,
                 next_attempt_at = CASE WHEN %s OR (%s AND collector_match_queue.status='complete')
                                        THEN EXCLUDED.next_attempt_at
                                        ELSE collector_match_queue.next_attempt_at END,
                 final_attempt_done = CASE WHEN %s OR (%s AND collector_match_queue.status='complete')
                                           THEN false
                                           ELSE collector_match_queue.final_attempt_done END,
                 last_error = CASE WHEN %s OR (%s AND collector_match_queue.status='complete')
                                   THEN NULL ELSE collector_match_queue.last_error END,
                 updated_at=now()""",
            (
                match_id, discovered_at, discovered_at,
                force, reopen_complete, force, reopen_complete,
                force, reopen_complete, force, reopen_complete,
            ),
        )

    def due_matches(self, limit: int, *, match_id: int | None = None) -> list[tuple[int, datetime]]:
        if limit <= 0:
            return []
        target_sql = "AND match_id=%s" if match_id is not None else ""
        params = (self.clock.now(), match_id, limit) if match_id is not None else (
            self.clock.now(), limit
        )
        return self.conn.execute(
            f"""SELECT match_id,discovered_at FROM collector_match_queue
               WHERE status IN ('pending','unavailable','error')
                 AND NOT final_attempt_done AND next_attempt_at <= %s
                 {target_sql}
               ORDER BY next_attempt_at,match_id LIMIT %s""",
            params,
        ).fetchall()

    def mark_complete(self, match_id: int) -> None:
        self._mark(match_id, "complete", None, None, False)

    def mark_non_cm(self, match_id: int) -> None:
        self._mark(match_id, "non_cm", None, None, True)

    def mark_not_ready(self, match_id: int, discovered_at: datetime) -> str:
        queue_status, due, final = missing_draft_transition(
            discovered_at, self.clock.now()
        )
        self._mark(match_id, queue_status, due, None, final)
        self.conn.execute(
            "UPDATE matches SET draft_state=%s WHERE match_id=%s AND data_source='pro_match' "
            "AND draft_state <> 'complete'",
            ("unavailable" if queue_status == "unavailable" else "pending", match_id),
        )
        return queue_status

    def mark_error(self, match_id: int, error: str, *, terminal: bool = False) -> None:
        self._mark(
            match_id,
            "error",
            None if terminal else self.clock.now() + timedelta(minutes=15),
            error[:1000],
            terminal,
        )

    def _mark(self, match_id, queue_status, next_at, error, final) -> None:
        self.conn.execute(
            """UPDATE collector_match_queue
               SET status=%s,next_attempt_at=%s,last_error=%s,final_attempt_done=%s,updated_at=now()
               WHERE match_id=%s""",
            (queue_status, next_at, error, final, match_id),
        )

    def get_scan(self, source: str = "pro_matches", *, window_days: int = 90) -> ScanState:
        row = self.conn.execute(
            "SELECT mode,cutoff_at,next_less_than_match_id,stop_at_match_id,cycle_high_match_id "
            "FROM collector_pro_scans WHERE source=%s",
            (source,),
        ).fetchone()
        if row is None:
            cutoff = self.clock.now() - timedelta(days=window_days)
            self.conn.execute(
                "INSERT INTO collector_pro_scans(source,mode,cutoff_at) VALUES (%s,'backfill',%s)",
                (source, cutoff),
            )
            return ScanState("backfill", cutoff, None, None, None)
        return ScanState(*row)

    def advance_scan(self, scan: ScanState, *, next_less: int, high: int | None) -> None:
        cycle_high = max(filter(lambda x: x is not None, (scan.cycle_high_match_id, high)), default=None)
        self.conn.execute(
            """UPDATE collector_pro_scans SET next_less_than_match_id=%s,
               cycle_high_match_id=%s,updated_at=now() WHERE source='pro_matches'""",
            (next_less, cycle_high),
        )

    def finish_scan(self, scan: ScanState, *, high: int | None) -> None:
        cycle_high = max(filter(lambda x: x is not None, (scan.cycle_high_match_id, high)), default=None)
        previous = self.conn.execute(
            "SELECT last_seen_id FROM collector_cursors WHERE source='pro_matches'"
        ).fetchone()
        last_seen = max(filter(lambda x: x is not None, ((previous[0] if previous else None), cycle_high)), default=None)
        self.conn.execute(
            """INSERT INTO collector_cursors(source,last_seen_id,last_seen_at)
               VALUES ('pro_matches',%s,%s)
               ON CONFLICT(source) DO UPDATE SET last_seen_id=EXCLUDED.last_seen_id,
                 last_seen_at=EXCLUDED.last_seen_at,updated_at=now()""",
            (last_seen, self.clock.now()),
        )
        self.conn.execute(
            """UPDATE collector_pro_scans SET mode='incremental',cutoff_at=%s,
               next_less_than_match_id=NULL,stop_at_match_id=%s,cycle_high_match_id=NULL,
               updated_at=now() WHERE source='pro_matches'""",
            (self.clock.now() - timedelta(days=90), last_seen),
        )

    def reset_incremental_cycle(self) -> ScanState:
        row = self.conn.execute(
            "SELECT last_seen_id FROM collector_cursors WHERE source='pro_matches'"
        ).fetchone()
        stop = row[0] if row else None
        cutoff = self.clock.now() - timedelta(days=90)
        self.conn.execute(
            """UPDATE collector_pro_scans SET cutoff_at=%s,next_less_than_match_id=NULL,
               stop_at_match_id=%s,cycle_high_match_id=NULL,updated_at=now()
               WHERE source='pro_matches'""",
            (cutoff, stop),
        )
        return ScanState("incremental", cutoff, None, stop, None)

    def try_lock(self) -> bool:
        return bool(self.conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0])

    def unlock(self) -> None:
        self.conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
