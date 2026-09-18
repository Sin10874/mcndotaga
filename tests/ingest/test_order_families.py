"""顺序族（规格 §5.3 / §6.0 澄清）：**全量**守护 + 下游契约。

规格 §6.0 的 `shared/draft_template.TEMPLATE` 只是 **2026 年那一族**；2016–2026 的语料里
实测有 **20 / 22 / 24 三种手数、十种支持度 >= 1% 的合法顺序**。族逻辑的唯一权威是
`ingest/order_families.py`（下游 `analysis/`、`models/` 必须调它，不得各自抄表）。

本文件分三段：

1. **纯函数**：登记表的自洽性、`family_for` 的契约（顺序无关、n_actions 必须等于 len、
   `None` 的语义）、`spec_6_0_24` 逐字等于共享模板；
2. **全量数据侧**（需 506.7 MB 缓存）：202,957 场 `anomaly=false` 且有 BP 序列的场次
   **零未分类、恰好一族**，逐族支持度等于实测表，且入库写下的 `matches.anomaly` 与该判定
   逐场一致（两套独立实现互证）；
3. **稳定性**：同一输入重跑两次，族分布与异常种类逐项相同（族是派生量、不落库，
   重跑的稳定性只能由"同一输入 → 同一分类"保证）。
"""
from __future__ import annotations

import json

import pytest

from tests.ingest.conftest import draft_actions_for, load_all

#: 实测支持度（2026-09-18，`tests/fixtures/kaggle/` 的全部 19 个目录，提交 `6dca26e` 的缓存快照）。
#: 这些数字是**测出来的**，不是目标值：重下上游数据集（版本会变）时它们必须跟着改，
#: 改的那一刻就该有人看一眼新分布而不是无脑刷新。
MEASURED_FAMILY_SUPPORT: dict[str, int] = {
    "cm24_a": 68_775, "cm24_b": 46_347, "spec_6_0_24": 15_135,
    "cm24_c": 14_045, "cm24_d": 5_220,
    "cm22_a": 26_126, "cm22_b": 8_035, "cm22_c": 4_767,
    "cm20_a": 12_340, "cm20_b": 2_167,
}

#: 有 BP 序列且 `anomaly=false` 的场次总数（= 十族支持度之和）。
MEASURED_CLASSIFIED_TOTAL = 202_957


def _family_hits_sql() -> tuple[str, list]:
    """由 `DRAFT_ORDERS`（**单一真源**）生成"逐族逐手命中"的 SQL 与参数。

    为什么用 SQL 而不是把 4.7M 行 `draft_actions` 拉进 Python：那一趟要几十秒且吃内存；
    十族模板与 `draft_actions` 的等值 join 交给 Postgres，一次约 14 s。

    返回的查询给出五个数（见 `test_every_anomaly_false_match_classifies_into_exactly_one_family`）：
    全库分母、被判定为合法的场次数、命中多族的场次数、被判定合法但 `matches.anomaly=true` 的
    场次数、以及逐族支持度（JSON）。
    """
    from ingest.order_families import DRAFT_ORDERS

    values: list[str] = []
    params: list = []
    for family, template in DRAFT_ORDERS.items():
        for ord_, (is_pick, who) in enumerate(template):
            values.append("(%s,%s,%s,%s,%s)")
            params += [family, len(template), ord_, is_pick, who]
    sql = f"""
    WITH tmpl(family, n, ord, is_pick, who) AS (VALUES {", ".join(values)}),
    hits AS (
        -- 一场 × 一族一行：只有"该场每一手都与这一族逐手一致"才留下（count(*) = 族的手数）
        SELECT m.match_id, t.family, bool_or(m.anomaly) AS flagged
        FROM matches m
        JOIN draft_actions d ON d.match_id = m.match_id
        JOIN tmpl t ON t.n = m.n_draft_actions AND t.ord = d.ord
        WHERE m.first_pick_team IN (0, 1)
        GROUP BY m.match_id, t.family, t.n, m.first_pick_team
        HAVING count(*) = min(t.n)
           AND bool_and(d.is_pick = t.is_pick
                        AND d.team = CASE WHEN t.who = 'F' THEN m.first_pick_team
                                          ELSE 1 - m.first_pick_team END)
    ),
    per_match AS (
        SELECT match_id, count(*) AS n_families, bool_or(flagged) AS flagged
        FROM hits GROUP BY match_id
    )
    SELECT
        (SELECT count(*) FROM matches
          WHERE NOT anomaly AND n_draft_actions IS NOT NULL) AS universe,
        (SELECT count(*) FROM per_match WHERE NOT flagged) AS classified,
        (SELECT count(*) FROM per_match WHERE NOT flagged AND n_families <> 1) AS ambiguous,
        (SELECT count(*) FROM per_match WHERE flagged) AS legal_but_flagged,
        (SELECT jsonb_object_agg(family, n) FROM (
             SELECT family, count(*) AS n FROM hits WHERE NOT flagged GROUP BY 1) s) AS families
    """
    return sql, params


