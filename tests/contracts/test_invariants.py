"""契约不变式（规格 §6）的回归：阳性示例 + 每条守护的阴性用例。

阳性示例一律取自规格 §6 的代码块（去掉 `//` 注释后 `json.loads`，与
`/tmp` 的抽取脚本同源），并在测试内先用 `validator_for(<资源>)` 过 schema ——
规格示例同时在 schema 与不变式两层成立，是本轮的第一条验收线。

阴性用例**全部是 schema 合法的**：先 `validate` 通过，再断言 `check_*` 拒绝。
这样失败必然来自不变式而非形态，避免"测试其实被 schema 拦住"的假阳性。
"""
from __future__ import annotations
import copy
import json

import jsonschema
import pytest

from contracts.tools.invariants import (check_value, check_policy, check_advise,
                                        check_playbook, check_profile, check_resolve)


def _spec(text: str) -> dict:
    """规格示例：注释已剥离，其余逐字保留。"""
    return json.loads(text)


SPEC_VALUE = {"radiant_win_prob": 0.530, "contributions": [
    {"factor": "patch_strength", "delta": 0.011}, {"factor": "counter_matchup", "delta": -0.014},
    {"factor": "player_comfort", "delta": 0.024}, {"factor": "first_pick", "delta": 0.009}]}

# 规格 §6.2「→ 200」示例（真实比赛 8996973546 的前 12 手）
SPEC_POLICY = _spec("""
{
  "next_ord": 12,
  "team": 1,
  "is_pick": true,
  "candidates": [
    {"hero_id": 112, "prob": 0.180,
     "reasons": ["该队在此阶段的历史首选", "克制对方已选核心"],
     "evidence_match_ids": [8996973546, 8988636430]}
  ],
  "top_n": 10,
  "other_prob": 0.820,
  "model": "sequence-v1",
  "baseline": {"frequency_top1": 0.041, "model_top1": null},
  "sources_used": ["pro_match"]
}
""")

# 规格 §6.3「→ 200」示例（逐字，仅去注释）
SPEC_PLAYBOOK = _spec("""
{
  "matchup": {
    "us":   {"team_id": 10251056, "name": "Dawn Bulls", "tag": "DB"},
    "them": {"team_id": 10232231, "name": "Klim Sani4", "tag": "KS"},
    "patch": "7.41e",
    "first_pick_team": 0,
    "side_map": {"us": 0, "them": 1}
  },
  "sources_used": ["pro_match"],
  "coverage": {
    "pro_match": {"n_matches": 42, "n_stat_available": 38},
    "pub_match": {"n_matches": 0,  "n_stat_available": 0}
  },
  "data_quality": {
    "n_pending_draft": 1,
    "n_unavailable_draft": 0,
    "n_anomalous_draft": 2,
    "oldest_pending_hours": 31,
    "note": "1 场 BP 数据尚未就绪（OpenDota 约 2.6 天滞后），未计入统计"
  },
  "bans": {
    "must_ban": [{"hero_id": 55, "why": "对手签名英雄，我方无人擅长应对",
                  "their_wr": 0.71, "our_wr_against": 0.29, "n": 24}],
    "consider": [{"hero_id": 77, "why": "...",
                  "if_we_leave_it_open": {
                    "hero_id": 77,
                    "if_we_pick":  {"wr": 0.47, "n": 33},
                    "if_we_ban":   {"wr": 0.49, "n": 58},
                    "if_we_leave": {"our_wr": 0.55, "their_wr": 0.45, "n": 40,
                                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
                    "recommendation": "leave_and_counter"}}],
    "bait_candidates": [{"hero_id": 90, "why": "双方都不擅长，浪费对手 ban 位"}]
  },
  "op_hero_decision": [{
    "hero_id": 83,
    "if_we_pick":  {"wr": 0.44, "n": 34},
    "if_we_ban":   {"wr": 0.50, "n": 62},
    "if_we_leave": {"our_wr": 0.58, "their_wr": 0.42, "n": 41,
                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
    "recommendation": "leave_and_counter"
  }],
  "branches": [
    {
      "branch_id": "A",
      "applies_to_game": 1,
      "condition": {"first_pick": "us", "their_opening": "teamfight"},
      "plans": [{
        "label": "A1",
        "goal": "拖到中后期，靠分推拉扯",
        "key_picks": [{"priority": 1, "hero_id": 105, "by_ord": 13,
                       "why": "...", "fallback": [67, 19]}],
        "expected_wr": 0.52, "n": 31,
        "robustness_delta": 0.04,
        "penalized_score": 0.52
      }]
    },
    {
      "branch_id": "B",
      "applies_to_game": 2,
      "condition": {"first_pick": "them", "their_opening": "push"},
      "plans": [{
        "label": "B1",
        "goal": "对手先手时抢下反制核心",
        "key_picks": [{"priority": 1, "hero_id": 19, "by_ord": 12,
                       "why": "...", "fallback": [67]}],
        "expected_wr": 0.51, "n": 31,
        "robustness_delta": 0.05,
        "penalized_score": 0.51
      }]
    }
  ],
  "series": {
    "series_id": 1141522,
    "games": [
      {"game_no": 1, "first_pick_team": 0},
      {"game_no": 2, "first_pick_team": 1,
       "note": "第 1 局的负者获得第 2 局先手权；该值在第 1 局结束后才能确定，未确定时为 null——此时不得产出指向该局的 them 分支"}
    ],
    "game1_plan": "...",
    "adjustment_rules": [{"if": "对手第 1 局暴露推进体系", "then": "..."}]
  },
  "positions": [
    {"side": "them", "role": 4,
     "notes": [{"kind": "ward", "text": "...", "evidence": [],
                "value": null, "reason": "needs_replay", "needs": "Phase B"}]}
  ]
}
""")

