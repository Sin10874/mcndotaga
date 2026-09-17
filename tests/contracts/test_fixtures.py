"""fixture 校验入口与**边界覆盖**守护（计划 Task 7）。

本文件的两条测试守两件不同的事：

1. `test_all_fixtures_conform`（计划 Task 7 Step 1 原文）：每个 fixture 都
   **合法**——过其资源 schema，且过 `contracts/tools/invariants.py` 的该资源不变式。
2. `test_every_fixture_covers_its_declared_boundary`（控制器追加）：每个 fixture
   都**有覆盖**——规格 §11 / 计划 Task 7 Step 4 表声明的那条边界真的落在它身上。

第 2 条不可省：只有第 1 条时，fixture 可以被"漂白"成合法但平庸的响应（例如把
`value__low_confidence.json` 改成 `confidence:"high"` + `n_samples:412`——schema
与不变式全绿），前端于是对着**从未触发边界**的 mock 开发，而 mock 的全部价值
恰在边界上。`MIN_FIXTURES` 只保证"数量够"，保证不了"覆盖到"。

谓词约定：
- 每条谓词只读该 fixture 自身的响应体——fixtures 是**纯响应体**，没有请求上下文。
  依赖请求的边界（`sources_used ⊆ sources`、`next_ord == resolve(...)`、
  `options[].hero_id` 不与 draft 重复）由 Plan 2+ 的接口集成测试覆盖，
  见 `contracts/tools/invariants.py` 末尾的留白清单。
- 谓词在 schema/不变式之外断言"边界的**存在性**"：哪些字段真的出现、哪些值真的
  为 null、哪种形状真的被选中。算术（求和/等式/复算）不在这里重复——那是
  `main()` 的职责，重复一份只会制造第二个定义。
- 新增 fixture 必须同时在 `CASES` 里加一行：下面的 `set(paths) == set(CASES)`
  双向比对会让"新文件未登记"与"表里有文件却不存在"都立刻变红。
"""
from __future__ import annotations
import json
import pathlib

from contracts.tools.validate_fixtures import main

FIX = pathlib.Path(__file__).parents[2] / "contracts" / "fixtures"


def test_all_fixtures_conform():
    assert main() == 0


# ── 每条边界的谓词（一条 = 计划 Task 7 Step 4 表的一行）────────────────────

# 规格 §6.0「降级契约第一层」的原样形态：value 必为 null、reason 必填，
# 非 needs_replay 时**必须省略** needs（不得传 null）。用整字典相等而不是
# 逐键判断，是为了让"多夹带一个绝对值/needs"也同样失败。
_DEGRADED_INSUFFICIENT = {"value": None, "reason": "insufficient_samples"}

# 规格 §7 维度 6 + §6.4：六类原型原型恒为这六项（含 initiate）。
_ARCHETYPES = {"initiate", "protect", "push", "teamfight", "pickoff", "splitpush"}

# 规格 §7.3 / §6.4：这五项依赖位置（同侪集合按位置切分）；role 推断不出时必须
# 整体降级，而 hero_archetype 不依赖位置、仍必须返回。
_POSITION_DIMENSIONS = ("hero_pool", "laning", "combat", "map_vision", "tempo")


def _value_spec_example(b: dict) -> bool:
    """规格 §6.1「→ 200」示例（真实比赛 8996973546 的加项分解）。

    识别标志：四个 delta 逐字等于规格示例（0.011 / -0.014 / 0.024 / 0.009），
    `n_samples=412` 落在 high 桶。求和不变式（0.030 == 0.530 - 0.5）由
    `check_value` 守，这里只认身份。
    """
    return (b["radiant_win_prob"] == 0.530 and b["confidence"] == "high"
            and b["n_samples"] == 412
            and {c["factor"]: c["delta"] for c in b["contributions"]}
            == {"patch_strength": 0.011, "counter_matchup": -0.014,
                "player_comfort": 0.024, "first_pick": 0.009})


def _value_low_confidence(b: dict) -> bool:
    """规格 §11 表（value__low_confidence 行）/ §6.0 枚举表：n_samples < 30 → low。

    低样本是本项目的常态（对手在本版本往往只有个位数场次），前端必须能渲染
    `confidence:"low"` 而不是只见过 high。
    """
    return b["confidence"] == "low" and b["n_samples"] < 30


