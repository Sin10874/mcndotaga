from __future__ import annotations

from datetime import date
import json

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb


MATCH_ID = 91001
LEAGUE_ID = 92001
METHOD_VERSION = "team_lane_economy_v1"


def _seed_public_match(db, *, source: str = "pro_match", league_name: str = "Official Event"):
    db.execute(
        "INSERT INTO heroes(hero_id, name, localized_name) VALUES (1, 'hero_one', 'One')"
    )
    db.execute(
        "INSERT INTO leagues(league_id, name, tier) VALUES (%s, %s, 'premium')",
        (LEAGUE_ID, league_name),
    )
    db.execute(
        """INSERT INTO matches(
               match_id, data_source, started_at, league_id, parse_state, draft_state
           ) VALUES (%s, %s, '2026-09-20T12:00:00Z', %s, 'full', 'complete')""",
        (MATCH_ID, source, LEAGUE_ID),
    )
    with db.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO match_players(
                   match_id, player_slot, team, hero_id, position, gpm, xpm,
                   last_hits, net_worth, lane_role, stats_available
               ) VALUES (%s, %s, 0, 1, %s, %s, %s, %s, %s, %s, true)""",
            [
                (MATCH_ID, slot, 3 if slot == 0 else None, 600 - slot, 650 - slot,
                 200 - slot, 12000 - slot,
                 1 if slot < 2 else (2 if slot == 2 else 3))
                for slot in range(5)
            ],
        )


def _tier_config(*, league_id: int = LEAGUE_ID, name: str = "Official Event"):
    return {
        "schema_version": 1,
        "method_version": "official_event_list_v1",
        "reviewed_at": "2026-09-22T08:00:00Z",
        "classification_note": "仅按官方赛事列表逐项核验",
        "mappings": [
            {
                "league_id": league_id,
                "verified_name": name,
                "canonical_tier": "tier1",
                "source_url": "https://example.com/events/official",
                "evidence": {"publisher": "official"},
            }
        ],
    }


def _stub_inference(monkeypatch):
    import analysis.profile_annotations as subject

    def fingerprint(match, rows):
        return f"fingerprint:{match['match_id']}:{rows[0]['team']}"

    def infer(match, rows):
        return [
            {
                "player_slot": row["player_slot"],
                "position": None,
                "reason": "测试推断",
                "method_version": METHOD_VERSION,
                "evidence_level": "unknown",
                "evidence": {"lane_role": row["lane_role"]},
            }
            for row in rows
        ]

    monkeypatch.setattr(subject, "team_input_fingerprint", fingerprint)
    monkeypatch.setattr(subject, "infer_team_positions", infer)
    monkeypatch.setattr(subject, "POSITION_METHOD_VERSION", METHOD_VERSION)
    return subject


def test_profile_annotation_tables_keep_provenance_outside_raw_fields(db):
    columns = db.execute(
        """SELECT table_name, column_name
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name IN (
                 'profile_position_annotations',
                 'profile_league_tiers'
             )
           ORDER BY table_name, ordinal_position"""
    ).fetchall()

    by_table: dict[str, list[str]] = {}
    for table_name, column_name in columns:
        by_table.setdefault(table_name, []).append(column_name)

    assert by_table == {
        "profile_league_tiers": [
            "league_id",
            "verified_name",
            "canonical_tier",
            "source_url",
            "method_version",
            "evidence",
            "reviewed_at",
        ],
        "profile_position_annotations": [
            "match_id",
            "team",
            "input_fingerprint",
            "method_version",
            "annotations",
            "created_at",
        ],
    }

    position_foreign_keys = db.execute(
        """SELECT ccu.table_name
           FROM information_schema.table_constraints AS tc
           JOIN information_schema.constraint_column_usage AS ccu
             ON ccu.constraint_name = tc.constraint_name
            AND ccu.constraint_schema = tc.constraint_schema
           WHERE tc.table_schema = 'public'
             AND tc.table_name = 'profile_position_annotations'
             AND tc.constraint_type = 'FOREIGN KEY'"""
    ).fetchall()
    assert position_foreign_keys == [("matches",)]


def test_dry_run_reports_all_changes_without_writing(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)

    summary = subject.apply_profile_annotations(
        db,
        tier_config=_tier_config(),
    )

    assert summary == {
        "dry_run": True,
        "sources": ["pro_match"],
        "since": None,
        "position_groups_scanned": 1,
        "position_annotations_planned": 1,
        "position_annotations_written": 0,
        "position_annotations_unchanged": 0,
        "league_tiers_planned": 1,
        "league_tiers_written": 0,
        "league_tiers_unchanged": 0,
    }
    assert db.execute("SELECT count(*) FROM profile_position_annotations").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM profile_league_tiers").fetchone() == (0,)


def test_apply_writes_side_tables_and_never_changes_raw_source_fields(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)

    summary = subject.apply_profile_annotations(
        db,
        tier_config=_tier_config(),
        dry_run=False,
    )

    assert summary["position_annotations_written"] == 1
    assert summary["league_tiers_written"] == 1
    stored_position = db.execute(
        """SELECT input_fingerprint, method_version, annotations
           FROM profile_position_annotations WHERE match_id = %s AND team = 0""",
        (MATCH_ID,),
    ).fetchone()
    assert stored_position[0] == f"fingerprint:{MATCH_ID}:0"
    assert stored_position[1] == METHOD_VERSION
    assert [row["player_slot"] for row in stored_position[2]] == [0, 1, 2, 3, 4]
    assert db.execute(
        """SELECT verified_name, canonical_tier, evidence
           FROM profile_league_tiers WHERE league_id = %s""",
        (LEAGUE_ID,),
    ).fetchone() == (
        "Official Event",
        "tier1",
        {
            "publisher": "official",
            "classification_note": "仅按官方赛事列表逐项核验",
        },
    )
    assert db.execute(
        "SELECT position FROM match_players WHERE match_id = %s ORDER BY player_slot",
        (MATCH_ID,),
    ).fetchall() == [(3,), (None,), (None,), (None,), (None,)]
    assert db.execute(
        "SELECT tier FROM leagues WHERE league_id = %s", (LEAGUE_ID,)
    ).fetchone() == ("premium",)


def test_identical_apply_is_idempotent_and_does_not_refresh_created_at(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)
    subject.apply_profile_annotations(
        db, tier_config=_tier_config(), dry_run=False
    )
    created_at = db.execute(
        "SELECT created_at FROM profile_position_annotations WHERE match_id = %s AND team = 0",
        (MATCH_ID,),
    ).fetchone()[0]
    db.execute("SELECT pg_sleep(0.02)")

    summary = subject.apply_profile_annotations(
        db, tier_config=_tier_config(), dry_run=False
    )

    assert summary["position_annotations_planned"] == 0
    assert summary["position_annotations_written"] == 0
    assert summary["position_annotations_unchanged"] == 1
    assert summary["league_tiers_planned"] == 0
    assert summary["league_tiers_written"] == 0
    assert summary["league_tiers_unchanged"] == 1
    assert db.execute(
        "SELECT created_at FROM profile_position_annotations WHERE match_id = %s AND team = 0",
        (MATCH_ID,),
    ).fetchone()[0] == created_at


def test_caller_owns_transaction_and_can_roll_back_apply(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)

    subject.apply_profile_annotations(
        db, tier_config=_tier_config(), dry_run=False
    )

    assert db.info.transaction_status == TransactionStatus.INTRANS
    db.rollback()
    assert db.execute("SELECT count(*) FROM profile_position_annotations").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM profile_league_tiers").fetchone() == (0,)


def test_scrim_is_rejected_before_any_annotation_write(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db, source="scrim")

    with pytest.raises(ValueError, match="scrim"):
        subject.apply_profile_annotations(
            db,
            sources=("scrim",),
            tier_config=_tier_config(),
            dry_run=False,
        )

    assert db.execute("SELECT count(*) FROM profile_position_annotations").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM profile_league_tiers").fetchone() == (0,)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("duplicate", "重复 league_id"),
        ("unknown_id", "未知 league_id"),
        ("name_mismatch", "赛事名称不一致"),
        ("tier", "canonical_tier"),
        ("url", "HTTPS"),
        ("reviewed_at", "reviewed_at"),
        ("method_version", "method_version"),
    ],
)
def test_invalid_tier_config_fails_before_any_partial_write(
    db, monkeypatch, case, expected
):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)
    config = _tier_config()
    if case == "duplicate":
        config["mappings"].append(dict(config["mappings"][0]))
    elif case == "unknown_id":
        config["mappings"][0]["league_id"] = LEAGUE_ID + 1
    elif case == "name_mismatch":
        config["mappings"][0]["verified_name"] = "Wrong Event"
    elif case == "tier":
        config["mappings"][0]["canonical_tier"] = "premium"
    elif case == "url":
        config["mappings"][0]["source_url"] = "http://example.com/not-secure"
    elif case == "reviewed_at":
        config["reviewed_at"] = "yesterday"
    elif case == "method_version":
        config["method_version"] = ""

    with pytest.raises(ValueError, match=expected):
        subject.apply_profile_annotations(
            db, tier_config=config, dry_run=False
        )

    assert db.execute("SELECT count(*) FROM profile_position_annotations").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM profile_league_tiers").fetchone() == (0,)


def test_since_filters_position_groups_but_not_explicit_tier_evidence(db, monkeypatch):
    subject = _stub_inference(monkeypatch)
    _seed_public_match(db)

    summary = subject.apply_profile_annotations(
        db,
        since=date(2026, 9, 21),
        tier_config=_tier_config(),
    )

    assert summary["since"] == "2026-09-21"
    assert summary["position_groups_scanned"] == 0
    assert summary["position_annotations_planned"] == 0
    assert summary["league_tiers_planned"] == 1


def test_real_position_interface_produces_sha256_annotation(db):
    from analysis.profile_annotations import apply_profile_annotations

    _seed_public_match(db)

    summary = apply_profile_annotations(
        db, tier_config=_tier_config(), dry_run=False
    )

    fingerprint, annotations = db.execute(
        """SELECT input_fingerprint, annotations
           FROM profile_position_annotations WHERE match_id = %s AND team = 0""",
        (MATCH_ID,),
    ).fetchone()
    assert summary["position_annotations_written"] == 1
    assert len(fingerprint) == 64
    assert [row["player_slot"] for row in annotations] == [0, 1, 2, 3, 4]
    assert {row["method_version"] for row in annotations} == {METHOD_VERSION}


def test_cli_defaults_to_dry_run_and_apply_commits_one_transaction(
    dsn, monkeypatch, capsys
):
    import analysis.profile_annotations as subject

    cli_match_id = MATCH_ID + 99
    with psycopg.connect(dsn) as setup:
        setup.execute(
            """INSERT INTO matches(match_id, data_source, started_at, draft_state)
               VALUES (%s, 'pro_match', '2026-09-20T12:00:00Z', 'complete')""",
            (cli_match_id,),
        )

    calls = []

    def fake_apply(conn, *, sources=("pro_match",), since=None, tier_config=None, dry_run=True):
        calls.append((since, dry_run))
        if not dry_run:
            conn.execute(
                """INSERT INTO profile_position_annotations(
                       match_id, team, input_fingerprint, method_version, annotations
                   ) VALUES (%s, 0, 'cli', %s, %s)""",
                (cli_match_id, METHOD_VERSION, Jsonb([])),
            )
        return {"dry_run": dry_run, "since": since.isoformat() if since else None}

    monkeypatch.setattr(subject, "apply_profile_annotations", fake_apply)
    monkeypatch.setenv("DATABASE_URL", dsn)
    try:
        subject.main([])
        assert json.loads(capsys.readouterr().out) == {
            "dry_run": True,
            "since": None,
        }
        with psycopg.connect(dsn) as observer:
            assert observer.execute(
                "SELECT count(*) FROM profile_position_annotations WHERE match_id = %s",
                (cli_match_id,),
            ).fetchone() == (0,)

        subject.main(["--apply", "--since", "2026-09-21"])
        assert json.loads(capsys.readouterr().out) == {
            "dry_run": False,
            "since": "2026-09-21",
        }
        with psycopg.connect(dsn) as observer:
            assert observer.execute(
                "SELECT count(*) FROM profile_position_annotations WHERE match_id = %s",
                (cli_match_id,),
            ).fetchone() == (1,)
        assert calls == [(None, True), (date(2026, 9, 21), False)]
    finally:
        with psycopg.connect(dsn) as cleanup:
            cleanup.execute(
                "DELETE FROM profile_position_annotations WHERE match_id = %s",
                (cli_match_id,),
            )
            cleanup.execute("DELETE FROM matches WHERE match_id = %s", (cli_match_id,))