# 规格 §6.4「→ 200」示例（逐字，仅去注释）
SPEC_PROFILE = _spec("""
{
  "team_id": 10232231,
  "patch": "7.41e",
  "as_of": "2026-09-16",
  "sources_used": ["pro_match", "pub_match"],
  "coverage": {"pro_match": {"n_matches": 42, "n_stat_available": 38,
                             "n_position_unknown": 5},
               "pub_match": {"n_matches": 310, "n_stat_available": 298,
                             "n_position_unknown": 12}},
  "players": [{
    "account_id": 123456,
    "name": "示例选手",
    "role": 4,
    "hero_pool": {
      "window_games": 48,
      "signature":   [{"hero_id": 55, "games": 12, "wr": 0.75, "pct": 25}],
      "comfortable": [{"hero_id": 77, "games": 8,  "wr": 0.50, "pct": 17}],
      "effective_count": 14,
      "presence_pick_rate": 0.81
    },
    "dimensions": {
      "hero_pool":     {"percentile": 88, "n": 120},
      "laning":        {"percentile": 71, "n": 120},
      "combat":        {"percentile": 64, "n": 120},
      "map_vision":    {"percentile": 47, "n": 120},
      "tempo":         {"percentile": 55, "n": 120},
      "hero_archetype":{"initiate": 0.24, "protect": 0.18, "push": 0.17,
                        "teamfight": 0.19, "pickoff": 0.13, "splitpush": 0.09}
    }
  }],
  "team_bp_tendency": {
    "first_phase_ban_freq": [{"hero_id": 55, "freq": 0.42, "n": 31}],
    "first_pick_freq":      [{"hero_id": 123, "freq": 0.28, "n": 25}],
    "ban_by_phase":         [{"ord": 9, "hero_id": 53, "freq": 0.31, "n": 29}]
  }
}
""")

# 规格 §6.4 的 ProfileRef 示例
SPEC_PROFILE_REF = _spec("""
{"team_id": 10232231, "name": "Klim Sani4", "tag": "KS", "logo_url": null}
""")

# 规格 §6.6「→ 200 (mode="realtime")」示例（逐字，仅去注释）
SPEC_ADVISE = _spec("""
{
  "next_ord": 13,
  "team": 0,
  "is_pick": true,
  "options": [
    {"hero_id": 105, "expected_wr": 0.552, "robustness_delta": 0.04,
     "penalized_score": 0.552,
     "why": "对手按预测分布应对时最优；换招后仍不劣于 0.51",
     "fallback": [67, 19],
     "counterparty_plan": "对手若抢 105，我方案转为 ..."},
    {"hero_id": 67,  "expected_wr": 0.548, "robustness_delta": 0.03,
     "penalized_score": 0.548,
     "why": "...", "fallback": [19, 105], "counterparty_plan": "..."},
    {"hero_id": 19,  "expected_wr": 0.556, "robustness_delta": 0.22,
     "penalized_score": 0.436,
     "risk_note": "原始胜率最高，但对手一旦不按预测出牌，本方案会明显劣化",
     "why": "...", "fallback": [67, 105], "counterparty_plan": "..."}
  ],
  "assumptions": {"opponent_model": "sequence-v1", "value_model": "value-v1"},
  "sources_used": ["pro_match"]
}
""")


