"""实测的合法 CM 顺序族与族判定 —— **规格 §6.0 的模板只是其中一族**（§5.3 的异常口径）。

下游（`analysis/` 与 `models/`）判定/过滤一场比赛所属顺序族时，**只从这里取**。

## 为什么需要它

`shared/draft_template.TEMPLATE`（规格 §6.0）是 **2026 年现行 CM 顺序**的定义，不是全库唯一的
合法顺序：Valve 历次改版换过 CM 的 ban 顺序，2016–2026 的 Kaggle 语料（**211,051 场**，
实测 2026-09-18）里存在 **20 / 22 / 24 三种手数、十种支持度 >= 1% 的合法顺序**。
只认 §6.0 那一种模板判异常，首次真实入库实测把 **58.33%** 的正常比赛判成异常
（异常率断言必然假红，而"异常"这个字段也就失去了意义）。

## 下游契约（消费者必须按这张表使用）

| 项 | 约定 |
|---|---|
| 入口 | `family_for(n_actions, first_pick_team, actions)` → 族名（`str`）或 `None` |
| 输入 | `n_actions` = `matches.n_draft_actions`；`first_pick_team` = `matches.first_pick_team`；`actions` = 该场 `draft_actions` 的 `{"ord", "is_pick", "team"}`（**顺序无关**，内部按 `ord` 排序） |
| 唯一性 | 族由上述三者**唯一复算**：`family_for` 返回 `None` 当且仅当该场不与任何已登记族逐手一致 |
| **派生量，不落库** | `matches` **没有**族列，`draft_anomalies.detail->>'order_family'` 对异常行是 `null`。族一律现算；**不得**要求入库层加列（契约 §5.1 的 DDL 冻结），也**不得**在别处再抄一份 `DRAFT_ORDERS`（多份副本必然漂移） |
| 模板选择 | `shared.draft_template.resolve()` **只对 `spec_6_0_24` 族正确**；其它族必须用本模块的 `resolve_in()` |
| `resolve_in` 的错误行为 | 与 `shared.draft_template.resolve` 一致：**未知族名 / `first_pick_team` 不在 {0,1} → `ValueError`**（不回落、不猜）。`ord` 必须在该族手数内，越界即调用方违约 —— 两个调用方（`family_for` / `deviations`）都已先行跳过越界 ord |
| 畸形输入 | ord 有**空洞**（如 20 手但 ords = `0..18, 20`）时 `family_for` 返回 `None`、`is_legal` 返回 `False`，**绝不抛异常**：入库循环跑 21 万场，一次 `IndexError` 就会中断整轮（违反规格 §5.3 的"不阻断入库"），下游拿 `is_legal(...)` 当过滤器同样会被打挂 |
| 异常口径 | `family_for(...) is None` 且手数属于 `HAND_COUNTS` → `type_deviation`；手数不在 `HAND_COUNTS` 里 → 手数本身未登记。**时代不同但合法的顺序不是异常**（`anomaly=false`、`draft_state='complete'`） |

## 登记门槛与实测支持度

登记门槛：**在各自手数里支持度 >= 1%**（尾部稀有 pattern 一律算异常）。实测（2026-09-18，
`tests/fixtures/kaggle/` 的全部 19 个目录，202,957 场 `anomaly=false` 且有 BP 序列的场次）：

| 族 | 手数 | 场次 | 占该手数 | 出现的年度 |
|---|---|---|---|---|
| `cm24_a` | 24 | 68,775 | 46.00% | 2023–2025 |
| `cm24_b` | 24 | 46,347 | 31.00% | 2021–2023 |
| `spec_6_0_24`（规格 §6.0 = `shared/draft_template.TEMPLATE`） | 24 | 15,135 | 10.12% | 2025 下半年 + 全部 2026 目录 |
| `cm24_c` | 24 | 14,045 | 9.39% | 2020–2021 |
| `cm24_d` | 24 | 5,220 | 3.49% | 仅 2021 |
| `cm22_a` | 22 | 26,126 | 67.04% | 2018–2020 |
| `cm22_b` | 22 | 8,035 | 20.62% | 仅 2020 |
| `cm22_c` | 22 | 4,767 | 12.23% | 2017–2018 |
| `cm20_a` | 20 | 12,340 | 84.85% | 2016–2017 |
| `cm20_b` | 20 | 2,167 | 14.90% | 仅 2016 |

十族合计 202,957 场 = 全部 `anomaly=false` 且有 BP 序列的场次（**零未分类**，见
`tests/ingest/test_order_families.py` 的全量守护）。`spec_6_0_24` 占**全库** 7.17%
（15,135/211,051）、占有 BP 序列场次的 7.38%、占 24 手场次的 10.12%；2026 年有 BP 序列的
场次里 99.59%（14,131/14,189）是它，其余 58 场是异常。**故它不能当作全库口径的合法性判据。**

「归属」列是相对**先手方** F（由 ord=0 的 team 推出，规格 §8①）的；实测 ord=0 的队与第一手
pick 的队永远相同（§16.3 的不变式在真实数据上成立）。
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from shared.draft_template import TEMPLATE as SPEC_TEMPLATE

_F = "F"          # 先手方（first pick team）
_O = "O"          # 后手方

#: 规格 §6.0 那一族的族名（`shared/draft_template.TEMPLATE` 的**唯一**合法标签）。
SPEC_FAMILY = "spec_6_0_24"

#: 实测的合法 CM 顺序族。每族一个 `(is_pick, 归属)` 元组序列，len() = 该族的手数。
DRAFT_ORDERS: dict[str, tuple[tuple[bool, str], ...]] = {
    # bbbbPPPPbbbbPPPPbbPP / FOFOFOOFOFOFOFOFOFFO —— 12340 场（84.85%），2016/2017
    "cm20_a": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _O), (False, _F), (False, _O), (False, _F),
        (True, _O), (True, _F), (True, _O), (True, _F), (False, _O), (False, _F),
        (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbPPPPbbPP / FOFOFOOFFOFOOFOFOFOF —— 2167 场（14.90%），仅 2016
    "cm20_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _O), (True, _F), (True, _O), (True, _F), (False, _O), (False, _F),
        (True, _O), (True, _F),
    ),
    # bbbbbbPPPPbbbbPPPPbbPP / FOFOFOFOOFFOFOOFOFOFFO —— 26126 场（67.04%），2018-2020
    "cm22_a": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _F), (True, _O), (True, _O), (True, _F), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbbbPPPPbbPPPPbbPP / FOFOFOFOFOOFFOOFOFOFFO —— 8035 场（20.62%），仅 2020
    "cm22_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _F), (True, _O), (True, _O), (True, _F),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbPPPPbbbbPPPPbbPP / FOFOFOFOOFOFOFOFOFOFFO —— 4767 场（12.23%），2017/2018
    "cm22_c": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _F), (True, _O), (True, _O), (True, _F), (False, _O), (False, _F),
        (False, _O), (False, _F), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbbPPbbbPPPPPPbbbbPP / FOOFOOFFOFFOOFFOOFFOOFFO —— 68775 场（46.00%），2023-2025
    "cm24_a": (
        (False, _F), (False, _O), (False, _O), (False, _F), (False, _O), (False, _O),
        (False, _F), (True, _F), (True, _O), (False, _F), (False, _F), (False, _O),
        (True, _O), (True, _F), (True, _F), (True, _O), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOOFFOFOFOOFFOFOFOFO —— 46347 场（31.00%），2021-2023
    "cm24_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _F), (True, _O),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOFOFOFOFOOFOFFOFOFO —— 14045 场（9.39%），2020/2021
    "cm24_c": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _F), (True, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOOFFOFOFOOFOFFOFOFO —— 5220 场（3.49%），仅 2021
    "cm24_d": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # 规格 §6.0 / `shared/draft_template.TEMPLATE`（**直接 import，不复制**）。
    # 实测就是 2025 年下半年起 + 全部 2026 目录在用的那一族（15135 场 = 24 手场次的 10.12%），
    # 也是 OpenDota 实时 API 上 match 8996973546 的顺序 —— 即**当下**的 CM 顺序。
    # 2023–2025 上半年的比赛用的是 cm24_a（46.00%），与它只差两段 ban。
    # 键**必须**等于 SPEC_FAMILY（`tests/ingest/test_order_families.py` 断言）。
    "spec_6_0_24": tuple(SPEC_TEMPLATE),
}

#: 已登记族的手数集合（{20, 22, 24}）。手数不在此集合里 = 手数本身没登记，
#: 与"手数登记了但顺序不在任何族里"（`type_deviation`）是两回事。
HAND_COUNTS: frozenset[int] = frozenset(len(template) for template in DRAFT_ORDERS.values())


def _template(family: str) -> tuple[tuple[bool, str], ...]:
    """族模板；未知族名一律 `ValueError`（`resolve_in` / `deviations` 共用同一错误口径）。"""
    try:
        return DRAFT_ORDERS[family]
    except KeyError:
        raise ValueError(f"未登记的族：{family!r}（已登记：{sorted(DRAFT_ORDERS)}）") from None


def resolve_in(family: str, ord_: int, first_pick_team: int) -> tuple[bool, int]:
    """按**指定顺序族**推导该手的 `(is_pick, team)`，语义与 `shared.draft_template.resolve` 相同。

    `shared.draft_template.resolve` 只定义 §6.0 那一种（`spec_6_0_24`）；其它族一律走这里。

    错误行为（**显式**，与 `shared.draft_template.resolve` 同一口径 —— 不回落、不猜）：

    - 未知族名 → `ValueError`（回落成 `spec_6_0_24` 会把别的年代的手序静默错判）；
    - `first_pick_team` 不在 {0,1} → `ValueError`（没有先手方就没有"应当是哪一队"）；
    - `ord_` 必须在该族的手数内 —— 越界是**调用方违约**，故本函数不做钳位，直接由模板索引
      拒绝（`IndexError`）。两个调用方都已先行跳过越界 ord：`family_for` 在整族比较前跳过
      有空洞的输入，`deviations` 逐手跳过越界 ord。
    """
    template = _template(family)
    if first_pick_team not in (0, 1):
        raise ValueError(f"first_pick_team 必须是 0 或 1，收到 {first_pick_team!r}")
    is_pick, who = template[ord_]
    return is_pick, (first_pick_team if who == _F else 1 - first_pick_team)


def _sorted_actions(actions: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return sorted(actions, key=lambda a: int(a["ord"]))


def family_for(n_actions: int, first_pick_team: int | None,
               actions: Sequence[Mapping[str, object]]) -> str | None:
    """该场命中的顺序族名；没有任何已登记族逐手一致时返回 `None`。

    契约（下游必须按此调用）：

    - `n_actions` 必须等于 `len(actions)`（= `matches.n_draft_actions` = 该场 `draft_actions` 行数）；
      两者不一致（入库边界丢过手/加了手）时返回 `None`，不猜。
    - `first_pick_team` 必须是 0/1；`None` 或不合法时返回 `None`（缺 ord=0 的场次本就算异常）。
    - `actions` 顺序无关；元素只需 `{"ord", "is_pick", "team"}` 三个键（`hero_id` 不参与判定）。
    - 同一场**至多**命中一族（同手数的模板两两不同），故返回值可以直接当分类用。
    - **ord 有空洞时返回 `None`，不抛异常**：任一手落在该族手数之外（如 20 手却出现 ord=20），
      这一族就不可能逐手一致，`continue` 到下一族即可。异常是**数据**（写进
      `draft_anomalies.kinds`），不是崩溃 —— 见模块 docstring 的「畸形输入」行。
    """
    if first_pick_team not in (0, 1):
        return None
    if n_actions != len(actions):
        return None
    ordinals = [a["ord"] for a in actions]
    if any(type(ord_) is not int for ord_ in ordinals):
        return None
    if set(ordinals) != set(range(n_actions)):
        return None
    for family, template in DRAFT_ORDERS.items():
        if len(template) != n_actions:
            continue
        if all((bool(a["is_pick"]), int(a["team"])) == resolve_in(family, a["ord"],
                                                                 first_pick_team)
               for a in actions):
            return family
    return None


def is_legal(n_actions: int, first_pick_team: int | None,
             actions: Sequence[Mapping[str, object]]) -> bool:
    """该场是否与**某一**已登记的合法 CM 顺序逐手一致（规格 §5.3 的 `anomaly=false` 口径）。"""
    return family_for(n_actions, first_pick_team, actions) is not None


def deviations(actions: Sequence[Mapping[str, object]], first_pick_team: int,
               family: str) -> list[dict]:
    """该场与**指定族**的逐手偏差（`draft_anomalies.detail.deviations` 用）。

    越界 ord（该族手数之外，如有空洞的 20 手场次里的 ord=20）**跳过而不是索引模板**：
    逐手偏差只回答"与模板对不上的手"，"手本身缺了/多了"是 `kinds` 那一路的事实。
    未知族名与 `resolve_in` 同一口径：`ValueError`。
    """
    template_len = len(_template(family))
    out = []
    for action in _sorted_actions(actions):
        ord_ = int(action["ord"])
        if not 0 <= ord_ < template_len:
            continue
        want_pick, want_team = resolve_in(family, ord_, first_pick_team)
        if bool(action["is_pick"]) != want_pick or int(action["team"]) != want_team:
            out.append({"ord": ord_, "is_pick": bool(action["is_pick"]),
                        "team": int(action["team"]),
                        "expected_is_pick": want_pick, "expected_team": want_team})
    return out


def closest_family(n_actions: int, first_pick_team: int | None,
                   actions: Sequence[Mapping[str, object]]) -> str | None:
    """手数相同、与实测差得最少的族（只为把偏差说清楚，不改变异常判定）。"""
    if first_pick_team not in (0, 1):
        return None
    candidates = [f for f, t in DRAFT_ORDERS.items() if len(t) == n_actions]
    if not candidates:
        return None
    actions = _sorted_actions(actions)
    return min(candidates, key=lambda f: len(deviations(actions, first_pick_team, f)))
