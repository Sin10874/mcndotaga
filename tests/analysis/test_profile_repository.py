from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from db.sources import SourceNotAllowed


UTC = timezone.utc


def _seed_reference_data(db) -> None:
    db.execute(
        """INSERT INTO patches(version_name, base_version, released_at)
           VALUES ('7.41d', '7.41', '2026-01-01T00:00:00Z'),
                  ('7.41e', '7.41', '2026-02-01T00:00:00Z'),
                  ('7.40c', '7.40', '2025-12-01T00:00:00Z')"""
    )
    db.execute(
        """INSERT INTO teams(team_id, name, tag)
           VALUES (100, 'Target', 'T'), (200, 'Peer A', 'A'), (300, 'Peer B', 'B')"""
    )
    db.execute(
        """INSERT INTO leagues(league_id, name, tier)
           VALUES (1, 'Canonical', 'tier1'), (2, 'Unknown spelling', 'premium')"""
    )
    db.execute(
        """INSERT INTO heroes(hero_id, name, localized_name, roles)
           VALUES (1, 'npc_dota_hero_one', 'One', ARRAY['Carry', 'Escape']),
                  (2, 'npc_dota_hero_two', 'Two', NULL)"""
    )
    db.execute(
        """INSERT INTO players(account_id, name)
           VALUES (10, 'Alice'), (20, NULL)"""
    )
    db.execute(
        """INSERT INTO metric_weights(metric, data_source, weight)
           VALUES ('hero_pool', 'pro_match', 1.5),
                  ('hero_pool', 'pub_match', 0.25),
                  ('hero_pool', 'scrim', 9),
                  ('map_vision', 'pro_match', 1),
                  ('map_vision', 'pub_match', 0.25),
                  ('tempo', 'pro_match', 1),
                  ('tempo', 'pub_match', 0.25)"""
    )
    db.execute(
        """INSERT INTO app_config_kv(key, value)
           VALUES ('min_sample_n', '12'::jsonb)"""
    )


def _patch_id(db, name: str) -> int:
    return db.execute(
        "SELECT patch_id FROM patches WHERE version_name = %s", (name,)
    ).fetchone()[0]


def _insert_match(
    db,
    match_id: int,
    *,
    source: str,
    started_at: datetime,
    patch: str,
    league_id: int = 1,
    game_mode: int | None = 2,
    radiant_team_id: int | None = 100,
    dire_team_id: int | None = 200,
) -> None:
    db.execute(
        """INSERT INTO matches(
               match_id, data_source, patch_id, started_at, league_id, game_mode,
               radiant_team_id, dire_team_id, draft_state
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'complete')""",
        (
            match_id,
            source,
            _patch_id(db, patch),
            started_at,
            league_id,
            game_mode,
            radiant_team_id,
            dire_team_id,
        ),
    )


