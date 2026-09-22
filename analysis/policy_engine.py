"""Policy 请求校验与纯计算引擎。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from db.sources import SourceNotAllowed, resolve_sources
from models.policy_model import ALGORITHM_VERSION, fit_conditional_model, predict_distribution, stage_for
from shared.draft_template import resolve


class PolicyRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _validate_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise PolicyRequestError("invalid_request", "请求必须是对象")
    required = {"patch", "first_pick_team", "radiant_team_id", "dire_team_id", "draft"}
    if missing := required - set(request):
        raise PolicyRequestError("invalid_request", f"请求缺少字段：{sorted(missing)}")
    patch = request["patch"]
    if not isinstance(patch, str) or not patch.strip():
        raise PolicyRequestError("invalid_request", "patch 必须是非空字符串")
    first_pick_team = request["first_pick_team"]
    if type(first_pick_team) is not int or first_pick_team not in (0, 1):
        raise PolicyRequestError("invalid_request", "first_pick_team 必须是 0 或 1")
    for field in ("radiant_team_id", "dire_team_id"):
        if type(request[field]) is not int or request[field] <= 0:
            raise PolicyRequestError("invalid_request", f"{field} 必须是正整数")
    if request["radiant_team_id"] == request["dire_team_id"]:
        raise PolicyRequestError("invalid_request", "双方 team_id 不能相同")
    top_n = request.get("top_n", 10)
    if type(top_n) is not int or not 1 <= top_n <= 127:
        raise PolicyRequestError("invalid_request", "top_n 必须在 1..127")
    as_of = request.get("as_of", date.today().isoformat())
    if not isinstance(as_of, str):
        raise PolicyRequestError("invalid_request", "as_of 必须是 YYYY-MM-DD")
    try:
        parsed_as_of = date.fromisoformat(as_of)
    except ValueError:
        raise PolicyRequestError("invalid_request", "as_of 必须是有效的 YYYY-MM-DD") from None
    if parsed_as_of > datetime.now(timezone.utc).date():
        raise PolicyRequestError("invalid_request", "as_of 不能晚于 UTC 今天")
    sources = request.get("sources", ["pro_match"])
    if not isinstance(sources, list) or not sources or any(not isinstance(x, str) for x in sources):
        raise PolicyRequestError("invalid_request", "sources 必须是非空字符串数组")
    try:
        sources = resolve_sources(sources)
    except SourceNotAllowed as exc:
        raise PolicyRequestError("source_not_allowed", str(exc)) from None

    draft = request["draft"]
    if not isinstance(draft, list):
        raise PolicyRequestError("invalid_request", "draft 必须是数组")
    if len(draft) >= 24:
        raise PolicyRequestError("invalid_request", "draft 已满 24 手，没有下一手")
    seen: set[int] = set()
    for expected_ord, action in enumerate(draft):
        if not isinstance(action, dict):
            raise PolicyRequestError("invalid_request", "draft 每一手必须是对象")
        if set(action) != {"ord", "is_pick", "team", "hero_id"}:
            raise PolicyRequestError("invalid_request", "draft 每一手字段必须完整且无额外字段")
        if action["ord"] != expected_ord:
            raise PolicyRequestError("invalid_request", "draft ord 必须从 0 连续递增")
        want_pick, want_team = resolve(expected_ord, first_pick_team)
        if type(action["is_pick"]) is not bool or action["is_pick"] != want_pick:
            raise PolicyRequestError("invalid_request", "draft is_pick 与当前 24 手模板不一致")
        if type(action["team"]) is not int or action["team"] != want_team:
            raise PolicyRequestError("invalid_request", "draft team 与当前 24 手模板不一致")
        hero_id = action["hero_id"]
        if type(hero_id) is not int or hero_id <= 0 or hero_id in seen:
            raise PolicyRequestError("invalid_request", "draft hero_id 必须为不重复的正整数")
        seen.add(hero_id)
    return {
        **request,
        "patch": patch,
        "as_of": as_of,
        "sources": sources,
        "top_n": top_n,
        "draft": draft,
    }


def _frequency_distribution(dataset: dict[str, Any], *, ord_: int, seen: set[int]) -> dict[int, float]:
    hero_ids = [int(hero_id) for hero_id in dataset["hero_ids"] if int(hero_id) not in seen]
    is_pick, _ = resolve(ord_, 0)
    wanted_stage = stage_for(ord_)
    counts: Counter[int] = Counter()
    for match in dataset["matches"]:
        for action in match["actions"]:
            if stage_for(int(action["ord"])) == wanted_stage and bool(action["is_pick"]) == is_pick:
                hero_id = int(action["hero_id"])
                if hero_id not in seen:
                    counts[hero_id] += 1
    total = sum(counts.values())
    alpha = 0.25
    denominator = total + alpha * len(hero_ids)
    return {hero_id: (counts[hero_id] + alpha) / denominator for hero_id in hero_ids}


def _evidence(dataset: dict[str, Any], *, ord_: int, acting_team_id: int, hero_id: int) -> list[int]:
    evidence = []
    for match in reversed(dataset["matches"]):
        action = next((row for row in match["actions"] if int(row["ord"]) == ord_), None)
        if action is None or int(action["hero_id"]) != hero_id:
            continue
        team_id = match.get("radiant_team_id") if int(action["team"]) == 0 else match.get("dire_team_id")
        if team_id == acting_team_id:
            evidence.append(int(match["match_id"]))
            if len(evidence) == 3:
                break
    return evidence


def build_policy(
    dataset: dict[str, Any],
    request: dict[str, Any],
    model: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从已按请求截断的数据集生成冻结 Policy shape。"""

    clean = _validate_request(request)
    if (
        dataset.get("patch") != clean["patch"]
        or dataset.get("as_of") != clean["as_of"]
        or list(dataset.get("sources", [])) != clean["sources"]
    ):
        raise PolicyRequestError("invalid_request", "数据集与请求的 patch、sources 或 as_of 不一致")
    hero_ids = {int(hero_id) for hero_id in dataset["hero_ids"]}
    seen = {int(row["hero_id"]) for row in clean["draft"]}
    if not seen.issubset(hero_ids):
        raise PolicyRequestError("invalid_request", "draft 含当前合法池之外的英雄")

    next_ord = len(clean["draft"])
    is_pick, team = resolve(next_ord, clean["first_pick_team"])
    acting_team_id = clean["radiant_team_id"] if team == 0 else clean["dire_team_id"]
    release_ready = bool(evaluation and evaluation.get("ready") and model is not None)
    experimental_ready = bool(
        evaluation and evaluation.get("experimental_ready") and model is not None
    )
    model_available = release_ready or experimental_ready
    if model_available:
        probabilities = predict_distribution(
            model,
            patch=clean["patch"],
            ord_=next_ord,
            acting_team_id=acting_team_id,
            seen_hero_ids=seen,
        )
    else:
        probabilities = _frequency_distribution(dataset, ord_=next_ord, seen=seen)

    ordered = sorted(probabilities, key=lambda hero_id: (-probabilities[hero_id], hero_id))
    selected = ordered[: clean["top_n"]]
    candidates = []
    for hero_id in selected:
        if model_available:
            reasons = ["条件概率模型综合当前手次、行动战队与已出现英雄"]
        else:
            reasons = ["请求窗口内该阶段与 pick/ban 类型的历史频率基线"]
        candidates.append(
            {
                "hero_id": hero_id,
                "prob": round(probabilities[hero_id], 8),
                "reasons": reasons,
                "evidence_match_ids": _evidence(
                    dataset,
                    ord_=next_ord,
                    acting_team_id=acting_team_id,
                    hero_id=hero_id,
                ),
            }
        )
    if len(selected) == len(probabilities):
        other_prob = 0.0
    else:
        listed_probability = sum(probabilities[hero_id] for hero_id in selected)
        other_prob = round(max(0.0, 1.0 - listed_probability), 8)
    frequency_top1 = evaluation.get("frequency_top1") if evaluation else None
    if release_ready:
        model_top1 = evaluation.get("model_top1")
    elif experimental_ready:
        model_top1 = evaluation.get("experimental_model_top1")
    else:
        model_top1 = None
    return {
        "next_ord": next_ord,
        "team": team,
        "is_pick": is_pick,
        "candidates": candidates,
        "top_n": clean["top_n"],
        "other_prob": other_prob,
        "model": (
            ALGORITHM_VERSION
            if release_ready
            else f"{ALGORITHM_VERSION}-experimental"
            if experimental_ready
            else None
        ),
        "baseline": {
            "frequency_top1": frequency_top1,
            "model_top1": model_top1,
        },
        "sources_used": list(clean["sources"]),
    }


def fit_request_model(dataset: dict[str, Any]) -> dict[str, Any]:
    return fit_conditional_model(dataset["matches"], dataset["hero_ids"])
