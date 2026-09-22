"""职业比赛增量采集 CLI。"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from datetime import UTC, datetime

import psycopg

from ingest.collector_state import CollectorState, StopRequested, SystemClock
from ingest.live_store import (
    SourceConflict,
    discover_public_match,
    public_match_needs_detail,
    upsert_public_match,
)
from ingest.opendota import DailyBudgetExhausted, OpenDotaClient, UpstreamError


class AlreadyRunning(RuntimeError):
    pass


class Collector:
    def __init__(self, conn, api, state: CollectorState, *, clock=None):
        self.conn = conn
        self.api = api
        self.state = state
        self.clock = clock or SystemClock()

    def run_once(
        self, *, max_pages: int, max_details: int, match_id: int | None = None
    ) -> dict[str, int | str]:
        if not self.state.try_lock():
            raise AlreadyRunning("同一数据库已有职业比赛采集器在运行")
        stats: dict[str, int | str] = {
            "status": "ok",
            "pages": 0,
            "discovered": 0,
            "details": 0,
            "complete": 0,
            "pending": 0,
            "non_cm": 0,
            "errors": 0,
        }
        try:
            if match_id is not None:
                self.state.enqueue(match_id, force=True)
            try:
                self._discover(max_pages, stats)
            except (UpstreamError, psycopg.Error, KeyError, TypeError, ValueError):
                stats["errors"] += 1
                stats["status"] = "degraded"
            self._details(max_details, stats, match_id=match_id)
            if stats["errors"]:
                stats["status"] = "degraded"
            stats["daily_used"] = self.state.daily_used()
            return stats
        finally:
            self.state.unlock()

    def _discover(self, max_pages: int, stats: dict) -> None:
        if max_pages <= 0:
            return
        scan = self.state.get_scan()
        for _ in range(max_pages):
            self._raise_if_stopped()
            page = self.api.get_pro_matches(scan.next_less_than_match_id)
            stats["pages"] += 1
            if not page:
                self.state.finish_scan(scan, high=None)
                return
            page_ids = [int(item["match_id"]) for item in page]
            high = max(page_ids)
            reached_boundary = False
            for summary in page:
                self._raise_if_stopped()
                match_id = int(summary["match_id"])
                started_at = datetime.fromtimestamp(int(summary["start_time"]), UTC)
                if started_at < scan.cutoff_at:
                    reached_boundary = True
                    continue
                if scan.mode == "incremental" and scan.stop_at_match_id is not None:
                    if match_id <= scan.stop_at_match_id:
                        reached_boundary = True
                        continue
                try:
                    created = discover_public_match(self.conn, summary)
                except SourceConflict:
                    stats["errors"] += 1
                    continue
                if created:
                    stats["discovered"] += 1
                if created or public_match_needs_detail(self.conn, match_id):
                    self.state.enqueue(
                        match_id,
                        discovered_at=self.clock.now(),
                        reopen_complete=not created,
                    )
            if reached_boundary:
                self.state.finish_scan(scan, high=high)
                return
            next_less = min(page_ids)
            self.state.advance_scan(scan, next_less=next_less, high=high)
            scan = self.state.get_scan()

    def _details(self, max_details: int, stats: dict, *, match_id: int | None = None) -> None:
        for match_id, discovered_at in self.state.due_matches(
            max_details, match_id=match_id
        ):
            self._raise_if_stopped()
            try:
                detail = self.api.get_match(match_id)
                result = upsert_public_match(self.conn, detail, detail)
            except SourceConflict:
                stats["errors"] += 1
                self.state.fail_latest_detail_attempt(match_id, "SourceConflict")
                self.state.mark_error(match_id, "source_conflict", terminal=True)
                continue
            except DailyBudgetExhausted:
                raise
            except UpstreamError as exc:
                stats["errors"] += 1
                self.state.mark_error(match_id, str(exc))
                continue
            except (psycopg.Error, KeyError, TypeError, ValueError) as exc:
                stats["errors"] += 1
                self.state.fail_latest_detail_attempt(match_id, type(exc).__name__)
                self.state.mark_error(match_id, type(exc).__name__)
                continue
            stats["details"] += 1
            if result == "complete":
                self.state.mark_complete(match_id)
                stats["complete"] += 1
            elif result == "non_cm":
                self.state.mark_non_cm(match_id)
                stats["non_cm"] += 1
            else:
                queue_status = self.state.mark_not_ready(match_id, discovered_at)
                stats["pending"] += queue_status == "pending"

    def _raise_if_stopped(self) -> None:
        if getattr(self.clock, "stopped", False):
            raise StopRequested("采集器已收到停止信号")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenDota 职业比赛增量采集器")
    parser.add_argument("--once", action="store_true", help="只运行一个有界批次")
    parser.add_argument("--max-pages", type=int, default=5, help="单轮最多抓取列表页数")
    parser.add_argument("--max-details", type=int, default=20, help="单轮最多抓取详情数")
    parser.add_argument("--daily-limit", type=int, default=2760, help="UTC 日请求预算")
    parser.add_argument("--match-id", type=int, help="强制把单场加入修复队列")
    parser.add_argument("--interval", type=float, default=300.0, help="常驻模式轮询间隔秒数")
    return parser


def _dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("缺少 DATABASE_URL")
    return value.replace("postgresql+psycopg://", "postgresql://")


def result_exit_code(result: dict) -> int:
    return 0 if result.get("status") in {"ok", "stopped"} else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.max_pages < 0
        or args.max_details < 0
        or args.daily_limit <= 0
        or args.interval <= 0
    ):
        raise SystemExit(
            "max-pages/max-details 不得为负，daily-limit/interval 必须为正"
        )

    stopping = False
    clock = SystemClock()

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        clock.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    exit_code = 0
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        state = CollectorState(conn, clock=clock)
        api = OpenDotaClient.create(state, daily_limit=args.daily_limit, clock=clock)
        collector = Collector(conn, api, state, clock=clock)
        try:
            while not stopping:
                try:
                    result = collector.run_once(
                        max_pages=0 if args.match_id is not None else args.max_pages,
                        max_details=args.max_details,
                        match_id=args.match_id,
                    )
                except StopRequested:
                    result = {"status": "stopped", "daily_used": state.daily_used()}
                    stopping = True
                except (AlreadyRunning, DailyBudgetExhausted) as exc:
                    result = {"status": "blocked", "error": str(exc), "daily_used": state.daily_used()}
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                exit_code = result_exit_code(result)
                if args.once:
                    break
                try:
                    clock.sleep(args.interval)
                except StopRequested:
                    stopping = True
        finally:
            api.close()
    return exit_code if args.once else 0


if __name__ == "__main__":
    sys.exit(main())
