"""TS 生成物的新鲜度与完整性。

路径一律由 ``__file__`` 推出仓库根，**不依赖 cwd**：从 ``tests/`` 里跑
pytest 与从仓库根跑必须同结果（仓库内其余测试同样 cwd 无关）。
"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).parents[2]
TS = ROOT / "web" / "src" / "types" / "contract.ts"
GENERATOR = "contracts.tools.gen_ts_types"

# 生成器要求存在、前端需要引用的枚举（contracts/tools/gen_ts_types.REQUIRED_ENUMS
# 的超集检查见 test_ts_enum_members_match_the_contract：契约里的**每个**枚举都比对）
REQUIRED_ENUMS = ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                  "UnavailableReason", "ErrorCode", "Side"]

# 冻结的枚举成员基线——**前端可见枚举面的唯一权威期望**。
#
# 为什么不从 openapi.yaml 现算：契约与生成物可能被同一次改动一起重建，
# 那样两边的「一致」是自洽的退化，漏值不会被发现（实测：删掉
# TheirOpening.unknown 后重建两边，只比对二者的测试全绿）。基线把期望钉在
# 评审过的字面量上，删成员必须连带改这里，于是**必然**出现在 diff 里。
# 改名/增删成员的步骤：改契约 → 重建 → 改这里 → 重生成 → 一起提交。
ENUM_MEMBERS: dict[str, list[str]] = {
    "Confidence": ["low", "medium", "high"],
    "ErrorCode": ["insufficient_data", "invalid_request", "source_not_allowed",
                  "not_found", "upstream_unavailable"],
    "Factor": ["patch_strength", "counter_matchup", "player_comfort", "first_pick"],
    "NoteKind": ["ward", "timing", "lane", "smoke", "combat", "resource", "communication"],
    "Recommendation": ["pick", "ban", "leave_and_counter", "insufficient_data"],
    "Side": ["us", "them"],
    "Team": ["0", "1"],
    "TheirOpening": ["teamfight", "push", "pickoff", "splitpush", "protect",
                     "initiate", "unknown"],
    "UnavailableReason": ["needs_replay", "insufficient_samples", "source_not_allowed",
                          "stat_unavailable"],
}

# 冻结的 Resource 成员基线。生成器把这五个名字**硬编码**在 RESOURCE_MEMBERS
# （不从 components.schemas 推导——推导会让契约里凭空多出的组件自动成为合法
# Resource 成员），所以「成员集变化必须改生成器」；这里的基线让同一变化
# **同时**必须改测试，于是必然出现在 diff 里。
#
# 没有它时 ``Resource`` 是唯一无人看守的导出：把联合换成
# ``'Value' | 'Policy' | 'Bogus'`` 再重生成，87 条测试全绿（实测）。
# 加成员/改名/增删资源的步骤：改契约 → 改生成器 RESOURCE_MEMBERS →
# 改这里 → 重生成 → 一起提交。
RESOURCE_MEMBERS: list[str] = ["Value", "Policy", "Playbook", "Profile", "Advise"]

# 「export type X = A | B;」一行一条，无嵌套、无换行——小显式解析器足够。
_ENUM_DECL = re.compile(r"^export type (\w+) = (.+);$", re.M)
_QUOTED = re.compile(r"""^(?:"[^"]*"|'[^']*')$""")   # 字符串枚举成员（生成器用 "…"，Resource 用 '…'）
_NUMERIC = re.compile(r"^\d+$")                       # 整数枚举成员（Team）


def ts_enums() -> dict[str, list[str]]:
    """{枚举名: [成员裸值]}——解析 ``export type X = A | B;``，按生成顺序保留。"""
    out: dict[str, list[str]] = {}
    for name, body in _ENUM_DECL.findall(TS.read_text(encoding="utf-8")):
        members = [m.strip() for m in body.split("|")]
        assert all(_QUOTED.match(m) or _NUMERIC.match(m) for m in members), (
            f"{name} 含非法成员（既不是字符串字面量也不是整数）: {members}")
        out[name] = [m[1:-1] if _QUOTED.match(m) else m for m in members]  # 去引号
    return out


def _contract_members(values: list) -> list[str]:
    """契约枚举值 → 裸值形式（与 ``ts_enums()`` 的输出同形）。"""
    return [str(v) for v in values]