def _op_hero_decision(pick, ban, leave, counter_wr=None, rec="pick", n=40):
    """按 §9.1 的三量构造一个 op_hero_decision[0]（n 一律充足，只考验复算）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    counters = [] if counter_wr is None else [{"hero_id": 36, "wr": counter_wr, "n": 31}]
    body["op_hero_decision"] = [{
        "hero_id": 83,
        "if_we_pick": {"wr": pick, "n": n},
        "if_we_ban": {"wr": ban, "n": n},
        "if_we_leave": {"our_wr": leave, "their_wr": round(1.0 - leave, 4), "n": n,
                        "our_counter_options": counters},
        "recommendation": rec,
    }]
    return body


# ── 规格示例：schema + 不变式两层都要通过 ────────────────────────────────

def test_spec_value_example_passes(validator_for):
    body = copy.deepcopy(SPEC_VALUE)
    body |= {"confidence": "high", "n_samples": 412, "sources_used": ["pro_match"]}
    validator_for("Value").validate(body)
    assert check_value(body) == []

def test_spec_policy_example_passes(validator_for):
    validator_for("Policy").validate(SPEC_POLICY)
    assert check_policy(SPEC_POLICY) == []

def test_spec_playbook_example_passes(validator_for):
    validator_for("Playbook").validate(SPEC_PLAYBOOK)
    assert check_playbook(SPEC_PLAYBOOK) == []

def test_spec_profile_example_passes(validator_for):
    validator_for("Profile").validate(SPEC_PROFILE)
    assert check_profile(SPEC_PROFILE) == []

def test_spec_profile_ref_example_passes(validator_for):
    validator_for("ProfileRef").validate(SPEC_PROFILE_REF)

def test_spec_advise_example_passes(validator_for):
    validator_for("Advise").validate(SPEC_ADVISE)
    assert check_advise(SPEC_ADVISE) == []


# ── §6.1 Value ────────────────────────────────────────────────────────────

def test_value_detects_the_original_round1_defect():
    """规格一轮抓到的原始错误：求和 0.054 而 radiant_win_prob-0.5 = 0.030。"""
    bad = {**SPEC_VALUE, "contributions": [
        {"factor": "patch_strength", "delta": 0.021}, {"factor": "counter_matchup", "delta": -0.014},
        {"factor": "player_comfort", "delta": 0.038}, {"factor": "first_pick", "delta": 0.009}]}
    assert check_value(bad) != []

def test_value_detects_unrequested_source():
    body = {**SPEC_VALUE, "sources_used": ["pro_match", "pub_match"]}
    assert check_value(body, requested_sources=["pro_match"]) != []

def test_value_rejects_non_finite_delta(validator_for):
    """I5：delta 无上下界，NaN 能过 schema；`abs(nan-x) > TOL` 恒 False，
    不加非有限扫描则求和校验静默通过。"""
    body = {**SPEC_VALUE, "contributions": [
        {"factor": "patch_strength", "delta": float("nan")}, {"factor": "counter_matchup", "delta": -0.014},
        {"factor": "player_comfort", "delta": 0.024}, {"factor": "first_pick", "delta": 0.009}]}
    validator_for("Value").validate(dict(body, confidence="high", n_samples=412, sources_used=[]))
    errs = check_value(body)
    assert any("非有限数值" in e and "contributions[0].delta" in e for e in errs), errs


# ── §6.2 Policy ───────────────────────────────────────────────────────────

def test_policy_rejects_non_finite_prob(validator_for):
    """I5：NaN 会让 `abs(sum - 1.0) > TOL` 静默为 False。
    注意 NaN 连 schema 的数值上下界都逃得掉（`minimum`/`maximum` 用比较实现，
    `nan < min` 与 `nan > max` 都是 False）—— 故只有 checker 这一层能拦。"""
    body = {**SPEC_POLICY,
            "candidates": [{"hero_id": 112, "prob": float("nan"), "reasons": ["x"],
                            "evidence_match_ids": []}]}
    validator_for("Policy").validate(body)          # NaN 过 schema（I5 的前提）
    errs = check_policy(body)
    assert any("非有限数值" in e and "candidates[0].prob" in e for e in errs), errs

def test_policy_names_the_reasonless_candidate(validator_for):
    """§6.2 reasons 非空：schema 有 minItems，checker 作为第二层并点出 hero_id。"""
    body = {**SPEC_POLICY, "candidates": [{"hero_id": 112, "prob": 0.180, "reasons": [],
                                           "evidence_match_ids": []}]}
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Policy").validate(body)
    errs = check_policy(body)
    assert any("hero 112" in e and "reasons" in e for e in errs), errs


# ── §6.4 Profile ──────────────────────────────────────────────────────────

def test_profile_rejects_five_category_distribution(validator_for):
    """规格四轮抓到的原始缺陷：只有 5 类且凑巧和为 1.0。
    schema 也会拦（HeroArchetype 要求六个键齐备），checker 的第二层要指出缺了哪个键。"""
    body = {"players": [{
        "account_id": 1,
        "name": "甲",
        "role": 4,                       # role 非 null ⇒ 不触发 §7.3 的降级联动
        "hero_pool": {"window_games": 20, "signature": [], "comfortable": [],
                      "effective_count": 6, "presence_pick_rate": 0.5},
        "dimensions": {
            "hero_pool": {"percentile": 50, "n": 100}, "laning": {"percentile": 50, "n": 100},
            "combat": {"percentile": 50, "n": 100}, "map_vision": {"percentile": 50, "n": 100},
            "tempo": {"percentile": 50, "n": 100},
            "hero_archetype": {"teamfight": 0.32, "push": 0.18, "pickoff": 0.21,
                               "splitpush": 0.11, "protect": 0.18}}}]}
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("键集不完整" in e and "initiate" in e for e in errs), errs

def test_profile_rejects_non_finite_archetype_weight(validator_for):
    """I5：NaN 权重同样让 `abs(sum - 1.0) > TOL` 静默为 False，
    且 NaN 能过 schema 的 `minimum`/`maximum`（比较对 NaN 恒假）——checker 是唯一防线。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["dimensions"]["hero_archetype"]["initiate"] = float("nan")
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("非有限数值" in e and "initiate" in e for e in errs), errs

