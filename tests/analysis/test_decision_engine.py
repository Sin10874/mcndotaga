from __future__ import annotations

import pytest
import yaml
from jsonschema import Draft202012Validator
from pathlib import Path

from analysis.decision_engine import DecisionUnavailable, _bans_from_op, build_advise, build_playbook
from shared.draft_template import resolve


OPENAPI = yaml.safe_load((Path(__file__).parents[2] / "contracts/openapi.yaml").read_text())
ROOT_VALIDATOR = Draft202012Validator(OPENAPI)


def validate(resource, body):
    ROOT_VALIDATOR.evolve(schema=OPENAPI["components"]["schemas"][resource]).validate(body)


def prefix(length: int, first_pick_team: int = 0) -> list[dict]:
    return [
        {"ord": ord_, "is_pick": resolve(ord_, first_pick_team)[0],
         "team": resolve(ord_, first_pick_team)[1], "hero_id": ord_ + 1}
        for ord_ in range(length)
    ]


def metadata(**overrides):
    base = {
        "side_map": {"us": 0, "them": 1},
        "hero_ids": list(range(20, 50)),
        "opening_hero_ids": {name: list(range(20, 50)) for name in (
            "teamfight", "push", "pickoff", "splitpush", "protect", "initiate"
        )},
        "robustness_lambda": 1.0,
        "max_options": 3,
        "response_limit": 2,
        "as_of": "2026-09-22",
        "team_refs": {
            "us": {"team_id": 10, "name": "我方", "tag": "US"},
            "them": {"team_id": 20, "name": "对手", "tag": "THEM"},
        },
        "coverage": {
            "pro_match": {"n_matches": 60, "n_stat_available": 55},
            "pub_match": {"n_matches": 0, "n_stat_available": 0},
        },
        "data_quality": {
            "n_pending_draft": 0, "n_unavailable_draft": 0,
            "n_anomalous_draft": 0, "oldest_pending_hours": None,
            "note": "当前窗口无待补齐 BP。",
        },
        "op_hero_decision": [],
        "positions": [],
    }
    base.update(overrides)
    return base


def test_realtime_uses_expected_value_and_penalizes_opponent_switches():
    draft = prefix(14)

    def policy_fn(request):
        current = request["draft"]
        is_pick, team = resolve(len(current), request["first_pick_team"])
        if len(current) == 14:
            candidates = [(20, .40), (21, .35), (22, .25)]
        else:
            candidates = [(30, .70), (31, .20)]
        return {
            "next_ord": len(current), "team": team, "is_pick": is_pick,
            "candidates": [
                {"hero_id": hero, "prob": prob, "reasons": ["时间截断样本"],
                 "evidence_match_ids": []}
                for hero, prob in candidates
            ],
            "top_n": len(candidates), "other_prob": max(0.0, 1 - sum(p for _, p in candidates)),
            "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    scores = {
        (20, 30): .70, (20, 31): .40,
        (21, 30): .62, (21, 31): .58,
        (22, 30): .59, (22, 31): .57,
    }

    def value_fn(request):
        picks = [row["hero_id"] for row in request["radiant"]["heroes"]]
        enemies = [row["hero_id"] for row in request["dire"]["heroes"]]
        our = next(hero for hero in (20, 21, 22) if hero in picks)
        enemy = next(hero for hero in (30, 31) if hero in enemies)
        score = scores[(our, enemy)]
        return {
            "value": {"radiant_win_prob": score, "confidence": "medium", "n_samples": 80,
                      "contributions": [{"factor": "patch_strength", "delta": score - .5}],
                      "sources_used": ["pro_match"]},
            "detail": {}, "model": "value-v1",
        }

    body = build_advise({
        "mode": "realtime", "patch": "7.41e", "us": 10, "them": 20,
        "us_side": 0, "first_pick_team": 0, "draft": draft,
        "sources": ["pro_match"],
    }, value_fn, policy_fn, metadata(response_limit=1))

    assert [option["hero_id"] for option in body["options"]] == [21, 22, 20]
    assert body["options"][0]["expected_wr"] == pytest.approx(.608)
    risky = body["options"][2]
    assert risky["expected_wr"] == pytest.approx(.61)
    assert risky["robustness_delta"] == pytest.approx(.21)
    assert risky["penalized_score"] == pytest.approx(.50)
    assert risky["risk_note"]
    assert "31" in risky["counterparty_plan"]
    assert "21" in risky["counterparty_plan"]
    assert all(option["fallback"] for option in body["options"])
    assert all(option["counterparty_plan"] for option in body["options"])
    assert body["assumptions"] == {
        "opponent_model": "conditional-sequence-v1", "value_model": "value-v1"
    }
    validate("Advise", body)