def test_loads_base_version_window_and_complete_nested_rows_in_stable_order(db):
    from analysis.profile_repository import load_profile_dataset

    _seed_reference_data(db)
    as_of = datetime.now(UTC).date() - timedelta(days=2)
    lower = datetime.combine(as_of - timedelta(days=89), time.min, UTC)
    upper_last_second = datetime.combine(as_of, time(23, 59, 59), UTC)

    _insert_match(db, 101, source="pro_match", started_at=lower, patch="7.41e")
    _insert_match(
        db,
        102,
        source="pro_match",
        started_at=upper_last_second,
        patch="7.41d",
        league_id=2,
        game_mode=None,
        radiant_team_id=200,
        dire_team_id=300,
    )
    _insert_match(
        db,
        103,
        source="pro_match",
        started_at=lower - timedelta(microseconds=1),
        patch="7.41e",
    )
    _insert_match(
        db,
        104,
        source="pro_match",
        started_at=upper_last_second + timedelta(seconds=1),
        patch="7.41e",
    )
    _insert_match(db, 105, source="pro_match", started_at=lower, patch="7.40c")

    db.execute(
        """INSERT INTO match_players(
               match_id, player_slot, account_id, team, hero_id, position,
               kills, stats_available
           ) VALUES
               (101, 4, 10, 0, 1, 1, 7, true),
               (101, 0, NULL, 0, 2, NULL, NULL, false)"""
    )
    db.execute(
        """INSERT INTO draft_actions(match_id, ord, is_pick, team, hero_id)
           VALUES (101, 3, true, 0, 1), (101, 0, false, 1, 2)"""
    )

    got = load_profile_dataset(
        db, team_id=100, patch="7.41e", as_of=as_of, sources=["pro_match"]
    )

    assert got["team_id"] == 100
    assert got["patch"] == "7.41e"
    assert got["base_version"] == "7.41"
    assert got["as_of"] == as_of.isoformat()
    assert got["sources"] == ["pro_match"]
    assert [row["match_id"] for row in got["matches"]] == [101, 102]
    assert got["matches"][0]["patch"] == "7.41e"
    assert got["matches"][0]["base_version"] == "7.41"
    assert got["matches"][0]["tier"] == "tier1"
    assert got["matches"][1]["patch"] == "7.41d"
    assert got["matches"][1]["tier"] is None
    assert got["matches"][1]["players"] == []
    assert got["matches"][1]["draft"] == []

    players = got["matches"][0]["players"]
    assert [row["player_slot"] for row in players] == [0, 4]
    assert players[0]["account_id"] is None
    assert players[0]["name"] is None
    assert players[1]["name"] == "Alice"
    assert players[1]["kills"] == 7
    assert [row["ord"] for row in got["matches"][0]["draft"]] == [0, 3]
    assert got["hero_roles"] == {1: ["Carry", "Escape"], 2: []}
    assert got["weights"] == {
        "hero_pool": {"pro_match": 1.5},
        "map_vision": {"pro_match": 1.0},
        "tempo": {"pro_match": 1.0},
    }
    assert got["min_sample_n"] == 30


def test_pub_is_optional_non_cm_and_scrim_rows_never_enter_dataset(db):
    from analysis.profile_repository import load_profile_dataset

    _seed_reference_data(db)
    as_of = datetime.now(UTC).date() - timedelta(days=1)
    at = datetime.combine(as_of, time(12), UTC)
    _insert_match(db, 201, source="pro_match", started_at=at, patch="7.41e")
    _insert_match(
        db, 202, source="pro_match", started_at=at, patch="7.41e", game_mode=1
    )
    _insert_match(
        db, 203, source="pub_match", started_at=at, patch="7.41e", game_mode=1
    )
    _insert_match(db, 204, source="scrim", started_at=at, patch="7.41e")

    pro_only = load_profile_dataset(
        db, team_id=100, patch="7.41e", as_of=as_of, sources=["pro_match"]
    )
    with_pub = load_profile_dataset(
        db,
        team_id=100,
        patch="7.41e",
        as_of=as_of,
        sources=["pub_match", "pro_match", "pub_match"],
    )
    pub_only = load_profile_dataset(
        db, team_id=100, patch="7.41e", as_of=as_of, sources=["pub_match"]
    )

    assert [row["match_id"] for row in pro_only["matches"]] == [201]
    assert [row["match_id"] for row in with_pub["matches"]] == [201, 203]
    assert [row["match_id"] for row in pub_only["matches"]] == [203]
    assert with_pub["sources"] == ["pro_match", "pub_match"]
    assert with_pub["weights"]["hero_pool"] == {
        "pro_match": 1.5,
        "pub_match": 0.25,
    }


def test_today_excludes_matches_that_have_not_happened_yet(db):
    from analysis.profile_repository import load_profile_dataset

    _seed_reference_data(db)
    as_of = datetime.now(UTC).date()
    _insert_match(
        db,
        301,
        source="pro_match",
        started_at=datetime.now(UTC) - timedelta(minutes=1),
        patch="7.41e",
    )
    _insert_match(
        db,
        302,
        source="pro_match",
        started_at=datetime.now(UTC) + timedelta(minutes=10),
        patch="7.41e",
    )

    got = load_profile_dataset(
        db, team_id=100, patch="7.41e", as_of=as_of, sources=["pro_match"]
    )

    assert [row["match_id"] for row in got["matches"]] == [301]


@pytest.mark.parametrize(
    ("team_id", "patch"),
    [(999, "7.41e"), (100, "9.99z")],
)
def test_unknown_team_or_patch_raises_profile_not_found(db, team_id, patch):
    from analysis.profile_repository import ProfileNotFound, load_profile_dataset

    _seed_reference_data(db)

    with pytest.raises(ProfileNotFound):
        load_profile_dataset(
            db,
            team_id=team_id,
            patch=patch,
            as_of=datetime.now(UTC).date(),
            sources=["pro_match"],
        )


