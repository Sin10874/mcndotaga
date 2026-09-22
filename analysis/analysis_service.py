"""公开对局分析的应用编排与冻结响应校验。"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from time import monotonic

from jsonschema import Draft202012Validator
import yaml

from analysis.analysis_repository import load_analysis_catalog, load_matchup_summary
from analysis.profile_service import ProfileServiceError
from contracts.tools.invariants import check_value, check_policy, check_advise, check_playbook
from shared.draft_template import resolve


def error(code, message):
    return {"error": {"code": code, "message": message}}


@lru_cache(maxsize=1)
def validators():
    doc = yaml.safe_load((Path(__file__).parents[1] / "contracts/openapi.yaml").read_text())
    root = Draft202012Validator(doc)
    return {name: root.evolve(schema=doc["components"]["schemas"][name]) for name in ("Value", "Policy", "Advise", "Playbook")}


def checked(resource, body, request, *, lam=1.0):
    if body.get("error"):
        return body
    validators()[resource].validate(body)
    checkers = {"Value": lambda: check_value(body, request["sources"]),
                "Policy": lambda: check_policy(body), "Advise": lambda: check_advise(body, lam=lam),
                "Playbook": lambda: check_playbook(body)}
    if checkers[resource]() or not set(body["sources_used"]).issubset(request["sources"]):
        raise ProfileServiceError("upstream_unavailable", "分析结果未通过契约校验")
    if resource == "Policy" or resource == "Advise" and "options" in body:
        next_ord = len(request["draft"])
        pick, team = resolve(next_ord, request["first_pick_team"])
        if (body["next_ord"], body["is_pick"], body["team"]) != (next_ord, pick, team):
            raise ProfileServiceError("upstream_unavailable", "响应与当前 BP 手数不一致")
        used = {action["hero_id"] for action in request["draft"]}
        options = body.get("candidates", body.get("options", []))
        if any(row["hero_id"] in used or set(row.get("fallback", [])) & used for row in options):
            raise ProfileServiceError("upstream_unavailable", "候选或替代英雄与已发生 BP 冲突")
    return body


def as_policy_request(request):
    return {"patch": request["patch"], "as_of": request["as_of"], "sources": request["sources"],
            "first_pick_team": request["first_pick_team"], "draft": request["draft"], "top_n": request.get("top_n", 10),
            "radiant_team_id": request["us"] if request["us_side"] == 0 else request["them"],
            "dire_team_id": request["us"] if request["us_side"] == 1 else request["them"]}


def as_value_request(request):
    policy = as_policy_request(request)
    return {"patch": request["patch"], "as_of": request["as_of"], "sources": request["sources"],
            "first_pick_team": request["first_pick_team"],
            **{label: {"team_id": policy[label + "_team_id"],
                       "heroes": [{"hero_id": row["hero_id"]} for row in request["draft"] if row["is_pick"] and row["team"] == side]}
               for side, label in enumerate(("radiant", "dire"))}}


def cached_callback(callback):
    cache = {}
    def call(request):
        key = json.dumps(request, sort_keys=True)
        if key not in cache:
            cache[key] = callback(request)
        return cache[key]
    return call


def load_analysis(dsn, resource, request):
    if resource == "catalog":
        return load_analysis_catalog(dsn)
    from analysis.value_engine import build_value_analysis
    from analysis.value_repository import load_value, prepare_value
    from analysis.policy_engine import build_policy, PolicyRequestError
    from analysis.policy_repository import load_policy, prepare_policy
    from analysis.decision_engine import build_advise, build_playbook, DecisionUnavailable
    from analysis.decision_repository import load_decision_metadata
    if resource == "value":
        return checked("Value", load_value(dsn, request), request)
    if resource == "policy":
        try:
            return checked("Policy", load_policy(dsn, request), request)
        except PolicyRequestError as exc:
            return error(exc.code, exc.message)
    started = monotonic()
    summary = load_matchup_summary(dsn, request)
    metadata = load_decision_metadata(dsn, request)
    value_request = as_value_request(request)
    policy_request = as_policy_request(request)
    value_dataset, value_artifact = prepare_value(dsn, value_request)
    def evaluate_value(value_input):
        result = build_value_analysis(value_input, value_dataset, value_artifact)
        # 实验展示与决策门分开。已知比固定胜率基准更差的模型不进入搜索。
        model_metadata = result.get("model") or {}
        if model_metadata.get("validation", {}).get("log_loss", float("inf")) >= model_metadata.get("baseline", {}).get("log_loss", float("inf")):
            result = {**result, "model": None}
        return result
    value_fn = cached_callback(evaluate_value)
    value_analysis = build_value_analysis(value_request, value_dataset, value_artifact)
    full_draft = len(request["draft"]) == 24
    preparation_request = {**policy_request, "draft": []} if full_draft else policy_request
    try:
        dataset, model, evaluation = prepare_policy(dsn, preparation_request)
        policy_failure = None
    except PolicyRequestError as exc:
        dataset, model = {"metadata": {}}, None
        evaluation = {"ready": False, "quality_gate": exc.code}
        policy_failure = error(exc.code, exc.message)
    def evaluate_policy(policy_input):
        if policy_failure:
            return policy_failure
        try:
            return build_policy(dataset, policy_input, model=model, evaluation=evaluation)
        except PolicyRequestError as exc:
            return error(exc.code, exc.message)
    policy_fn = cached_callback(evaluate_policy)

    def decision(fn, current_request):
        try:
            body = fn(current_request, value_fn, policy_fn, metadata)
            return checked("Playbook" if fn == build_playbook else "Advise", body, current_request, lam=metadata["robustness_lambda"])
        except (DecisionUnavailable, PolicyRequestError) as exc:
            return error(exc.code, exc.message)

    if resource == "advise":
        return decision(build_advise, request)
    if resource == "playbook":
        return decision(build_playbook, request)
    value = checked("Value", value_analysis["value"], value_request)
    if full_draft:
        policy = error("insufficient_data", "24 手 BP 已完成，没有下一手")
        advice = error("insufficient_data", "BP 已完成，请查看最终阵容的局面评估")
    else:
        policy = checked("Policy", policy_fn(policy_request), policy_request)
        _, acting_side = resolve(len(request["draft"]), request["first_pick_team"])
        advice = decision(build_advise, request) if acting_side == request["us_side"] else error("insufficient_data", "当前轮到对手，请先查看下一手候选；录入这一手后再生成我方建议")
    # 剧本是赛前条件搜索，输入前缀不作为已发生的固定路径。
    playbook = decision(build_playbook, {**request, "draft": []})
    return {
        "request": request, "matchup_summary": summary, "decision_mode": "experimental", "release_ready": False,
        "value": value, "policy": policy, "advise": advice, "playbook": playbook,
        "value_diagnostics": {"detail": value_analysis.get("detail"), "model": value_analysis.get("model")},
        "policy_diagnostics": {"evaluation": evaluation, "data": dataset.get("metadata")},
        "decision_diagnostics": {key: metadata[key] for key in ("pool_basis", "coverage", "data_quality", "op_hero_decision")},
        "elapsed_seconds": round(monotonic() - started, 3),
        "notes": [
            "局面与决策来自实验模型。样本等级描述数据数量，不代表胜率已经校准或建议已通过实战验证。",
            "本轮模型改动复用了已经查看的时间留出集，当前用于交互与开发测试；正式质量验收需要新的独立样本。",
            "真实历史 BP 前缀仅作为输入。当前截点的模型可能已经见过该场比赛，不能把本次交互当作独立回测。",
            "英雄候选池缺少版本快照时使用当前常量；不宣称已恢复历史 CM 可选池。",
            "选手条件对位逐量要求 30 场。样本不足时贡献为零并说明原因，OP 三选一保留不足状态。",
        ],
    }