def _policy_spec_example(b: dict) -> bool:
    """规格 §6.2「→ 200」示例（真实比赛 8996973546 的前 12 手 BP）。

    识别标志：`draft` 长度 12 ⇒ `next_ord = 12`（§6.0：next_ord = max(ord)+1），
    由 §6.0 模板推出 team=1 / is_pick=true；候选 hero 112 与两条真实比赛 id。
    """
    cand = b["candidates"][0]
    return (b["next_ord"] == 12 and b["team"] == 1 and b["is_pick"] is True
            and cand["hero_id"] == 112
            and cand["evidence_match_ids"] == [8996973546, 8988636430])


def _policy_no_model(b: dict) -> bool:
    """规格 §11 表（policy__no_model 行）/ §6.2：模型未就绪态。

    `baseline.model_top1` 为 null 且**字段存在**（必填但可空，不得省略字段），
    `model` 同样为 null。M6 的前端必须能渲染"无模型"而不是显示 0 或崩溃。
    """
    return ("model_top1" in b["baseline"] and b["baseline"]["model_top1"] is None
            and b["model"] is None)


def _playbook_spec_example(b: dict) -> bool:
    """规格 §6.3「→ 200」示例（真实比赛 8996973546，series 1141522）。

    识别标志：真实队伍 id、`MustBan` 的 hero 55 且 n=24、以及示例修正后的自洽
    事实——**两个分支都带 `applies_to_game`**，第 2 局 `first_pick_team = 1`
    （规格 §6.3「示例验算」段）。
    """
    games = {g["game_no"]: g["first_pick_team"] for g in b["series"]["games"]}
    return (b["matchup"]["us"]["team_id"] == 10251056
            and b["matchup"]["them"]["team_id"] == 10232231
            and b["series"]["series_id"] == 1141522
            and games == {1: 0, 2: 1}
            and [br.get("applies_to_game") for br in b["branches"]] == [1, 2]
            and b["bans"]["must_ban"][0]["hero_id"] == 55
            and b["bans"]["must_ban"][0]["n"] == 24
            and b["op_hero_decision"][0]["recommendation"] == "leave_and_counter")


def _playbook_draft_incomplete(b: dict) -> bool:
    """规格 §11 表（playbook__draft_incomplete 行）/ §12 R1：数据不完整的载体。

    `n_pending_draft > 0` 且 `n_unavailable_draft > 0`——UI 据此标注"统计不完整"。
    同时钉住 §6.3 的当且仅当：pending > 0 ⇒ `oldest_pending_hours` 必须给值
    （不得用 0 冒充"有值"）。
    """
    dq = b["data_quality"]
    return (dq["n_pending_draft"] > 0 and dq["n_unavailable_draft"] > 0
            and dq["oldest_pending_hours"] is not None)


def _playbook_anomalous(b: dict) -> bool:
    """规格 §11 表（playbook__anomalous 行）/ §5.3：异常手序的场次计数必须暴露。

    `n_anomalous_draft > 0`（不得静默剔除异常场次）。本 fixture 落在
    `n_pending_draft == 0` 一侧，故同时钉住 §6.3「oldest_pending_hours 为 null
    当且仅当 n_pending_draft == 0」的另一半。
    """
    dq = b["data_quality"]
    return (dq["n_anomalous_draft"] > 0 and dq["n_pending_draft"] == 0
            and dq["oldest_pending_hours"] is None)


def _playbook_positions_phase_b(b: dict) -> bool:
    """规格 §11 表（playbook__positions_phase_b 行）/ §6.0 第一层 + §6.5。

    两半合起来才是"`needs` 存在**当且仅当** reason == needs_replay"：
    - ward（眼位坐标，Phase A 必须不存在）降级为 `value: null` +
      `reason: "needs_replay"` + `needs: "Phase B"`；
    - 另有一条非 needs_replay 的降级 note **不含** `needs`
      （§6.0：其余 reason 下必须省略该字段，不得传 null）。
    """
    notes = [n for p in b["positions"] for n in p["notes"]]
    ward = [n for n in notes if n["kind"] == "ward"]
    others = [n for n in notes if n["reason"] != "needs_replay"]
    return (bool(ward)
            and all(n["value"] is None and n["reason"] == "needs_replay"
                    and n["needs"] == "Phase B" for n in ward)
            and bool(others)
            and all("needs" not in n for n in others))


def _playbook_op_insufficient(b: dict) -> bool:
    """规格 §11 表（playbook__op_insufficient 行）/ §9.1 + §6.0。

    某一胜率量样本不足（本 fixture 是 `if_we_ban`）⇒ 该量降级为
    `insufficient_samples` 且**无** `needs`，结论必须是 `insufficient_data`
    而非猜测（§9.1 第一条；§6.3：逐量判定，不是逐条目判定）。
    """
    oh = b["op_hero_decision"][0]
    ban = oh["if_we_ban"]
    return (oh["recommendation"] == "insufficient_data"
            and ban.get("value", 0) is None and "wr" not in ban
            and ban["reason"] == "insufficient_samples" and "needs" not in ban)