def test_profile_rejects_effective_count_over_window_third(validator_for):
    """F5/§6.4：effective_count <= window_games / 3（示例 14 <= 48/3 = 16）。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["hero_pool"]["effective_count"] = 20   # 20 > 48/3
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("effective_count=20" in e and "48/3" in e for e in errs), errs

def test_profile_role_null_requires_degraded_position_dimensions(validator_for):
    """F5/§6.4+§7.3：role 推断不出时，五个依赖位置的维度必须降级为 insufficient_samples。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["role"] = None
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    for dim in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        assert any(dim in e and "insufficient_samples" in e for e in errs), (dim, errs)

def test_profile_role_null_accepts_all_five_degraded_dimensions(validator_for):
    """阴性用例的对照：五个维度都降级时 role=null 合法（hero_archetype 仍返回）。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["role"] = None
    degraded = {"value": None, "reason": "insufficient_samples"}
    for dim in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        body["players"][0]["dimensions"][dim] = copy.deepcopy(degraded)
    validator_for("Profile").validate(body)
    assert check_profile(body) == []


# ── §6.3 Playbook ─────────────────────────────────────────────────────────

def test_playbook_flags_opponent_by_ord_in_own_branch(validator_for):
    """规格 §6.3：`by_ord` 的归属必须与所属分支自洽（此处 ord 12 属于后手方）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]                                # 单局剧本
    body["branches"] = [body["branches"][0]]           # 只留分支 A（first_pick=us）
    body["branches"][0]["plans"][0]["key_picks"][0]["by_ord"] = 12
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("by_ord 12 属于对手" in e for e in errs), errs

