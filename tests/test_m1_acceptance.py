"""M1 验收门（规格 §14 的 M1 行 + §15 的判据 + 本文件额外接管的 M0 契约条款）。

**这个文件是本计划的最后一道门**：M1 之后 `analysis/`、`models/`、`web/` 三条线并行开工，
它们信任的每一件东西（常量层、版本表、引导语料、契约校验、目录边界）都在这里被一次性复核。
故它与其余测试的分工是「**汇总 + 逐条点名**」，不是替代：单条守护各自住在自己的测试文件里
（例如异常率的族口径在 `tests/ingest/test_order_families.py`），本文件负责让**验收口径**本身
可执行、且不可能空过。

## 三条不可动摇的口径（每一条都是前几轮评审的实际后果）

1. **缺数据 = 失败，不是跳过**（2026-09-18 Fix 4）。本文件经 `m1_db` 取数：缓存不存在时它返回
   `None`（**不 skip、不 raise**），由测试体自己 `assert` 失败并打印补齐命令。直接请求
   `db_after_bootstrap` 会得到 `1 skipped` + 退出码 0 —— 在没下过数据的机器上，那看起来就是
   「验收通过」。M1 的验收对象就是那份 506.7 MB 子集，**没有数据的 M1 不是 M1**。
   两条实现约束：不能请求会 skip 的 fixture（skip 在 setup 阶段发生，测试体没机会说话）；
   也不能在 fixture 里 `pytest.fail`（会被记成 **error** 而不是 failed）。
   **不要**用 `pytest_plugins = ["tests.ingest.conftest"]` 这个旧修法：单独跑本文件能用，
   全量 `pytest -q` 会以 `ValueError: Plugin already registered` 中止收集（pytest 9.1.1 实测）。
   `db_after_bootstrap` 已因此上移到根 `tests/conftest.py`。

2. **异常率的分母必须是全库**（评审 F6）。`WHERE n_draft_actions = 24` 会让 `< 2%` **恒真**：
   24 手子集实测异常 **0/149,522**。全库口径实测 **2,048/211,051 = 0.97%**（规格 §15 的判据）。

3. **每条断言的失败信息必须说「下一步做什么」**。M1 的计数是**版本化常量快照**的产物：新英雄、
   新版本、新数据集都会让数字变，而正确动作是提升 `constants_snapshot.snapshot_version`
   并重派生/重训（规格 §5.1），**不是**改这条断言。每个 `_check` 都带 `action`，说的就是这件事。

## 覆盖范围（逐条对应规格 §14 的 M1 行）

| 规格 §14 M1 条款 | 本文件 | 别处的单条守护 |
|---|---|---|
| 常量表英雄 = 127、道具 = 501 | ✅ 逐值 | `tests/constants/test_load.py` |
| 版本表含 **84 个字母子版本** | ✅ 逐值（118 总数） | `tests/constants/test_patches.py`（Valve 清单口径） |
| 引导数据集 506 MB 子集入库 | ✅ 精确 211,051 / 4,772,342 | `tests/ingest/test_bootstrap.py` |
| `anomaly=true` 占比 **< 2%** | ✅ 全库分母 | `tests/ingest/test_bootstrap.py` |
| `matches`/`draft_actions`/`leagues` 可查 | ✅ 精确计数 + `SELECT 1` | `tests/ingest/test_bootstrap.py` |

本文件额外接管的条款（M0 的收尾，Task 13 是最后一个能钉住它们的机会）：
`contracts/openapi.yaml` 过 OpenAPI **文档级** schema 校验（`tests/contracts/` 只做子 schema
的 jsonschema 校验；`Makefile` 的 `lint-spec` 做文档级校验，但 `make` 在本机不可用，
故这里用 `openapi_spec_validator` 直接把它变成测试）、17 个 fixture 过契约校验、三条线的目录边界
（`analysis/` `models/` `web/`）、`web/src/types/contract.ts` 新鲜（`gen_ts_types --check`）。
以及数据侧的一致性与完整性（异常表与 `matches.anomaly` 互证、`pending` 语义、pre-2018 归属、
token 索引派生规则、族全域分类）—— 它们不是 §14 的 M1 字面条款，但 M1「数据地基可用」的
全部含义就是这些：数字对得上**且**互相自洽。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).parents[1]
CACHE = ROOT / "tests" / "fixtures" / "kaggle"

#: 契约侧的冻结版本口径（Task 10 的 Valve 清单：118 个版本 / 84 个字母版本）。
#: 字母子版本数**只能来自 Valve**（patchdates 有 141 个槽位，两者差 57，见规格 §3.2/§15③）。
N_PATCHES, N_PATCHES_LETTERED = 118, 84

#: 常量表逐值口径（规格 §14 M1：英雄 = 127、道具 = 501）。
N_HEROES, N_ITEMS = 127, 501

#: 实测值（2026-09-18，全部 19 个目录 / 506.7 MB 子集）。**这些是测出来的，不是目标值**：
#: 重下上游数据集或改了 loader 之后它们必须跟着动，动的那一刻就该有人看一眼新数字。
MEASURED_MATCHES = 211_051
MEASURED_DRAFT_ACTIONS = 4_772_342
MEASURED_LEAGUES = 1_557
MEASURED_ANOMALIES = 2_048          # 2,048 / 211,051 = 0.97%，阈值是规格的 < 2%
#: 异常**绝对数**的容忍带。为什么要有它：`rate < 2%` 允许最坏 4,221 场，只钉比率时
#: "loader 少判了 4,000 场异常"（例如 DRAFT_ORDERS 被加宽）与"数据集只剩 1/3"都会悄悄过关。
#: ±5% 的带子是刻意的：异常是**数据**（3,367 场 `type_deviation` + 3,806 场
#: `unregistered_hand_count`，有重叠），精确值会随上游修订漂移，钉死反而制造假红。
ANOMALY_TOLERANCE = 0.05
MEASURED_PENDING = 6_046            # anomaly=false 但没有 BP 序列（规格 §5.2 的 pending）
MEASURED_PRE_2018 = 17_552          # 8.32%，全部 patch_id IS NULL（Valve 清单从 7.08 起）
MEASURED_CLASSIFIED = 202_957       # anomaly=false 且有 BP 序列 = 十族支持度之和（零未分类）
#: hero_id 135 的 dense_index —— 规格 §15「token 映射测试」点名的那个英雄
#: （`hero_id != dense_index`，是「token 用 dense_index 而非 hero_id」的唯一验证锚点）。
MEASURED_HERO_135_DENSE = 121

#: 三条线的目录边界（规格 §11）：M1 之后 `analysis/` 与 `models/` 开工，`web/` 由前端线接管。
LINE_DIRECTORIES = ("analysis", "models", "web")
TS_TYPES = pathlib.Path("web/src/types/contract.ts")


# --------------------------------------------------------------------- 口径工具

class M1Violations:
    """逐条收集违规，最后一次性报出。

    为什么不是「每条一个 `assert`，第一个失败就退出」：验收失败时人需要的是**全部**坏点
    （一次 bootstrap 2 分钟，改一条跑一次代价太高）；而且数据缺失时也不能因此跳过契约侧
    的检查（那是另一类事实）。收集式断言让一次运行报出全部违规 + 每条的可操作指引。

    仍然计为**一条**测试（`1 passed` / `1 failed`）—— M1 验收是一个整体判断，不是一个参数化集合。
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def check(self, ok: bool, clause: str, measured: object, want: str, *, action: str = "") -> None:
        if ok:
            return
        line = f"✗ {clause}：实测 {measured!r}，要求 {want}"
        if action:
            line += f" —— {action}"
        self.lines.append(line)

    def raise_if_any(self) -> None:
        if self.lines:
            pytest.fail("M1 验收未通过（%d 条）：\n%s" % (len(self.lines), "\n".join(self.lines)))


