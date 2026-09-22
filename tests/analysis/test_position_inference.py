from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest

from analysis.position_inference import (
    METHOD_VERSION,
    infer_team_positions,
    team_input_fingerprint,
)


def _match(*, parse_state: str = "full") -> dict:
    return {
        "match_id": 900,
        "data_source": "pro_match",
        "parse_state": parse_state,
        "ignored": "不进入指纹",
    }


def _row(
    slot: int,
    lane_role: int,
    last_hits: int | float,
    net_worth: int | float,
    gpm: int | float,
    xpm: int | float,
    *,
    team: int = 0,
    position: int | None = None,
    stats_available: bool = True,
    account_id: int | None = None,
) -> dict:
    return {
        "match_id": 900,
        "player_slot": slot,
        "team": team,
        "account_id": account_id if account_id is not None else 10_000 + slot,
        "hero_id": 100 + slot,
        "position": position,
        "stats_available": stats_available,
        "lane_role": lane_role,
        "last_hits": last_hits,
        "net_worth": net_worth,
        "gpm": gpm,
        "xpm": xpm,
        "name": "不进入指纹",
    }


def _standard_rows() -> list[dict]:
    return [
        _row(0, 1, 320, 21_000, 680, 750),
        _row(1, 1, 25, 6_000, 270, 350),
        _row(2, 2, 260, 18_000, 600, 720),
        _row(3, 3, 180, 14_000, 480, 620),
        _row(4, 3, 70, 9_000, 350, 460),
    ]


def _by_slot(result: list[dict]) -> dict[int, dict]:
    return {item["player_slot"]: item for item in result}


def test_dire_normalized_slots_infer_roles_independently_of_slot_order():
    rows = _standard_rows()
    for row in rows:
        row["team"] = 1
        row["player_slot"] += 5
    result = infer_team_positions(_match(), list(reversed(rows)))
    assert [(item["player_slot"], item["position"]) for item in result] == [
        (5, 1), (6, 5), (7, 2), (8, 3), (9, 4)
    ]


def _assert_all_unknown(result: list[dict], reason: str) -> None:
    assert len(result) > 0
    assert all(item["position"] is None for item in result)
    assert all(item["evidence_level"] == "unknown" for item in result)
    assert all(item["reason"] == reason for item in result)


def test_standard_team_infers_all_positions_without_mutating_input() -> None:
    match = _match()
    rows = list(reversed(_standard_rows()))
    before_match = copy.deepcopy(match)
    before_rows = copy.deepcopy(rows)

    result = infer_team_positions(match, rows)

    assert match == before_match
    assert rows == before_rows
    assert [item["player_slot"] for item in result] == [0, 1, 2, 3, 4]
    assert {slot: item["position"] for slot, item in _by_slot(result).items()} == {
        0: 1,
        1: 5,
        2: 2,
        3: 3,
        4: 4,
    }
    assert {slot: item["evidence_level"] for slot, item in _by_slot(result).items()} == {
        0: "core_economy",
        1: "support_economy",
        2: "unique_mid",
        3: "core_economy",
        4: "support_economy",
    }
    assert all(item["method_version"] == METHOD_VERSION for item in result)
    assert _by_slot(result)[0]["evidence"] == {
        "lane_role": 1,
        "economy": {
            "last_hits": 320,
            "net_worth": 21_000,
            "gpm": 680,
            "xpm": 750,
        },
        "team_ranks": {
            "last_hits": 1,
            "net_worth": 1,
            "gpm": 1,
            "xpm": 1,
        },
    }


