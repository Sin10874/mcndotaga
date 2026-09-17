"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。

**前置条件**：传入的 `body` 必须**已通过 schema 校验**（`contracts/openapi.yaml`
里对应的资源组件）。本模块只负责 JSON Schema 表达不了的那一半：

- 求和/等式——§6.1 的 delta 求和、§6.2 的概率求和、§6.3 的 `our_wr + their_wr`、
  §6.6 的降权等式（JSON Schema 没有算术）。
- 跨字段条件——§6.3 的先手权/`by_ord` 自洽、§9.1 的逐量门槛与 `recommendation`
  复算、§6.4 的 `role` 与降级联动（需要先算再判）。
- 跨条目一致性——有序性、非空列表、键集完整性、`applies_to_game` 的指向。
- 值的来源——`sources_used` ⊆ 请求的 `sources`。
- 非有限数值——`NaN`/`Infinity` 会让一切算术静默通过（见 `_nonfinite_errors`）。

形态类约束（required/enum/range/降级形态/对象封闭性/oneOf 判别）由 schema 负责。
本模块里少数与 schema 重复的形态判断是**防御性的**：把 live 响应直接喂进来时
也能得到一条可读错误，而不是 KeyError——不构成第二套定义。
"""
from __future__ import annotations
import math
from shared.draft_template import resolve

# 容差：规格 §6.1「容差 ±0.001」、§6.2「容差 ±0.001」、§6.3「±0.001」、
# §6.6「（±0.001）」——四处一致，故只此一个常量，不得各写各的。
TOL = 1e-3


def _nonfinite_errors(node, path: str = "$") -> list[str]:
    """递归找出 NaN/±Infinity（I5）。

    JSON 标准不允许裸 `NaN`/`Infinity`，但 `json.loads` 默认接受它们，而
    `abs(nan - x) > TOL` 恒为 False —— 所有求和/等式校验都会被**静默绕过**。
    故每个 `check_*` 都在最前面扫一遍；发现非有限数值即提前返回：
    此时一切算术都无意义（`resolve(by_ord=nan)` 还会直接抛 TypeError）。
    """
    if isinstance(node, float) and not math.isfinite(node):
        return [f"{path}: 非有限数值 {node}（NaN/Infinity 会让求和校验静默通过）"]
    errs: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            errs += _nonfinite_errors(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            errs += _nonfinite_errors(v, f"{path}[{i}]")
    return errs


# check_resolve 校验 §6.2/§6.6 的 next_ord/team/is_pick 推导。
# **本 chunk 的 fixtures 是纯响应体，不含请求上下文**，故无法在 fixture 层调用它；
# 它由 Plan 2+ 的接口集成测试使用（那里能同时拿到请求与响应）。
# 推导逻辑本身已在 Task 4 的 shared/draft_template.py 中受测。
def check_resolve(next_ord: int, team: int, is_pick: bool, first_pick_team: int) -> list[str]:
    errs = _nonfinite_errors([next_ord, team, is_pick, first_pick_team], "resolve 参数")
    if errs:
        return errs
    exp_pick, exp_team = resolve(next_ord, first_pick_team)
    if bool(exp_pick) != bool(is_pick):
        errs.append(f"ord {next_ord} 类型应为 {'pick' if exp_pick else 'ban'}")
    if exp_team != team:
        errs.append(f"ord {next_ord} 归属应为 team {exp_team}，实际 {team}")
    return errs


def check_value(body: dict, requested_sources: list[str] | None = None) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
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
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    s = sum(c["prob"] for c in body["candidates"]) + body["other_prob"]
    if abs(s - 1.0) > TOL:
        errs.append(f"sum(candidates.prob)+other_prob = {s:.4f} != 1.0")
    if len(body["candidates"]) > body["top_n"]:
        errs.append(f"len(candidates)={len(body['candidates'])} > top_n={body['top_n']}")
    for c in body["candidates"]:
        # schema 有 minItems:1，这里是防御性重复（live 响应可直接喂入）
        if not c.get("reasons"):
            errs.append(f"candidate hero {c.get('hero_id')}: reasons 为空（§6.2：不得返回无依据的候选）")
    return errs


def check_playbook(body: dict) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    mu = body["matchup"]
    us_team = mu["side_map"]["us"]
    want_first = "us" if us_team == mu["first_pick_team"] else "them"
    # 先手权自洽（§6.3）：单局剧本所有分支必须等于 expected；
    # 系列赛剧本（响应含 series）允许两种先手权，但**每个**分支都必须带
    # applies_to_game 指向 series.games[] 中的某一局。
    series = body.get("series")
    games = {g["game_no"]: g.get("first_pick_team")
             for g in (series or {}).get("games") or []}
    for br in body["branches"]:
        fp = br["condition"]["first_pick"]
        game_no = br.get("applies_to_game")
        if series is not None:
            if game_no is None:
                errs.append(f"分支 {br['branch_id']} 缺少 applies_to_game："
                            f"系列赛剧本的每个分支都必须指向 series.games[] 中的某一局（§6.3）")
                br_fpt = mu["first_pick_team"]
            elif game_no not in games:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 不在 series.games 中")
                br_fpt = mu["first_pick_team"]
            elif games[game_no] is None:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 指向的"
                            f"first_pick_team 为 null，无法推导 expected，不得作为目标（§6.3）")
                br_fpt = mu["first_pick_team"]
            else:
                g_fpt = games[game_no]
                exp = "us" if us_team == g_fpt else "them"
                if fp != exp:
                    errs.append(f"分支 {br['branch_id']} 在第 {game_no} 局的 first_pick 应为 {exp}")
                br_fpt = g_fpt
        else:
            if fp != want_first:
                errs.append(f"分支 {br['branch_id']} 的 first_pick 应为 {want_first}"
                            f"（单局剧本：响应不含 series；若要覆盖另一先手权，"
                            f"需带 series 与 applies_to_game）")
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
    # consider[].if_we_leave_it_open 与 op_hero_decision[] 同形（§6.3），共用一份检查
    for i, bc in enumerate((body.get("bans") or {}).get("consider") or []):
        _check_op_hero_option(bc["if_we_leave_it_open"],
                              f"bans.consider[{i}].if_we_leave_it_open", errs)
    for i, oh in enumerate(body.get("op_hero_decision") or []):
        _check_op_hero_option(oh, f"op_hero_decision[{i}]", errs)
    return errs


# ── §9.1 的逐量门槛与 recommendation 复算 ────────────────────────────────
_N_MIN = 30        # §9.1：三个胜率量各自的 n >= 30（§6.3：「逐量判定，不是逐条目判定」）
_OP_DELTA = 0.02   # §9.1：最大与次大之差 < 0.02 → 噪声内（app_config.op_decision_min_delta 默认值）


def _wr_n(q: dict) -> tuple[float | None, int | None]:
    """取一个胜率量的 (胜率, n)。

    `if_we_pick`/`if_we_ban` 是 `WinRateSample`（`wr`），`if_we_leave` 是
    `LeaveEvaluation`（`our_wr`）——**两者形状不同**（§6.3），故分开取。
    降级形态（§6.0 第一层，只有 value/reason/needs）没有胜率 → (None, None)。
    """
    if "wr" in q:
        return q["wr"], q.get("n")
    if "our_wr" in q:
        return q["our_wr"], q.get("n")
    return None, None


def _max_counter_wr(leave: dict) -> float | None:
    """`wr_counter` = 我方反制选项的最高胜率（§9.1）。全部降级时无值 → None。"""
    wrs = [c["wr"] for c in leave.get("our_counter_options") or [] if "wr" in c]
    return max(wrs) if wrs else None


def _check_op_hero_option(oh: dict, where: str, errs: list[str]) -> None:
    """OpHeroOption 的两处使用共用（§6.3：`consider[].if_we_leave_it_open` 与
    `op_hero_decision[]` **同形**，不得各写一套）——一处修好，两处生效。"""
    tag = f"{where} hero {oh['hero_id']}"
    lv = oh["if_we_leave"]
    if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
        errs.append(f"{tag}: our_wr + their_wr != 1.0（同一批比赛的两个视角，§6.3）")
    errs.extend(_recommendation_errors(oh, tag))


def _recommendation_errors(oh: dict, tag: str) -> list[str]:
    """§9.1 的数值判定规则，逐字对应规格伪代码（规格 §9.1 原文）：

        if 任一项 n < 30:                      -> insufficient_data
        elif max(wr_pick, wr_ban, wr_leave) - second_max < 0.02: -> insufficient_data
        elif wr_pick 为最大:                    -> pick
        elif wr_ban  为最大:                    -> ban
        else:                                   -> leave_and_counter
                                                （并要求 wr_counter > wr_leave，否则退化为 ban）

    `wr_pick`/`wr_ban`/`wr_leave` 是三个量各自的胜率（`if_we_leave` 取 `our_wr`；
    `their_wr` 是"对手拿的胜率"，只用于 `our_wr + their_wr == 1.0`，不参与比较）。
    规格未写明的一处（本实现取保守解并在此声明）：三个量**全部或部分降级**时
    无法取 max，按第一条判为 `insufficient_data`；`if_we_leave` 为最大但
    `our_counter_options[]` 全部降级时取不到 `wr_counter`，按"否则退化为 ban"处理。
    """
    errs: list[str] = []
    wr: dict[str, float] = {}
    short: list[str] = []
    for qname in ("if_we_pick", "if_we_ban", "if_we_leave"):
        w, n = _wr_n(oh[qname])
        if w is None:            # 已是降级形态：n 不可得 ⇒ §9.1 视作 n < 30
            short.append(f"{qname}(已降级)")
        elif n is None or n < _N_MIN:
            errs.append(f"{tag}: {qname} 的 n={n} < {_N_MIN} 却未降级"
                        f"（§9.1 的门槛逐量判定，不是逐条目判定）")
            short.append(f"{qname}(n={n})")
        else:
            wr[qname] = w
    rec = oh["recommendation"]
    if short:
        if rec != "insufficient_data":
            errs.append(f"{tag}: {'、'.join(short)} 样本不足，"
                        f"recommendation={rec} 应为 insufficient_data（§9.1）")
        return errs
    ranked = sorted(wr.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top = ranked[0]
    second_name, second = ranked[1]
    if top - second < _OP_DELTA:
        expected = "insufficient_data"
        why = (f"最大 {top_name}={top:.4f} 与次大 {second_name}={second:.4f} 之差 "
               f"{top - second:.4f} < {_OP_DELTA}（噪声内）")
    elif top_name == "if_we_pick":
        expected, why = "pick", f"最大 {top_name}={top:.4f}"
    elif top_name == "if_we_ban":
        expected, why = "ban", f"最大 {top_name}={top:.4f}"
    else:
        counter = _max_counter_wr(oh["if_we_leave"])
        if counter is not None and counter > top:
            expected = "leave_and_counter"
            why = f"最大 if_we_leave={top:.4f} 且 wr_counter={counter:.4f} > wr_leave"
        else:
            expected = "ban"
            why = (f"最大 if_we_leave={top:.4f} 但 wr_counter={counter} 未超过 wr_leave"
                   f"（§9.1：要求 wr_counter > wr_leave，否则退化为 ban）")
    if rec != expected:
        errs.append(f"{tag}: recommendation={rec} 与 §9.1 复算的 {expected} 不符（{why}）")
    return errs


def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    """支持两种 mode：realtime 返回 options[]，offline 返回 branches[].plans[]。

    `lam` 即 §6.6 的 λ，取自 `app_config.robustness_lambda`（默认 1.0）：
    排序依据是 `expected_wr − λ × max(0, robustness_delta − 0.10)`，不是 `expected_wr`。
    """
    errs = _nonfinite_errors(body)
    if errs:
        return errs
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
            # 以下几项 schema 已能表达（robustness_delta>0.10 ⇒ risk_note、
            # fallback/counterparty_plan 非空）——这里是**防御性重复**：
            # 未过 schema 的 live 响应直接喂进来时仍得到可读错误，而不是静默通过。
            if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
                errs.append(f"{gname}/{key}: robustness_delta > 0.10 但缺 risk_note")
            # realtime 的 fallback 在 option 级；offline 的在 key_picks[] 内
            if "fallback" in o and not o["fallback"]:
                errs.append(f"{gname}/{key}: fallback 为空")
            # §6.6：options[] 每项必须有非空 counterparty_plan（与 fallback 同级的硬要求）
            if "counterparty_plan" in o and not o["counterparty_plan"]:
                errs.append(f"{gname}/{key}: counterparty_plan 为空")
    return errs


_ARCHETYPES = {"initiate", "protect", "push", "teamfight", "pickoff", "splitpush"}

# §7.3：这五项是**依赖位置**的维度；role 推断不出时必须整体降级为 insufficient_samples。
# hero_archetype 不依赖位置，仍必须返回。
_POSITION_DIMENSIONS = ("hero_pool", "laning", "combat", "map_vision", "tempo")


def check_profile(body: dict) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    for p in body["players"]:
        dims = p["dimensions"]
        ha = dims["hero_archetype"]
        if set(ha) != _ARCHETYPES:
            missing = sorted(_ARCHETYPES - set(ha))
            extra = sorted(set(ha) - _ARCHETYPES)
            errs.append(f"player {p['account_id']}: hero_archetype 键集不完整"
                        f"（缺 {missing}，多 {extra}）")
        elif abs(sum(ha.values()) - 1.0) > TOL:
            errs.append(f"player {p['account_id']}: hero_archetype 和 != 1.0")
        # §6.4：effective_count 的上界依据 —— effective_count <= window_games / 3。
        # 用整数乘法避免浮点边界（3 × effective_count <= window_games 与规格等价）。
        hp = p["hero_pool"]
        if 3 * hp["effective_count"] > hp["window_games"]:
            errs.append(f"player {p['account_id']}: effective_count={hp['effective_count']} > "
                        f"window_games/3 = {hp['window_games']}/3（§6.4）")
        # §6.4 + §7.3：role 推断不出（null 或缺省）时，五个依赖位置的维度
        # 必须都是 §6.0 的降级形态且 reason == insufficient_samples
        if p.get("role") is None:
            for dname in _POSITION_DIMENSIONS:
                d = dims.get(dname)
                if not (isinstance(d, dict) and d.get("value", 0) is None
                        and d.get("reason") == "insufficient_samples"):
                    errs.append(f"player {p['account_id']}: role 为 null 时 {dname} 必须降级为 "
                                f"insufficient_samples（§6.4/§7.3），实际 {d!r}")
    return errs


# ─────────────────────────────────────────────────────────────────────────
# 尚未落成可执行守护的规则清单（**有意留白，不是遗忘**）——免得下一轮重新发现：
#
# 需要请求上下文（fixtures 是纯响应体，拿不到请求；归 Plan 2+ 的接口集成测试）：
#   1. `sources_used` ⊆ 请求的 `sources`：check_value(..., requested_sources=...) 已就绪，
#      Policy/Playbook/Profile/Advise 的对应参数待接口层接入。
#   2. `check_resolve`：§6.2/§6.6 的 next_ord/team/is_pick 由 §6.0 resolve() 推出，
#      需要请求的 draft/first_pick_team —— 本模块已实现并有单测，fixture 层无法调用。
#   3. §6.2：`candidates[].hero_id` 不得与请求 draft 重复。
#   4. §6.6：mode=offline 时请求的 `branches` 参数与响应 `branches[].condition` 的双射
#      （「每个请求值都必须出现」）。
#   5. §6.6：`options[].hero_id` 必须已被 resolve() 判定为可行动作（不与 draft 重复）。
#
# 与规格示例直接冲突、需先做规格决策（F4）：
#   6. §6.3「所有（2 先手 × 7 体系）组合必须被覆盖或有显式 unknown 分支」——
#      §6.3 自身的示例只有 2 个分支，与该条冲突；示例同时是 §15 模板推导测试的输入，
#      故不实现，等规格决策。
#
# 已知留白 / 待规格澄清（不加固，避免把猜测冻结进契约）：
#   7. §9.1 原文说五个量（含 wr_counter）都要 n >= 30；本轮按控制器决定只对
#      OpHeroOption 内的三个量强制，故 `our_counter_options[].n < 30` **不要求降级**，
#      `wr_counter` 直接取可用项的最大值（见 _max_counter_wr）。
#   8. §6.3：`applies_to_game` 出现在不带 series 的响应里（且 first_pick 恰好等于
#      单局 expected）不会被拒 —— 无害，但语义未定义。
#   9. §6.3：`bans.consider[].hero_id` 与 `if_we_leave_it_open.hero_id` 未校验一致。
#  10. §6.3：`data_quality.oldest_pending_hours` 为 null **当且仅当**
#      `n_pending_draft == 0` —— 已在规格显式规定（本轮只做文档化），未落成守护。
#  11. 明确跳过的两项：`Assumptions` 组件改名（M6，纯外观）、`pct` 的取整模式
#      （M8，Plan 3 territory）。
# ─────────────────────────────────────────────────────────────────────────