def test_scrim_source_is_rejected_before_any_database_access():
    from analysis.profile_repository import load_profile_dataset

    with pytest.raises(SourceNotAllowed):
        load_profile_dataset(
            object(),
            team_id=100,
            patch="7.41e",
            as_of=date(2026, 9, 20),
            sources=["pro_match", "scrim"],
        )


def test_future_as_of_and_missing_sample_configuration_are_rejected(db):
    from analysis.profile_repository import load_profile_dataset

    _seed_reference_data(db)
    future = datetime.now(UTC).date() + timedelta(days=1)
    with pytest.raises(ValueError, match="as_of"):
        load_profile_dataset(
            db, team_id=100, patch="7.41e", as_of=future, sources=["pro_match"]
        )

    db.execute("DELETE FROM app_config_kv WHERE key = 'min_sample_n'")
    with pytest.raises(ValueError, match="min_sample_n"):
        load_profile_dataset(
            db,
            team_id=100,
            patch="7.41e",
            as_of=datetime.now(UTC).date(),
            sources=["pro_match"],
        )


def test_as_of_too_early_for_complete_window_is_rejected_as_value_error():
    from analysis.profile_repository import load_profile_dataset

    with pytest.raises(ValueError, match="as_of"):
        load_profile_dataset(
            object(),
            team_id=100,
            patch="7.41e",
            as_of=date.min,
            sources=["pro_match"],
        )


def test_missing_required_weight_configuration_is_rejected(db):
    from analysis.profile_repository import load_profile_dataset

    _seed_reference_data(db)
    db.execute(
        """DELETE FROM metric_weights
           WHERE metric = 'map_vision' AND data_source = 'pub_match'"""
    )

    with pytest.raises(ValueError, match="map_vision.*pub_match"):
        load_profile_dataset(
            db,
            team_id=100,
            patch="7.41e",
            as_of=datetime.now(UTC).date(),
            sources=["pro_match", "pub_match"],
        )


def _provenance_fixture(db):
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from analysis.position_inference import METHOD_VERSION, team_input_fingerprint

    _seed_reference_data(db)
    as_of = datetime.now(UTC).date() - timedelta(days=1)
    _insert_match(db, 901, source="pro_match", started_at=datetime.combine(as_of, time(12), UTC), patch="7.41e", league_id=2)
    db.execute("UPDATE matches SET parse_state = 'full' WHERE match_id = 901")
    for slot, lane, economy in [(0, 1, 500), (1, 2, 400), (2, 3, 300), (3, 3, 200), (4, 1, 100)]:
        db.execute(
            """INSERT INTO match_players(match_id, player_slot, team, hero_id,
                   lane_role, stats_available, last_hits, net_worth, gpm, xpm)
               VALUES (901, %s, 0, 1, %s, true, %s, %s, %s, %s)""",
            (slot, lane, economy, economy * 20, economy, economy),
        )
    with db.cursor(row_factory=dict_row) as cur:
        match = cur.execute("SELECT * FROM matches WHERE match_id = 901").fetchone()
        rows = cur.execute("SELECT * FROM match_players WHERE match_id = 901 ORDER BY player_slot").fetchall()
    annotations = [{"player_slot": n, "position": n + 1, "method_version": METHOD_VERSION,
                    "evidence_level": "core_economy", "reason": "测试证据", "evidence": {}} for n in range(5)]
    db.execute(
        """INSERT INTO profile_position_annotations(match_id, team, input_fingerprint, method_version, annotations)
           VALUES (901, 0, %s, %s, %s)""",
        (team_input_fingerprint(match, rows), METHOD_VERSION, Jsonb(annotations)),
    )
    db.execute(
        """INSERT INTO profile_league_tiers(league_id, verified_name, canonical_tier,
               source_url, method_version, evidence, reviewed_at)
           VALUES (2, 'Unknown spelling', 'qualifier', 'https://example.org/event',
                   'league_tier_review_v1', '{}'::jsonb, now())"""
    )
    return as_of, annotations


def _load_provenance(db, as_of):
    from analysis.profile_repository import load_profile_dataset
    return load_profile_dataset(db, team_id=100, patch="7.41e", as_of=as_of, sources=["pro_match"])["matches"][0]


