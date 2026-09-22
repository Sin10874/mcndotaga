from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from analysis.profile_engine import ProfileInsufficientData, _percentile, build_profile
from contracts.tools.invariants import check_profile
from ingest.order_families import SPEC_FAMILY, resolve_in


AS_OF = dt.date(2026, 9, 22)
TEAM_ID = 10


def _player(
    account_id: int | None,
    *,
    team: int,
    hero_id: int,
    position: int | None = 1,
    name: str | None = None,
    stats_available: bool = True,
    strong: bool = False,
    firstblood_claimed: bool = False,
) -> dict:
    scale = 2 if strong else 1
    return {
        "player_slot": team * 5,
        "account_id": account_id,
        "name": name,
        "team": team,
        "hero_id": hero_id,
        "position": position,
        "kills": 8 * scale,
        "deaths": 2,
        "assists": 12 * scale,
        "gpm": 400 * scale,
        "xpm": 450 * scale,
        "last_hits": 100 * scale,
        "denies": 5,
        "hero_damage": 10_000 * scale,
        "hero_healing": 100 * scale,
        "tower_damage": 1_000,
        "damage_taken": {},
        "damage_taken_total": 8_000 * scale,
        "net_worth": 10_000,
        "obs_placed": 3 * scale,
        "sen_placed": 2 * scale,
        "observer_kills": 1 * scale,
        "sentry_kills": 1 * scale,
        "camps_stacked": 2 * scale,
        "rune_pickups": 4 * scale,
        "lane_role": 1,
        "is_roaming": False,
        "firstblood_claimed": firstblood_claimed,
        "stats_available": stats_available,
    }


def _anonymous_team(team: int, *, strong: bool = False) -> list[dict]:
    rows = []
    for slot in range(1, 5):
        row = _player(None, team=team, hero_id=100 + slot, position=slot + 1, strong=strong)
        row["player_slot"] = team * 5 + slot
        rows.append(row)
    return rows


def _spec_draft(*, picked_hero: int, first_pick_team: int = 0) -> list[dict]:
    actions = []
    used_target_pick = False
    target_bans = iter(range(50, 62))
    for ord_ in range(24):
        is_pick, team = resolve_in(SPEC_FAMILY, ord_, first_pick_team)
        if team == 0 and is_pick and not used_target_pick:
            hero_id = picked_hero
            used_target_pick = True
        elif team == 0 and not is_pick:
            hero_id = next(target_bans)
        else:
            hero_id = 200 + ord_
        actions.append({"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": hero_id})
    return actions


def _match(
    match_id: int,
    *,
    team_id: int | None,
    account_id: int | None,
    patch: str,
    base_version: str = "7.41",
    tier: str | None = "tier1",
    position: int | None = 1,
    stats_available: bool = True,
    source: str = "pro_match",
    age_days: int = 0,
    hero_id: int = 1,
    won: bool = True,
    strong: bool = False,
    include_bp: bool = True,
    parse_state: str = "full",
) -> dict:
    radiant_team_id = team_id if team_id is not None else 20
    row = _player(
        account_id,
        team=0,
        hero_id=hero_id,
        position=position,
        name="目标选手" if account_id == 101 else "同侪",
        stats_available=stats_available,
        strong=strong,
        firstblood_claimed=strong,
    )
    return {
        "match_id": match_id,
        "data_source": source,
        "patch": patch,
        "base_version": base_version,
        "started_at": f"{AS_OF - dt.timedelta(days=age_days)}T12:00:00+00:00",
        "tier": tier,
        "game_mode": 2,
        "radiant_team_id": radiant_team_id,
        "dire_team_id": 30,
        "radiant_win": won,
        "draft_state": "complete" if include_bp else "pending",
        "parse_state": parse_state,
        "n_draft_actions": 24 if include_bp else None,
        "anomaly": False,
        "first_pick_team": 0,
        "players": [row, *_anonymous_team(0), *_anonymous_team(1)],
        "draft": _spec_draft(picked_hero=hero_id) if include_bp else [],
    }