def _profile_spec_example(b: dict) -> bool:
    """规格 §6.4「→ 200」示例（`window_games = 48`）。

    识别标志：真实队伍 id、`window_games == 48`、signature 里 hero 55 的
    `12/48 = 25%`（§6.4 字段表的示例验算）、`effective_count = 14 <= 48/3`。
    """
    pool = b["players"][0]["hero_pool"]
    sig = pool["signature"][0]
    return (b["team_id"] == 10232231 and pool["window_games"] == 48
            and pool["effective_count"] == 14
            and (sig["hero_id"], sig["games"], sig["wr"], sig["pct"]) == (55, 12, 0.75, 25))


def _profile_map_vision_counts_only(b: dict) -> bool:
    """规格 §11 表（profile__map_vision_counts_only 行）/ §6.5 + §7.3。

    位置未知（`coverage.*.n_position_unknown > 0`）**不得**降级数量口径的
    `map_vision`——它返回真实 percentile；§6.5 明文提醒"不要误降级它"。
    本 fixture 里降级的只有 `laning`（需要位置同侪集合的那一项），
    `hero_pool`/`combat`/`map_vision`/`tempo` 都仍是 live 百分位。
    非降级形态还须满足 §7.3 的同侪下限（`n >= 30`）——否则"没降级"本身就是
    违规：`{"percentile": 0, "n": 0}` 同样满足只有形态的谓词。
    """
    mv = b["players"][0]["dimensions"]["map_vision"]
    return (b["coverage"]["pro_match"]["n_position_unknown"] > 0
            and "percentile" in mv and "n" in mv and "reason" not in mv
            and mv["n"] >= 30)


def _profile_position_unknown(b: dict) -> bool:
    """规格 §11 表（profile__position_unknown 行）/ §6.4 + §7.3。

    role 推断不出（null）⇒ 五个**依赖位置**的维度全部降级为
    `insufficient_samples`（且不带 needs），而**不依赖位置**的 `hero_archetype`
    仍返回六项、和为 1.0（±0.001）。这条区分专门防实现者把六维一起降级。
    """
    player = b["players"][0]
    dims = player["dimensions"]
    archetype = dims["hero_archetype"]
    return (b["coverage"]["pro_match"]["n_position_unknown"] > 0
            and player.get("role") is None
            and all(dims[d] == _DEGRADED_INSUFFICIENT for d in _POSITION_DIMENSIONS)
            and set(archetype) == _ARCHETYPES
            and abs(sum(archetype.values()) - 1.0) <= 0.001)


def _advise_realtime(b: dict) -> bool:
    """规格 §11 表（advise__realtime 行）/ §6.6：realtime 返回 `options[]` 形状。

    `mode` 是**请求**字段：`Advise` schema 是封闭的（additionalProperties:false）
    且没有 `mode` 属性，响应靠形状判别（schema 的 oneOf）——故谓词不能读
    `b["mode"]`，只能断言形状本身。
    """
    return ("options" in b and "branches" not in b and len(b["options"]) >= 1
            and {"next_ord", "team", "is_pick", "assumptions"} <= set(b)
            and all(o["fallback"] and o["counterparty_plan"] for o in b["options"]))


def _advise_offline(b: dict) -> bool:
    """规格 §11 表（advise__offline 行）/ §6.6 mode 表：offline 返回
    `branches[].plans[]`，不返回 `options[]`；每支必须带 `branch_id` 与
    `plans`。两个 `condition` 必须**逐字**等于这两个互不相同的取值——
    只断言"condition 存在"的话，把两支改成同一个 condition（多分支结果
    无法归属）也照样通过。同上：响应里没有 `mode` 字段。
    """
    return ("branches" in b and "options" not in b
            and all(br.get("branch_id") and br.get("plans") for br in b["branches"])
            and [br["condition"] for br in b["branches"]]
            == [{"first_pick": "us", "their_opening": "teamfight"},
                {"first_pick": "them", "their_opening": "push"}])


