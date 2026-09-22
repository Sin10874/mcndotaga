"""Playbook 与 Advise 的纯决策计算。

本模块只编排已验证的 Value 与 Policy 回调，不自行伪造胜率或预测。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable

from contracts.tools.invariants import check_advise, check_playbook
from shared.draft_template import TEMPLATE, resolve


OPENINGS = ("teamfight", "push", "pickoff", "splitpush", "protect", "initiate", "unknown")


class DecisionUnavailable(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Evaluation:
    hero_id: int
    expected_wr: float
    worst_wr: float
    n: int
    most_likely_response_hero: int | None
    worst_response_hero: int | None
    value_model: str


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise DecisionUnavailable("upstream_unavailable", f"{name} 不是有限数值")
    return float(value)


def _side_map(request: dict, metadata: dict) -> dict[str, int]:
    mapping = metadata.get("side_map")
    if mapping is None and request.get("us_side") in (0, 1):
        mapping = {"us": int(request["us_side"]), "them": 1 - int(request["us_side"])}
    if not isinstance(mapping, dict) or {mapping.get("us"), mapping.get("them")} != {0, 1}:
        raise DecisionUnavailable("invalid_request", "缺少明确且互异的 us/them 阵营映射")
    return {"us": int(mapping["us"]), "them": int(mapping["them"])}


def _validate_request(request: dict, metadata: dict) -> tuple[dict[str, int], list[dict]]:
    if request.get("first_pick_team") not in (0, 1):
        raise DecisionUnavailable("invalid_request", "first_pick_team 必须是 0 或 1")
    if not isinstance(request.get("patch"), str) or not request["patch"]:
        raise DecisionUnavailable("invalid_request", "patch 必须是非空字符串")
    if not isinstance(request.get("sources"), list) or not request["sources"]:
        raise DecisionUnavailable("invalid_request", "sources 必须是非空列表")
    if "scrim" in request["sources"]:
        raise DecisionUnavailable("source_not_allowed", "对手决策路径不允许训练赛来源")
    side_map = _side_map(request, metadata)
    draft = deepcopy(request.get("draft") or [])
    if len(draft) >= len(TEMPLATE):
        raise DecisionUnavailable("invalid_request", "BP 已满 24 手")
    seen = set()
    for expected_ord, action in enumerate(draft):
        if not isinstance(action, dict) or action.get("ord") != expected_ord:
            raise DecisionUnavailable("invalid_request", "draft 必须从 0 开始连续排列")
        exp_pick, exp_team = resolve(expected_ord, request["first_pick_team"])
        if action.get("is_pick") is not exp_pick or action.get("team") != exp_team:
            raise DecisionUnavailable("invalid_request", f"draft 第 {expected_ord} 手与合法模板不一致")
        hero_id = action.get("hero_id")
        if isinstance(hero_id, bool) or not isinstance(hero_id, int) or hero_id <= 0 or hero_id in seen:
            raise DecisionUnavailable("invalid_request", "draft 英雄必须是互异的正整数")
        seen.add(hero_id)
    return side_map, draft


def _policy_request(request: dict, draft: list[dict], metadata: dict, top_n: int) -> dict:
    side_map = _side_map(request, metadata)
    radiant_id = request["us"] if side_map["us"] == 0 else request["them"]
    dire_id = request["us"] if side_map["us"] == 1 else request["them"]
    result = {
        "patch": request["patch"], "first_pick_team": request["first_pick_team"],
        "radiant_team_id": radiant_id, "dire_team_id": dire_id,
        "draft": deepcopy(draft), "sources": list(request["sources"]), "top_n": top_n,
    }
    as_of = request.get("as_of") or metadata.get("as_of")
    if as_of:
        result["as_of"] = as_of
    return result


def _ready_policy(policy_fn: Callable[[dict], dict], request: dict, draft: list[dict],
                  metadata: dict, top_n: int) -> dict:
    body = policy_fn(_policy_request(request, draft, metadata, top_n))
    if not isinstance(body, dict):
        raise DecisionUnavailable("upstream_unavailable", "Policy 未返回对象")
    if body.get("error"):
        error = body["error"]
        raise DecisionUnavailable(error.get("code", "upstream_unavailable"),
                                  error.get("message", "Policy 不可用"))
    if not body.get("model"):
        raise DecisionUnavailable("insufficient_data", "下一手模型尚未就绪，不能生成决策")
    next_ord = len(draft)
    exp_pick, exp_team = resolve(next_ord, request["first_pick_team"])
    if (body.get("next_ord"), body.get("team"), body.get("is_pick")) != (
        next_ord, exp_team, exp_pick
    ):
        raise DecisionUnavailable("upstream_unavailable", "Policy 的下一手身份与合法模板不一致")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise DecisionUnavailable("insufficient_data", "Policy 没有可用候选")
    return body


def _model_name(model: Any) -> str:
    if isinstance(model, str) and model:
        return model
    if isinstance(model, dict):
        for key in ("name", "model", "model_id", "version"):
            if isinstance(model.get(key), str) and model[key]:
                return model[key]
    raise DecisionUnavailable("insufficient_data", "Value 模型尚未就绪，不能生成决策")


def _value_request(request: dict, draft: list[dict], metadata: dict) -> dict:
    side_map = _side_map(request, metadata)
    heroes = {0: [], 1: []}
    for action in draft:
        if action["is_pick"]:
            heroes[action["team"]].append({"hero_id": action["hero_id"]})
    radiant_id = request["us"] if side_map["us"] == 0 else request["them"]
    dire_id = request["us"] if side_map["us"] == 1 else request["them"]
    result = {
        "patch": request["patch"],
        "radiant": {"team_id": radiant_id, "heroes": heroes[0]},
        "dire": {"team_id": dire_id, "heroes": heroes[1]},
        "sources": list(request["sources"]),
        "first_pick_team": request["first_pick_team"],
    }
    as_of = request.get("as_of") or metadata.get("as_of")
    if as_of:
        result["as_of"] = as_of
    return result


def _score_value(value_fn: Callable[[dict], dict], request: dict, draft: list[dict],
                 metadata: dict) -> tuple[float, int, str]:
    analysis = value_fn(_value_request(request, draft, metadata))
    if not isinstance(analysis, dict):
        raise DecisionUnavailable("upstream_unavailable", "Value 未返回对象")
    if "value" in analysis:
        body, model = analysis.get("value"), analysis.get("model")
    else:
        body, model = analysis, analysis.get("model")
    if not isinstance(body, dict):
        raise DecisionUnavailable("upstream_unavailable", "Value 分析缺少响应")
    if body.get("error"):
        error = body["error"]
        raise DecisionUnavailable(error.get("code", "upstream_unavailable"),
                                  error.get("message", "Value 不可用"))
    model_name = _model_name(model)
    radiant = _finite(body.get("radiant_win_prob"), "radiant_win_prob")
    if not 0 <= radiant <= 1:
        raise DecisionUnavailable("upstream_unavailable", "Value 胜率超出 0 至 1")
    our_score = radiant if _side_map(request, metadata)["us"] == 0 else 1 - radiant
    n = body.get("n_samples", 0)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise DecisionUnavailable("upstream_unavailable", "Value 样本量无效")
    return our_score, n, model_name


def _append_action(draft: list[dict], first_pick_team: int, hero_id: int) -> list[dict]:
    if hero_id in {row["hero_id"] for row in draft}:
        raise DecisionUnavailable("upstream_unavailable", "模型返回已出现的英雄")
    ord_ = len(draft)
    is_pick, team = resolve(ord_, first_pick_team)
    return [*deepcopy(draft), {"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": hero_id}]


def _roll_to_opponent_pick(request: dict, draft: list[dict], policy_fn: Callable,
                           metadata: dict, response_limit: int) -> tuple[list[dict], dict | None]:
    current = deepcopy(draft)
    us_side = _side_map(request, metadata)["us"]
    while len(current) < len(TEMPLATE):
        # 体系条件在决策层按英雄原型过滤。Policy 需返回完整候选分布，
        # 后续用 Value 覆盖全部返回候选，避免把截断分布冒充总体期望。
        body = _ready_policy(policy_fn, request, current, metadata, 127)
        if body["is_pick"] and body["team"] != us_side:
            return current, body
        current = _append_action(current, request["first_pick_team"],
                                 int(body["candidates"][0]["hero_id"]))
    return current, None


def _evaluate_candidate(hero_id: int, request: dict, draft: list[dict], value_fn: Callable,
                        policy_fn: Callable, metadata: dict, response_limit: int) -> _Evaluation:
    after = _append_action(draft, request["first_pick_team"], hero_id)
    projected, opponent = _roll_to_opponent_pick(request, after, policy_fn, metadata, response_limit)
    if opponent is None:
        score, n, value_model = _score_value(value_fn, request, projected, metadata)
        return _Evaluation(hero_id, score, score, n, None, None, value_model)

    candidates = opponent["candidates"]
    opening = request.get("their_opening")
    conditioned = bool(opening and opening != "unknown")
    if conditioned:
        opening_ids = (metadata.get("opening_hero_ids") or {}).get(opening)
        if not isinstance(opening_ids, list) or not opening_ids:
            raise DecisionUnavailable("insufficient_data", f"缺少 {opening} 体系的英雄原型映射")
        allowed = set(opening_ids)
        candidates = [row for row in candidates if row.get("hero_id") in allowed]
    scored = []
    value_model = ""
    for candidate in candidates:
        response = _append_action(projected, request["first_pick_team"], int(candidate["hero_id"]))
        score, n, current_model = _score_value(value_fn, request, response, metadata)
        value_model = value_model or current_model
        scored.append((float(candidate["prob"]), score, n, int(candidate["hero_id"])))
    if not scored:
        raise DecisionUnavailable("insufficient_data", "对手响应分布为空")
    probability = sum(item[0] for item in scored)
    if probability < 0 or probability > 1 + 1e-6:
        raise DecisionUnavailable("upstream_unavailable", "Policy 候选概率无效")
    worst_item = min(scored, key=lambda item: (item[1], -item[0], item[3]))
    worst = worst_item[1]
    if conditioned:
        if probability <= 0:
            raise DecisionUnavailable("insufficient_data", f"{opening} 体系的候选概率为零")
        expected = sum(prob * score for prob, score, _, _ in scored) / probability
    else:
        residual = max(0.0, 1.0 - probability)
        expected = sum(prob * score for prob, score, _, _ in scored) + residual * worst
    most_likely = max(scored, key=lambda item: item[0])[3]
    return _Evaluation(hero_id, expected, worst, min(item[2] for item in scored),
                       most_likely, worst_item[3], value_model)


def _candidate_ids(policy: dict, draft: list[dict], metadata: dict, limit: int) -> list[int]:
    seen = {row["hero_id"] for row in draft}
    result = []
    for row in policy.get("candidates") or []:
        hero_id = row.get("hero_id")
        if isinstance(hero_id, int) and hero_id > 0 and hero_id not in seen and hero_id not in result:
            result.append(hero_id)
    for hero_id in metadata.get("hero_ids") or []:
        if isinstance(hero_id, int) and hero_id > 0 and hero_id not in seen and hero_id not in result:
            result.append(hero_id)
    return result[:limit]


def _build_realtime(request: dict, value_fn: Callable, policy_fn: Callable,
                    metadata: dict) -> tuple[dict, dict[int, int]]:
    side_map, draft = _validate_request(request, metadata)
    next_ord = len(draft)
    is_pick, team = resolve(next_ord, request["first_pick_team"])
    if team != side_map["us"]:
        raise DecisionUnavailable("invalid_request", "当前手不属于我方，不能生成我方整手建议")
    max_options = min(8, max(2, int(metadata.get("max_options", 3))))
    response_limit = 127
    current_policy = _ready_policy(policy_fn, request, draft, metadata, max_options)
    candidate_ids = _candidate_ids(current_policy, draft, metadata, max_options)
    if len(candidate_ids) < 2:
        raise DecisionUnavailable("insufficient_data", "候选不足，无法提供 fallback")
    evaluations = []
    skipped = []
    for hero_id in candidate_ids:
        try:
            evaluations.append(
                _evaluate_candidate(hero_id, request, draft, value_fn, policy_fn,
                                    metadata, response_limit)
            )
        except DecisionUnavailable as exc:
            if exc.code != "insufficient_data":
                raise
            skipped.append(f"{hero_id}: {exc.message}")
    if len(evaluations) < 2:
        diagnostic = "；".join(skipped) or "没有两个可完整评估的候选"
        raise DecisionUnavailable(
            "insufficient_data", f"可完整评估的候选不足 2 个，无法提供 fallback。{diagnostic}"
        )
    lam = _finite(metadata.get("robustness_lambda", 1.0), "robustness_lambda")
    if lam < 0:
        raise DecisionUnavailable("upstream_unavailable", "robustness_lambda 不能为负")
    ranked = sorted(evaluations, key=lambda item: (
        -(item.expected_wr - lam * max(0.0, item.expected_wr - item.worst_wr - .10)),
        -item.expected_wr, item.hero_id,
    ))
    options = []
    for item in ranked:
        robustness = max(0.0, item.expected_wr - item.worst_wr)
        penalized = item.expected_wr - lam * max(0.0, robustness - .10)
        fallback = [other.hero_id for other in ranked if other.hero_id != item.hero_id][:2]
        option = {
            "hero_id": item.hero_id,
            "expected_wr": round(item.expected_wr, 6),
            "robustness_delta": round(robustness, 6),
            "penalized_score": round(penalized, 6),
            "why": "按对手模型分布逐项调用 Value 后的期望结果",
            "fallback": fallback,
            "counterparty_plan": (
                f"对手若选择最差响应英雄 {item.worst_response_hero}，转向备选 "
                + " / ".join(str(hero) for hero in fallback)
                if item.worst_response_hero is not None else
                "对手后续分支不足，按当前局面复评并保留备选 "
                + " / ".join(str(hero) for hero in fallback)
            ),
        }
        if robustness > .10:
            option["risk_note"] = "对手偏离主要预测分支时，本方案的 Value 明显下降"
        options.append(option)
    body = {
        "next_ord": next_ord, "team": team, "is_pick": is_pick,
        "options": options,
        "assumptions": {"opponent_model": str(current_policy["model"]),
                        "value_model": ranked[0].value_model},
        "sources_used": [source for source in request["sources"]
                         if source in current_policy.get("sources_used", [])],
    }
    errors = check_advise(body, lam=lam)
    if errors:
        raise DecisionUnavailable("upstream_unavailable", errors[0])
    return body, {item.hero_id: item.n for item in ranked}


def _seed_to_us_pick(request: dict, policy_fn: Callable, metadata: dict) -> list[dict]:
    draft: list[dict] = []
    us_side = _side_map(request, metadata)["us"]
    while len(draft) < len(TEMPLATE):
        is_pick, team = resolve(len(draft), request["first_pick_team"])
        if is_pick and team == us_side:
            return draft
        body = _ready_policy(policy_fn, request, draft, metadata, 3)
        draft = _append_action(draft, request["first_pick_team"], int(body["candidates"][0]["hero_id"]))
    raise DecisionUnavailable("insufficient_data", "没有可规划的我方 pick 手")


def _parse_branches(values: Any) -> list[tuple[str, str]]:
    raw = values if values is not None else [f"{side}:{opening}" for side in ("us", "them") for opening in OPENINGS]
    if not isinstance(raw, list) or not raw:
        raise DecisionUnavailable("invalid_request", "branches 必须是非空数组")
    result = []
    for value in raw:
        if not isinstance(value, str) or value.count(":") != 1:
            raise DecisionUnavailable("invalid_request", "branch 必须采用 <先手>:<体系>")
        side, opening = value.split(":")
        if side not in ("us", "them") or opening not in OPENINGS or (side, opening) in result:
            raise DecisionUnavailable("invalid_request", "branch 含重复或未知取值")
        result.append((side, opening))
    return result


def _build_offline(request: dict, value_fn: Callable, policy_fn: Callable,
                   metadata: dict) -> dict:
    side_map = _side_map(request, metadata)
    branches = []
    sources_used = set(request["sources"])
    for index, (first_side, opening) in enumerate(_parse_branches(request.get("branches")), 1):
        branch_request = dict(request)
        branch_request["mode"] = "realtime"
        branch_request["first_pick_team"] = side_map[first_side]
        branch_request["their_opening"] = opening
        try:
            branch_request["draft"] = _seed_to_us_pick(branch_request, policy_fn, metadata)
            realtime, counts = _build_realtime(branch_request, value_fn, policy_fn, metadata)
        except DecisionUnavailable as exc:
            if exc.code == "insufficient_data":
                raise DecisionUnavailable(
                    exc.code, f"分支 {first_side}:{opening} 数据不足。{exc.message}"
                ) from exc
            raise
        plans = []
        for rank, option in enumerate(realtime["options"], 1):
            plan = {
                "label": f"{index}.{rank}",
                "goal": f"针对对手 {opening} 体系的 Value 搜索方案",
                "key_picks": [{"priority": 1, "hero_id": option["hero_id"],
                               "by_ord": realtime["next_ord"], "why": option["why"],
                               "fallback": option["fallback"]}],
                "expected_wr": option["expected_wr"], "n": counts[option["hero_id"]],
                "robustness_delta": option["robustness_delta"],
                "penalized_score": option["penalized_score"],
            }
            if "risk_note" in option:
                plan["risk_note"] = option["risk_note"]
            plans.append(plan)
        branches.append({"branch_id": f"{first_side}:{opening}",
                         "condition": {"first_pick": first_side, "their_opening": opening},
                         "plans": plans})
        sources_used &= set(realtime["sources_used"])
    body = {"branches": branches,
            "sources_used": [source for source in request["sources"] if source in sources_used]}
    errors = check_advise(body, lam=float(metadata.get("robustness_lambda", 1.0)))
    if errors:
        raise DecisionUnavailable("upstream_unavailable", errors[0])
    return body


def build_advise(request: dict, value_fn: Callable[[dict], dict],
                 policy_fn: Callable[[dict], dict], metadata: dict) -> dict:
    """构建实时整手建议或离线分支计划。"""
    mode = request.get("mode", "realtime")
    if mode == "realtime":
        return _build_realtime(request, value_fn, policy_fn, metadata)[0]
    if mode == "offline":
        _validate_request({**request, "draft": request.get("draft", [])}, metadata)
        return _build_offline(request, value_fn, policy_fn, metadata)
    raise DecisionUnavailable("invalid_request", "mode 只接受 realtime 或 offline")


def _bans_from_op(options: list[dict]) -> dict:
    must_ban, consider, bait = [], [], []
    for option in options:
        rec = option.get("recommendation")
        leave = option.get("if_we_leave") or {}
        if rec == "ban" and "their_wr" in leave:
            must_ban.append({"hero_id": option["hero_id"],
                             "why": "三种公开样本路径复算后 ban 的结果最高",
                             "their_wr": leave["their_wr"], "our_wr_against": leave["our_wr"],
                             "n": leave["n"]})
        elif rec not in (None, "insufficient_data"):
            consider.append({"hero_id": option["hero_id"], "why": "公开样本三路径比较",
                             "if_we_leave_it_open": option})
    return {"must_ban": must_ban, "consider": consider, "bait_candidates": bait}


def build_playbook(request: dict, value_fn: Callable[[dict], dict],
                   policy_fn: Callable[[dict], dict], metadata: dict) -> dict:
    """由同一决策搜索生成单局 Playbook，报告外壳来自只读 metadata。"""
    side_map = _side_map(request, metadata)
    series = metadata.get("series")
    if request.get("series_id") is not None and series is None:
        raise DecisionUnavailable("insufficient_data", "请求的系列赛没有可核查数据")
    if series is None:
        first_side = "us" if side_map["us"] == request.get("first_pick_team") else "them"
        offline_request = {**request, "mode": "offline",
                           "branches": [f"{first_side}:{opening}" for opening in OPENINGS]}
        advise = build_advise(offline_request, value_fn, policy_fn, metadata)
        branches = advise["branches"]
        sources_used = advise["sources_used"]
    else:
        branches = []
        sources_used = list(request["sources"])
        for game in series.get("games") or []:
            game_first = game.get("first_pick_team")
            if game_first not in (0, 1):
                continue
            first_side = "us" if side_map["us"] == game_first else "them"
            game_request = {**request, "mode": "offline", "first_pick_team": game_first,
                            "branches": [f"{first_side}:{opening}" for opening in OPENINGS]}
            game_advise = build_advise(game_request, value_fn, policy_fn, metadata)
            sources_used = [source for source in sources_used if source in game_advise["sources_used"]]
            for branch in game_advise["branches"]:
                branch = deepcopy(branch)
                branch["branch_id"] = f"g{game['game_no']}:{branch['branch_id']}"
                branch["applies_to_game"] = game["game_no"]
                branches.append(branch)
        if not branches:
            raise DecisionUnavailable("insufficient_data", "系列赛各局先手权均未知，不能生成分支")
    op = deepcopy(metadata.get("op_hero_decision") or [])
    body = {
        "matchup": {"us": deepcopy(metadata["team_refs"]["us"]),
                    "them": deepcopy(metadata["team_refs"]["them"]),
                    "patch": request["patch"], "first_pick_team": request["first_pick_team"],
                    "side_map": side_map},
        "sources_used": sources_used,
        "coverage": deepcopy(metadata["coverage"]),
        "data_quality": deepcopy(metadata["data_quality"]),
        "bans": _bans_from_op(op), "op_hero_decision": op,
        "branches": branches,
        "positions": deepcopy(metadata.get("positions") or []),
    }
    if series is not None:
        body["series"] = deepcopy(series)
    errors = check_playbook(body)
    if errors:
        raise DecisionUnavailable("upstream_unavailable", errors[0])
    return body
