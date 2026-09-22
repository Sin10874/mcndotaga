from __future__ import annotations

import pytest

from analysis.policy_engine import PolicyRequestError, build_policy
from contracts.tools.invariants import check_policy
from models.policy_model import fit_conditional_model
from shared.draft_template import resolve


HERO_IDS = list(range(1, 31))


def _training_match(match_id: int, hero_at_2: int = 29) -> dict:
    used = {hero_at_2}
    filler = iter(hero for hero in HERO_IDS if hero not in used)
    actions = []
    for ord_ in range(24):
        is_pick, team = resolve(ord_, 0)
        actions.append(
            {
                "ord": ord_,
                "is_pick": is_pick,
                "team": team,
                "hero_id": hero_at_2 if ord_ == 2 else next(filler),
            }
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


def _request(**updates) -> dict:
    request = {
        "patch": "7.41e",
        "as_of": "2026-09-22",
        "first_pick_team": 0,
        "radiant_team_id": 10,
        "dire_team_id": 20,
        "draft": [],
        "sources": ["pro_match"],
        "top_n": 5,
    }
    request.update(updates)
    return request


def _dataset() -> dict:
    matches = [_training_match(i) for i in range(1, 8)]
    return {
        "patch": "7.41e",
        "as_of": "2026-09-22",
        "sources": ["pro_match"],
        "hero_ids": HERO_IDS,
        "matches": matches,
    }


def test_empty_draft_returns_contract_valid_top_n_and_tail_probability() -> None:
    dataset = _dataset()
    model = fit_conditional_model(dataset["matches"], HERO_IDS)

    result = build_policy(
        dataset,
        _request(),
        model=model,
        evaluation={"ready": True, "model_top1": 0.25, "frequency_top1": 0.10},
    )

    assert check_policy(result) == []
    assert result["next_ord"] == 0
    assert result["team"] == 0
    assert result["is_pick"] is False
    assert len(result["candidates"]) == 5
    assert abs(sum(row["prob"] for row in result["candidates"]) + result["other_prob"] - 1) < 1e-9
    assert result["model"] == "conditional-sequence-v2"
    assert result["baseline"] == {"frequency_top1": 0.10, "model_top1": 0.25}
    assert all(row["reasons"] for row in result["candidates"])


def test_seen_heroes_are_excluded_and_evidence_is_request_scoped() -> None:
    dataset = _dataset()
    prefix = dataset["matches"][0]["actions"][:2]
    result = build_policy(
        dataset,
        _request(draft=prefix, top_n=30),
        model=fit_conditional_model(dataset["matches"], HERO_IDS),
        evaluation={"ready": True, "model_top1": 0.25, "frequency_top1": 0.10},
    )

    seen = {row["hero_id"] for row in prefix}
    assert not seen.intersection(row["hero_id"] for row in result["candidates"])
    assert result["other_prob"] == pytest.approx(0.0)
    assert result["candidates"][0]["evidence_match_ids"]
    assert set(result["candidates"][0]["evidence_match_ids"]).issubset(
        {row["match_id"] for row in dataset["matches"]}
    )


def test_model_not_ready_returns_honest_frequency_baseline() -> None:
    result = build_policy(
        _dataset(),
        _request(),
        model=None,
        evaluation={"ready": False, "model_top1": 0.08, "frequency_top1": 0.10},
    )

    assert result["model"] is None
    assert result["baseline"]["model_top1"] is None
    assert result["candidates"]
    assert check_policy(result) == []


def test_scoped_development_model_is_explicitly_experimental() -> None:
    dataset = _dataset()
    result = build_policy(
        dataset,
        _request(),
        model=fit_conditional_model(dataset["matches"], HERO_IDS),
        evaluation={
            "ready": False,
            "experimental_ready": True,
            "experimental_model_top1": 0.25,
            "frequency_top1": 0.10,
        },
    )

    assert result["model"] == "conditional-sequence-v2-experimental"
    assert result["baseline"] == {"frequency_top1": 0.10, "model_top1": 0.25}
    assert "条件概率模型" in result["candidates"][0]["reasons"][0]


@pytest.mark.parametrize(
    "draft",
    [
        [{"ord": 1, "is_pick": False, "team": 0, "hero_id": 1}],
        [{"ord": 0, "is_pick": True, "team": 0, "hero_id": 1}],
        [
            {"ord": 0, "is_pick": False, "team": 0, "hero_id": 1},
            {"ord": 1, "is_pick": False, "team": 0, "hero_id": 1},
        ],
    ],
)
def test_invalid_draft_is_rejected(draft: list[dict]) -> None:
    with pytest.raises(PolicyRequestError) as exc:
        build_policy(_dataset(), _request(draft=draft))
    assert exc.value.code == "invalid_request"


def test_complete_draft_is_rejected() -> None:
    with pytest.raises(PolicyRequestError, match="已满 24 手"):
        build_policy(_dataset(), _request(draft=_dataset()["matches"][0]["actions"]))


def test_dataset_identity_must_match_request() -> None:
    dataset = _dataset()
    dataset["patch"] = "7.41f"
    with pytest.raises(PolicyRequestError, match="数据集与请求"):
        build_policy(dataset, _request())
