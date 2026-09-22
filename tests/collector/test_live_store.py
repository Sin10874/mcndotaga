from __future__ import annotations

import pytest

from ingest.order_families import DRAFT_ORDERS, resolve_in


def _seed_heroes(db):
    ids = [*range(1, 24), 25, 67]
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO heroes(hero_id,name,localized_name) VALUES (%s,%s,%s)",
            [(hero_id, f"hero_{hero_id}", f"Hero {hero_id}") for hero_id in ids],
        )


def _summary(match_id=8996973546):
    return {
        "match_id": match_id,
        "start_time": 1_790_000_000,
        "duration": 2400,
        "radiant_team_id": 10251056,
        "radiant_name": "Dawn Bulls",
        "dire_team_id": 10232231,
        "dire_name": "Klim Sani4",
        "leagueid": 18123,
        "league_name": "RES Unchained",
        "series_id": 1141522,
        "series_type": 1,
        "radiant_win": True,
    }


def _detail(match_id=8996973546):
    heroes = [*range(1, 24), 25]
    actions = []
    for order, hero_id in enumerate(heroes):
        is_pick, team = resolve_in("spec_6_0_24", order, 0)
        actions.append({"order": order, "is_pick": is_pick, "team": team, "hero_id": hero_id})
    players = []
    for raw_slot in [0, 1, 2, 3, 4, 128, 129, 130, 131, 132]:
        slot = raw_slot if raw_slot < 128 else raw_slot - 123
        players.append({
            "player_slot": raw_slot,
            "account_id": None if slot in (2, 8) else 1000 + slot,
            "name": None if slot in (2, 8) else f"P{slot}",
            "hero_id": 67,
            "kills": slot,
            "deaths": 1,
            "assists": 2,
            "gold_per_min": 500,
            "xp_per_min": 600,
            "damage_taken": {"hero": 100 + slot, "tower": 50},
            "net_worth": 20_000,
            "obs_placed": 0,
            "lane_role": 1,
            "firstblood_claimed": slot == 0,
        })
    return {
        **_summary(match_id),
        "game_mode": 2,
        "version": 22,
        "lobby_type": 1,
        "first_blood_time": 35,
        "picks_bans": actions,
        "players": players,
        "objectives": [
            {"type": "building_kill", "key": "npc_dota_goodguys_melee_rax_top", "time": 400},
            {"type": "building_kill", "key": "npc_dota_goodguys_tower1_bot", "time": 826},
            {"type": "CHAT_MESSAGE_ROSHAN_KILL", "time": 1200},
        ],
    }


def test_complete_detail_is_idempotent_keeps_anonymous_slots_and_uses_legal_family(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    for _ in range(2):
        upsert_public_match(db, _summary(), _detail())

    row = db.execute(
        "SELECT data_source,draft_state,n_draft_actions,anomaly,parse_state,first_tower_time,"
        "first_roshan_time,game_mode FROM matches WHERE match_id=8996973546"
    ).fetchone()
    assert row == ("pro_match", "complete", 24, False, "full", 826, 1200, 2)
    assert db.execute("SELECT count(*) FROM draft_actions WHERE match_id=8996973546").fetchone()[0] == 24
    assert db.execute("SELECT count(*) FROM match_players WHERE match_id=8996973546").fetchone()[0] == 10
    assert db.execute(
        "SELECT player_slot,team,damage_taken_total,position,stats_available "
        "FROM match_players WHERE match_id=8996973546 AND player_slot IN (0,8) ORDER BY player_slot"
    ).fetchall() == [(0, 0, 150, None, True), (8, 1, 158, None, True)]
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=8996973546 AND account_id IS NULL"
    ).fetchone()[0] == 2


