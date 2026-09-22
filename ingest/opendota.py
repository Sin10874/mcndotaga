"""有持久配额账本的 OpenDota HTTP 客户端。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import math

import httpx

from ingest.collector_state import CollectorState, StopRequested, SystemClock, classify_detail

DEFAULT_BASE_URL = "https://api.opendota.com/api"


class DailyBudgetExhausted(RuntimeError):
    def __init__(self, used: int, limit: int):
        self.used = used
        self.limit = limit
        super().__init__(f"OpenDota UTC 日配额已用尽：{used}/{limit}")


class UpstreamError(RuntimeError):
    pass


class PersistentRateLimiter:
    def __init__(self, state: CollectorState, *, daily_limit: int, clock=None):
        self.state = state
        self.daily_limit = daily_limit
        self.clock = clock or SystemClock()

    def acquire(self) -> None:
        not_before = self.state.opendota_not_before()
        now = self.clock.now()
        if not_before is not None and not_before > now:
            self.clock.sleep((not_before - now).total_seconds())
        used = self.state.daily_used(self.clock.now())
        if used >= self.daily_limit:
            raise DailyBudgetExhausted(used, self.daily_limit)
        count, oldest = self.state.minute_window(self.clock.now())
        if count >= 60 and oldest is not None:
            wait = max(0.001, 60 - (self.clock.now() - oldest).total_seconds() + 0.001)
            self.clock.sleep(wait)


def _retry_after_seconds(value: str | None, now: datetime, *, fallback: float = 60.0) -> float:
    if not value:
        return fallback
    try:
        seconds = float(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        seconds = (parsed - now).total_seconds()
    return seconds if math.isfinite(seconds) and seconds > 0 else fallback


class OpenDotaClient:
    def __init__(
        self,
        client: httpx.Client,
        limiter: PersistentRateLimiter,
        state: CollectorState,
        *,
        clock=None,
        max_attempts: int = 3,
    ):
        self.client = client
        self.limiter = limiter
        self.state = state
        self.clock = clock or SystemClock()
        self.max_attempts = max_attempts

    @classmethod
    def create(cls, state: CollectorState, *, daily_limit: int, timeout=20.0, clock=None):
        clock = clock or SystemClock()
        limiter = PersistentRateLimiter(state, daily_limit=daily_limit, clock=clock)
        client = httpx.Client(base_url=DEFAULT_BASE_URL, timeout=timeout)
        return cls(client, limiter, state, clock=clock)

    def close(self) -> None:
        self.client.close()

    def get_pro_matches(self, less_than_match_id: int | None = None) -> list[dict]:
        params = {"less_than_match_id": less_than_match_id} if less_than_match_id is not None else None
        payload = self._get("/proMatches", source="pro_matches", params=params)
        if not isinstance(payload, list):
            raise UpstreamError("/proMatches 返回值不是数组")
        return payload

    def get_match(self, match_id: int) -> dict:
        payload = self._get(f"/matches/{int(match_id)}", source="match_details", match_id=match_id)
        if not isinstance(payload, dict):
            raise UpstreamError(f"/matches/{match_id} 返回值不是对象")
        return payload

    def _get(self, path: str, *, source: str, match_id=None, params=None):
        last_error = None
        for attempt in range(self.max_attempts):
            if getattr(self.clock, "stopped", False):
                raise StopRequested("采集器已收到停止信号")
            self.limiter.acquire()
            attempt_id = self.state.begin_attempt(source, match_id=match_id)
            try:
                response = self.client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                self.state.finish_attempt(
                    attempt_id, outcome="error", detail=type(exc).__name__
                )
                if attempt + 1 < self.max_attempts:
                    self.clock.sleep(float(2**attempt))
                    continue
                raise UpstreamError(str(exc)) from exc

            if response.status_code == 429:
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After"), self.clock.now(),
                    fallback=60.0 * 2**attempt,
                )
                self.state.set_opendota_not_before(
                    self.clock.now() + timedelta(seconds=retry_after)
                )
                self.state.finish_attempt(
                    attempt_id,
                    outcome="ratelimited",
                    detail=f"HTTP 429 retry_after_s={retry_after:.3f}",
                )
                if attempt + 1 < self.max_attempts:
                    self.clock.sleep(retry_after)
                    continue
                raise UpstreamError("OpenDota 连续返回 HTTP 429")

            if response.is_error:
                self.state.finish_attempt(
                    attempt_id,
                    outcome="error",
                    detail=f"HTTP {response.status_code}",
                )
                if response.status_code >= 500 and attempt + 1 < self.max_attempts:
                    self.clock.sleep(float(2**attempt))
                    continue
                raise UpstreamError(f"OpenDota HTTP {response.status_code}")

            try:
                payload = response.json()
            except ValueError as exc:
                self.state.finish_attempt(
                    attempt_id, outcome="error", detail="invalid_json"
                )
                raise UpstreamError("OpenDota 返回了无效 JSON") from exc

            if source == "pro_matches" and not isinstance(payload, list):
                self.state.finish_attempt(
                    attempt_id, outcome="error", detail="invalid_pro_matches_shape"
                )
                raise UpstreamError("/proMatches 返回值不是数组")
            if source == "match_details":
                if not isinstance(payload, dict):
                    self.state.finish_attempt(
                        attempt_id, outcome="error", detail="invalid_match_shape"
                    )
                    raise UpstreamError(f"/matches/{match_id} 返回值不是对象")
                if "error" in payload:
                    self.state.finish_attempt(
                        attempt_id, outcome="error", detail="upstream_error_object"
                    )
                    raise UpstreamError("OpenDota 返回错误对象")
                if payload.get("match_id") is None or int(payload["match_id"]) != int(match_id):
                    self.state.finish_attempt(
                        attempt_id, outcome="error", detail="invalid_match_id"
                    )
                    raise UpstreamError("OpenDota 详情 match_id 与请求不一致")
                if "game_mode" not in payload or payload.get("game_mode") is None:
                    self.state.finish_attempt(
                        attempt_id, outcome="error", detail="missing_game_mode"
                    )
                    raise UpstreamError("OpenDota 详情缺少 game_mode")
            outcome = "ok"
            if source == "match_details" and isinstance(payload, dict):
                outcome = "not_ready" if classify_detail(payload) == "not_ready" else "ok"
            self.state.finish_attempt(attempt_id, outcome=outcome)
            return payload
        raise UpstreamError(str(last_error or "OpenDota 请求失败"))
