"""Policy 的只读 PostgreSQL 查询层与在线入口。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from analysis.policy_engine import (
    PolicyRequestError,
    _validate_request,
    build_policy,
    fit_request_model,
)
from ingest.order_families import SPEC_FAMILY, family_for
from models.policy_model import ALGORITHM_VERSION


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVALUATION_PATH = ROOT / "data" / "models" / "policy-evaluation.json"


def _cutoff(as_of: str) -> datetime:
    requested_exclusive = datetime.combine(
        date.fromisoformat(as_of) + timedelta(days=1), time.min, UTC
    )
    return min(requested_exclusive, datetime.now(UTC))


def _latest_vocab(cursor: Any, patch_row: dict[str, Any]) -> tuple[list[int], dict[str, Any]]:
    snapshot = cursor.execute(
        """SELECT snapshot_version, fetched_at, n_heroes
           FROM constants_snapshot
           ORDER BY snapshot_version DESC
           LIMIT 1"""
    ).fetchone()
    if snapshot is None:
        raise PolicyRequestError("upstream_unavailable", "缺少英雄 token 快照")
    indexed = list(
        cursor.execute(
            """SELECT hero_id, dense_index
               FROM hero_token_index
               WHERE snapshot_version=%s
               ORDER BY dense_index""",
            (snapshot["snapshot_version"],),
        )
    )
    if len(indexed) != int(snapshot["n_heroes"]):
        raise PolicyRequestError("upstream_unavailable", "英雄 token 快照行数不完整")
    hero_ids = [int(row["hero_id"]) for row in indexed]

    pool = patch_row.get("is_cm_pool_snapshot")
    historical_exact = False
    if isinstance(pool, list) and pool and all(type(hero_id) is int for hero_id in pool):
        allowed = set(pool)
        hero_ids = [hero_id for hero_id in hero_ids if hero_id in allowed]
        historical_exact = True
    return hero_ids, {
        "vocab_rule": "latest hero_token_index snapshot",
        "constants_snapshot_version": int(snapshot["snapshot_version"]),
        "constants_fetched_at": snapshot["fetched_at"].isoformat(),
        "historical_cm_pool_exact": historical_exact,
        "limitation": (
            None
            if historical_exact
            else "patch 缺少 is_cm_pool_snapshot，候选池使用当前冻结英雄词表，不是严格历史 CM 池回放"
        ),
    }


def _load_matches(
    cursor: Any,
    *,
    patch: str | None,
    sources: list[str],
    since: datetime | None,
    until: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    clauses = [
        "m.data_source = ANY(%s)",
        "m.started_at < %s",
        "m.duration_s IS NOT NULL",
        "m.started_at + make_interval(secs => m.duration_s) <= %s",
        "m.game_mode = 2",
        "m.draft_state = 'complete'",
        "NOT m.anomaly",
        "m.n_draft_actions = 24",
    ]
    params: list[Any] = [sources, until, until]
    if since is not None:
        clauses.append("m.started_at >= %s")
        params.append(since)
    if patch is not None:
        clauses.append("p.version_name = %s")
        params.append(patch)
    match_rows = list(
        cursor.execute(
            f"""SELECT m.match_id, m.started_at, m.first_pick_team,
                       m.radiant_team_id, m.dire_team_id, m.data_source,
                       p.version_name AS patch
                FROM opponent_profile_matches_with_pub m
                JOIN patches p USING (patch_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY m.started_at, m.match_id""",
            params,
        )
    )
    if not match_rows:
        return [], {"hard_gate_rows": 0, "not_current_order_family": 0}

    match_ids = [int(row["match_id"]) for row in match_rows]
    actions_by_match: dict[int, list[dict[str, Any]]] = {match_id: [] for match_id in match_ids}
    for row in cursor.execute(
        """SELECT match_id, ord, is_pick, team, hero_id
           FROM draft_actions
           WHERE match_id = ANY(%s)
           ORDER BY match_id, ord""",
        (match_ids,),
    ):
        actions_by_match[int(row["match_id"])].append(
            {
                "ord": int(row["ord"]),
                "is_pick": bool(row["is_pick"]),
                "team": int(row["team"]),
                "hero_id": int(row["hero_id"]),
            }
        )

    accepted = []
    excluded = 0
    for raw in match_rows:
        match_id = int(raw["match_id"])
        actions = actions_by_match[match_id]
        if family_for(24, raw["first_pick_team"], actions) != SPEC_FAMILY:
            excluded += 1
            continue
        accepted.append(
            {
                "match_id": match_id,
                "started_at": raw["started_at"].isoformat(),
                "patch": str(raw["patch"]),
                "source": str(raw["data_source"]),
                "first_pick_team": int(raw["first_pick_team"]),
                "radiant_team_id": (
                    int(raw["radiant_team_id"]) if raw["radiant_team_id"] is not None else None
                ),
                "dire_team_id": (
                    int(raw["dire_team_id"]) if raw["dire_team_id"] is not None else None
                ),
                "actions": actions,
            }
        )
    return accepted, {
        "hard_gate_rows": len(match_rows),
        "not_current_order_family": excluded,
    }


def load_policy_dataset(conn: Any, request: dict[str, Any]) -> dict[str, Any]:
    """读取与请求严格同 patch、同来源且不晚于 as_of 的合法当前 24 手族。"""

    clean = _validate_request(request)
    with conn.cursor(row_factory=dict_row) as cursor:
        patch_row = cursor.execute(
            """SELECT patch_id, version_name, is_cm_pool_snapshot
               FROM patches WHERE version_name=%s""",
            (clean["patch"],),
        ).fetchone()
        if patch_row is None:
            raise PolicyRequestError("not_found", "请求的 patch 不存在")
        found_team_ids = {
            int(row["team_id"])
            for row in cursor.execute(
                "SELECT team_id FROM teams WHERE team_id = ANY(%s)",
                ([clean["radiant_team_id"], clean["dire_team_id"]],),
            )
        }
        if found_team_ids != {clean["radiant_team_id"], clean["dire_team_id"]}:
            raise PolicyRequestError("not_found", "请求的战队不存在")
        hero_ids, metadata = _latest_vocab(cursor, patch_row)
        matches, flow = _load_matches(
            cursor,
            patch=clean["patch"],
            sources=clean["sources"],
            since=None,
            until=_cutoff(clean["as_of"]),
        )
    return {
        "patch": clean["patch"],
        "as_of": clean["as_of"],
        "sources": clean["sources"],
        "hero_ids": hero_ids,
        "matches": matches,
        "metadata": {**metadata, **flow},
    }


def _unready(reason: str, **metadata: Any) -> dict[str, Any]:
    return {
        "ready": False,
        "release_ready": False,
        "experimental_ready": False,
        "independent_holdout": None,
        "evaluation_status": "unavailable",
        "frequency_top1": metadata.pop("frequency_top1", None),
        "model_top1": None,
        "quality_gate": reason,
        **metadata,
    }


def _load_evaluation(patch: str, sources: list[str], as_of: str) -> dict[str, Any]:
    path = Path(os.environ.get("POLICY_EVALUATION_PATH", DEFAULT_EVALUATION_PATH))
    if not path.is_file():
        return _unready("missing_or_invalid_evaluation")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("algorithm_version") != ALGORITHM_VERSION:
            return _unready("missing_or_invalid_evaluation")
        overall = report["metrics"]["overall"]
        patch_metrics = report["metrics"].get("by_patch", {}).get(patch)
        independent_holdout = bool(
            report.get("protocol", {}).get("independent_holdout", False)
        )
        metadata = {
            "overall_metrics": overall,
            "patch_metrics": patch_metrics,
            "evaluation_until_exclusive": report["data"].get("until_exclusive"),
            "independent_holdout": independent_holdout,
        }
        if sorted(report["data"].get("sources", [])) != sorted(sources):
            return _unready("source_mismatch")
        evaluation_until = datetime.fromisoformat(
            str(report["data"]["until_exclusive"]).replace("Z", "+00:00")
        )
        if evaluation_until > _cutoff(as_of):
            return _unready("evaluation_after_request_cutoff")
        development_ready = bool(report.get("development_ready", report.get("ready")))
        if not development_ready:
            return _unready(
                "overall_not_better_than_baseline",
                frequency_top1=overall["stage_pickban_frequency"]["top1"],
                evaluation_status="failed_quality_gate",
                **metadata,
            )
        if not patch_metrics or int(patch_metrics.get("n_predictions", 0)) < 100:
            return _unready("patch_insufficient_holdout", **metadata)
        patch_model_top1 = patch_metrics["model"]["top1"]
        patch_frequency_top1 = patch_metrics["stage_pickban_frequency"]["top1"]
        if patch_model_top1 is None or patch_frequency_top1 is None or patch_model_top1 <= patch_frequency_top1:
            return _unready(
                "patch_not_better_than_baseline",
                frequency_top1=patch_frequency_top1,
                **metadata,
            )
        if not report.get("release_ready"):
            return _unready(
                "holdout_not_independent",
                frequency_top1=patch_frequency_top1,
                experimental_ready=True,
                experimental_model_top1=patch_model_top1,
                evaluation_status="development_reused_holdout",
                **metadata,
            )
        return {
            "ready": True,
            "release_ready": True,
            "experimental_ready": False,
            "independent_holdout": independent_holdout,
            "evaluation_status": "release_ready",
            "frequency_top1": patch_frequency_top1,
            "model_top1": patch_model_top1,
            "quality_gate": "passed",
            **metadata,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return _unready("missing_or_invalid_evaluation")


def prepare_policy_from_connection(
    conn: Any, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    dataset = load_policy_dataset(conn, request)
    evaluation = _load_evaluation(dataset["patch"], dataset["sources"], dataset["as_of"])
    if len(dataset["matches"]) < 30:
        evaluation = {
            **evaluation,
            "ready": False,
            "model_top1": None,
            "quality_gate": "request_window_insufficient_matches",
        }
    model = (
        fit_request_model(dataset)
        if evaluation["ready"] or evaluation.get("experimental_ready")
        else None
    )
    return dataset, model, evaluation


def load_policy_from_connection(conn: Any, request: dict[str, Any]) -> dict[str, Any]:
    dataset, model, evaluation = prepare_policy_from_connection(conn, request)
    return build_policy(dataset, request, model=model, evaluation=evaluation)


def prepare_policy(
    dsn: str, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """单次只读快照与单次拟合，供决策层重复调用纯 build_policy。"""
    _validate_request(request)
    try:
        normalized = dsn.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(normalized, connect_timeout=5) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            return prepare_policy_from_connection(conn, request)
    except PolicyRequestError:
        raise
    except psycopg.Error:
        raise PolicyRequestError("upstream_unavailable", "Policy 数据库暂时不可用") from None


def load_policy(dsn: str, request: dict[str, Any]) -> dict[str, Any]:
    """便捷入口，单次准备后构建一个 Policy 响应。"""

    dataset, model, evaluation = prepare_policy(dsn, request)
    return build_policy(dataset, request, model=model, evaluation=evaluation)


def load_training_snapshot(
    dsn: str,
    *,
    since: datetime,
    until: datetime | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """训练脚本专用只读快照，只返回合法当前 24 手族。"""

    sources = sources or ["pro_match"]
    clean_sources = _validate_request(
        {
            "patch": "_training_",
            "first_pick_team": 0,
            "radiant_team_id": 1,
            "dire_team_id": 2,
            "draft": [],
            "sources": sources,
        }
    )["sources"]
    until = min(until or datetime.now(UTC), datetime.now(UTC))
    normalized = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(normalized, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        with conn.cursor(row_factory=dict_row) as cursor:
            snapshot = cursor.execute(
                """SELECT snapshot_version, fetched_at, n_heroes
                   FROM constants_snapshot ORDER BY snapshot_version DESC LIMIT 1"""
            ).fetchone()
            if snapshot is None:
                raise PolicyRequestError("upstream_unavailable", "缺少英雄 token 快照")
            rows = list(
                cursor.execute(
                    """SELECT hero_id FROM hero_token_index
                       WHERE snapshot_version=%s ORDER BY dense_index""",
                    (snapshot["snapshot_version"],),
                )
            )
            hero_ids = [int(row["hero_id"]) for row in rows]
            if len(hero_ids) != int(snapshot["n_heroes"]):
                raise PolicyRequestError("upstream_unavailable", "英雄 token 快照不完整")
            matches, flow = _load_matches(
                cursor,
                patch=None,
                sources=clean_sources,
                since=since,
                until=until,
            )
            snapshot_at = cursor.execute(
                "SELECT transaction_timestamp() AS snapshot_at, txid_current_snapshot() AS pg_snapshot"
            ).fetchone()
    return {
        "hero_ids": hero_ids,
        "matches": matches,
        "metadata": {
            "snapshot_at": snapshot_at["snapshot_at"].isoformat(),
            "pg_snapshot": snapshot_at["pg_snapshot"],
            "transaction_isolation": "repeatable read",
            "transaction_read_only": True,
            "sources": clean_sources,
            "since_inclusive": since.isoformat(),
            "until_exclusive": until.isoformat(),
            "constants_snapshot_version": int(snapshot["snapshot_version"]),
            "constants_fetched_at": snapshot["fetched_at"].isoformat(),
            "vocab_rule": "latest hero_token_index snapshot",
            "historical_cm_pool_exact": False,
            "limitation": "patch CM 池快照缺失，评估使用当前冻结英雄词表，不是严格历史线上回放",
            **flow,
        },
    }