def test_public_write_refuses_same_match_id_owned_by_scrim(db):
    from ingest.live_store import SourceConflict, upsert_public_match

    _seed_heroes(db)
    db.execute(
        "INSERT INTO matches(match_id,data_source,started_at) VALUES (8996973546,'scrim',now())"
    )
    with pytest.raises(SourceConflict):
        upsert_public_match(db, _summary(), _detail())
    assert db.execute(
        "SELECT data_source FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == "scrim"


def test_parsed_detection_does_not_depend_on_gpm_and_non_cm_is_explicit(db):
    from ingest.live_store import parsed_player_stats, upsert_public_match

    assert parsed_player_stats({"gold_per_min": 600}) is False
    assert parsed_player_stats({"gold_per_min": 600, "net_worth": 20_000}) is False
    assert parsed_player_stats({"gold_per_min": 600, "damage_taken": {}}) is True

    _seed_heroes(db)
    detail = _detail(9010442404)
    detail.update({"game_mode": 1, "picks_bans": None})
    upsert_public_match(db, _summary(9010442404), detail)
    assert db.execute(
        "SELECT game_mode,draft_state,parse_state FROM matches WHERE match_id=9010442404"
    ).fetchone() == (1, "unavailable", "full")


def test_real_nested_detail_upserts_team_names_tags_and_league_without_inventing_tier(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    detail = _detail()
    for key in (
        "radiant_team_id", "radiant_name", "dire_team_id", "dire_name", "leagueid", "league_name"
    ):
        detail.pop(key, None)
    detail["radiant_team"] = {"team_id": 10251056, "name": "Dawn Bulls", "tag": "DB"}
    detail["dire_team"] = {"team_id": 10232231, "name": "Klim Sani4", "tag": "KS"}
    detail["league"] = {"leagueid": 18123, "name": "RES Unchained", "tier": "professional"}
    upsert_public_match(db, detail, detail)
    assert db.execute(
        "SELECT team_id,name,tag FROM teams ORDER BY team_id"
    ).fetchall() == [
        (10232231, "Klim Sani4", "KS"),
        (10251056, "Dawn Bulls", "DB"),
    ]
    assert db.execute(
        "SELECT league_id,name,tier FROM leagues WHERE league_id=18123"
    ).fetchone() == (18123, "RES Unchained", None)
    assert db.execute(
        "SELECT radiant_team_id,dire_team_id,league_id FROM matches WHERE match_id=8996973546"
    ).fetchone() == (10251056, 10232231, 18123)


def test_firstblood_integer_is_written_as_boolean(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    detail = _detail()
    detail["players"][0]["firstblood_claimed"] = 1
    detail["players"][1]["firstblood_claimed"] = 0
    upsert_public_match(db, _summary(), detail)
    assert db.execute(
        "SELECT player_slot,firstblood_claimed FROM match_players "
        "WHERE match_id=8996973546 AND player_slot IN (0,1) ORDER BY player_slot"
    ).fetchall() == [(0, True), (1, False)]


def test_parse_state_and_stats_only_upgrade_and_never_regress(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    header_players = [
        {"player_slot": p["player_slot"], "account_id": p["account_id"], "hero_id": p["hero_id"],
         "gold_per_min": p["gold_per_min"], "net_worth": p["net_worth"]}
        for p in full["players"]
    ]
    header = {**full, "players": header_players}
    upsert_public_match(db, _summary(), header)
    assert db.execute(
        "SELECT parse_state FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == "header_only"

    no_bp_full_stats = {**full, "picks_bans": None}
    upsert_public_match(db, _summary(), no_bp_full_stats)
    assert db.execute(
        "SELECT draft_state,parse_state FROM matches WHERE match_id=8996973546"
    ).fetchone() == ("complete", "full")
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=8996973546 AND stats_available"
    ).fetchone()[0] == 10

    upsert_public_match(db, _summary(), header)
    assert db.execute(
        "SELECT parse_state FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == "full"
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=8996973546 AND stats_available"
    ).fetchone()[0] == 10


def test_sparse_parsed_markers_cannot_replace_an_existing_full_player_set(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    upsert_public_match(db, _summary(), full)
    before = db.execute(
        "SELECT count(*) FILTER (WHERE stats_available),sum(damage_taken_total) "
        "FROM match_players WHERE match_id=8996973546"
    ).fetchone()
    assert before == (10, 1545)

    sparse = []
    for player in full["players"]:
        row = {
            "player_slot": player["player_slot"],
            "account_id": player["account_id"],
            "hero_id": player["hero_id"],
            "gold_per_min": player["gold_per_min"],
            "net_worth": player["net_worth"],
        }
        row["obs_placed"] = 0
        sparse.append(row)
    upsert_public_match(db, _summary(), {**full, "players": sparse})

    assert db.execute(
        "SELECT parse_state FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == "full"
    assert db.execute(
        "SELECT count(*) FILTER (WHERE stats_available),sum(damage_taken_total) "
        "FROM match_players WHERE match_id=8996973546"
    ).fetchone() == before
    assert db.execute(
        "SELECT hero_id FROM match_players WHERE match_id=8996973546 AND player_slot=0"
    ).fetchone()[0] == 67


def test_full_player_refresh_rejects_slot_identity_changes_instead_of_splicing_stats(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    upsert_public_match(db, _summary(), full)
    changed = _detail()
    changed["players"][0]["hero_id"] = 1
    with pytest.raises(ValueError, match="身份变化"):
        upsert_public_match(db, _summary(), changed)
    assert db.execute(
        "SELECT hero_id,damage_taken_total,stats_available FROM match_players "
        "WHERE match_id=8996973546 AND player_slot=0"
    ).fetchone() == (67, 150, True)


def test_missing_fields_cannot_hide_later_slot_identity_conflict_or_commit_metadata(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    upsert_public_match(db, _summary(), full)
    sparse = []
    for player in full["players"]:
        sparse.append({
            "player_slot": player["player_slot"],
            "account_id": player["account_id"],
            "hero_id": 1 if player["player_slot"] == 1 else player["hero_id"],
            "gold_per_min": player["gold_per_min"],
            "net_worth": player["net_worth"],
            "obs_placed": 0,
        })
    changed = {**full, "duration": 999, "players": sparse}
    with pytest.raises(ValueError, match="slot=1.*身份变化"):
        upsert_public_match(db, _summary(), changed)
    assert db.execute(
        "SELECT duration_s FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == 2400
    assert db.execute(
        "SELECT hero_id,damage_taken_total FROM match_players "
        "WHERE match_id=8996973546 AND player_slot=1"
    ).fetchone() == (67, 151)


def test_missing_slot_cannot_hide_identity_conflict_or_commit_metadata(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    upsert_public_match(db, _summary(), full)
    nine_players = [dict(player) for player in full["players"][:-1]]
    nine_players[1]["hero_id"] = 1
    changed = {**full, "duration": 999, "players": nine_players}
    with pytest.raises(ValueError, match="slot=1.*身份变化"):
        upsert_public_match(db, _summary(), changed)
    assert db.execute(
        "SELECT duration_s FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == 2400
    assert db.execute(
        "SELECT count(*),min(hero_id),max(hero_id) FROM match_players "
        "WHERE match_id=8996973546"
    ).fetchone() == (10, 67, 67)


def test_duplicate_normalized_slot_is_rejected_before_it_can_hide_a_missing_slot(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    full = _detail()
    upsert_public_match(db, _summary(), full)
    duplicate = [dict(player) for player in full["players"][:-1]]
    duplicate.append(dict(full["players"][0]))
    changed = {**full, "duration": 999, "players": duplicate}
    with pytest.raises(ValueError, match="重复 player_slot=0"):
        upsert_public_match(db, _summary(), changed)
    assert db.execute(
        "SELECT duration_s FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == 2400
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=8996973546"
    ).fetchone()[0] == 10


def test_nonempty_all_invalid_draft_is_complete_anomalous_and_does_not_retry(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    match_id = 777
    summary = _summary(match_id)
    detail = {
        **summary,
        "game_mode": 2,
        "picks_bans": [
            {"order": 25, "is_pick": False, "team": 0, "hero_id": 1}
        ],
        "players": [],
    }
    assert upsert_public_match(db, summary, detail) == "complete"
    assert db.execute(
        "SELECT draft_state,n_draft_actions,anomaly FROM matches WHERE match_id=%s",
        (match_id,),
    ).fetchone() == ("complete", 0, True)
    assert db.execute(
        "SELECT n_actions,kinds,detail->>'ord_out_of_range' "
        "FROM draft_anomalies WHERE match_id=%s",
        (match_id,),
    ).fetchone() == (0, ["ord_out_of_range"], "1")
    assert db.execute(
        "SELECT count(*) FROM draft_actions WHERE match_id=%s", (match_id,)
    ).fetchone()[0] == 0


def test_all_invalid_draft_response_does_not_erase_existing_valid_bp(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    upsert_public_match(db, _summary(), _detail())
    bad = {
        **_summary(),
        "game_mode": 2,
        "picks_bans": [
            {"order": 25, "is_pick": False, "team": 0, "hero_id": 1}
        ],
        "players": [],
    }
    assert upsert_public_match(db, _summary(), bad) == "complete"
    assert db.execute(
        "SELECT draft_state,n_draft_actions,anomaly FROM matches WHERE match_id=8996973546"
    ).fetchone() == ("complete", 24, False)
    assert db.execute(
        "SELECT count(*) FROM draft_actions WHERE match_id=8996973546"
    ).fetchone()[0] == 24

def test_missing_bp_response_never_erases_an_existing_complete_draft(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    upsert_public_match(db, _summary(), _detail())
    shallow = {**_summary(), "game_mode": 2, "picks_bans": None, "players": []}
    assert upsert_public_match(db, _summary(), shallow) == "complete"
    assert db.execute(
        "SELECT draft_state,n_draft_actions,parse_state FROM matches WHERE match_id=8996973546"
    ).fetchone() == ("complete", 24, "full")
    assert db.execute(
        "SELECT count(*) FROM draft_actions WHERE match_id=8996973546"
    ).fetchone()[0] == 24


def test_detail_write_is_transactional_when_an_action_references_unknown_hero(db):
    from ingest.live_store import upsert_public_match

    _seed_heroes(db)
    detail = _detail()
    detail["picks_bans"][0]["hero_id"] = 999
    with pytest.raises(Exception):
        upsert_public_match(db, _summary(), detail)
    assert db.execute(
        "SELECT count(*) FROM matches WHERE match_id=8996973546"
    ).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM teams").fetchone()[0] == 0
