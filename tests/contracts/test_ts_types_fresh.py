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
    before = TS.read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", GENERATOR], check=True, cwd=ROOT)
    assert before == TS.read_text(encoding="utf-8"), "contract.ts 已过期，运行 make contract-ts 并提交"


def test_ts_contains_all_required_enums():
    """生成器漏掉 §6.0 的枚举时失败。"""
    ts = TS.read_text(encoding="utf-8")
    for name in REQUIRED_ENUMS:
        assert f"export type {name} =" in ts, f"{name} 未生成"


def test_ts_enum_members_match_the_contract(contract_doc):
    """逐成员比对契约、生成物与冻结基线——漏值/多值/取错 schema 都必须变红。

    ``test_ts_contains_all_required_enums`` 只断言 ``export type X =`` 存在，
    对「成员被静默丢掉」或「成员来自另一个 schema」无能为力；本测试补上。
    比对范围是**契约里全部带 enum 的 schema**（REQUIRED_ENUMS 只是其中前端
    硬依赖的子集），外加生成器固定输出的 ``Resource``。
    """
    enums = ts_enums()
    assert set(enums) == set(ENUM_MEMBERS) | {"Resource"}, (
        f"contract.ts 的导出枚举与冻结基线不一致：\n"
        f"  仅 TS 有: {sorted(set(enums) - set(ENUM_MEMBERS) - {'Resource'})}\n"
        f"  仅基线有: {sorted(set(ENUM_MEMBERS) - set(enums))}")
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
