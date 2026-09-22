from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from analysis.policy_engine import PolicyRequestError
from shared.draft_template import resolve


UTC = timezone.utc


def _seed_policy_reference(db) -> None:
    db.execute(
        """INSERT INTO constants_snapshot(snapshot_version, n_heroes, n_items)
           VALUES (1, 30, 0)"""
    )
    with db.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO heroes(hero_id, name, localized_name, cm_enabled) VALUES (%s, %s, %s, true)",
            [(hero_id, f"hero_{hero_id}", f"Hero {hero_id}") for hero_id in range(1, 31)],
        )
        cursor.executemany(
            "INSERT INTO hero_token_index(snapshot_version, hero_id, dense_index) VALUES (1, %s, %s)",
            [(hero_id, hero_id - 1) for hero_id in range(1, 31)],
        )
    db.execute("INSERT INTO teams(team_id, name) VALUES (10, 'A'), (20, 'B')")
    db.execute(
        """INSERT INTO patches(version_name, base_version, released_at)
           VALUES ('7.41e', '7.41', '2026-01-01T00:00:00Z'),
                  ('7.41f', '7.41', '2026-06-01T00:00:00Z')"""
    )


def _insert_match(
    db,
    match_id: int,
    *,
    source: str,
    patch: str,
    started_at: str,
    duration_s: int = 3600,
    game_mode: int = 2,
) -> None:
    patch_id = db.execute(
        "SELECT patch_id FROM patches WHERE version_name=%s", (patch,)
    ).fetchone()[0]
    db.execute(
        """INSERT INTO matches(
               match_id, data_source, patch_id, started_at, first_pick_team,
               radiant_team_id, dire_team_id, draft_state, n_draft_actions, anomaly,
               duration_s, game_mode
           ) VALUES (%s,%s,%s,%s,0,10,20,'complete',24,false,%s,%s)""",
        (match_id, source, patch_id, started_at, duration_s, game_mode),
    )
    rows = []
    for ord_ in range(24):
        is_pick, team = resolve(ord_, 0)
        rows.append((match_id, ord_, is_pick, team, ord_ + 1))
    with db.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO draft_actions(match_id,ord,is_pick,team,hero_id) VALUES (%s,%s,%s,%s,%s)",
            rows,
        )


def _request(**updates) -> dict:
    request = {
        "patch": "7.41e",
        "as_of": "2026-09-21",
        "first_pick_team": 0,
        "radiant_team_id": 10,
        "dire_team_id": 20,
        "draft": [],
        "sources": ["pro_match"],
        "top_n": 5,
    }
    request.update(updates)
    return request


def test_repository_enforces_patch_source_and_as_of_cutoff(db) -> None:
    from analysis.policy_repository import load_policy_dataset

    _seed_policy_reference(db)
    _insert_match(db, 101, source="pro_match", patch="7.41e", started_at="2026-09-20T12:00:00Z")
    _insert_match(db, 102, source="pro_match", patch="7.41e", started_at="2026-09-22T00:00:00Z")
    _insert_match(db, 103, source="pro_match", patch="7.41f", started_at="2026-09-20T12:00:00Z")
    _insert_match(db, 104, source="pub_match", patch="7.41e", started_at="2026-09-20T12:00:00Z")

    dataset = load_policy_dataset(db, _request())

    assert [row["match_id"] for row in dataset["matches"]] == [101]
    assert dataset["patch"] == "7.41e"
    assert dataset["as_of"] == "2026-09-21"
    assert dataset["sources"] == ["pro_match"]
    assert dataset["hero_ids"] == list(range(1, 31))
    assert dataset["metadata"]["vocab_rule"] == "latest hero_token_index snapshot"
    assert dataset["metadata"]["historical_cm_pool_exact"] is False


def test_repository_can_include_requested_public_source_without_cross_source_leak(db) -> None:
    from analysis.policy_repository import load_policy_dataset

    _seed_policy_reference(db)
    _insert_match(db, 201, source="pro_match", patch="7.41e", started_at="2026-09-20T12:00:00Z")
    _insert_match(db, 202, source="pub_match", patch="7.41e", started_at="2026-09-20T13:00:00Z")

    dataset = load_policy_dataset(db, _request(sources=["pub_match"]))

    assert [row["match_id"] for row in dataset["matches"]] == [202]
    assert dataset["sources"] == ["pub_match"]


def test_load_policy_returns_frequency_mode_when_evaluation_is_absent(db, monkeypatch, tmp_path) -> None:
    from analysis.policy_repository import load_policy_from_connection

    _seed_policy_reference(db)
    for match_id in range(301, 305):
        _insert_match(
            db,
            match_id,
            source="pro_match",
            patch="7.41e",
            started_at=f"2026-09-{match_id - 290:02d}T12:00:00Z",
        )
    monkeypatch.setenv("POLICY_EVALUATION_PATH", str(tmp_path / "missing.json"))

    result = load_policy_from_connection(db, _request())

    assert result["model"] is None
    assert result["baseline"]["model_top1"] is None
    assert result["candidates"]


