"""M3 画像引擎的 PostgreSQL 数据集查询层。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any

from psycopg.rows import dict_row

from db.sources import SourceNotAllowed, resolve_sources


UTC = timezone.utc
MIN_SAMPLE_N = 30
_PRO_VIEW = "opponent_profile_matches"
_PUBLIC_VIEW = "opponent_profile_matches_with_pub"
_REQUIRED_WEIGHT_METRICS = ("hero_pool", "map_vision", "tempo")
_MIN_AS_OF = date.min + timedelta(days=89)


class ProfileNotFound(ValueError):
    """请求的战队或精确版本不存在。"""


def _validated_parameters(
    team_id: int, patch: str, as_of: date, sources: list[str]
) -> tuple[list[str], datetime, datetime]:
    if isinstance(team_id, bool) or not isinstance(team_id, int):
        raise ValueError("team_id 必须是整数")
    if not isinstance(patch, str) or not patch.strip():
        raise ValueError("patch 必须是非空字符串")
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise ValueError("as_of 必须是 date 对象")
    if as_of < _MIN_AS_OF:
        raise ValueError("as_of 过早，无法形成完整的 90 天窗口")
    if as_of > datetime.now(UTC).date():
        raise ValueError("as_of 不能晚于 UTC 今天")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources 必须是非空列表")

    try:
        resolved_sources = resolve_sources(sources)
    except SourceNotAllowed:
        if "scrim" in sources:
            raise SourceNotAllowed("画像查询禁止使用 scrim 来源") from None
        raise SourceNotAllowed("画像查询含不支持的数据来源") from None
    lower = datetime.combine(as_of - timedelta(days=89), time.min, UTC)
    upper = datetime.combine(as_of + timedelta(days=1), time.min, UTC)
    return resolved_sources, lower, upper


def _configured_min_sample_n(cursor: Any) -> int:
    row = cursor.execute(
        "SELECT value FROM app_config_kv WHERE key = 'min_sample_n'"
    ).fetchone()
    if row is None:
        raise ValueError("缺少 min_sample_n 配置")

    raw = row["value"]
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not float(raw).is_integer()
        or raw < 0
    ):
        raise ValueError("min_sample_n 配置必须是非负整数")
    return max(MIN_SAMPLE_N, int(raw))


def load_profile_dataset(
    conn: Any,
    *,
    team_id: int,
    patch: str,
    as_of: date,
    sources: list[str],
) -> dict[str, Any]:
    """读取画像计算所需的公共比赛、常量和配置。

    调用者负责开启只读、repeatable-read 事务。本函数不提交事务，也不改变
    连接或会话属性。
    """

    resolved_sources, lower, upper = _validated_parameters(
        team_id, patch, as_of, sources
    )
    view = _PUBLIC_VIEW if "pub_match" in resolved_sources else _PRO_VIEW

    with conn.cursor(row_factory=dict_row) as cursor:
        if cursor.execute(
            "SELECT 1 AS found FROM teams WHERE team_id = %s", (team_id,)
        ).fetchone() is None:
            raise ProfileNotFound(f"未知 team_id：{team_id}")

        patch_row = cursor.execute(
            """SELECT patch_id, version_name, base_version
               FROM patches
               WHERE version_name = %s""",
            (patch,),
        ).fetchone()
        if patch_row is None:
            raise ProfileNotFound(f"未知 patch：{patch}")

        min_sample_n = _configured_min_sample_n(cursor)

        matches = list(
            cursor.execute(
                f"""SELECT
                        m.*,
                        p.version_name AS patch,
                        p.base_version,
                        CASE WHEN l.tier IN ('tier1', 'tier2', 'qualifier', 'other')
                             THEN l.tier ELSE NULL END AS tier
                    FROM {view} AS m
                    JOIN patches AS p ON p.patch_id = m.patch_id
                    LEFT JOIN leagues AS l ON l.league_id = m.league_id
                    WHERE p.base_version = %s
                      AND m.data_source = ANY(%s)
                      AND m.started_at >= %s
                      AND m.started_at < %s
                      AND m.started_at <= CURRENT_TIMESTAMP
                      AND (m.data_source <> 'pro_match'
                           OR m.game_mode = 2
                           OR m.game_mode IS NULL)
                    ORDER BY m.started_at, m.match_id""",
                (patch_row["base_version"], resolved_sources, lower, upper),
            ).fetchall()
        )

        match_ids = [row["match_id"] for row in matches]
        players_by_match: dict[int, list[dict[str, Any]]] = {
            match_id: [] for match_id in match_ids
        }
        draft_by_match: dict[int, list[dict[str, Any]]] = {
            match_id: [] for match_id in match_ids
        }

        if match_ids:
            player_rows = cursor.execute(
                """SELECT mp.*, p.name
                   FROM match_players AS mp
                   LEFT JOIN players AS p ON p.account_id = mp.account_id
                   WHERE mp.match_id = ANY(%s)
                   ORDER BY mp.match_id, mp.player_slot""",
                (match_ids,),
            ).fetchall()
            for player in player_rows:
                players_by_match[player["match_id"]].append(player)

            draft_rows = cursor.execute(
                """SELECT match_id, ord, is_pick, team, hero_id
                   FROM draft_actions
                   WHERE match_id = ANY(%s)
                   ORDER BY match_id, ord""",
                (match_ids,),
            ).fetchall()
            for action in draft_rows:
                draft_by_match[action["match_id"]].append(action)

        for match in matches:
            match_id = match["match_id"]
            match["players"] = players_by_match[match_id]
            match["draft"] = draft_by_match[match_id]

        hero_roles = {
            row["hero_id"]: list(row["roles"] or [])
            for row in cursor.execute(
                "SELECT hero_id, roles FROM heroes ORDER BY hero_id"
            ).fetchall()
        }

        weights: dict[str, dict[str, float]] = {}
        weight_rows = cursor.execute(
            """SELECT metric, data_source, weight
               FROM metric_weights
               WHERE data_source = ANY(%s)
               ORDER BY metric, data_source""",
            (resolved_sources,),
        ).fetchall()
        for row in weight_rows:
            weights.setdefault(row["metric"], {})[row["data_source"]] = float(
                row["weight"]
            )
        for metric in _REQUIRED_WEIGHT_METRICS:
            for source in resolved_sources:
                weight = weights.get(metric, {}).get(source)
                if weight is None:
                    raise ValueError(f"缺少权重配置：{metric}/{source}")
                if not isfinite(weight) or weight < 0:
                    raise ValueError(f"权重配置必须是有限非负数：{metric}/{source}")

    return {
        "team_id": team_id,
        "patch": patch_row["version_name"],
        "base_version": patch_row["base_version"],
        "as_of": as_of.isoformat(),
        "sources": resolved_sources,
        "matches": matches,
        "hero_roles": hero_roles,
        "weights": weights,
        "min_sample_n": min_sample_n,
    }
