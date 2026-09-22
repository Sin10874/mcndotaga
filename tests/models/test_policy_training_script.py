from __future__ import annotations

from scripts.train_policy_model import build_outputs
from shared.draft_template import resolve


def _match(match_id: int) -> dict:
    actions = []
    for ord_ in range(24):
        is_pick, team = resolve(ord_, 0)
        actions.append(
            {"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
        )
    return {
        "match_id": match_id,
        "started_at": f"2026-01-{match_id:02d}T00:00:00+00:00",
        "patch": "7.41e",
        "first_pick_team": 0,
        "radiant_team_id": 10,
        "dire_team_id": 20,
        "actions": actions,
    }


def test_training_outputs_keep_raw_rows_out_of_public_evaluation() -> None:
    snapshot = {
        "hero_ids": list(range(1, 31)),
        "matches": [_match(i) for i in range(1, 11)],
        "metadata": {
            "snapshot_at": "2026-09-22T00:00:00+00:00",
            "pg_snapshot": "1:2:",
            "transaction_isolation": "repeatable read",
            "transaction_read_only": True,
            "sources": ["pro_match"],
            "since_inclusive": "2025-01-01T00:00:00+00:00",
            "until_exclusive": "2026-09-22T00:00:00+00:00",
            "constants_snapshot_version": 1,
            "constants_fetched_at": "2026-09-22T00:00:00+00:00",
            "vocab_rule": "latest hero_token_index snapshot",
            "historical_cm_pool_exact": False,
            "limitation": "当前冻结词表，不是严格历史回放",
            "hard_gate_rows": 10,
            "not_current_order_family": 0,
        },
    }

    artifact, evaluation = build_outputs(snapshot)

    assert artifact["training"]["n_matches"] == 10
    assert artifact["model"]["n_matches"] == 10
    assert "matches" not in artifact
    assert "matches" not in evaluation
    assert evaluation["data"]["historical_cm_pool_exact"] is False
    assert evaluation["data"]["limitation"]
    assert evaluation["protocol"]["holdout_used_for_tuning"] is False
    assert evaluation["protocol"]["independent_holdout"] is False
    assert "development_ready" in evaluation
    assert evaluation["ready"] is evaluation["release_ready"]
    assert evaluation["release_ready"] is False
