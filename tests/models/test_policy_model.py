from __future__ import annotations

from copy import deepcopy

from models.policy_model import (
    ALGORITHM_VERSION,
    evaluate_chronological,
    fit_conditional_model,
    predict_distribution,
)
from shared.draft_template import resolve


HERO_IDS = list(range(1, 31))


def _match(match_id: int, started_at: str, *, team_one_first_hero: int) -> dict:
    actions = []
    used = {team_one_first_hero}
    filler = iter(hero for hero in HERO_IDS if hero not in used)
    for ord_ in range(24):
        is_pick, team = resolve(ord_, 0)
        hero_id = team_one_first_hero if ord_ == 2 else next(filler)
        actions.append(
            {"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": hero_id}
        )
    return {
        "match_id": match_id,
        "started_at": started_at,
        "patch": "7.41e",
        "first_pick_team": 0,
        "radiant_team_id": 10,
        "dire_team_id": 20,
        "actions": actions,
    }


def test_conditional_model_returns_normalized_legal_distribution() -> None:
    rows = [
        _match(i, f"2026-01-{i:02d}T00:00:00+00:00", team_one_first_hero=29)
        for i in range(1, 8)
    ]
    model = fit_conditional_model(rows, HERO_IDS)

    probabilities = predict_distribution(
        model,
        patch="7.41e",
        ord_=2,
        acting_team_id=20,
        seen_hero_ids={1, 2, 3},
    )

    assert set(probabilities) == set(HERO_IDS) - {1, 2, 3}
    assert abs(sum(probabilities.values()) - 1.0) < 1e-12
    assert max(probabilities, key=probabilities.get) == 29
    assert model["algorithm_version"] == ALGORITHM_VERSION


def test_prefix_context_changes_prediction_without_mutating_model() -> None:
    rows = []
    for i in range(1, 7):
        row = _match(i, f"2026-02-{i:02d}T00:00:00+00:00", team_one_first_hero=29)
        row["actions"][0]["hero_id"] = 28
        rows.append(row)
    for i in range(7, 13):
        row = _match(i, f"2026-02-{i:02d}T00:00:00+00:00", team_one_first_hero=30)
        row["actions"][0]["hero_id"] = 27
        rows.append(row)
    model = fit_conditional_model(rows, HERO_IDS)
    before = deepcopy(model)

    with_28 = predict_distribution(
        model, patch="7.41e", ord_=2, acting_team_id=20, seen_hero_ids={28}
    )
    with_27 = predict_distribution(
        model, patch="7.41e", ord_=2, acting_team_id=20, seen_hero_ids={27}
    )

    assert with_28[29] > with_28[30]
    assert with_27[30] > with_27[29]
    assert model == before


def test_chronological_evaluation_uses_holdout_only_after_split() -> None:
    rows = [
        _match(i, f"2026-03-{i:02d}T00:00:00+00:00", team_one_first_hero=29)
        for i in range(1, 11)
    ]

    report = evaluate_chronological(rows, HERO_IDS)

    assert report["split"]["train_matches"] == 8
    assert report["split"]["holdout_matches"] == 2
    assert report["split"]["train_max_started_at"] < report["split"]["holdout_min_started_at"]
    assert report["protocol"]["holdout_used_for_tuning"] is False
    assert report["protocol"]["parameter_search"] is False
    assert report["metrics"]["overall"]["n_predictions"] == 48
    assert set(report["metrics"]["overall"]) >= {
        "uniform_random_expected",
        "global_frequency",
        "stage_pickban_frequency",
        "model",
    }
    assert set(report["metrics"]["by_stage"]) == {"1-8", "9-16", "17-24"}


def test_unseen_patch_uses_registered_global_backoff_instead_of_uniform() -> None:
    rows = [
        _match(i, f"2026-04-{i:02d}T00:00:00+00:00", team_one_first_hero=29)
        for i in range(1, 11)
    ]
    model = fit_conditional_model(rows, HERO_IDS)

    probabilities = predict_distribution(
        model,
        patch="7.41f",
        ord_=2,
        acting_team_id=20,
        seen_hero_ids={1, 2},
    )

    assert probabilities[29] > probabilities[30]
    assert model["config"]["patch_min_samples"] == 30