def test_playbook_requires_key_pick_fallback(validator_for):
    """规格 §6.3：fallback 在 key_picks[] 内且非空 —— schema 与 check 两层都拦。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]
    body["branches"] = [body["branches"][0]]
    kp = body["branches"][0]["plans"][0]["key_picks"][0]
    del kp["fallback"]                                # 整字段缺失：schema 已拦
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Playbook").validate(body)
    assert any("缺少 fallback" in e for e in check_playbook(body))
    kp["fallback"] = []                               # 空数组：schema 也拦（minItems:1）
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Playbook").validate(body)
    assert any("缺少 fallback" in e for e in check_playbook(body))

def test_playbook_nested_consider_enforces_leave_wr_pair(validator_for):
    """F1：`consider[].if_we_leave_it_open` 与 `op_hero_decision[]` 同形（OpHeroOption），
    our_wr + their_wr == 1.0 必须同样生效（此前只查后者）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["bans"]["consider"][0]["if_we_leave_it_open"]["if_we_leave"]["their_wr"] = 0.50
    validator_for("Playbook").validate(body)      # schema 不看这条等式
    errs = check_playbook(body)
    assert any("bans.consider[0].if_we_leave_it_open" in e and "their_wr" in e for e in errs), errs

def test_playbook_series_branch_requires_applies_to_game(validator_for):
    """F2/§6.3：系列赛剧本（响应含 series）**每个**分支都必须带 applies_to_game；
    且失败信息不得再误称"单局剧本"。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["branches"][1]["applies_to_game"]
    validator_for("Playbook").validate(body)      # applies_to_game 是可选字段
    errs = check_playbook(body)
    assert any("分支 B 缺少 applies_to_game" in e for e in errs), errs
    assert not any("单局剧本" in e for e in errs), errs

def test_playbook_single_game_branch_keeps_single_game_message(validator_for):
    """对照：响应不含 series 时，先手权不一致仍报"单局剧本"（该措辞只在此时正确）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]
    body["branches"] = [body["branches"][0]]        # 只留分支 A（"us" 与 expected 一致）
    validator_for("Playbook").validate(body)
    assert check_playbook(body) == []
    body["branches"][0]["condition"]["first_pick"] = "them"   # 单局剧本里不允许
    errs = check_playbook(body)
    assert any("单局剧本" in e for e in errs), errs

def test_playbook_rejects_applies_to_game_pointing_at_null_first_pick():
    """§6.3：该局 first_pick_team 为 null 时无法推导 expected，不得作为 applies_to_game 的目标。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["series"]["games"][1]["first_pick_team"] = None
    errs = check_playbook(body)
    assert any("分支 B" in e and "null" in e for e in errs), errs

def test_playbook_n_below_threshold_must_degrade(validator_for):
    """F3/§9.1：逐量 n >= 30。n=4 的量必须降级，且 recommendation 必须是 insufficient_data。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["op_hero_decision"][0]["if_we_pick"] = {"wr": 0.44, "n": 4}
    validator_for("Playbook").validate(body)      # n 无下界，schema 放行
    errs = check_playbook(body)
    assert any("if_we_pick 的 n=4 < 30 却未降级" in e for e in errs), errs
    assert any("应为 insufficient_data" in e for e in errs), errs

def test_playbook_degraded_quantity_forces_insufficient_data(validator_for):
    """F3/§6.3：任一量降级 ⇒ recommendation 必须是 insufficient_data。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["op_hero_decision"][0]["if_we_ban"] = {"value": None, "reason": "insufficient_samples"}
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("if_we_ban(已降级)" in e and "insufficient_data" in e for e in errs), errs

def test_playbook_rejects_recommendation_contradicting_9_1(validator_for):
    """F3：三量都充分时必须按 §9.1 复算 —— 下面这组应为 pick, 却写 leave_and_counter。"""
    body = _op_hero_decision(0.60, 0.50, 0.45, counter_wr=0.61, rec="leave_and_counter")
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("与 §9.1 复算的 pick 不符" in e for e in errs), errs

@pytest.mark.parametrize("pick,ban,leave,counter,expected,why", [
    (0.60, 0.50, 0.45, None, "pick", "wr_pick 最大且领先 0.10"),
    (0.45, 0.60, 0.50, None, "ban", "wr_ban 最大"),
    (0.44, 0.50, 0.58, 0.61, "leave_and_counter", "规格 §6.3 示例验算"),
    (0.45, 0.48, 0.58, 0.55, "ban", "leave 最大但 wr_counter 未超过 wr_leave → 退化 ban"),
    (0.45, 0.48, 0.58, None, "ban", "leave 最大但无反制选项可取 wr_counter → 退化 ban"),
    (0.55, 0.54, 0.50, None, "insufficient_data", "最大-次大 = 0.01 < 0.02 噪声窗"),
    (0.50, 0.50, 0.45, None, "insufficient_data", "并列最大（差 0 < 0.02）"),
])
def test_spec_9_1_recompute_matches_recommendation(validator_for, pick, ban, leave, counter,
                                                   expected, why):
    """§9.1 复算的七种出口都必须被接受（否则守护会退化成"一律报错"）。"""
    body = _op_hero_decision(pick, ban, leave, counter_wr=counter, rec=expected)
    validator_for("Playbook").validate(body)
    assert check_playbook(body) == [], (why, check_playbook(body))

@pytest.mark.parametrize("wrong", ["pick", "ban", "leave_and_counter"])
def test_spec_9_1_noise_window_rejects_any_confident_recommendation(validator_for, wrong):
    """§9.1：0.01 的噪声窗内只能给 insufficient_data（F3 的第三个变体）。"""
    body = _op_hero_decision(0.55, 0.54, 0.50, counter_wr=None, rec=wrong)
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any(f"recommendation={wrong} 与 §9.1 复算的 insufficient_data 不符" in e
               for e in errs), errs

def test_playbook_reports_all_offenders_with_location(validator_for):
    """报错必须点名位置（两处 OpHeroOption 同形，只有位置能区分）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["bans"]["consider"][0]["if_we_leave_it_open"]["recommendation"] = "pick"
    body["op_hero_decision"][0]["if_we_leave"]["their_wr"] = 0.50
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any(e.startswith("bans.consider[0].if_we_leave_it_open hero 77") for e in errs), errs
    assert any(e.startswith("op_hero_decision[0] hero 83") for e in errs), errs