def test_fingerprint_uses_only_frozen_fields_and_is_order_independent() -> None:
    match = _match()
    rows = _standard_rows()
    filtered_rows = [
        {
            key: row[key]
            for key in (
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
        }
        for row in rows
    ]
    payload = {
        "match_id": 900,
        "data_source": "pro_match",
        "parse_state": "full",
        "rows": filtered_rows,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected = hashlib.sha256(encoded).hexdigest()

    assert team_input_fingerprint(match, list(reversed(rows))) == expected
    assert team_input_fingerprint({**match, "ignored": "变化"}, rows) == expected
    assert team_input_fingerprint(match, [{**row, "name": "变化"} for row in rows]) == expected
    changed = copy.deepcopy(rows)
    changed[0]["last_hits"] += 1
    assert team_input_fingerprint(match, changed) != expected


def test_existing_position_stops_the_entire_team_heuristic() -> None:
    rows = _standard_rows()
    rows[0]["position"] = 1

    result = infer_team_positions(_match(), rows)

    _assert_all_unknown(result, "整队已有原始 position，停止启发式推断")


def test_crossing_core_economy_leaves_that_core_and_supports_unknown() -> None:
    rows = _standard_rows()
    rows[0]["last_hits"] = 20

    result = _by_slot(infer_team_positions(_match(), rows))

    assert result[2]["position"] == 2
    assert result[3]["position"] == 3
    assert result[0]["position"] is None
    assert result[1]["position"] is None
    assert result[4]["position"] is None
    assert result[0]["reason"] == "同路经济并列、交叉或队内名次不足"
    assert result[1]["reason"] == "同路经济并列、交叉或队内名次不足"
    assert result[4]["reason"] == "1、2、3 号位未全部确定，不能区分辅助"


def test_core_exact_tie_is_not_broken_by_slot() -> None:
    rows = _standard_rows()
    rows[0]["gpm"] = rows[1]["gpm"]

    result = _by_slot(infer_team_positions(_match(), rows))

    assert result[0]["position"] is None
    assert result[1]["position"] is None


def test_position_one_must_pass_the_team_top_three_gate() -> None:
    rows = _standard_rows()
    rows[0].update(last_hits=100, net_worth=21_000, gpm=680)
    rows[1].update(last_hits=20, net_worth=6_000, gpm=270)
    rows[2]["last_hits"] = 300
    rows[3]["last_hits"] = 200
    rows[4]["last_hits"] = 150

    result = _by_slot(infer_team_positions(_match(), rows))

    assert result[0]["position"] is None
    assert result[0]["evidence"]["team_ranks"]["last_hits"] == 4


def test_supports_stay_unknown_when_four_economy_orders_cross() -> None:
    rows = _standard_rows()
    rows[1]["last_hits"] = 80

    result = _by_slot(infer_team_positions(_match(), rows))

    assert result[0]["position"] == 1
    assert result[2]["position"] == 2
    assert result[3]["position"] == 3
    assert result[1]["position"] is None
    assert result[4]["position"] is None
    assert result[1]["reason"] == "剩余辅助经济排序不一致"
    assert result[4]["reason"] == "剩余辅助经济排序不一致"


def test_nonstandard_lane_shape_is_entirely_unknown() -> None:
    rows = _standard_rows()
    rows[4]["lane_role"] = 2

    result = infer_team_positions(_match(), rows)

    _assert_all_unknown(result, "整队分路形状必须为 2/1/2")


@pytest.mark.parametrize(
    ("mutate_match", "mutate_rows", "reason"),
    [
        (
            lambda match: match.update(parse_state="header_only"),
            lambda rows: None,
            "比赛必须为 full parse",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(stats_available=False),
            "整队 stats_available 必须为 true",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(lane_role=4),
            "lane_role 只接受 1、2、3",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(lane_role=math.nan),
            "lane_role 只接受 1、2、3",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(net_worth=-1),
            "四项经济必须为有限非负数",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(xpm=math.nan),
            "四项经济必须为有限非负数",
        ),
        (
            lambda match: None,
            lambda rows: rows[0].update(gpm=math.inf),
            "四项经济必须为有限非负数",
        ),
    ],
)
def test_invalid_parse_lane_or_economy_rejects_the_entire_team(
    mutate_match, mutate_rows, reason: str
) -> None:
    match = _match()
    rows = _standard_rows()
    mutate_match(match)
    mutate_rows(rows)

    result = infer_team_positions(match, rows)

    _assert_all_unknown(result, reason)
    json.dumps(result, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows.pop(), "单侧必须恰好有五名选手"),
        (lambda rows: rows[1].update(player_slot=rows[0]["player_slot"]), "player_slot 必须唯一"),
        (
            lambda rows: rows[4].update(team=1, player_slot=9),
            "输入必须属于同一侧",
        ),
        (lambda rows: rows[0].update(match_id=901), "选手行必须属于目标比赛"),
        (lambda rows: rows[0].update(player_slot=5), "player_slot 与 team 侧别不一致"),
    ],
)
def test_invalid_team_or_slot_shape_rejects_the_entire_group(mutate, reason: str) -> None:
    rows = _standard_rows()
    mutate(rows)

    result = infer_team_positions(_match(), rows)

    _assert_all_unknown(result, reason)


def test_anonymous_players_do_not_change_position_rules() -> None:
    rows = _standard_rows()
    for row in rows:
        row["account_id"] = None

    result = infer_team_positions(_match(), rows)

    assert [item["position"] for item in result] == [1, 5, 2, 3, 4]
