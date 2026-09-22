from __future__ import annotations

import hashlib
import json
import math


METHOD_VERSION = "team_lane_economy_v1"
_FINGERPRINT_ROW_FIELDS = (
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
_ECONOMY_FIELDS = ("last_hits", "net_worth", "gpm", "xpm")
_CORE_FIELDS = ("last_hits", "net_worth", "gpm")


def team_input_fingerprint(match: dict, rows: list[dict]) -> str:
    canonical_rows = [
        {field: row.get(field) for field in _FINGERPRINT_ROW_FIELDS}
        for row in rows
    ]
    canonical_rows.sort(
        key=lambda row: (
            _sort_key(row.get("player_slot")),
            _canonical_json(row),
        )
    )
    payload = {
        "match_id": match.get("match_id"),
        "data_source": match.get("data_source"),
        "parse_state": match.get("parse_state"),
        "rows": canonical_rows,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def infer_team_positions(match: dict, rows: list[dict]) -> list[dict]:
    ordered = sorted(
        enumerate(rows),
        key=lambda item: (_sort_key(item[1].get("player_slot")), item[0]),
    )
    ordered_rows = [row for _, row in ordered]

    rejection = _validate_team(match, ordered_rows)
    if rejection is not None:
        return _unknown_results(ordered_rows, rejection)

    ranks = {
        int(row["player_slot"]): {
            field: _team_rank(row, ordered_rows, field) for field in _ECONOMY_FIELDS
        }
        for row in ordered_rows
    }
    result = {
        int(row["player_slot"]): _annotation(
            row,
            position=None,
            reason="1、2、3 号位未全部确定，不能区分辅助",
            evidence_level="unknown",
            team_ranks=ranks[int(row["player_slot"])],
        )
        for row in ordered_rows
    }

    lanes = {
        lane_role: [row for row in ordered_rows if row["lane_role"] == lane_role]
        for lane_role in (1, 2, 3)
    }
    mid = lanes[2][0]
    result[int(mid["player_slot"])] = _annotation(
        mid,
        position=2,
        reason="整队唯一中路",
        evidence_level="unique_mid",
        team_ranks=ranks[int(mid["player_slot"])],
    )

    resolved_core_slots = set()
    for lane_role, position, rank_limit in ((1, 1, 3), (3, 3, 4)):
        pair = lanes[lane_role]
        core = _strict_dominator(pair[0], pair[1], _CORE_FIELDS)
        if core is not None and all(
            ranks[int(core["player_slot"])][field] <= rank_limit
            for field in _CORE_FIELDS
        ):
            slot = int(core["player_slot"])
            resolved_core_slots.add(slot)
            result[slot] = _annotation(
                core,
                position=position,
                reason="同路核心经济三项严格领先且满足队内名次",
                evidence_level="core_economy",
                team_ranks=ranks[slot],
            )
            continue
        for row in pair:
            slot = int(row["player_slot"])
            result[slot] = _annotation(
                row,
                position=None,
                reason="同路经济并列、交叉或队内名次不足",
                evidence_level="unknown",
                team_ranks=ranks[slot],
            )

    if len(resolved_core_slots) == 2:
        assigned_slots = {
            slot for slot, item in result.items() if item["position"] in (1, 2, 3)
        }
        supports = [
            row for row in ordered_rows if int(row["player_slot"]) not in assigned_slots
        ]
        higher = _strict_dominator(supports[0], supports[1], _ECONOMY_FIELDS)
        if higher is not None:
            lower = supports[1] if higher is supports[0] else supports[0]
            for row, position in ((higher, 4), (lower, 5)):
                slot = int(row["player_slot"])
                result[slot] = _annotation(
                    row,
                    position=position,
                    reason="剩余辅助四项经济排序一致",
                    evidence_level="support_economy",
                    team_ranks=ranks[slot],
                )
        else:
            for row in supports:
                slot = int(row["player_slot"])
                result[slot] = _annotation(
                    row,
                    position=None,
                    reason="剩余辅助经济排序不一致",
                    evidence_level="unknown",
                    team_ranks=ranks[slot],
                )

    return [result[int(row["player_slot"])] for row in ordered_rows]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sort_key(value: object) -> tuple[int, object]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    return (1, repr(value))


def _valid_number(value: object) -> bool:
    if value is None or isinstance(value, (bool, str, bytes)):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(numeric) and numeric >= 0


def _validate_team(match: dict, rows: list[dict]) -> str | None:
    if len(rows) != 5:
        return "单侧必须恰好有五名选手"
    if any(row.get("match_id") != match.get("match_id") for row in rows):
        return "选手行必须属于目标比赛"
    teams = {row.get("team") for row in rows}
    if len(teams) != 1:
        return "输入必须属于同一侧"
    slots = [row.get("player_slot") for row in rows]
    if len(set(slots)) != 5:
        return "player_slot 必须唯一"
    team = rows[0].get("team")
    if (
        not isinstance(team, int)
        or isinstance(team, bool)
        or team not in (0, 1)
        or any(
            not isinstance(slot, int)
            or isinstance(slot, bool)
            or not 0 <= slot <= 9
            or ((slot < 5) != (team == 0))
            for slot in slots
        )
    ):
        return "player_slot 与 team 侧别不一致"
    if match.get("parse_state") != "full":
        return "比赛必须为 full parse"
    if any(row.get("stats_available") is not True for row in rows):
        return "整队 stats_available 必须为 true"
    if any(
        not _valid_number(row.get(field))
        for row in rows
        for field in _ECONOMY_FIELDS
    ):
        return "四项经济必须为有限非负数"
    if any(row.get("position") is not None for row in rows):
        return "整队已有原始 position，停止启发式推断"
    if any(
        not isinstance(row.get("lane_role"), int)
        or isinstance(row.get("lane_role"), bool)
        or row.get("lane_role") not in (1, 2, 3)
        for row in rows
    ):
        return "lane_role 只接受 1、2、3"
    lane_counts = tuple(
        sum(row["lane_role"] == lane_role for row in rows)
        for lane_role in (1, 2, 3)
    )
    if lane_counts != (2, 1, 2):
        return "整队分路形状必须为 2/1/2"
    return None


def _strict_dominator(first: dict, second: dict, fields: tuple[str, ...]) -> dict | None:
    if all(first[field] > second[field] for field in fields):
        return first
    if all(second[field] > first[field] for field in fields):
        return second
    return None


def _team_rank(row: dict, rows: list[dict], field: str) -> int:
    return 1 + sum(other[field] > row[field] for other in rows)


def _safe_economy_value(value: object) -> object:
    return value if _valid_number(value) else None


def _safe_json_scalar(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _evidence(row: dict, team_ranks: dict[str, int] | None = None) -> dict:
    return {
        "lane_role": _safe_json_scalar(row.get("lane_role")),
        "economy": {
            field: _safe_economy_value(row.get(field)) for field in _ECONOMY_FIELDS
        },
        "team_ranks": {
            field: team_ranks.get(field) if team_ranks is not None else None
            for field in _ECONOMY_FIELDS
        },
    }


def _annotation(
    row: dict,
    *,
    position: int | None,
    reason: str,
    evidence_level: str,
    team_ranks: dict[str, int] | None,
) -> dict:
    return {
        "player_slot": row.get("player_slot"),
        "position": position,
        "reason": reason,
        "method_version": METHOD_VERSION,
        "evidence_level": evidence_level,
        "evidence": _evidence(row, team_ranks),
    }


def _unknown_results(rows: list[dict], reason: str) -> list[dict]:
    return [
        _annotation(
            row,
            position=None,
            reason=reason,
            evidence_level="unknown",
            team_ranks=None,
        )
        for row in rows
    ]