# ── §6.6 Advise ───────────────────────────────────────────────────────────

def test_advise_handles_offline_branches_shape():
    """offline 模式返回 branches[].plans[]，不含 options —— 不得 KeyError。"""
    body = {"branches": [{"branch_id": "A", "condition": {"first_pick": "us", "their_opening": "teamfight"},
                          "plans": [{"label": "A1", "expected_wr": 0.52, "robustness_delta": 0.04,
                                     "penalized_score": 0.52,
                                     "key_picks": [{"hero_id": 105, "fallback": [67]}]}]}]}
    assert check_advise(body) == []

def test_advise_rejects_empty_counterparty_plan(validator_for):
    """F6/§6.6：每项必须有非空 counterparty_plan —— schema（minLength）与 check 两层都拦。"""
    body = copy.deepcopy(SPEC_ADVISE)
    body["options"][0]["counterparty_plan"] = ""
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Advise").validate(body)
    errs = check_advise(body)
    assert any("counterparty_plan 为空" in e for e in errs), errs

def test_advise_rejects_non_finite_score(validator_for):
    """I5：NaN 的 expected_wr 会让降权等式静默通过；NaN 也能过 schema 的数值界，
    故 checker 的非有限扫描是这条不变式的唯一防线。"""
    body = copy.deepcopy(SPEC_ADVISE)
    body["options"][0]["expected_wr"] = float("nan")
    validator_for("Advise").validate(body)
    errs = check_advise(body)
    assert any("非有限数值" in e and "options[0].expected_wr" in e for e in errs), errs

def test_advise_lam_follows_app_config_robustness_lambda():
    """§6.6：λ 取自 app_config.robustness_lambda（默认 1.0），可配置。"""
    body = copy.deepcopy(SPEC_ADVISE)
    assert check_advise(body) == []
    body["options"][2]["penalized_score"] = 0.556 - 2.0 * (0.22 - 0.10)   # λ=2 下的正确值
    assert check_advise(body, lam=2.0) == []
    assert check_advise(body) != []


# ── §6.0 resolve()：check_resolve 的单测（此前零执行覆盖） ────────────────

def test_check_resolve_accepts_spec_6_2_example():
    """§6.2 示例：first_pick_team=0，第 12 手由后手方 pick → team=1。"""
    assert check_resolve(next_ord=12, team=1, is_pick=True, first_pick_team=0) == []

def test_check_resolve_rejects_wrong_team_and_type():
    errs = check_resolve(next_ord=12, team=0, is_pick=False, first_pick_team=0)
    assert any("归属应为 team 1" in e for e in errs), errs
    assert any("应为 pick" in e for e in errs), errs

def test_check_resolve_rejects_non_finite_argument():
    """I5 同族：NaN 会让 resolve() 抛 TypeError，必须在入口拦下。"""
    errs = check_resolve(next_ord=float("nan"), team=1, is_pick=True, first_pick_team=0)
    assert any("非有限数值" in e for e in errs), errs