def _dataset(*, target_n: int = 30, peer_n: int = 30) -> dict:
    matches = []
    for i in range(target_n):
        hero_id = 2 if i < 6 else 1
        won = i % 2 == 0 if hero_id == 2 else True
        matches.append(
            _match(
                1_000 + i,
                team_id=TEAM_ID,
                account_id=101,
                patch="7.41e",
                age_days=i,
                hero_id=hero_id,
                won=won,
                strong=True,
            )
        )
    for i in range(peer_n):
        matches.append(
            _match(
                2_000 + i,
                team_id=None,
                account_id=201,
                patch="7.41d",
                age_days=i,
                hero_id=3,
                won=i % 2 == 0,
            )
        )
    return {
        "team_id": TEAM_ID,
        "patch": "7.41e",
        "base_version": "7.41",
        "as_of": AS_OF.isoformat(),
        "sources": ["pro_match", "pub_match"],
        "matches": matches,
        "hero_roles": {
            1: ["Initiator", "Durable"],
            2: ["Pusher", "Carry"],
            3: ["Support", "Disabler"],
        },
        "weights": {
            "hero_pool": {"pro_match": 1.0, "pub_match": 1.0},
            "map_vision": {"pro_match": 1.0, "pub_match": 0.25},
            "tempo": {"pro_match": 1.0, "pub_match": 0.25},
            "bp_tendency": {"pro_match": 1.5, "pub_match": 0.0},
        },
        "min_sample_n": 30,
    }


def _validator_for_profile() -> Draft202012Validator:
    root_path = Path(__file__).parents[2] / "contracts" / "openapi.yaml"
    document = yaml.safe_load(root_path.read_text(encoding="utf-8"))
    root = Draft202012Validator(document)
    return root.evolve(schema=document["components"]["schemas"]["Profile"])


def _entry(entries: list[dict], hero_id: int) -> dict:
    return next(entry for entry in entries if entry["hero_id"] == hero_id)


def test_build_profile_obeys_frozen_shape_patch_roster_pool_peers_and_bp() -> None:
    dataset = _dataset()
    dataset["matches"].extend(
        [
            _match(3_001, team_id=TEAM_ID, account_id=999, patch="7.41d", age_days=1),
            _match(3_002, team_id=TEAM_ID, account_id=998, patch="7.41e", age_days=90),
            _match(3_003, team_id=TEAM_ID, account_id=997, patch="7.41e", age_days=-1),
        ]
    )
    invalid_peer_cases = [
        {"position": 2},
        {"base_version": "7.40"},
        {"tier": "tier2"},
        {"age_days": 90},
        {"stats_available": False},
        {"source": "scrim"},
    ]
    for case_no, overrides in enumerate(invalid_peer_cases):
        for i in range(5):
            params = dict(
                match_id=4_000 + case_no * 10 + i,
                team_id=None,
                account_id=300 + case_no,
                patch="7.41d",
                age_days=i,
            )
            params.update(overrides)
            dataset["matches"].append(_match(**params))

    result = build_profile(dataset)

    _validator_for_profile().validate(result)
    assert check_profile(result) == []
    assert result["team_id"] == TEAM_ID
    assert result["patch"] == "7.41e"
    assert result["as_of"] == AS_OF.isoformat()
    assert result["sources_used"] == ["pro_match"]
    assert result["coverage"] == {
        "pro_match": {"n_matches": 30, "n_stat_available": 30, "n_position_unknown": 0},
        "pub_match": {"n_matches": 0, "n_stat_available": 0, "n_position_unknown": 0},
    }

    assert [player["account_id"] for player in result["players"]] == [101]
    player = result["players"][0]
    assert player["role"] == 1
    assert player["hero_pool"] == {
        "window_games": 30,
        "signature": [{"hero_id": 1, "games": 24, "wr": 1.0, "pct": 80}],
        "comfortable": [{"hero_id": 2, "games": 6, "wr": 0.5, "pct": 20}],
        "effective_count": 2,
        "presence_pick_rate": 1.0,
    }
    # 目标分子只用 7.41e；同侪分母按 base_version 纳入额外的 7.41d 行。
    assert player["dimensions"]["laning"] == {"percentile": 75, "n": 61}
    assert all(player["dimensions"][name]["n"] == 61 for name in (
        "hero_pool", "combat", "map_vision", "tempo"
    ))
    archetype = player["dimensions"]["hero_archetype"]
    assert archetype["initiate"] == pytest.approx(4 / 9)
    assert archetype["teamfight"] == pytest.approx(4 / 9)
    assert archetype["push"] == pytest.approx(1 / 9)
    assert archetype["protect"] == archetype["pickoff"] == archetype["splitpush"] == 0

    bp = result["team_bp_tendency"]
    assert _entry(bp["first_phase_ban_freq"], 50) == {"hero_id": 50, "freq": 1.0, "n": 30}
    assert _entry(bp["first_phase_ban_freq"], 51) == {"hero_id": 51, "freq": 1.0, "n": 30}
    assert sum(entry["freq"] for entry in bp["first_phase_ban_freq"]) > 1
    assert _entry(bp["first_pick_freq"], 1) == {"hero_id": 1, "freq": 0.8, "n": 30}
    assert _entry(bp["first_pick_freq"], 2) == {"hero_id": 2, "freq": 0.2, "n": 30}
    assert any(entry["ord"] == 0 and entry["n"] == 30 for entry in bp["ban_by_phase"])


