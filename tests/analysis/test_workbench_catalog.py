from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json

import pytest


UTC = timezone.utc


def _seed_reference_data(db) -> None:
    db.execute(
        """INSERT INTO patches(patch_id, version_name, base_version, released_at)
           VALUES (11, '7.41d', '7.41', '2026-01-01T00:00:00Z'),
                  (12, '7.41e', '7.41', '2026-02-01T00:00:00Z')"""
    )
    db.execute(
        """INSERT INTO teams(team_id, name, tag)
           VALUES (10, 'Alpha', 'A'), (20, 'Bravo', 'B'),
                  (30, 'Charlie', NULL), (40, '训练赛私密队', 'S'),
                  (50, '未来赛队伍', 'F')"""
    )
    db.execute(
        """INSERT INTO leagues(league_id, name, tier)
           VALUES (1, 'Reviewed Event', 'premium'),
                  (2, 'Renamed Event', 'premium')"""
    )
    db.execute(
        """INSERT INTO heroes(hero_id, name, localized_name)
           VALUES (1, 'npc_dota_hero_antimage', 'Anti-Mage'),
                  (2, 'npc_dota_hero_axe', 'Axe')"""
    )
    db.execute(
        """INSERT INTO profile_league_tiers(
               league_id, verified_name, canonical_tier, source_url,
               method_version, evidence, reviewed_at
           ) VALUES
               (1, 'Reviewed Event', 'tier1', 'https://example.org/reviewed',
                'league_tier_review_v1', '{}'::jsonb, '2026-01-10T08:00:00Z'),
               (2, 'Old Event Name', 'tier2', 'https://example.org/stale',
                'league_tier_review_v1', '{}'::jsonb, '2026-01-11T08:00:00Z')"""
    )


def _insert_match(
    db,
    match_id: int,
    *,
    source: str,
    started_at: datetime,
    patch_id: int = 12,
    league_id: int = 1,
    game_mode: int | None = 2,
    radiant_team_id: int = 10,
    dire_team_id: int = 20,
) -> None:
    db.execute(
        """INSERT INTO matches(
               match_id, data_source, patch_id, started_at, league_id, game_mode,
               radiant_team_id, dire_team_id, draft_state
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'complete')""",
        (
            match_id,
            source,
            patch_id,
            started_at,
            league_id,
            game_mode,
            radiant_team_id,
            dire_team_id,
        ),
    )


def test_empty_catalog_uses_utc_today_without_inventing_defaults(db):
    from analysis.workbench_catalog import build_catalog_from_connection

    before = datetime.now(UTC)
    got = build_catalog_from_connection(db)
    after = datetime.now(UTC)

    generated_at = datetime.fromisoformat(got["generated_at"])
    assert before <= generated_at <= after
    assert generated_at.tzinfo == UTC
    assert got["window"] == {
        "as_of": before.date().isoformat(),
        "from": (before.date() - timedelta(days=89)).isoformat(),
    }
    assert got["defaults"] == {"team_id": None, "patch": None, "as_of": before.date().isoformat()}
    assert got["teams"] == []
    assert got["patches"] == []
    assert got["heroes"] == {}
    assert got["summary"] == {
        "public_cm_matches": 0,
        "detailed_matches": 0,
        "team_count": 0,
        "latest_match_at": None,
    }
    assert got["provenance"]["leagues"] == []


def test_catalog_isolates_public_pro_cm_and_builds_windowed_defaults(db):
    from analysis.workbench_catalog import build_catalog_from_connection

    _seed_reference_data(db)
    as_of = date(2026, 8, 15)
    latest = datetime.combine(as_of, time(20, 30), UTC)
    lower = datetime.combine(as_of - timedelta(days=89), time.min, UTC)

    _insert_match(db, 101, source="pro_match", started_at=latest)
    _insert_match(
        db,
        102,
        source="pro_match",
        started_at=latest - timedelta(days=1),
        league_id=2,
        game_mode=None,
        radiant_team_id=20,
        dire_team_id=30,
    )
    _insert_match(db, 103, source="pro_match", started_at=lower, patch_id=11)

    _insert_match(db, 201, source="pro_match", started_at=lower - timedelta(microseconds=1))
    _insert_match(db, 202, source="pub_match", started_at=latest, radiant_team_id=40, dire_team_id=50)
    _insert_match(db, 203, source="scrim", started_at=latest, radiant_team_id=40, dire_team_id=50)
    _insert_match(db, 204, source="pro_match", started_at=latest, game_mode=1, radiant_team_id=40, dire_team_id=50)
    _insert_match(
        db,
        205,
        source="pro_match",
        started_at=datetime.now(UTC) + timedelta(days=2),
        radiant_team_id=40,
        dire_team_id=50,
    )

    db.execute(
        """INSERT INTO match_players(match_id, player_slot, team, hero_id)
           VALUES (101, 0, 0, 1), (101, 1, 0, 2), (101, 5, 1, 1),
                  (102, 0, 0, 1)"""
    )

    got = build_catalog_from_connection(db)

    assert got["window"] == {"as_of": "2026-08-15", "from": "2026-05-18"}
    assert got["defaults"] == {"team_id": 20, "patch": "7.41e", "as_of": "2026-08-15"}
    assert got["summary"] == {
        "public_cm_matches": 3,
        "detailed_matches": 2,
        "team_count": 3,
        "latest_match_at": latest.isoformat(),
    }

    teams = {row["team_id"]: row for row in got["teams"]}
    assert set(teams) == {10, 20, 30}
    assert teams[10]["n_matches"] == 2
    assert teams[10]["n_detailed_matches"] == 1
    assert teams[10]["patches"] == ["7.41e", "7.41d"]
    assert teams[20]["n_matches"] == 3
    assert teams[20]["n_detailed_matches"] == 2
    assert teams[20]["patches"] == ["7.41e", "7.41d"]
    assert teams[30]["patches"] == ["7.41e"]
    assert "训练赛私密队" not in json.dumps(got, ensure_ascii=False)
    assert "未来赛队伍" not in json.dumps(got, ensure_ascii=False)
    assert "scrim" not in got

    patches = {row["patch"]: row for row in got["patches"]}
    assert patches["7.41e"]["n_matches"] == 2
    assert patches["7.41e"]["n_detailed_matches"] == 2
    assert patches["7.41d"]["n_matches"] == 1
    assert patches["7.41d"]["n_detailed_matches"] == 0
    assert got["heroes"] == {
        "1": {"name": "npc_dota_hero_antimage", "localized_name": "Anti-Mage"},
        "2": {"name": "npc_dota_hero_axe", "localized_name": "Axe"},
    }