# ===========================================================================
# 纯函数：登记表与 family_for 的契约
# ===========================================================================

def test_every_registered_family_is_self_consistent_and_covers_20_22_24_hands():
    """登记表必须自洽：每族与自身逐手一致、`family_for` 回同一个族名、`is_legal` 为真。

    另钉三条结构事实：手数集合 = {20, 22, 24}；十族；族名唯一（字典键）。
    """
    from ingest.order_families import DRAFT_ORDERS, HAND_COUNTS, family_for, is_legal

    assert len(DRAFT_ORDERS) == 10, sorted(DRAFT_ORDERS)
    assert HAND_COUNTS == frozenset({20, 22, 24}), sorted(HAND_COUNTS)
    for family in DRAFT_ORDERS:
        for first_pick in (0, 1):
            actions = [{"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
                       for ord_, is_pick, team, _hero in draft_actions_for(family, first_pick)]
            assert family_for(len(actions), first_pick, actions) == family
            assert is_legal(len(actions), first_pick, actions) is True
            # 镜像：先手方翻转后仍是同一族（归属由 first_pick_team 决定）
            assert family_for(len(actions), 1 - first_pick,
                              [{"ord": a["ord"], "is_pick": a["is_pick"],
                                "team": 1 - a["team"]} for a in actions]) == family


def test_spec_6_0_24_is_exactly_the_shared_contract_template():
    """`DRAFT_ORDERS` 里的 §6.0 那一族必须**逐字**等于 `shared/draft_template.TEMPLATE`。

    这条防的是"为了迁就数据把契约偷偷改掉"：`shared/draft_template.py` 是规格 §6.0 的唯一定义处，
    `ingest/order_families.py` 只能引用它，不能另写一份。同时钉住：契约模板 = 24 手，
    且它只是十族之一（历史比赛用别的族，且**不是异常**）。
    """
    from ingest.order_families import DRAFT_ORDERS, SPEC_FAMILY
    from shared.draft_template import TEMPLATE

    assert SPEC_FAMILY == "spec_6_0_24"
    assert SPEC_FAMILY in DRAFT_ORDERS
    assert DRAFT_ORDERS[SPEC_FAMILY] == tuple(TEMPLATE)
    assert len(DRAFT_ORDERS[SPEC_FAMILY]) == 24
    # 契约模板与其余九族都不同 —— 否则"§6.0 只对 spec_6_0_24 正确"这句话就没有内容
    others = {name: t for name, t in DRAFT_ORDERS.items() if name != SPEC_FAMILY}
    assert DRAFT_ORDERS[SPEC_FAMILY] not in others.values()


def test_family_for_contract_is_order_insensitive_and_rejects_inconsistent_input():
    """`family_for` 的契约：`actions` 顺序无关；`n_actions` 必须等于 `len(actions)`；
    `first_pick_team` 不合法一律 `None`（消费方拿到的 `None` = 该场不可对齐任何合法顺序）。"""
    from ingest.order_families import family_for, is_legal

    actions = [{"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
               for ord_, is_pick, team, _hero in draft_actions_for("cm22_b", 1)]
    assert family_for(22, 1, actions) == "cm22_b"
    assert family_for(22, 1, list(reversed(actions))) == "cm22_b"      # 顺序无关
    assert family_for(22, 1, actions) == family_for(22, 1, actions)    # 幂等

    assert family_for(21, 1, actions) is None        # n_actions 与 len 不一致 → 不猜
    assert family_for(22, None, actions) is None     # 缺先手方
    assert family_for(22, 2, actions) is None        # 非法先手方
    assert is_legal(22, 1, actions) is True

    broken = [dict(a) for a in actions]
    broken[7]["team"] = 1 - broken[7]["team"]        # 一手归属方写反 → 不再属于任何族
    assert family_for(22, 1, broken) is None
    assert is_legal(22, 1, broken) is False

    # 空 actions：0 手是 pending（规格 §5.2），不是"合法族"
    assert family_for(0, None, []) is None


def test_closest_family_points_at_the_least_deviating_registered_family():
    """偏差诊断：写反一手时，最近的族必须是正确的那个，且偏差位置/期望值都要报出来。"""
    from ingest.order_families import closest_family, deviations

    actions = [{"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
               for ord_, is_pick, team, _hero in draft_actions_for("cm24_c", 0)]
    actions[3] = {**actions[3], "team": 1 - actions[3]["team"]}
    nearest = closest_family(len(actions), 0, actions)
    assert nearest == "cm24_c"
    diff = deviations(actions, 0, nearest)
    assert [d["ord"] for d in diff] == [3]
    assert diff[0]["team"] != diff[0]["expected_team"]


# ===========================================================================
# 全量数据侧：零未分类 + 恰好一族 + 与入库写的 anomaly 互证
# ===========================================================================

def test_every_anomaly_false_match_classifies_into_exactly_one_family(db_after_bootstrap):
    """**全量**（不是抽样）：每一场有 BP 序列且 `anomaly=false` 的比赛，都恰好命中一族。

    四个断言各挡一种失败：

    1. `universe == MEASURED_CLASSIFIED_TOTAL` —— 分母对得上（202,957）；
    2. `classified == universe` —— **零未分类**（任何一场对不上所有族都会在这里露出来）；
    3. `ambiguous == 0` —— **恰好一族**（同手数模板两两不同，命中两族说明实现错了）；
    4. `legal_but_flagged == 0` —— 入库写下的 `matches.anomaly` 与本文件的独立 SQL 判定
       **逐场一致**（入库用的是 Python 的 `family_for`，这里是 SQL 模板 join：两套实现互证）。

    变异守护：从 `DRAFT_ORDERS` 里摘掉任何一族 → (2) 立刻失败（见计划 Task 12 的变异证明）。
    """
    total = db_after_bootstrap.execute(
        "SELECT count(*) FROM matches WHERE NOT anomaly AND n_draft_actions IS NOT NULL"
    ).fetchone()[0]
    if total == 0:
        pytest.skip("引导库为空（常量层/缓存没就绪）")

    sql, params = _family_hits_sql()
    universe, classified, ambiguous, legal_but_flagged, families = \
        db_after_bootstrap.execute(sql, params).fetchone()
    families = families or {}
    print(f"全库分母 {universe}；判定合法 {classified}；命中多族 {ambiguous}；"
          f"判定合法却被标 anomaly {legal_but_flagged}")
    print(f"逐族支持度：{json.dumps(families, ensure_ascii=False, sort_keys=True)}")

    assert universe == MEASURED_CLASSIFIED_TOTAL, \
        f"分母变了：{universe} != {MEASURED_CLASSIFIED_TOTAL}（上游数据集换版本？）"
    assert classified == universe, (
        f"{universe - classified} 场 anomaly=false 的比赛对不上任何已登记的合法顺序"
        f"（零反例是规格 §15 的硬要求）")
    assert ambiguous == 0, f"{ambiguous} 场同时命中多个族：模板之间不该重叠"
    assert legal_but_flagged == 0, (
        f"{legal_but_flagged} 场被独立判定为合法、入库却标了 anomaly=true："
        f"入库的族判定与 ingest.order_families 不一致")


def test_family_support_counts_match_the_measured_distribution(db_after_bootstrap):
    """十族的支持度必须与**实测分布**逐族相同，且每族在各自手数里 >= 1%（登记门槛）。

    实测（2026-09-18 全量缓存，命令见计划 Task 12 实测记录）：

    | 手数 | 族 → 场次 |
    |---|---|
    | 24 | `cm24_a` 68,775 / `cm24_b` 46,347 / `spec_6_0_24` 15,135 / `cm24_c` 14,045 / `cm24_d` 5,220 |
    | 22 | `cm22_a` 26,126 / `cm22_b` 8,035 / `cm22_c` 4,767 |
    | 20 | `cm20_a` 12,340 / `cm20_b` 2,167 |
    """
    from ingest.order_families import DRAFT_ORDERS

    if db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0] == 0:
        pytest.skip("引导库为空（常量层/缓存没就绪）")

    sql, params = _family_hits_sql()
    _universe, _classified, _ambiguous, _flagged, families = \
        db_after_bootstrap.execute(sql, params).fetchone()
    families = families or {}
    assert set(families) == set(DRAFT_ORDERS), (
        f"支持度为 0 或未登记的族：{sorted(set(DRAFT_ORDERS) ^ set(families))}")
    assert families == MEASURED_FAMILY_SUPPORT, (
        f"逐族支持度与实测表不符：\n实测 {MEASURED_FAMILY_SUPPORT}\n现在 {families}")
    assert sum(MEASURED_FAMILY_SUPPORT.values()) == MEASURED_CLASSIFIED_TOTAL

    by_hands: dict[int, int] = {}
    for family, count in families.items():
        by_hands[len(DRAFT_ORDERS[family])] = by_hands.get(len(DRAFT_ORDERS[family]), 0) + count
    for family, count in families.items():
        share = count / by_hands[len(DRAFT_ORDERS[family])]
        print(f"{family}（{len(DRAFT_ORDERS[family])} 手）：{count} 场 = 该手数的 {share:.2%}")
        assert share >= 0.01, f"{family} 在该手数里只有 {share:.2%}，低于 1% 登记门槛"


# ===========================================================================
# 稳定性：同一输入重跑两次 → 族分布逐项相同
# ===========================================================================

def test_family_distribution_is_stable_across_reloads(db, synthetic_cache):
    """族是**派生量、不落库**：它的稳定性只能靠"同一输入 → 同一分类"来保证。

    这里用合成数据集把 loader 跑两遍（8 场 / 4 族 / 2 类异常），断言
    `order_families` 与 `anomaly_kinds` 逐项相同、且非空（空字典的"相等"是空过）。

    全量语料的重跑（19 个目录 / 约 2 分钟）由 `db_after_bootstrap` 会话 fixture 承担一次；
    入库的全部写入幂等（`tests/ingest/test_bootstrap.py::test_reload_is_idempotent_...`），
    故"同输入 → 同行集 → 同族"这条链条在两个尺度上都被钉住。
    """
    first = load_all(db, synthetic_cache)
    second = load_all(db, synthetic_cache)

    assert first["order_families"], "合成数据没有命中任何族，这条稳定性断言是空过"
    assert first["order_families"] == second["order_families"]
    assert first["anomaly_kinds"] == second["anomaly_kinds"]
    # 重跑不得改变行数（幂等）：族分布稳定必须建立在行集稳定之上
    assert (first["matches"], first["draft_actions"], first["anomalies"]) == \
        (second["matches"], second["draft_actions"], second["anomalies"])
