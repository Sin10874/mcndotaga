"""画像位置和赛事层级旁表写入。"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from analysis.position_inference import (
    METHOD_VERSION as POSITION_METHOD_VERSION,
    infer_team_positions,
    team_input_fingerprint,
)
from db.sources import resolve_sources


DEFAULT_TIER_CONFIG = Path(__file__).parents[1] / "config" / "league_tiers.json"
CANONICAL_TIERS = frozenset({"tier1", "tier2", "qualifier", "other"})
EVIDENCE_LEVELS = frozenset({"unique_mid", "core_economy", "support_economy", "unknown"})
_POSITION_FIELDS = (
    "match_id",
    "player_slot",
    "team",
    "account_id",
    "hero_id",
    "position",
    "stats_available",
    "lane_role",
    "last_hits",
    "net_worth",
    "gpm",
    "xpm",
)


def _nonempty_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value


def _reviewed_at(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("reviewed_at 必须是含时区的 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("reviewed_at 必须是含时区的 ISO 8601 时间") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("reviewed_at 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _load_tier_config(tier_config):
    if tier_config is not None:
        return tier_config
    try:
        return json.loads(DEFAULT_TIER_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("赛事层级配置不存在") from None
    except (OSError, json.JSONDecodeError):
        raise ValueError("赛事层级配置无法读取或不是合法 JSON") from None


def _validated_tier_rows(conn, tier_config) -> list[dict]:
    config = _load_tier_config(tier_config)
    if not isinstance(config, Mapping):
        raise ValueError("tier_config 必须是对象")
    required = {
        "schema_version",
        "method_version",
        "reviewed_at",
        "classification_note",
        "mappings",
    }
    if set(config) != required:
        raise ValueError("tier_config 顶层字段不符合约定")
    if isinstance(config["schema_version"], bool) or config["schema_version"] != 1:
        raise ValueError("schema_version 当前仅支持 1")
    method_version = _nonempty_text(config["method_version"], "method_version")
    reviewed_at = _reviewed_at(config["reviewed_at"])
    classification_note = _nonempty_text(
        config["classification_note"], "classification_note"
    )
    mappings = config["mappings"]
    if not isinstance(mappings, list):
        raise ValueError("mappings 必须是数组")

    rows: list[dict] = []
    seen_ids: set[int] = set()
    entry_fields = {
        "league_id",
        "verified_name",
        "canonical_tier",
        "source_url",
        "evidence",
    }
    for index, entry in enumerate(mappings):
        if not isinstance(entry, Mapping) or set(entry) != entry_fields:
            raise ValueError(f"mappings[{index}] 字段不符合约定")
        league_id = entry["league_id"]
        if isinstance(league_id, bool) or not isinstance(league_id, int) or league_id <= 0:
            raise ValueError(f"mappings[{index}].league_id 必须是正整数")
        if league_id in seen_ids:
            raise ValueError(f"重复 league_id：{league_id}")
        seen_ids.add(league_id)
        verified_name = _nonempty_text(
            entry["verified_name"], f"mappings[{index}].verified_name"
        )
        canonical_tier = entry["canonical_tier"]
        if canonical_tier not in CANONICAL_TIERS:
            raise ValueError(f"mappings[{index}].canonical_tier 不合法")
        source_url = _nonempty_text(
            entry["source_url"], f"mappings[{index}].source_url"
        )
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError(f"mappings[{index}].source_url 必须是 HTTPS URL")
        evidence = entry["evidence"]
        if not isinstance(evidence, Mapping):
            raise ValueError(f"mappings[{index}].evidence 必须是对象")
        evidence = dict(evidence)
        if (
            "classification_note" in evidence
            and evidence["classification_note"] != classification_note
        ):
            raise ValueError("evidence.classification_note 与顶层配置不一致")
        evidence["classification_note"] = classification_note
        try:
            json.dumps(evidence, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError(f"mappings[{index}].evidence 不是合法 JSON") from None
        rows.append(
            {
                "league_id": league_id,
                "verified_name": verified_name,
                "canonical_tier": canonical_tier,
                "source_url": source_url,
                "method_version": method_version,
                "evidence": evidence,
                "reviewed_at": reviewed_at,
            }
        )

    if rows:
        existing = {
            int(row[0]): row[1]
            for row in conn.execute(
                "SELECT league_id, name FROM leagues WHERE league_id = ANY(%s)",
                ([row["league_id"] for row in rows],),
            ).fetchall()
        }
        for row in rows:
            league_id = row["league_id"]
            if league_id not in existing:
                raise ValueError(f"未知 league_id：{league_id}")
            if row["verified_name"] != existing[league_id]:
                raise ValueError(
                    f"league_id={league_id} 赛事名称不一致"
                )
    return rows


def _validated_sources(sources) -> list[str]:
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence) or not sources:
        raise ValueError("sources 必须是非空序列")
    if any(not isinstance(source, str) for source in sources):
        raise ValueError("sources 只能包含字符串")
    return resolve_sources(list(sources))


def _validated_since(since) -> tuple[date | datetime | None, str | None]:
    if since is None:
        return None, None
    if isinstance(since, datetime):
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("since datetime 必须包含时区")
        value = since.astimezone(timezone.utc)
        return value, value.isoformat()
    if isinstance(since, date):
        return datetime.combine(since, time.min, timezone.utc), since.isoformat()
    raise ValueError("since 必须是 date、含时区的 datetime 或 None")


def _load_position_groups(conn, sources: list[str], since_value):
    view = (
        "opponent_profile_matches_with_pub"
        if "pub_match" in sources
        else "opponent_profile_matches"
    )
    params: list[object] = [sources]
    since_clause = ""
    if since_value is not None:
        since_clause = " AND m.started_at >= %s"
        params.append(since_value)
    with conn.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            f"""SELECT m.match_id, m.data_source, m.parse_state,
                       mp.player_slot, mp.team, mp.account_id, mp.hero_id,
                       mp.position, mp.stats_available, mp.lane_role,
                       mp.last_hits, mp.net_worth, mp.gpm, mp.xpm
                FROM {view} AS m
                JOIN match_players AS mp ON mp.match_id = m.match_id
                WHERE m.data_source = ANY(%s){since_clause}
                ORDER BY m.match_id, mp.team, mp.player_slot""",
            params,
        ).fetchall()

    grouped: dict[tuple[int, int], dict] = {}
    for record in records:
        key = (int(record["match_id"]), int(record["team"]))
        group = grouped.setdefault(
            key,
            {
                "match": {
                    "match_id": int(record["match_id"]),
                    "data_source": record["data_source"],
                    "parse_state": record["parse_state"],
                },
                "rows": [],
            },
        )
        group["rows"].append({field: record[field] for field in _POSITION_FIELDS})
    return [grouped[key] for key in sorted(grouped)]


def _validated_annotations(match: dict, rows: list[dict]) -> dict:
    fingerprint = team_input_fingerprint(match, rows)
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("位置输入指纹必须是非空字符串")
    annotations = infer_team_positions(match, rows)
    if not isinstance(annotations, list):
        raise ValueError("位置推断结果必须是数组")
    expected_slots = sorted(int(row["player_slot"]) for row in rows)
    actual_slots: list[int] = []
    positions: list[int] = []
    required = {
        "player_slot",
        "position",
        "reason",
        "method_version",
        "evidence_level",
        "evidence",
    }
    for annotation in annotations:
        if not isinstance(annotation, Mapping) or set(annotation) != required:
            raise ValueError("位置推断结果字段不完整")
        slot = annotation["player_slot"]
        if isinstance(slot, bool) or not isinstance(slot, int):
            raise ValueError("位置推断 player_slot 必须是整数")
        actual_slots.append(slot)
        position = annotation["position"]
        if position is not None:
            if isinstance(position, bool) or not isinstance(position, int) or position not in range(1, 6):
                raise ValueError("位置推断 position 必须为 1 至 5 或 null")
            positions.append(position)
        _nonempty_text(annotation["reason"], "位置推断 reason")
        if annotation["method_version"] != POSITION_METHOD_VERSION:
            raise ValueError("位置推断 method_version 不一致")
        if annotation["evidence_level"] not in EVIDENCE_LEVELS:
            raise ValueError("位置推断 evidence_level 不合法")
        if not isinstance(annotation["evidence"], Mapping):
            raise ValueError("位置推断 evidence 必须是对象")
    if actual_slots != expected_slots:
        raise ValueError("位置推断结果必须按 slot 完整稳定排序")
    if len(positions) != len(set(positions)):
        raise ValueError("位置推断的非空 position 必须唯一")
    try:
        json.dumps(annotations, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("位置推断结果不是合法 JSON") from None
    return {
        "match_id": match["match_id"],
        "team": rows[0]["team"],
        "input_fingerprint": fingerprint,
        "method_version": POSITION_METHOD_VERSION,
        "annotations": annotations,
    }


def _changed_position_rows(conn, desired: list[dict]) -> tuple[list[dict], int]:
    if not desired:
        return [], 0
    existing = {
        (int(row[0]), int(row[1])): (row[2], row[3], row[4])
        for row in conn.execute(
            """SELECT match_id, team, input_fingerprint, method_version, annotations
               FROM profile_position_annotations
               WHERE match_id = ANY(%s)""",
            (sorted({row["match_id"] for row in desired}),),
        ).fetchall()
    }
    changed = []
    unchanged = 0
    for row in desired:
        value = (
            row["input_fingerprint"],
            row["method_version"],
            row["annotations"],
        )
        if existing.get((row["match_id"], row["team"])) == value:
            unchanged += 1
        else:
            changed.append(row)
    return changed, unchanged


def _changed_tier_rows(conn, desired: list[dict]) -> tuple[list[dict], int]:
    if not desired:
        return [], 0
    existing = {
        int(row[0]): tuple(row[1:])
        for row in conn.execute(
            """SELECT league_id, verified_name, canonical_tier, source_url,
                      method_version, evidence, reviewed_at
               FROM profile_league_tiers
               WHERE league_id = ANY(%s)""",
            ([row["league_id"] for row in desired],),
        ).fetchall()
    }
    changed = []
    unchanged = 0
    for row in desired:
        value = (
            row["verified_name"],
            row["canonical_tier"],
            row["source_url"],
            row["method_version"],
            row["evidence"],
            row["reviewed_at"],
        )
        if existing.get(row["league_id"]) == value:
            unchanged += 1
        else:
            changed.append(row)
    return changed, unchanged


def _write_positions(conn, rows: list[dict]) -> int:
    written = 0
    for row in rows:
        result = conn.execute(
            """INSERT INTO profile_position_annotations(
                   match_id, team, input_fingerprint, method_version, annotations
               ) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT(match_id, team) DO UPDATE SET
                   input_fingerprint = EXCLUDED.input_fingerprint,
                   method_version = EXCLUDED.method_version,
                   annotations = EXCLUDED.annotations,
                   created_at = now()
               WHERE (profile_position_annotations.input_fingerprint,
                      profile_position_annotations.method_version,
                      profile_position_annotations.annotations)
                     IS DISTINCT FROM
                     (EXCLUDED.input_fingerprint,
                      EXCLUDED.method_version,
                      EXCLUDED.annotations)
               RETURNING match_id""",
            (
                row["match_id"],
                row["team"],
                row["input_fingerprint"],
                row["method_version"],
                Jsonb(row["annotations"]),
            ),
        ).fetchone()
        written += result is not None
    return written


def _write_tiers(conn, rows: list[dict]) -> int:
    written = 0
    for row in rows:
        result = conn.execute(
            """INSERT INTO profile_league_tiers(
                   league_id, verified_name, canonical_tier, source_url,
                   method_version, evidence, reviewed_at
               ) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(league_id) DO UPDATE SET
                   verified_name = EXCLUDED.verified_name,
                   canonical_tier = EXCLUDED.canonical_tier,
                   source_url = EXCLUDED.source_url,
                   method_version = EXCLUDED.method_version,
                   evidence = EXCLUDED.evidence,
                   reviewed_at = EXCLUDED.reviewed_at
               WHERE (profile_league_tiers.verified_name,
                      profile_league_tiers.canonical_tier,
                      profile_league_tiers.source_url,
                      profile_league_tiers.method_version,
                      profile_league_tiers.evidence,
                      profile_league_tiers.reviewed_at)
                     IS DISTINCT FROM
                     (EXCLUDED.verified_name,
                      EXCLUDED.canonical_tier,
                      EXCLUDED.source_url,
                      EXCLUDED.method_version,
                      EXCLUDED.evidence,
                      EXCLUDED.reviewed_at)
               RETURNING league_id""",
            (
                row["league_id"],
                row["verified_name"],
                row["canonical_tier"],
                row["source_url"],
                row["method_version"],
                Jsonb(row["evidence"]),
                row["reviewed_at"],
            ),
        ).fetchone()
        written += result is not None
    return written


def apply_profile_annotations(
    conn,
    *,
    sources=("pro_match",),
    since=None,
    tier_config=None,
    dry_run=True,
) -> dict:
    """计算并选择性写入旁表，事务提交或回滚由调用者负责。"""
    resolved_sources = _validated_sources(sources)
    since_value, since_summary = _validated_since(since)
    tier_rows = _validated_tier_rows(conn, tier_config)
    groups = _load_position_groups(conn, resolved_sources, since_value)
    desired_positions = [
        _validated_annotations(group["match"], group["rows"])
        for group in groups
    ]
    changed_positions, unchanged_positions = _changed_position_rows(
        conn, desired_positions
    )
    changed_tiers, unchanged_tiers = _changed_tier_rows(conn, tier_rows)

    position_written = 0
    tier_written = 0
    if not dry_run:
        position_written = _write_positions(conn, changed_positions)
        tier_written = _write_tiers(conn, changed_tiers)

    return {
        "dry_run": bool(dry_run),
        "sources": resolved_sources,
        "since": since_summary,
        "position_groups_scanned": len(groups),
        "position_annotations_planned": len(changed_positions),
        "position_annotations_written": position_written,
        "position_annotations_unchanged": unchanged_positions,
        "league_tiers_planned": len(changed_tiers),
        "league_tiers_written": tier_written,
        "league_tiers_unchanged": unchanged_tiers,
    }


def _cli_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD") from None


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="生成画像位置和赛事层级旁表")
    parser.add_argument("--apply", action="store_true", help="提交旁表变更")
    parser.add_argument("--since", type=_cli_date, help="只处理该日期起的比赛")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("缺少 DATABASE_URL")
    try:
        with psycopg.connect(
            dsn.replace("postgresql+psycopg://", "postgresql://")
        ) as conn:
            with conn.transaction():
                summary = apply_profile_annotations(
                    conn,
                    since=args.since,
                    dry_run=not args.apply,
                )
    except psycopg.Error:
        parser.error("数据库操作失败")
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
