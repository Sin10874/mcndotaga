"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。"""
from __future__ import annotations
from shared.draft_template import resolve

TOL = 1e-3

# check_resolve 校验 §6.2/§6.6 的 next_ord/team/is_pick 推导。
# **本 chunk 的 fixtures 是纯响应体，不含请求上下文**，故无法在 fixture 层调用它；
# 它由 Plan 2+ 的接口集成测试使用（那里能同时拿到请求与响应）。
# 推导逻辑本身已在 Task 4 的 shared/draft_template.py 中受测。
def check_resolve(next_ord: int, team: int, is_pick: bool, first_pick_team: int) -> list[str]:
    exp_pick, exp_team = resolve(next_ord, first_pick_team)
    errs = []
    if bool(exp_pick) != bool(is_pick):
        errs.append(f"ord {next_ord} 类型应为 {'pick' if exp_pick else 'ban'}")
    if exp_team != team:
        errs.append(f"ord {next_ord} 归属应为 team {exp_team}，实际 {team}")
    return errs

def check_value(body: dict, requested_sources: list[str] | None = None) -> list[str]:
    errs = []
    total = sum(c["delta"] for c in body["contributions"])
    want = body["radiant_win_prob"] - 0.5
    if abs(total - want) > TOL:
        errs.append(f"contributions 求和 {total:.4f} != radiant_win_prob-0.5 {want:.4f}")
    if requested_sources is not None:
        extra = set(body.get("sources_used", [])) - set(requested_sources)
        if extra:
            errs.append(f"sources_used 含未请求的来源: {sorted(extra)}")
    return errs

def check_policy(body: dict) -> list[str]:
    errs = []
    s = sum(c["prob"] for c in body["candidates"]) + body["other_prob"]
    if abs(s - 1.0) > TOL:
        errs.append(f"sum(candidates.prob)+other_prob = {s:.4f} != 1.0")
    if len(body["candidates"]) > body["top_n"]:
        errs.append(f"len(candidates)={len(body['candidates'])} > top_n={body['top_n']}")
    if any(not c.get("reasons") for c in body["candidates"]):
        errs.append("存在 reasons 为空的候选")
    return errs

def check_playbook(body: dict) -> list[str]:
    errs = []
    mu = body["matchup"]
    us_team = mu["side_map"]["us"]
    want_first = "us" if us_team == mu["first_pick_team"] else "them"
    # 系列赛剧本允许分支覆盖两种先手权（规格 §6.3）：
    # 此时分支必须带 applies_to_game 指向 series.games[] 中某一局。
    games = {g["game_no"]: g.get("first_pick_team")
             for g in (body.get("series") or {}).get("games", [])}
    for br in body["branches"]:
        fp = br["condition"]["first_pick"]
        game_no = br.get("applies_to_game")
        if games and game_no is not None:
            g_fpt = games.get(game_no)
            if g_fpt is None:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 不在 series.games 中")
                br_fpt = mu["first_pick_team"]
            else:
                exp = "us" if us_team == g_fpt else "them"
                if fp != exp:
                    errs.append(f"分支 {br['branch_id']} 在第 {game_no} 局的 first_pick 应为 {exp}")
                br_fpt = g_fpt
        else:
            if fp != want_first:
                errs.append(f"分支 {br['branch_id']} 的 first_pick 应为 {want_first}"
                            f"（单局剧本；若要覆盖另一先手权，需带 series 与 applies_to_game）")
            br_fpt = mu["first_pick_team"]
        for pl in br["plans"]:
            kps = pl.get("key_picks") or []
            if not kps:
                errs.append(f"plan {pl['label']} 的 key_picks 为空")
            for kp in kps:
                # fallback 位于 key_picks[] 内（规格 §6.3 示例），不是 plan 级字段
                if not kp.get("fallback"):
                    errs.append(f"plan {pl['label']} 的 key_pick(by_ord {kp.get('by_ord')}) 缺少 fallback")
                _, owner_team = resolve(kp["by_ord"], br_fpt)
                if owner_team != us_team:
                    errs.append(f"plan {pl['label']}: by_ord {kp['by_ord']} 属于对手，"
                                f"与分支 first_pick={fp} 不符")
    for oh in body.get("op_hero_decision", []):
        lv = oh["if_we_leave"]
        if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
            errs.append(f"hero {oh['hero_id']}: our_wr + their_wr != 1.0")
    return errs

def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    """支持两种 mode：realtime 返回 options[]，offline 返回 branches[].plans[]。"""
    errs = []
    if "options" in body:
        groups = [("options", body["options"])]
    elif "branches" in body:
        # 规格 §6.6 只要求 options[] 有序；offline 的 plans[] 按**分支内**排序，
        # 不跨分支比较（不同分支的 expected_wr 不可比）。
        groups = [(br["branch_id"], br["plans"]) for br in body["branches"]]
    else:
        return ["advise 响应既无 options 也无 branches"]
    for gname, items in groups:
        scores = [o["penalized_score"] for o in items]
        if scores != sorted(scores, reverse=True):
            errs.append(f"{gname}: 未按 penalized_score 降序")
        for o in items:
            key = o.get("hero_id", o.get("label"))
            exp = o["expected_wr"] - lam * max(0.0, o["robustness_delta"] - 0.10)
            if abs(exp - o["penalized_score"]) > TOL:
                errs.append(f"{gname}/{key}: penalized_score {o['penalized_score']} != {exp:.4f}")
            if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
                errs.append(f"{gname}/{key}: robustness_delta > 0.10 但缺 risk_note")
            # realtime 的 fallback 在 option 级；offline 的在 key_picks[] 内
            if "fallback" in o and not o["fallback"]:
                errs.append(f"{gname}/{key}: fallback 为空")
    return errs

_ARCHETYPES = {"initiate","protect","push","teamfight","pickoff","splitpush"}

def check_profile(body: dict) -> list[str]:
    errs = []
    for p in body["players"]:
        ha = p["dimensions"]["hero_archetype"]
        if set(ha) != _ARCHETYPES:
            errs.append(f"player {p['account_id']}: hero_archetype 键集不完整")
        elif abs(sum(ha.values()) - 1.0) > TOL:
            errs.append(f"player {p['account_id']}: hero_archetype 和 != 1.0")
    return errs
