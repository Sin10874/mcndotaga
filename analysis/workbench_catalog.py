"""交互式画像工作台的只读目录。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from analysis.position_inference import METHOD_VERSION
from analysis.profile_service import ProfileServiceError


UTC = timezone.utc
_POSITION_NOTICE = "赛后规则推断，不是真值或同场赛前特征。"
_TIER_NOTICE = "逐赛事证据分类，未审赛事保留未知。"


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _empty_team(team_id: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "name": row["name"],
        "tag": row["tag"],
        "n_matches": 0,
        "n_detailed_matches": 0,
        "latest_match_at": None,
        "patch_ids": set(),
        "patch_detail_counts": {},
    }


def build_catalog_from_connection(conn) -> dict[str, Any]:
    """在调用者当前快照内构造目录，不提交或改变事务属性。"""
    with conn.cursor(row_factory=dict_row) as cursor:
        clock = cursor.execute(
            """SELECT CURRENT_TIMESTAMP AS generated_at,
                      COALESCE(
                          MAX((started_at AT TIME ZONE 'UTC')::date),
                          (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date
                      ) AS as_of
               FROM opponent_profile_matches
               WHERE started_at <= CURRENT_TIMESTAMP
                 AND (game_mode = 2 OR game_mode IS NULL)"""
        ).fetchone()
        generated_at = clock["generated_at"]
        as_of: date = clock["as_of"]
        from_date = as_of - timedelta(days=89)

        matches = list(
            cursor.execute(
                """SELECT
                           m.match_id, m.patch_id, p.version_name AS patch,
                           p.base_version, m.started_at, m.league_id,
                           m.radiant_team_id, m.dire_team_id,
                           EXISTS (
                               SELECT 1 FROM match_players AS mp
                               WHERE mp.match_id = m.match_id
                           ) AS has_detail
                    FROM opponent_profile_matches AS m
                    LEFT JOIN patches AS p ON p.patch_id = m.patch_id
                    WHERE m.started_at <= CURRENT_TIMESTAMP
                      AND (m.game_mode = 2 OR m.game_mode IS NULL)
                      AND (m.started_at AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
                    ORDER BY m.started_at, m.match_id""",
                (from_date, as_of),
            ).fetchall()
        )

        team_ids = sorted(
            {
                team_id
                for match in matches
                for team_id in (match["radiant_team_id"], match["dire_team_id"])
                if team_id is not None
            }
        )
        team_rows = []
        if team_ids:
            team_rows = list(
                cursor.execute(
                    """SELECT team_id, name, tag
                       FROM teams
                       WHERE team_id = ANY(%s)
                       ORDER BY team_id""",
                    (team_ids,),
                ).fetchall()
            )
        teams_by_id = {
            row["team_id"]: _empty_team(row["team_id"], row) for row in team_rows
        }

        patches_by_id: dict[int, dict[str, Any]] = {}
        for match in matches:
            patch_id = match["patch_id"]
            if patch_id is not None and match["patch"] is not None:
                patch = patches_by_id.setdefault(
                    patch_id,
                    {
                        "patch_id": patch_id,
                        "patch": match["patch"],
                        "base_version": match["base_version"],
                        "n_matches": 0,
                        "n_detailed_matches": 0,
                        "latest_match_at": None,
                    },
                )
                patch["n_matches"] += 1
                patch["n_detailed_matches"] += int(match["has_detail"])
                patch["latest_match_at"] = max(
                    filter(None, (patch["latest_match_at"], match["started_at"]))
                )

            for team_id in (match["radiant_team_id"], match["dire_team_id"]):
                team = teams_by_id.get(team_id)
                if team is None:
                    continue
                team["n_matches"] += 1
                team["n_detailed_matches"] += int(match["has_detail"])
                team["latest_match_at"] = max(
                    filter(None, (team["latest_match_at"], match["started_at"]))
                )
                if patch_id is not None and match["patch"] is not None:
                    team["patch_ids"].add(patch_id)
                    counts = team["patch_detail_counts"]
                    counts[patch_id] = counts.get(patch_id, 0) + int(
                        match["has_detail"]
                    )

        patch_order = sorted(
            patches_by_id,
            key=lambda patch_id: (
                -patches_by_id[patch_id]["n_detailed_matches"],
                patch_id,
            ),
        )
        default_patch_id = patch_order[0] if patch_order else None
        default_team_id = None
        if default_patch_id is not None:
            candidates = [
                team
                for team in teams_by_id.values()
                if default_patch_id in team["patch_ids"]
            ]
            if candidates:
                candidates.sort(
                    key=lambda team: (
                        -team["patch_detail_counts"][default_patch_id],
                        team["team_id"],
                    )
                )
                default_team_id = candidates[0]["team_id"]

        teams = []
        for team in sorted(
            teams_by_id.values(),
            key=lambda row: (
                -row["n_detailed_matches"],
                -row["n_matches"],
                row["team_id"],
            ),
        ):
            teams.append(
                {
                    "team_id": team["team_id"],
                    "name": team["name"],
                    "tag": team["tag"],
                    "n_matches": team["n_matches"],
                    "n_detailed_matches": team["n_detailed_matches"],
                    "latest_match_at": _iso_utc(team["latest_match_at"]),
                    "patches": [
                        patches_by_id[patch_id]["patch"]
                        for patch_id in patch_order
                        if patch_id in team["patch_ids"]
                    ],
                }
            )

        patches = [
            {
                "patch": patches_by_id[patch_id]["patch"],
                "base_version": patches_by_id[patch_id]["base_version"],
                "n_matches": patches_by_id[patch_id]["n_matches"],
                "n_detailed_matches": patches_by_id[patch_id][
                    "n_detailed_matches"
                ],
                "latest_match_at": _iso_utc(
                    patches_by_id[patch_id]["latest_match_at"]
                ),
            }
            for patch_id in patch_order
        ]

        heroes = {
            str(row["hero_id"]): {
                "name": row["name"],
                "localized_name": row["localized_name"],
            }
            for row in cursor.execute(
                "SELECT hero_id, name, localized_name FROM heroes ORDER BY hero_id"
            ).fetchall()
        }

        league_ids = sorted(
            {match["league_id"] for match in matches if match["league_id"] is not None}
        )
        league_rows = []
        if league_ids:
            league_rows = list(
                cursor.execute(
                    """SELECT l.league_id, l.name,
                              reviewed.canonical_tier AS tier,
                              reviewed.source_url, reviewed.reviewed_at
                       FROM leagues AS l
                       JOIN profile_league_tiers AS reviewed
                         ON reviewed.league_id = l.league_id
                        AND btrim(reviewed.verified_name) = btrim(l.name)
                       WHERE l.league_id = ANY(%s)
                       ORDER BY l.league_id""",
                    (league_ids,),
                ).fetchall()
            )

    latest_match_at = max(
        (match["started_at"] for match in matches), default=None
    )
    detailed_matches = sum(int(match["has_detail"]) for match in matches)
    default_patch = (
        patches_by_id[default_patch_id]["patch"]
        if default_patch_id is not None
        else None
    )
    return {
        "generated_at": _iso_utc(generated_at),
        "window": {"as_of": as_of.isoformat(), "from": from_date.isoformat()},
        "defaults": {
            "team_id": default_team_id,
            "patch": default_patch,
            "as_of": as_of.isoformat(),
        },
        "teams": teams,
        "patches": patches,
        "heroes": heroes,
        "summary": {
            "public_cm_matches": len(matches),
            "detailed_matches": detailed_matches,
            "team_count": len(teams),
            "latest_match_at": _iso_utc(latest_match_at),
        },
        "provenance": {
            "position_method": METHOD_VERSION,
            "position_notice": _POSITION_NOTICE,
            "tier_notice": _TIER_NOTICE,
            "leagues": [
                {
                    "league_id": row["league_id"],
                    "name": row["name"],
                    "tier": row["tier"],
                    "source_url": row["source_url"],
                    "reviewed_at": _iso_utc(row["reviewed_at"]),
                }
                for row in league_rows
            ],
        },
    }


def load_catalog(dsn: str) -> dict[str, Any]:
    """在单独的只读可重复读快照内加载目录。"""
    try:
        normalized_dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(normalized_dsn, connect_timeout=5) as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            conn.execute("SET LOCAL statement_timeout = '15s'")
            return build_catalog_from_connection(conn)
    except ProfileServiceError:
        raise
    except psycopg.Error:
        raise ProfileServiceError(
            "upstream_unavailable", "目录数据库暂时不可用"
        ) from None