def _count(db, sql: str, *params) -> int:
    return db.execute(sql, params or None).fetchone()[0]


def _snapshot_action(what: str) -> str:
    """计数变了时的统一指引（规格 §5.1：快照是版本化的，改数字不是正确动作）。"""
    return (f"若这是**上游例行变化**（新英雄 / 新版本 / 新数据集），按规格 §5.1 提升 "
            f"constants.load.SNAPSHOT_VERSION 并重派生 dense_index（= 重训模型）；"
            f"若这是**数据集换版**（{what}），更新本文件的实测常量并重跑 M1 验收 —— "
            f"**不要**为了让测试变绿而改这条断言。")


# ------------------------------------------------------------------ fixture 入口

@pytest.fixture(scope="session")
def m1_db(request):
    """M1 验收的数据入口：**缺缓存时返回 `None`（不 skip、也不 raise）**，由测试体自己 fail。

    见模块 docstring 第 1 条。（本 fixture 本身很短，但它的两条「不能」是踩出来的：
    请求 `db_after_bootstrap` 会在 setup 阶段 skip；在 fixture 里 fail 会记成 error。）
    """
    if not any(CACHE.glob("*/picks_bans.csv")):
        return None
    return request.getfixturevalue("db_after_bootstrap")


# ---------------------------------------------------------------------- 验收本体