def test_detail_counts_are_per_match_even_with_multiple_player_rows(db):
    from analysis.workbench_catalog import build_catalog_from_connection

    _seed_reference_data(db)
    occurred_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    _insert_match(db, 301, source="pro_match", started_at=occurred_at)
    db.execute(
        """INSERT INTO match_players(match_id, player_slot, team, hero_id)
           VALUES (301, 0, 0, 1), (301, 1, 0, 2),
                  (301, 5, 1, 1), (301, 6, 1, 2)"""
    )

    got = build_catalog_from_connection(db)

    assert got["summary"]["detailed_matches"] == 1
    assert got["patches"][0]["n_detailed_matches"] == 1
    assert {row["n_detailed_matches"] for row in got["teams"]} == {1}


def test_default_patch_and_team_break_equal_detail_counts_by_numeric_id(db):
    from analysis.workbench_catalog import build_catalog_from_connection

    _seed_reference_data(db)
    occurred_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    _insert_match(db, 311, source="pro_match", started_at=occurred_at, patch_id=11)
    _insert_match(db, 312, source="pro_match", started_at=occurred_at, patch_id=12)
    _insert_match(db, 313, source="pro_match", started_at=occurred_at, patch_id=12)
    db.execute(
        """INSERT INTO match_players(match_id, player_slot, team, hero_id)
           VALUES (311, 0, 0, 1), (312, 0, 0, 1)"""
    )

    got = build_catalog_from_connection(db)

    assert got["defaults"] == {
        "team_id": 10,
        "patch": "7.41d",
        "as_of": "2026-08-15",
    }


def test_provenance_only_returns_windowed_reviews_whose_name_still_matches(db):
    from analysis.workbench_catalog import build_catalog_from_connection

    _seed_reference_data(db)
    occurred_at = datetime(2026, 8, 15, 12, tzinfo=UTC)
    _insert_match(db, 401, source="pro_match", started_at=occurred_at, league_id=1)
    _insert_match(db, 402, source="pro_match", started_at=occurred_at, league_id=2)

    got = build_catalog_from_connection(db)

    assert got["provenance"] == {
        "position_method": "team_lane_economy_v1",
        "position_notice": "赛后规则推断，不是真值或同场赛前特征。",
        "tier_notice": "逐赛事证据分类，未审赛事保留未知。",
        "leagues": [
            {
                "league_id": 1,
                "name": "Reviewed Event",
                "tier": "tier1",
                "source_url": "https://example.org/reviewed",
                "reviewed_at": "2026-01-10T08:00:00+00:00",
            }
        ],
    }


def test_load_catalog_uses_read_only_repeatable_read_with_timeout(dsn, monkeypatch):
    import analysis.workbench_catalog as catalog_module

    observed = {}

    def inspect_transaction(conn):
        observed["settings"] = conn.execute(
            """SELECT current_setting('transaction_isolation'),
                      current_setting('transaction_read_only'),
                      current_setting('statement_timeout')"""
        ).fetchone()
        return {"ok": True}

    monkeypatch.setattr(catalog_module, "build_catalog_from_connection", inspect_transaction)

    assert catalog_module.load_catalog(dsn) == {"ok": True}
    assert observed["settings"] == ("repeatable read", "on", "15s")


def test_load_catalog_converts_database_failure_without_leaking_connection_details():
    from analysis.profile_service import ProfileServiceError
    from analysis.workbench_catalog import load_catalog

    dsn = "postgresql://private_user:private_password@127.0.0.1:1/secret_catalog"
    with pytest.raises(ProfileServiceError) as caught:
        load_catalog(dsn)

    assert caught.value.code == "upstream_unavailable"
    assert caught.value.message == "目录数据库暂时不可用"
    assert "private" not in str(caught.value)
    assert "secret_catalog" not in str(caught.value)