def test_percentile_treats_approximately_equal_values_as_one_bucket() -> None:
    assert _percentile(1.0, [1.0 - 5e-13] * 30) == 50


def test_anonymous_peers_contribute_to_row_level_dimensions() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        if match["radiant_team_id"] != TEAM_ID:
            match["players"][0]["account_id"] = None

    dimensions = build_profile(dataset)["players"][0]["dimensions"]

    assert dimensions["laning"] == {"percentile": 75, "n": 60}
    assert dimensions["combat"]["n"] == 60
    assert dimensions["map_vision"]["n"] == 60
    assert dimensions["hero_pool"]["n"] == 30
    assert dimensions["tempo"]["n"] == 30


def test_unknown_role_degrades_five_dimensions_but_keeps_archetype() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        if match["radiant_team_id"] == TEAM_ID:
            match["players"][0]["position"] = None

    result = build_profile(dataset)
    player = result["players"][0]

    assert player["role"] is None
    degraded = {"value": None, "reason": "insufficient_samples"}
    for name in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        assert player["dimensions"][name] == degraded
    assert sum(player["dimensions"]["hero_archetype"].values()) == pytest.approx(1.0)
    assert result["coverage"]["pro_match"]["n_position_unknown"] == 30
    assert check_profile(result) == []


def test_role_tie_is_unknown_and_anonymous_accounts_never_become_players() -> None:
    dataset = _dataset()
    for i, match in enumerate(dataset["matches"][:30]):
        match["players"][0]["position"] = 4 if i < 15 else 5

    result = build_profile(dataset)

    assert len(result["players"]) == 1
    assert result["players"][0]["account_id"] == 101
    assert result["players"][0]["role"] is None


def test_no_observable_presence_denominator_raises_instead_of_fabricating_zero() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        if match["radiant_team_id"] == TEAM_ID:
            match["draft_state"] = "pending"
            match["n_draft_actions"] = None
            match["draft"] = []

    with pytest.raises(ProfileInsufficientData, match="presence"):
        build_profile(dataset)


def test_presence_uses_only_effective_pool_and_is_not_identically_one() -> None:
    dataset = _dataset(target_n=0, peer_n=30)
    target_matches = []
    for i in range(5):
        in_pool = i < 3
        match = _match(
            5_000 + i,
            team_id=TEAM_ID,
            account_id=101,
            patch="7.41e",
            age_days=i,
            hero_id=3 if in_pool else 1,
            won=in_pool,
            strong=True,
        )
        target_matches.append(match)
    # 第五场只禁掉唯一有效英雄 3，因此该场不是可观测的放出机会。
    opponent_ban = next(
        action
        for action in target_matches[-1]["draft"]
        if not action["is_pick"] and action["team"] == 1
    )
    opponent_ban["hero_id"] = 3
    dataset["matches"].extend(target_matches)

    player = build_profile(dataset)["players"][0]

    assert player["hero_pool"]["signature"] == []
    assert player["hero_pool"]["comfortable"] == [
        {"hero_id": 3, "games": 3, "wr": 1.0, "pct": 60}
    ]
    assert player["hero_pool"]["presence_pick_rate"] == 0.75


def test_empty_effective_pool_raises_instead_of_fabricating_presence() -> None:
    dataset = _dataset(target_n=0, peer_n=30)
    dataset["matches"].extend(
        _match(
            5_100 + i,
            team_id=TEAM_ID,
            account_id=101,
            patch="7.41e",
            age_days=i,
            hero_id=1,
            won=False,
            strong=True,
        )
        for i in range(2)
    )

    with pytest.raises(ProfileInsufficientData, match="有效英雄池"):
        build_profile(dataset)