def test_m1_acceptance(m1_db):
    """规格 §14 的 M1 验收 + 本计划最后能钉住的 M0 契约条款（见模块 docstring 的对照表）。"""
    from tests.ingest.conftest import missing_dataset_message

    v = M1Violations()

    # =============================================================== 条款 A：数据在位
    # 缺缓存 → `1 failed`（不是 `1 skipped`），并打印补齐命令。
    v.check(m1_db is not None, "M1-A 引导数据集在位（§14 M1「506 MB 子集入库」）",
            "无 tests/fixtures/kaggle/", "缓存存在",
            action=missing_dataset_message())
    if m1_db is None:
        _check_contract_clauses(v)     # 数据不在也要把契约侧验完（那是另一类事实）
        v.raise_if_any()
    db = m1_db

    # =============================================================== 条款 1：常量表
    # 规格 §14 M1:「常量表英雄 = 127、道具 = 501」。
    n_heroes = _count(db, "SELECT count(*) FROM heroes")
    v.check(n_heroes == N_HEROES, "M1-1 常量表英雄数（§14 M1）", n_heroes, str(N_HEROES),
            action=_snapshot_action("heroes"))
    n_items = _count(db, "SELECT count(*) FROM items")
    v.check(n_items == N_ITEMS, "M1-1 常量表道具数（§14 M1）", n_items, str(N_ITEMS),
            action=_snapshot_action("items"))

    # 快照必须恰好一行，且与 `constants.load.SNAPSHOT_VERSION` 一致 —— 出现第二行意味着
    # 有人绕过 load_constants 手写了常量（快照是版本化的，规格 §5.1）。
    from constants.load import SNAPSHOT_VERSION
    snap = db.execute("SELECT snapshot_version, n_heroes, n_items FROM constants_snapshot").fetchall()
    v.check(snap == [(SNAPSHOT_VERSION, N_HEROES, N_ITEMS)],
            "M1-1 常量快照行（§5.1 版本化快照）", snap,
            f"[(SNAPSHOT_VERSION={SNAPSHOT_VERSION}, {N_HEROES}, {N_ITEMS})]",
            action="constants_snapshot 必须由 constants.load.load_constants 写入且恰好一行；"
                   "提升版本号时**同时**重派生 hero_token_index（规格 §5.1）。")

    # =============================================================== 条款 2：版本表
    # 规格 §14 M1:「版本表含 84 个字母子版本」；总数 118 由 §3.2/§15③ 钉住（行集来自 Valve，
    # 不是 patchdates 的 141 个槽位）。
    n_patches = _count(db, "SELECT count(*) FROM patches")
    v.check(n_patches == N_PATCHES, "M1-2 patches 总版本数（§3.2/§15③：行集来自 Valve）",
            n_patches, str(N_PATCHES),
            action="Valve 清单变了 → REFRESH_NETWORK=1 重抓 tests/fixtures/network/ 后核对；"
                   "若确是新版本，提升快照版本并同步本文件的 118/84。")
    n_lettered = _count(db, r"SELECT count(*) FROM patches WHERE version_name ~ '[a-z]$'")
    v.check(n_lettered == N_PATCHES_LETTERED, "M1-2 字母子版本数（§14 M1 的 84）",
            n_lettered, str(N_PATCHES_LETTERED),
            action="该数字**只能来自 Valve**（patchdates 有 141 个字母槽位）—— 差 57 说明行集"
                   "换成了 patchdates；核对 constants/patches.py 的 declare_lettered_versions()。")

    # =============================================================== 条款 3：引导语料
    # 规格 §14 M1:「引导数据集 506 MB 子集入库」—— 精确计数是**实测值**：偏了就说明
    # 缓存或 loader 变了，必须有人看一眼（新数据集版本要更新本文件的实测常量）。
    total = _count(db, "SELECT count(*) FROM matches")
    v.check(total == MEASURED_MATCHES, "M1-3 入库场次（§14 M1 引导子集）", total,
            str(MEASURED_MATCHES), action=_snapshot_action("matches"))
    n_actions = _count(db, "SELECT count(*) FROM draft_actions")
    v.check(n_actions == MEASURED_DRAFT_ACTIONS, "M1-3 入库 BP 手数（§14 M1 引导子集）",
            n_actions, str(MEASURED_DRAFT_ACTIONS),
            action=_snapshot_action("draft_actions"))
    n_leagues = _count(db, "SELECT count(*) FROM leagues")
    v.check(n_leagues == MEASURED_LEAGUES, "M1-3 联赛行数（§14 M1 引导子集）",
            n_leagues, str(MEASURED_LEAGUES),
            action="联赛名来自 Constants/Constants.Leagues.csv；行数变说明该文件或 metadata 变了"
                   "（同一 league_id 跨目录重复，表行数是 distinct：逐目录累加会大出 ~16%）。")

    # =============================================================== 条款 4：异常率
    # 规格 §14 M1:「anomaly=true 的场次占比 < 2%」；§15① 规定**全库**分母。
    # ⚠ 不得加 `WHERE n_draft_actions = 24`：24 手子集实测异常 0/149,522，任何阈值都恒真（空过）。
    n_anomaly = _count(db, "SELECT count(*) FROM matches WHERE anomaly")
    rate = n_anomaly / total if total else 0.0
    v.check(total > 0 and rate < 0.02, "M1-4 异常率 < 2%（§14 M1 + §15①，全库分母）",
            f"{n_anomaly}/{total} = {rate:.2%}", "< 2%",
            action="异常 = 不符合**任何一种**已登记的合法 CM 顺序（ingest/order_families.py 的 "
                   "DRAFT_ORDERS，十族 20/22/24 手），不是「不符合 §6.0 那一种」；"
                   "若真的变高，先看 draft_anomalies.kinds 的分布再决定。")
    # 绝对数：比率合格**不等于**判定还在工作（见 ANOMALY_TOLERANCE 的注释）。
    v.check(abs(n_anomaly - MEASURED_ANOMALIES) <= ANOMALY_TOLERANCE * MEASURED_ANOMALIES,
            "M1-4 异常场次绝对数 = 实测值 ±5%（§15①）",
            n_anomaly, f"{MEASURED_ANOMALIES} ± {ANOMALY_TOLERANCE:.0%}",
            action="比率合格但绝对数偏离，说明「异常」的判定范围变了（DRAFT_ORDERS 被加宽/"
                   "收紧）或语料规模变了 —— 两者都必须有人看一眼再决定，不能只看比率。")
    # 偏离场次必须**逐场**有记录，且 kinds 非空（空 kinds 等于没记原因）。
    n_anom_rows = _count(db, "SELECT count(*) FROM draft_anomalies")
    v.check(n_anom_rows == n_anomaly, "M1-4 draft_anomalies 与 matches.anomaly 互证（§5.3）",
            f"{n_anom_rows} vs {n_anomaly}", "相等",
            action="loader 重新加载时必须删除不再异常的场次的旧行（§5.2 幂等），"
                   "否则两张表会分叉 —— 而 M1 正是这么校验的。")
    empty_kinds = _count(db, "SELECT count(*) FROM draft_anomalies"
                             " WHERE kinds IS NULL OR cardinality(kinds) = 0")
    v.check(empty_kinds == 0, "M1-4 异常行必须给出 kinds（§5.3）", empty_kinds, "0",
            action="只报「异常」不报「差在哪」等于没报；kinds 由 "
                   "ingest.load_bootstrap.detect_anomaly 给出。")
    kind_rows = db.execute("SELECT k, count(*) FROM draft_anomalies, unnest(kinds) AS k"
                           " GROUP BY 1 ORDER BY 1").fetchall()
    kinds = [k for k, _ in kind_rows]
    v.check(kinds == ["type_deviation", "unregistered_hand_count"],
            "M1-4 异常 kind 只允许两档（§5.3 的口径）", kind_rows,
            "[('type_deviation', n), ('unregistered_hand_count', m)]",
            action="`short_draft` 是**已废的旧名**（20 手是合法手数，不是「手太少」）——"
                   "出现它说明库存的是旧 loader 写的行；新 kind 必须先在 detect_anomaly 里定义、"
                   "再同步本条与计划 Task 12 的 kinds 表。")

    # =============================================================== 条款 5：三表可查
    # 规格 §14 M1:「matches/draft_actions/leagues 可查」——用真实 SELECT 取行，而不是
    # `SELECT count(*) ... LIMIT 1`（后者返回表总行数，LIMIT 只作用于聚合后的那一行；
    # 实测 `211051 == 1` 必然失败，是计划原文的错）。
    for table in ("matches", "draft_actions", "leagues"):
        row = db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        v.check(row == (1,), f"M1-5 {table} 可查（§14 M1）", row, "(1,)")

    # =============================================================== 补充 1：token 索引
    # 规格 §15「token 映射测试」+ §10.1：序列 token 用 dense_index 而非 hero_id。
    n_tokens = _count(db, "SELECT count(*) FROM hero_token_index")
    v.check(n_tokens == N_HEROES, "M1-6 hero_token_index 行数（§5.1/§10.1）",
            n_tokens, str(N_HEROES), action=_snapshot_action("hero_token_index"))
    bad_dense = _count(db, """SELECT count(*) FROM (
        SELECT dense_index, row_number() OVER (ORDER BY hero_id) - 1 AS expected
        FROM hero_token_index) t WHERE dense_index <> expected""")
    v.check(bad_dense == 0, "M1-6 dense_index 派生规则（§5.1:214–216）", bad_dense, "0",
            action="dense_index = row_number() OVER (ORDER BY hero_id) - 1；不一致说明映射被改过。")
    dense_135 = db.execute("SELECT dense_index FROM hero_token_index WHERE hero_id = 135").fetchone()
    v.check(dense_135 == (MEASURED_HERO_135_DENSE,),
            "M1-6 hero_id 135 → dense_index（§15「token 映射测试」点名的锚点）",
            dense_135, f"({MEASURED_HERO_135_DENSE},)",
            action="127 个英雄占 1..155 里的 127 个 id（28 个空位），故 hero_id ≠ dense_index；"
                   f"135 → {MEASURED_HERO_135_DENSE} 是实测锚点，token 必须用后者。")

    # =============================================================== 补充 2：配置键
    # 规格 §5.1：这四个键未种入则 §6.6/§7/§9.1 的相关功能不可用。
    keys = {r[0] for r in db.execute("SELECT key FROM app_config_kv")}
    required_keys = {"archetype_role_map", "robustness_lambda",
                     "op_decision_min_delta", "min_sample_n"}
    v.check(required_keys <= keys, "M1-7 app_config_kv 四个必需键（§5.1）",
            sorted(keys), f"⊇ {sorted(required_keys)}",
            action="由 constants.load.load_constants 种入；缺键说明常量层没跑完。")

    # =============================================================== 补充 3：状态机
    # 规格 §5.2：没有 BP 序列 = pending（在等数据），**不是**异常。
    n_pending = _count(db, "SELECT count(*) FROM matches WHERE draft_state = 'pending'")
    v.check(n_pending == MEASURED_PENDING, "M1-8 pending 场次（§5.2 状态机）",
            n_pending, str(MEASURED_PENDING),
            action="pending 与 anomaly 是两件事：实测这 6,046 场 anomaly=false 却没有 BP 序列。")
    contradictory = _count(db, """SELECT count(*) FROM matches
        WHERE (draft_state = 'pending') <> (n_draft_actions IS NULL)""")
    v.check(contradictory == 0, "M1-8 pending ⟺ n_draft_actions IS NULL（§5.2）",
            contradictory, "0",
            action="draft_state 与手数必须同源（loader 的 _write_match）。")

    # =============================================================== 补充 4：版本归属
    # 规格 §3.2/§15「版本归属测试」：Valve 清单从 7.08 = 2018-02-01（1517472000）起，
    # 更早的场次**必须** patch_id IS NULL，之后的**必须**有值（0 个晚期 NULL）。
    n_pre = _count(db, "SELECT count(*) FROM matches WHERE started_at < to_timestamp(1517472000)")
    v.check(n_pre == MEASURED_PRE_2018, "M1-9 pre-2018 场次（§3.2 归属边界）",
            f"{n_pre}/{total} = {n_pre/total:.2%}" if total else n_pre, str(MEASURED_PRE_2018),
            action="pre-2018 占比是**测量结果**，不是预算；变了说明数据集年代分布变了。")
    late_nulls = _count(db, "SELECT count(*) FROM matches WHERE patch_id IS NULL"
                            " AND started_at >= to_timestamp(1517472000)")
    v.check(late_nulls == 0, "M1-9 归属边界之后 patch_id 必须全覆盖（§3.2）", late_nulls, "0",
            action="该日之后 Valve 清单必须覆盖；出现 NULL 说明归属实现退化成区间二分/钳位。")
    pre_nulls = _count(db, "SELECT count(*) FROM matches WHERE patch_id IS NULL"
                           " AND started_at < to_timestamp(1517472000)")
    v.check(pre_nulls == n_pre, "M1-9 pre-2018 必须全部 patch_id IS NULL（§3.2 反钳位）",
            f"{pre_nulls}/{n_pre}", "全部 NULL",
            action="对 patches.released_at 做二分/取最近行会把 2016–2017 的场次静默钳到 7.08，"
                   "必须走 subpatch_for_timestamp + version_name 查表。")

    # =============================================================== 补充 5：族全域分类
    # 规格 §15③：每个 anomaly=false 且有 BP 序列的场次必须**恰好**命中一个已登记族（零反例，
    # 实测 202,957/202,957）。
    #
    # 这里**复用** Task 12 的 `_family_hits_sql()`（而不是再写一份 join）：族逻辑必须只有
    # 一个真源，两份 SQL 必然漂移；该函数只依赖 `ingest.order_families.DRAFT_ORDERS`，
    # 与 pytest 会话状态无关（import 它不注册任何插件/夹具）。代价是一次约 11 s 的查询。
    from tests.ingest.test_order_families import (MEASURED_CLASSIFIED_TOTAL,
                                                  MEASURED_FAMILY_SUPPORT, _family_hits_sql)
    sql, params = _family_hits_sql()
    universe, classified, ambiguous, legal_but_flagged, families = db.execute(sql, params).fetchone()
    families = families or {}
    v.check(universe == MEASURED_CLASSIFIED, "M1-10 族分类分母（§15③）",
            universe, str(MEASURED_CLASSIFIED),
            action=_snapshot_action("anomaly=false 且有 BP 序列的场次"))
    v.check(classified == universe, "M1-10 零未分类（§15③：每场恰好命中一族）",
            f"{classified}/{universe}", "相等",
            action="有场次对不上任何已登记族 —— 要么 DRAFT_ORDERS 少了一族，"
                   "要么 loader 的异常判定与它不一致。")
    v.check(ambiguous == 0, "M1-10 恰好一族（§15③）", ambiguous, "0",
            action="同手数的族模板两两不同；命中多族说明模板表被改重了。")
    v.check(legal_but_flagged == 0, "M1-10 入库的 anomaly 与独立 SQL 判定互证（§5.3）",
            legal_but_flagged, "0",
            action="入库用 Python 的 family_for，这里用 SQL 模板 join —— 两套实现必须逐场一致。")
    v.check(families == MEASURED_FAMILY_SUPPORT, "M1-10 逐族支持度 = 实测分布（§15③）",
            families, str(MEASURED_FAMILY_SUPPORT),
            action="支持度表在 tests/ingest/test_order_families.py；族是**派生量、不落库**，"
                   "变了先确认 DRAFT_ORDERS，再更新那张实测表。")
    v.check(sum(families.values()) == MEASURED_CLASSIFIED_TOTAL,
            "M1-10 十族之和 = 已分类总数（§15③）",
            sum(families.values()), str(MEASURED_CLASSIFIED_TOTAL))

    # =============================================================== 条款 B：契约侧（M0）
    # Task 13 是本计划最后一个能把这些钉成**单一验收门**的机会。
    _check_contract_clauses(v)

    # 一次运行把全部违规报出来（见 M1Violations 的 docstring）。
    print(f"M1 实测：matches={total} draft_actions={n_actions} leagues={n_leagues} "
          f"patches={n_patches}/{n_lettered} 字母 heroes={n_heroes} items={n_items} "
          f"anomaly={n_anomaly}/{total}={rate:.2%} pending={n_pending} "
          f"pre2018={n_pre} 已分类={classified}/{universe}")
    v.raise_if_any()