def test_generated_ts_is_up_to_date():
    """用生成器的 ``--check`` 只读比对。

    **不要**回到「跑一遍生成器 → 比较前后文本」：那会覆写受版本控制的
    contract.ts，把一次本地手改直接抹掉（实测：注入 ``"TAMPERED"`` 成员后
    测试报 FAILED，文件却已被改回），失败信息里的 diff 也随之消失。
    ``--check`` 渲染在内存里、只比不写，手改完好无损地留在盘上。
    """
    r = subprocess.run([sys.executable, "-m", GENERATOR, "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, (
        f"contract.ts 已过期（--check exit {r.returncode}）——"
        f"运行 make contract-ts 并提交：\n{r.stdout}{r.stderr}")


def test_ts_contains_all_required_enums():
    """生成器漏掉 §6.0 的枚举时失败。"""
    ts = TS.read_text(encoding="utf-8")
    for name in REQUIRED_ENUMS:
        assert f"export type {name} =" in ts, f"{name} 未生成"


def assert_resource_matches(contract_doc, enums: dict[str, list[str]]) -> None:
    """Resource 联合的三方比对：冻结基线 ↔ 生成物 ↔ 契约的五个资源组件。

    三方都要看，理由与枚举相同：只比「契约 vs 生成物」时，两边被同一次改动
    一起重建就退化成自洽（``'Value' | 'Policy' | 'Bogus'`` 是自洽的），
    只有基线能把它顶出来。
    """
    assert enums["Resource"] == RESOURCE_MEMBERS, (
        f"Resource 生成成员与冻结基线不符（顺序也算）——生成器改了联合、"
        f"或 contract.ts 未重新生成：\n"
        f"  基线: {RESOURCE_MEMBERS}\n  生成: {enums['Resource']}")
    schemas = contract_doc["components"]["schemas"]
    absent = [n for n in RESOURCE_MEMBERS if n not in schemas]
    assert not absent, f"契约缺少 Resource 成员对应的组件: {absent}"


def test_ts_enum_members_match_the_contract(contract_doc):
    """逐成员比对契约、生成物与冻结基线——漏值/多值/取错 schema 都必须变红。

    ``test_ts_contains_all_required_enums`` 只断言 ``export type X =`` 存在，
    对「成员被静默丢掉」或「成员来自另一个 schema」无能为力；本测试补上。
    比对范围是**契约里全部带 enum 的 schema**（REQUIRED_ENUMS 只是其中前端
    硬依赖的子集），外加生成器固定输出的 ``Resource``（它同样按成员逐个比对，
    见 ``assert_resource_matches``）。
    """
    enums = ts_enums()
    assert set(enums) == set(ENUM_MEMBERS) | {"Resource"}, (
        f"contract.ts 的导出枚举与冻结基线不一致：\n"
        f"  仅 TS 有: {sorted(set(enums) - set(ENUM_MEMBERS) - {'Resource'})}\n"
        f"  仅基线有: {sorted(set(ENUM_MEMBERS) - set(enums))}")
    assert_resource_matches(contract_doc, enums)
    schemas = contract_doc["components"]["schemas"]
    for name in REQUIRED_ENUMS:                     # 前端依赖的枚举，缺席即失败
        assert name in schemas, f"契约缺少必需枚举 {name}"
    from_contract = {n: _contract_members(s["enum"])
                     for n, s in schemas.items() if s.get("enum")}
    assert set(from_contract) == set(ENUM_MEMBERS), (
        f"契约的枚举面与冻结基线不一致：\n"
        f"  仅契约有: {sorted(set(from_contract) - set(ENUM_MEMBERS))}\n"
        f"  仅基线有: {sorted(set(ENUM_MEMBERS) - set(from_contract))}")
    for name in sorted(ENUM_MEMBERS):
        want = ENUM_MEMBERS[name]
        assert from_contract[name] == want, (
            f"{name} 契约成员与冻结基线不符（顺序也算）：\n"
            f"  基线: {want}\n  契约: {from_contract[name]}")
        assert enums[name] == want, (
            f"{name} 生成成员与冻结基线不符（顺序也算）——生成器漏值/多值/取错 "
            f"schema，或契约改了而 contract.ts 未重新生成：\n"
            f"  基线: {want}\n  生成: {enums[name]}")