def test_verified_annotations_fill_positions_and_tier_without_changing_raw_data(db):
    as_of, _ = _provenance_fixture(db)
    match = _load_provenance(db, as_of)
    assert match["tier"] == "qualifier"
    assert [row["position"] for row in match["players"]] == [1, 2, 3, 4, 5]
    assert {row["position_source"] for row in match["players"]} == {"heuristic"}
    assert db.execute("SELECT count(position) FROM match_players WHERE match_id = 901").fetchone()[0] == 0
    assert db.execute("SELECT tier FROM leagues WHERE league_id = 2").fetchone()[0] == "premium"


@pytest.mark.parametrize("mutation", [
    "UPDATE match_players SET gpm = gpm + 1 WHERE match_id = 901 AND player_slot = 4",
    "UPDATE match_players SET hero_id = 2 WHERE match_id = 901 AND player_slot = 4",
    "UPDATE match_players SET account_id = 10 WHERE match_id = 901 AND player_slot = 4",
    "UPDATE match_players SET lane_role = 2 WHERE match_id = 901 AND player_slot = 4",
    "UPDATE match_players SET stats_available = false WHERE match_id = 901 AND player_slot = 4",
    "UPDATE matches SET parse_state = 'header_only' WHERE match_id = 901",
    "DELETE FROM match_players WHERE match_id = 901 AND player_slot = 4",
])
def test_changed_team_input_invalidates_entire_position_annotation(db, mutation):
    as_of, _ = _provenance_fixture(db)
    db.execute(mutation)
    match = _load_provenance(db, as_of)
    assert all(row["position"] is None for row in match["players"])
    assert {row["position_source"] for row in match["players"]} == {"unknown"}


def test_recorded_position_survives_invalidated_inference(db):
    as_of, _ = _provenance_fixture(db)
    db.execute("UPDATE match_players SET position = 3 WHERE match_id = 901 AND player_slot = 0")
    players = _load_provenance(db, as_of)["players"]
    assert players[0]["position"] == 3
    assert players[0]["position_source"] == "recorded"
    assert all(row["position"] is None for row in players[1:])


@pytest.mark.parametrize("malformation", ["missing_slot", "duplicate_slot", "duplicate_position", "bool_position", "invalid_position", "unknown_method", "wrong_fingerprint", "wrong_slot", "wrong_item_method", "not_dict"])
def test_malformed_or_stale_annotation_is_ignored(db, malformation):
    from psycopg.types.json import Jsonb
    as_of, annotations = _provenance_fixture(db)
    if malformation == "missing_slot":
        annotations.pop()
    elif malformation == "duplicate_slot":
        annotations[-1]["player_slot"] = 0
    elif malformation == "duplicate_position":
        annotations[-1]["position"] = 1
    elif malformation == "bool_position":
        annotations[-1]["position"] = True
    elif malformation == "invalid_position":
        annotations[-1]["position"] = 6
    elif malformation == "unknown_method":
        db.execute("UPDATE profile_position_annotations SET method_version = 'old_method'")
    elif malformation == "wrong_fingerprint":
        db.execute("UPDATE profile_position_annotations SET input_fingerprint = 'stale'")
    elif malformation == "wrong_slot":
        annotations[-1]["player_slot"] = 128
    elif malformation == "wrong_item_method":
        annotations[-1]["method_version"] = "old_method"
    elif malformation == "not_dict":
        annotations[-1] = None
    db.execute("UPDATE profile_position_annotations SET annotations = %s", (Jsonb(annotations),))
    players = _load_provenance(db, as_of)["players"]
    assert all(row["position"] is None for row in players)
    assert {row["position_source"] for row in players} == {"unknown"}


def test_name_change_invalidates_tier_mapping_and_canonical_raw_tier_has_priority(db):
    as_of, _ = _provenance_fixture(db)
    db.execute("UPDATE leagues SET name = 'Different event' WHERE league_id = 2")
    assert _load_provenance(db, as_of)["tier"] is None
    db.execute("UPDATE leagues SET name = ' Unknown spelling ', tier = 'tier2' WHERE league_id = 2")
    assert _load_provenance(db, as_of)["tier"] == "tier2"
    db.execute("UPDATE leagues SET tier = 'professional' WHERE league_id = 2")
    assert _load_provenance(db, as_of)["tier"] == "qualifier"