def _check_contract_clauses(v: M1Violations) -> None:
    """M0 的契约条款（规格 §14 的 M0 行）：openapi / fixtures / 目录边界 / TS 生成物。

    四条的**单条守护**都已存在（`tests/contracts/`），这里复核的是「它们作为**一套**可交付」：
    M1 之后三条线要按这份契约并行开工，契约本身必须是可校验、可生成、边界齐备的。
    """
    import yaml
    from openapi_spec_validator import validate as validate_openapi

    # B1：契约文档过官方 OpenAPI validator。`tests/contracts/` 用的是 jsonschema **子 schema**
    # 校验，不含文档级校验；文档级那条只在 `Makefile` 的 lint-spec 目标里（`make` 本机不可用），
    # 故这一条是本文件独有的 —— M0 的「通过 schema 校验」到这里才真正变成可执行的断言。
    spec_path = ROOT / "contracts" / "openapi.yaml"
    try:
        doc = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        validate_openapi(doc)
        openapi_ok, detail = True, f"openapi {doc.get('openapi')}"
    except Exception as exc:                      # validator 抛的是自家异常族，类型不稳定
        openapi_ok, detail = False, f"{type(exc).__name__}: {exc}"
    v.check(openapi_ok, "M0-B1 contracts/openapi.yaml 过 OpenAPI schema 校验（§14 M0）",
            detail, "校验通过",
            action="运行 python -m contracts.tools.build_openapi 重建，再核 contracts/schemas/*.yaml。")

    # B2：17 个 fixture 全部过契约校验（schema + 该资源的不变式）。
    from contracts.tools.validate_fixtures import main as validate_fixtures
    n_fixtures = len(list((ROOT / "contracts" / "fixtures").glob("*.json")))
    rc = validate_fixtures()
    v.check(rc == 0, "M0-B2 全部 fixture 过契约校验（§14 M0）", f"main()={rc}", "0",
            action=f"逐条跑 python -m contracts.tools.validate_fixtures（当前 {n_fixtures} 个 fixture）。")
    v.check(n_fixtures == 17, "M0-B2 fixture 数量 = 17（§11 的边界表）", n_fixtures, "17",
            action="新增/删除 fixture 必须同步 tests/contracts/test_fixtures.py 的 CASES 表与计划。")

    # B3：三条线的目录边界（规格 §11）—— M1 之后并行开工的前提。
    present = sorted(d for d in LINE_DIRECTORIES if (ROOT / d).is_dir())
    v.check(len(present) == len(LINE_DIRECTORIES),
            "M0-B3 三条线目录边界存在（§11/§14 M0）",
            present, f"全部 {list(LINE_DIRECTORIES)}",
            action="analysis/（线 A）、models/（线 C）、web/（前端）是并行开发的物理边界。")

    # B4：`web/src/types/contract.ts` 由契约生成且**新鲜**（--check 只比不写）。
    ts = subprocess.run([sys.executable, "-m", "contracts.tools.gen_ts_types", "--check"],
                        capture_output=True, text=True, cwd=ROOT)
    v.check(ts.returncode == 0, "M0-B4 web/src/types/contract.ts 新鲜（§14 M0）",
            f"--check exit {ts.returncode}", "exit 0",
            action=f"契约改了就要重新生成并提交：make contract-ts"
                   f"（{ts.stdout.strip()}{ts.stderr.strip()}）")
    v.check(TS_TYPES.is_file(), "M0-B4 生成物存在", str(TS_TYPES), "存在")