def _advise_robustness_penalized(b: dict) -> bool:
    """规格 §11 表（advise__robustness_penalized 行）/ §6.6 稳健性降权。

    这条边界的可见形态是「原始胜率最高的选项被降权后不再排第一」，故谓词从
    **expected_wr 的领先者**出发（而不是"某个 risky 项恰好不在首位"）：
    - 领先者必须就是那个高风险项（`robustness_delta > 0.10`）；
    - 它必须带 `risk_note` 且 `penalized_score < expected_wr`（降权真的发生了）；
    - 它不得落在首位——排序依据是 penalized_score，不是 expected_wr。
    """
    options = b["options"]
    leader = max(range(len(options)), key=lambda i: options[i]["expected_wr"])
    o = options[leader]
    return (o["robustness_delta"] > 0.10 and bool(o.get("risk_note"))
            and o["penalized_score"] < o["expected_wr"] and leader != 0)


def _error_insufficient_data(b: dict) -> bool:
    """规格 §11 表（error__insufficient_data 行）/ §6.0 第二层。

    整体样本不足是**业务结果**（HTTP 200，见 fixtures/README.md），
    `detail` 必须能说明"差多少"（n_samples < required），否则前端只能显示一句
    无信息量的错误。
    """
    err = b["error"]
    detail = err.get("detail") or {}
    return (err["code"] == "insufficient_data"
            and detail.get("n_samples") is not None
            and detail.get("required") is not None
            and detail["n_samples"] < detail["required"])


def _error_source_not_allowed(b: dict) -> bool:
    """规格 §11 表（error__source_not_allowed 行）/ §4.1 + §6.0 错误码表。

    请求了未授权的来源（对手画像路径请求 `scrim`）→ 403 且 `code` 为
    `source_not_allowed`；detail 点名被拒的来源，便于前端提示。
    """
    err = b["error"]
    return (err["code"] == "source_not_allowed"
            and (err.get("detail") or {}).get("source") == "scrim")


# 表 = 计划 Task 7 Step 4 的 17 行，一行一个文件。key 必须与文件名逐字相同。
CASES = {
    "advise__offline.json": _advise_offline,
    "advise__realtime.json": _advise_realtime,
    "advise__robustness_penalized.json": _advise_robustness_penalized,
    "error__insufficient_data.json": _error_insufficient_data,
    "error__source_not_allowed.json": _error_source_not_allowed,
    "playbook__anomalous.json": _playbook_anomalous,
    "playbook__draft_incomplete.json": _playbook_draft_incomplete,
    "playbook__op_insufficient.json": _playbook_op_insufficient,
    "playbook__positions_phase_b.json": _playbook_positions_phase_b,
    "playbook__spec_example.json": _playbook_spec_example,
    "policy__no_model.json": _policy_no_model,
    "policy__spec_example.json": _policy_spec_example,
    "profile__map_vision_counts_only.json": _profile_map_vision_counts_only,
    "profile__position_unknown.json": _profile_position_unknown,
    "profile__spec_example.json": _profile_spec_example,
    "value__low_confidence.json": _value_low_confidence,
    "value__spec_example.json": _value_spec_example,
}


def test_every_fixture_covers_its_declared_boundary():
    """每个 fixture 必须真的覆盖它声明的那条边界（防止 fixture 被漂白）。

    实现为**表驱动 + 逐行独立判定**（而不是 17 个 parametrize 用例）是刻意的：
    计划 Task 7/8 的计数（Step 5「2 passed」、Task 8「87 / 120 passed」）以本文件
    恰好两条测试为前提；逐行判定还能一次性报出**所有**丢覆盖的 fixture，
    而不是修一个跑一次。
    """
    paths = sorted(p.name for p in FIX.glob("*.json"))
    # 双向比对：新 fixture 未登记、表里的文件被删/改名，都必须失败。
    assert set(paths) == set(CASES), (
        f"CASES 表与 {FIX}/*.json 不一致：\n"
        f"  新增未登记: {sorted(set(paths) - set(CASES))}\n"
        f"  表中有但文件缺失: {sorted(set(CASES) - set(paths))}")

    failures = []
    for name in paths:
        body = json.loads((FIX / name).read_text(encoding="utf-8"))
        pred = CASES[name]
        try:
            ok = bool(pred(body))
        except Exception as exc:                    # 形状漂移也算丢覆盖，给出可读原因
            ok, note = False, f"（谓词求值失败：{type(exc).__name__}: {exc}）"
        else:
            note = ""
        if not ok:
            boundary = (pred.__doc__ or "").strip().splitlines()[0]
            failures.append(f"{name}: 未覆盖声明的边界「{boundary}」{note}")
    assert not failures, "fixture 丢失边界覆盖：\n" + "\n".join(failures)