def test_frequency_only_policy_is_not_treated_as_ready_model():
    def policy_fn(request):
        is_pick, team = resolve(len(request["draft"]), request["first_pick_team"])
        return {
            "next_ord": len(request["draft"]), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": 20, "prob": 1.0, "reasons": ["频率"],
                            "evidence_match_ids": []}],
            "top_n": 1, "other_prob": 0.0, "model": None,
            "baseline": {"frequency_top1": .1, "model_top1": None},
            "sources_used": ["pro_match"],
        }

    with pytest.raises(DecisionUnavailable) as caught:
        build_advise({
            "mode": "realtime", "patch": "7.41e", "us": 10, "them": 20,
            "us_side": 0, "first_pick_team": 0, "draft": prefix(14),
            "sources": ["pro_match"],
        }, lambda _: None, policy_fn, metadata())
    assert caught.value.code == "insufficient_data"


def test_value_insufficient_skips_whole_candidate_without_renormalizing_partial_response():
    def policy_fn(request):
        draft = request["draft"]
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        heroes = [(20, .4), (21, .35), (22, .25)] if len(draft) == 14 else [(30, .6), (31, .4)]
        return {
            "next_ord": len(draft), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": hero, "prob": prob, "reasons": ["模型"],
                            "evidence_match_ids": []} for hero, prob in heroes],
            "top_n": len(heroes), "other_prob": 0.0,
            "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    def value_fn(request):
        ours = {row["hero_id"] for row in request["radiant"]["heroes"]}
        theirs = {row["hero_id"] for row in request["dire"]["heroes"]}
        if 20 in ours and 31 in theirs:
            return {"value": {"error": {"code": "insufficient_data",
                                           "message": "该完整响应无 Value 样本"}},
                    "detail": {}, "model": "value-v1"}
        return {
            "value": {"radiant_win_prob": .55, "confidence": "medium", "n_samples": 40,
                      "contributions": [], "sources_used": ["pro_match"]},
            "detail": {}, "model": "value-v1",
        }

    body = build_advise({
        "mode": "realtime", "patch": "7.41e", "us": 10, "them": 20,
        "us_side": 0, "first_pick_team": 0, "draft": prefix(14),
        "sources": ["pro_match"],
    }, value_fn, policy_fn, metadata())

    assert [option["hero_id"] for option in body["options"]] == [21, 22]
    assert body["options"][0]["fallback"] == [22]


def test_playbook_reports_exact_branch_when_fewer_than_two_candidates_have_value():
    def policy_fn(request):
        draft = request["draft"]
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        used = {row["hero_id"] for row in draft}
        heroes = [hero for hero in range(20, 50) if hero not in used][:3]
        return {
            "next_ord": len(draft), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": hero, "prob": 1 / 3, "reasons": ["模型"],
                            "evidence_match_ids": []} for hero in heroes],
            "top_n": 3, "other_prob": 0.0, "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    def unavailable_value(_request):
        return {"value": {"error": {"code": "insufficient_data", "message": "无精确版本样本"}},
                "detail": {}, "model": "value-v1"}

    with pytest.raises(DecisionUnavailable) as caught:
        build_playbook({
            "us": 10, "them": 20, "patch": "7.41e", "first_pick_team": 0,
            "us_side": 0, "sources": ["pro_match"],
        }, unavailable_value, policy_fn, metadata())
    assert caught.value.code == "insufficient_data"
    assert "分支 us:teamfight" in caught.value.message
    assert "可完整评估的候选不足 2 个" in caught.value.message


def test_ban_requeries_policy_and_changes_future_opponent_distribution():
    seen_drafts = []

    def policy_fn(request):
        draft = request["draft"]
        seen_drafts.append(tuple(row["hero_id"] for row in draft))
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        used = {row["hero_id"] for row in draft}
        if len(draft) == 1:
            heroes = [20, 21, 22]
        elif is_pick and team == 1:
            heroes = [30, 31] if 20 in used else [32, 33]
        else:
            heroes = [hero for hero in range(34, 50) if hero not in used][:2]
        return {
            "next_ord": len(draft), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": h, "prob": .5, "reasons": ["模型"],
                            "evidence_match_ids": []} for h in heroes],
            "top_n": 2, "other_prob": 0.0, "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    def value_fn(request):
        enemy = {row["hero_id"] for row in request["dire"]["heroes"]}
        score = .35 if enemy & {30, 31} else .65
        return {
            "value": {"radiant_win_prob": score, "confidence": "medium", "n_samples": 50,
                      "contributions": [{"factor": "patch_strength", "delta": score - .5}],
                      "sources_used": ["pro_match"]},
            "detail": {}, "model": "value-v1",
        }

    body = build_advise({
        "mode": "realtime", "patch": "7.41e", "us": 10, "them": 20,
        "us_side": 0, "first_pick_team": 0, "draft": prefix(1),
        "sources": ["pro_match"],
    }, value_fn, policy_fn, metadata())

    assert body["is_pick"] is False
    assert any(20 in draft for draft in seen_drafts)
    assert len({round(option["expected_wr"], 4) for option in body["options"]}) > 1