def test_unparsed_target_stats_degrade_stat_dimensions() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        if match["radiant_team_id"] == TEAM_ID:
            match["players"][0]["stats_available"] = False

    player = build_profile(dataset)["players"][0]

    degraded = {"value": None, "reason": "stat_unavailable"}
    for name in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        assert player["dimensions"][name] == degraded


def test_match_parse_state_must_be_full_even_when_row_claims_stats_available() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        match["parse_state"] = "unparsed"

    dimensions = build_profile(dataset)["players"][0]["dimensions"]

    degraded = {"value": None, "reason": "stat_unavailable"}
    for name in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        assert dimensions[name] == degraded


def test_missing_target_map_fields_are_stat_unavailable_before_sample_checks() -> None:
    dataset = _dataset()
    for match in dataset["matches"]:
        if match["radiant_team_id"] == TEAM_ID:
            for field in (
                "obs_placed",
                "sen_placed",
                "observer_kills",
                "sentry_kills",
                "camps_stacked",
                "rune_pickups",
            ):
                match["players"][0][field] = None

    map_vision = build_profile(dataset)["players"][0]["dimensions"]["map_vision"]

    assert map_vision == {"value": None, "reason": "stat_unavailable"}


def test_sources_used_includes_same_patch_player_history_from_another_team() -> None:
    dataset = _dataset()
    dataset["matches"].extend(
        _match(
            5_200 + i,
            team_id=None,
            account_id=101,
            patch="7.41e",
            source="pub_match",
            age_days=i,
            hero_id=2,
            won=True,
            strong=True,
        )
        for i in range(3)
    )

    result = build_profile(dataset)

    assert result["sources_used"] == ["pro_match", "pub_match"]
    assert result["coverage"]["pub_match"] == {
        "n_matches": 0,
        "n_stat_available": 0,
        "n_position_unknown": 0,
    }


def test_source_weighted_dimensions_require_target_and_peer_threshold() -> None:
    dataset = _dataset(target_n=29, peer_n=30)

    player = build_profile(dataset)["players"][0]

    insufficient = {"value": None, "reason": "insufficient_samples"}
    assert player["dimensions"]["hero_pool"] == insufficient
    assert player["dimensions"]["map_vision"] == insufficient
    assert player["dimensions"]["tempo"] == insufficient
    assert player["dimensions"]["laning"]["n"] == 59


def test_anonymous_tempo_rows_cannot_inflate_identified_peer_weight_budget() -> None:
    dataset = _dataset()
    for metric in ("hero_pool", "map_vision", "tempo"):
        dataset["weights"][metric]["pub_match"] = 0.25
    for match in dataset["matches"]:
        if match["radiant_team_id"] == TEAM_ID:
            match["tier"] = None
        else:
            match["data_source"] = "pub_match"
    dataset["matches"].extend(
        _match(
            6_000 + i,
            team_id=None,
            account_id=None,
            patch="7.41d",
            source="pub_match",
            tier="tier1",
            age_days=i % 30,
        )
        for i in range(90)
    )

    tempo = build_profile(dataset)["players"][0]["dimensions"]["tempo"]

    assert tempo == {"value": None, "reason": "insufficient_samples"}


def test_tempo_uses_latest_twenty_with_seven_game_half_life() -> None:
    dataset = _dataset()
    target = [m for m in dataset["matches"] if m["radiant_team_id"] == TEAM_ID]
    peers = [m for m in dataset["matches"] if m["radiant_team_id"] != TEAM_ID]
    for i, match in enumerate(sorted(target, key=lambda m: m["started_at"], reverse=True)):
        match["radiant_win"] = i < 7
    for i, match in enumerate(sorted(peers, key=lambda m: m["started_at"], reverse=True)):
        match["radiant_win"] = i >= 7

    player = build_profile(dataset)["players"][0]

    assert player["dimensions"]["tempo"]["percentile"] > 50


def test_minimum_sample_floor_cannot_be_lowered_below_thirty() -> None:
    dataset = _dataset(target_n=20, peer_n=20)
    dataset["min_sample_n"] = 1

    player = build_profile(dataset)["players"][0]

    assert player["dimensions"]["hero_pool"] == {
        "value": None,
        "reason": "insufficient_samples",
    }