def test_scrim_is_rejected_before_repository_access(db) -> None:
    from analysis.policy_repository import load_policy_dataset

    with pytest.raises(PolicyRequestError) as exc:
        load_policy_dataset(db, _request(sources=["scrim"]))
    assert exc.value.code == "source_not_allowed"


def test_as_of_today_never_reads_future_rows(db) -> None:
    from analysis.policy_repository import load_policy_dataset

    _seed_policy_reference(db)
    today = datetime.now(UTC).date().isoformat()
    _insert_match(db, 401, source="pro_match", patch="7.41e", started_at="2099-01-01T00:00:00Z")

    dataset = load_policy_dataset(db, _request(as_of=today))

    assert dataset["matches"] == []


def test_repository_requires_completed_cm_matches(db) -> None:
    from analysis.policy_repository import load_policy_dataset

    _seed_policy_reference(db)
    _insert_match(
        db,
        501,
        source="pro_match",
        patch="7.41e",
        started_at="2026-09-21T23:30:00Z",
        duration_s=7200,
    )
    _insert_match(
        db,
        502,
        source="pro_match",
        patch="7.41e",
        started_at="2026-09-20T12:00:00Z",
        game_mode=1,
    )
    _insert_match(
        db,
        503,
        source="pro_match",
        patch="7.41e",
        started_at="2026-09-20T12:00:00Z",
    )

    dataset = load_policy_dataset(db, _request())

    assert [row["match_id"] for row in dataset["matches"]] == [503]


def test_evaluation_readiness_is_scoped_to_source_patch_and_request_cutoff(tmp_path, monkeypatch) -> None:
    from analysis.policy_repository import _load_evaluation

    path = tmp_path / "evaluation.json"
    report = {
        "algorithm_version": "conditional-sequence-v2",
        "ready": True,
        "release_ready": True,
        "protocol": {"independent_holdout": True},
        "data": {
            "sources": ["pro_match"],
            "until_exclusive": "2026-09-22T12:00:00+00:00",
        },
        "metrics": {
            "overall": {
                "n_predictions": 1000,
                "model": {"top1": 0.20},
                "stage_pickban_frequency": {"top1": 0.10},
            },
            "by_patch": {
                "7.41e": {
                    "n_predictions": 200,
                    "model": {"top1": 0.25},
                    "stage_pickban_frequency": {"top1": 0.15},
                },
                "7.41f": {
                    "n_predictions": 200,
                    "model": {"top1": 0.10},
                    "stage_pickban_frequency": {"top1": 0.15},
                },
            },
        },
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("POLICY_EVALUATION_PATH", str(path))

    ready = _load_evaluation("7.41e", ["pro_match"], "2026-09-22")
    wrong_source = _load_evaluation("7.41e", ["pub_match"], "2026-09-22")
    future_evaluation = _load_evaluation("7.41e", ["pro_match"], "2026-09-21")
    weak_patch = _load_evaluation("7.41f", ["pro_match"], "2026-09-22")

    assert ready["ready"] is True
    assert ready["quality_gate"] == "passed"
    assert wrong_source["ready"] is False
    assert wrong_source["quality_gate"] == "source_mismatch"
    assert "overall_metrics" not in wrong_source
    assert "patch_metrics" not in wrong_source
    assert future_evaluation["ready"] is False
    assert future_evaluation["quality_gate"] == "evaluation_after_request_cutoff"
    assert "overall_metrics" not in future_evaluation
    assert "patch_metrics" not in future_evaluation
    assert weak_patch["ready"] is False
    assert weak_patch["quality_gate"] == "patch_not_better_than_baseline"

    report["release_ready"] = False
    report["protocol"]["independent_holdout"] = False
    path.write_text(json.dumps(report), encoding="utf-8")
    reused_holdout = _load_evaluation("7.41e", ["pro_match"], "2026-09-22")
    assert reused_holdout["ready"] is False
    assert reused_holdout["quality_gate"] == "holdout_not_independent"
    assert reused_holdout["experimental_ready"] is True
    assert reused_holdout["release_ready"] is False
    assert reused_holdout["independent_holdout"] is False
    assert reused_holdout["evaluation_status"] == "development_reused_holdout"


def test_prepare_policy_opens_once_and_returns_reusable_parts(db, monkeypatch, tmp_path) -> None:
    from analysis.policy_repository import prepare_policy_from_connection

    _seed_policy_reference(db)
    for match_id in range(601, 631):
        _insert_match(
            db,
            match_id,
            source="pro_match",
            patch="7.41e",
            started_at=f"2026-08-{match_id - 600:02d}T12:00:00Z",
        )
    monkeypatch.setenv("POLICY_EVALUATION_PATH", str(tmp_path / "missing.json"))

    dataset, model, evaluation = prepare_policy_from_connection(db, _request())

    assert len(dataset["matches"]) == 30
    assert model is None
    assert evaluation["ready"] is False
    assert evaluation["quality_gate"] == "missing_or_invalid_evaluation"