def test_playbook_default_covers_seven_openings_for_known_first_pick_side():
    def policy_fn(request):
        assert "their_opening" not in request
        draft = request["draft"]
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        used = {row["hero_id"] for row in draft}
        heroes = [hero for hero in range(20, 80) if hero not in used][:3]
        return {
            "next_ord": len(draft), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": h, "prob": 1 / 3, "reasons": ["模型"],
                            "evidence_match_ids": []} for h in heroes],
            "top_n": 3, "other_prob": 0.0, "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    def value_fn(request):
        return {
            "value": {"radiant_win_prob": .55, "confidence": "medium", "n_samples": 40,
                      "contributions": [{"factor": "patch_strength", "delta": .05}],
                      "sources_used": ["pro_match"]},
            "detail": {}, "model": "value-v1",
        }

    body = build_playbook({
        "us": 10, "them": 20, "patch": "7.41e", "first_pick_team": 0,
        "us_side": 0, "sources": ["pro_match"],
    }, value_fn, policy_fn, metadata())

    assert {branch["condition"]["their_opening"] for branch in body["branches"]} == {
        "teamfight", "push", "pickoff", "splitpush", "protect", "initiate", "unknown"
    }
    assert {branch["condition"]["first_pick"] for branch in body["branches"]} == {"us"}
    assert all(branch["plans"] and branch["plans"][0]["key_picks"][0]["fallback"]
               for branch in body["branches"])
    assert body["bans"]["must_ban"] == []
    assert body["op_hero_decision"] == []
    validate("Playbook", body)


def test_insufficient_op_sample_is_not_relabelled_as_bait():
    degraded = {"value": None, "reason": "insufficient_samples"}
    bans = _bans_from_op([{
        "hero_id": 55, "if_we_pick": degraded, "if_we_ban": degraded,
        "if_we_leave": degraded, "recommendation": "insufficient_data",
    }])
    assert bans == {"must_ban": [], "consider": [], "bait_candidates": []}


def test_series_playbook_maps_each_known_game_to_its_first_pick_side():
    def policy_fn(request):
        draft = request["draft"]
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        used = {row["hero_id"] for row in draft}
        heroes = [hero for hero in range(20, 80) if hero not in used][:3]
        return {
            "next_ord": len(draft), "team": team, "is_pick": is_pick,
            "candidates": [{"hero_id": h, "prob": 1 / 3, "reasons": ["模型"],
                            "evidence_match_ids": []} for h in heroes],
            "top_n": 3, "other_prob": 0.0, "model": "conditional-sequence-v1",
            "baseline": {"frequency_top1": .1, "model_top1": .2},
            "sources_used": ["pro_match"],
        }

    def value_fn(request):
        return {
            "value": {"radiant_win_prob": .55, "confidence": "medium", "n_samples": 40,
                      "contributions": [{"factor": "patch_strength", "delta": .05}],
                      "sources_used": ["pro_match"]},
            "detail": {}, "model": "value-v1",
        }

    meta = metadata(series={
        "series_id": 99,
        "games": [{"game_no": 1, "first_pick_team": 0},
                  {"game_no": 2, "first_pick_team": 1}],
        "game1_plan": "按第一局公开画像执行",
        "adjustment_rules": [],
    })
    body = build_playbook({
        "us": 10, "them": 20, "patch": "7.41e", "first_pick_team": 0,
        "us_side": 0, "sources": ["pro_match"],
    }, value_fn, policy_fn, meta)
    assert len(body["branches"]) == 14
    assert {branch["applies_to_game"] for branch in body["branches"]} == {1, 2}
    for branch in body["branches"]:
        expected = "us" if branch["applies_to_game"] == 1 else "them"
        assert branch["condition"]["first_pick"] == expected
    validate("Playbook", body)


def test_unknown_requested_series_fails_closed_before_search():
    with pytest.raises(DecisionUnavailable) as caught:
        build_playbook({
            "us": 10, "them": 20, "us_side": 0, "patch": "7.41e",
            "first_pick_team": 0, "series_id": 999, "sources": ["pro_match"],
        }, lambda _: pytest.fail("不应调用 Value"), lambda _: pytest.fail("不应调用 Policy"),
            metadata())
    assert caught.value.code == "insufficient_data"
