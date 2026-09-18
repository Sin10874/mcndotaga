"""Kaggle 引导数据集 → `leagues` / `teams` / `matches` / `draft_actions` / `draft_anomalies`。

对应规格 §5.2（幂等）、§5.3（异常不阻断但必须记录）、§3.2/§15（版本归属）、
§10.2（`picks_bans.csv` 是 BP 序列的权威来源，`draft_timings.csv` 仅作补充 —— 本模块不读它）。

**这个模块是对着真实 CSV 写的，不是对着计划里的列名猜的。** 2026-09-18 在本机跑通真实下载后
逐列核对，四处与计划的假设不符（全部在计划 Task 12 的实测记录里留证）：

1. `main_metadata.csv` **没有 `start_time`**，只有 `start_date_time`
   （`'2016-01-02 15:12:19'`，朴素字符串，按 **UTC** 解释 —— OpenDota 的 `start_time`
   本就是 UTC epoch，导出时格式化成了这个形状）。
2. `main_metadata.csv` **没有 `league_name`**（也没有队名）：联赛名只在
   `Constants/Constants.Leagues.csv`（`leagueid,leaguename,tier`）里，而 `leagues.name`
   是 `NOT NULL`。故白名单必须补上这个文件（见 `ingest.kaggle_subset.EXTRA_FILES`）。
3. `picks_bans.csv` 的 `order`/`team`/`hero_id` 在 2016/2018 里是**浮点字符串**
   （`'0.0'`/`'78.0'`），2025 里是整数串；直接 `int()` 会 ValueError。故统一
   `int(float(...))`。
4. **规格 §6.0 的 24 手模板只是诸多合法 CM 顺序中的一种**：Valve 改过 ban 顺序，
   2025 年的比赛里 95.4% 用的是与 §6.0 不同的顺序（见 `ingest.order_families.DRAFT_ORDERS`）。
   按 §6.0 一种模板判异常会把 95%+ 的正常比赛判成异常 —— 异常率断言必然假红。
5. `picks_bans.csv` 的 `ord` 列（OpenDota 自己的序号）**只存在于 2016–2023**，且其中
   **26,539 行为空**（涉及 1,213 场：2016 目录 632 场、2023 目录 581 场）；`order` 列则在
   全部 19 个目录里行行有值。故逻辑列 `order` **以 `order` 为主、`ord` 只作别名**，
   且不得假设两列逐行相同。
6. 真实 CSV **没有 BOM**（49 个文件全部无 BOM，实测）：真正的坑是**首列的空列名**
   （pandas 导出的 index 列，2016–2023 的 24 个 CSV —— 8 个目录 ×
   `main_metadata`/`picks_bans`/`draft_timings` —— 都有）。
   故列一律**按名字**解析（`resolve_columns`），不得按位置取；`utf-8-sig` 只是廉价保险。
7. 联赛名/分级的**唯一来源**是 `Constants/Constants.Leagues.csv`（10,099 行）；实测有 **3 个
   `league_id` 在那里查不到**（20159 / 20169 / 20206），涉及 **69 场** —— 这些场次的
   `matches.league_id` 写 NULL，且**必须在运行摘要里逐个报出**（编造联赛名比留空更糟）。

**目录级契约（规格 §17-7）**：`<folder>/main_metadata.csv` 与 `<folder>/picks_bans.csv` **必须成对
出现**（实测 19 个真实目录都如此）。缺/改名 actions 文件的目录由 `actions_path_for` 在
`preflight_columns` 里**报错**，不得被当成"这批比赛还没有 BP 序列"（上一版正是这么做的：改名一个
文件会静默产出几万场 `pending`）。列名与文件存在性两项 preflight 都在写第一行之前完成。

**前置：常量层必须先入库。** `draft_actions.hero_id` 是 `heroes(hero_id)` 的外键、
`matches.patch_id` 是 `patches(patch_id)` 的外键，且归属要读 `patches`。没有它，第一条
INSERT 会以 FK 违规炸在深处；本模块在动手之前显式检查并给出可操作报错（`_require_constants`）。

**`order` → `ord` 的入库边界（规格 §17-7）**：CSV 的 `order` 起点**不假设**为 0。
`detect_ord_origin()` 按整个文件的 `min(order)` 判定（0 → 原样，1 → 统一减 1），两者都不是
就报错。实测全部 19 个目录都是 0 起（1-based 只是**约定**上的可能，不是实测到的形态）。

**异常判定（规格 §5.3）**：`anomaly=true` 的语义是「这份 draft 不符合**任何一种已知的合法
CM 顺序**」，而不是「不符合 §6.0 那一种」。理由见第 4 条：Valve 改过顺序，而 §6.0 的模板
如今只覆盖 2025 年 3.4% 的比赛。顺序族逻辑**不在本模块**：它住在 `ingest/order_families.py`
（`DRAFT_ORDERS` / `family_for` / `is_legal`），本模块只 import 并 re-export ——
下游（`analysis/`、序列模型）**必须调用该 helper**，不要各自抄一份表，也不要用
`shared.draft_template.resolve()` 去对齐非 `spec_6_0_24` 的场次（它只对那一族正确）。
族是**派生量、不落库**（`matches` 无该列；`draft_anomalies.detail["order_family"]` 只对
命中的场次有值，异常行是 null）；入库报告按族统计便于人核对，但它不是消费接口。

**`patch_id` 归属（规格 §3.2/§15）**：走
`constants.patches.subpatch_for_timestamp(start_time)` 拿**版本名**，再按 `version_name`
查 `patches`，**不做 `released_at` 的区间二分**。两者看着等价，其实差一个静默钳位：
对 `patches.released_at` 做二分/取最近行时，早于 7.08 的比赛会被钳到**最早的版本**（7.08），
2016–2017 的场次集体错标成 2018 年的版本，且不会有任何报错。
`patches` 的行集来自 Valve（Task 10 的 R7），Valve 清单从 7.08 = 1517472000（2018-02-01）起，
故只有 `start_time >= 1517472000` 才保证有行可指；更早的场次写 NULL，并在结束时**报出占比**
（pre-2018 占比是测量结果，不是可以预设的预算）。CSV 自带的 `patch` 列（OpenDota 粗粒度 id）
被用作**独立交叉校验**：与本模块解析出的 `patches.opendota_patch` 逐场比对并报出吻合率。

**幂等（规格 §5.2）**：`leagues`/`teams`/`matches` 用主键 upsert；`draft_actions` 按
`match_id` **先删后插**（否则上一次运行残留的手会留下来，`n_draft_actions` 与实际手数分叉）；
`draft_anomalies` 每场最多一行，重新加载后不再异常的场次要**删掉旧行**（否则
`draft_anomalies` 的行数与 `matches.anomaly` 的计数会分叉，而 M1 正是这么校验的）。
"""
from __future__ import annotations

import csv
import datetime
import json
import pathlib
import re
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from constants.patches import subpatch_for_timestamp
# noqa: F401 —— `DRAFT_ORDERS` / `resolve_in` / `SPEC_FAMILY` / `is_legal` 是**再导出**：
# 历史调用方（与 tests/ingest/conftest.py）从这里取；权威定义与契约在 ingest/order_families.py。
from ingest.order_families import (  # noqa: F401
    DRAFT_ORDERS, HAND_COUNTS, SPEC_FAMILY, closest_family, family_for, is_legal, resolve_in)
from ingest.order_families import deviations as family_deviations
from shared.draft_template import first_pick_team_from_actions

REPO = pathlib.Path(__file__).parents[1]
DEFAULT_CACHE_DIR = REPO / "tests" / "fixtures" / "kaggle"

METADATA_FILENAME = "main_metadata.csv"
ACTIONS_FILENAME = "picks_bans.csv"
#: 联赛名/分级的唯一来源（`leagueid,leaguename,tier`）。见模块 docstring 第 2 条。
LEAGUES_FILENAME = "Constants/Constants.Leagues.csv"

#: 规格 §3.2/§15：Valve 清单起点 7.08 = 2018-02-01（Unix 1517472000）。
#: **左闭**：恰好等于该时刻的比赛属于 7.08；早一秒的属于 patchdates 独有的旧版本，无行可指。
PATCH_ATTRIBUTION_MIN_START_TIME = 1517472000

ORD_MAX = 23

#: 逻辑列名 → CSV 里可接受的列名。只放**实测过**的别名；猜错时 `resolve_columns` 会报错
#: 并打印实际列名，而不是静默写 NULL（规格 §17-7）。
METADATA_COLUMNS: dict[str, tuple[str, ...]] = {
    "match_id": ("match_id",),
    # 实测只有 start_date_time；start_time 作为兼容别名保留（上游换 schema 时不至于立刻炸）
    "start_time": ("start_time", "start_date_time"),
    "duration_s": ("duration", "duration_s"),
    "league_id": ("leagueid", "league_id"),
    "league_name": ("league_name",),            # 实测缺失：联赛名走 Constants.Leagues.csv
    "series_id": ("series_id",),
    "series_type": ("series_type",),
    "radiant_team_id": ("radiant_team_id",),
    "radiant_team_name": ("radiant_team_name",),
    "dire_team_id": ("dire_team_id",),
    "dire_team_name": ("dire_team_name",),
    "radiant_win": ("radiant_win",),
    "lobby_type": ("lobby_type",),
    "opendota_patch": ("patch",),               # 仅供交叉校验，不参与 patch_id 归属
}
REQUIRED_METADATA = ("match_id", "start_time")

ACTIONS_COLUMNS: dict[str, tuple[str, ...]] = {
    "match_id": ("match_id",),
    # 实测 `order` 在 19 个目录里行行有值；`ord` 只在 2016–2023 存在，且其中 26,539 行
    # 为空（1,213 场：2016 632 场、2023 581 场）。故 `order` 是主列，`ord` 只作别名。
    "order": ("order", "ord"),
    "is_pick": ("is_pick",),
    "team": ("team",),
    "hero_id": ("hero_id",),
}

LEAGUES_COLUMNS: dict[str, tuple[str, ...]] = {
    "league_id": ("leagueid", "league_id"),
    "name": ("leaguename", "league_name", "name"),
    "tier": ("tier",),
}

_TRUE_WORDS = {"true", "t", "1", "yes", "y"}
_FALSE_WORDS = {"false", "f", "0", "no", "n"}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


# ------------------------------------------------------------------ 值解析

def parse_number(value) -> int:
    """`'78.0'` / `'78'` / `78.0` → `78`。

    实测 `picks_bans.csv` 的 `hero_id`/`team`/`order` 在 2016/2018 是**浮点字符串**
    （pandas 导出的痕迹），2025 是整数串；只认 `int()` 会在真实数据上直接 ValueError。
    只接受整值浮点：`12.5` 这种非整值一律报错，而不是悄悄截断。
    """
    if isinstance(value, bool):
        raise ValueError(f"布尔值不是数字：{value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"不是整值：{value!r}")
        return int(value)
    text = str(value).strip()
    if not _NUMBER_RE.fullmatch(text):
        raise ValueError(f"无法解析为数字：{value!r}")
    number = float(text)
    if not number.is_integer():
        raise ValueError(f"不是整值（拒绝截断）：{value!r}")
    return int(number)


def parse_bool(value) -> bool:
    """`True/1/'true'/'T'` 等都要认（CSV 里布尔是字符串，Python `repr` 会写成 `True`）。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError(f"无法解析为布尔值：{value!r}")


def parse_timestamp(value) -> int:
    """`start_time`（epoch 秒）或 `start_date_time`（`'YYYY-MM-DD HH:MM:SS'`）→ Unix 秒。

    朴素字符串按 **UTC** 解释：OpenDota 的 `start_time` 本就是 UTC epoch，Kaggle 的导出
    只是把它格式化成了 `start_date_time`。带显式偏移的 ISO 串按偏移换算，不做二次假设。
    这个假设由 `patch` 列的交叉校验间接验证（时区错会让补丁边界附近的场次系统性错配）。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if _NUMBER_RE.fullmatch(text):
        return int(float(text))
    try:
        parsed = datetime.datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as exc:
        raise ValueError(f"无法解析为时间戳：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp())


# --------------------------------------------------------------------- CSV 读取

def read_csv(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    """读 CSV，返回 `(列名, 行)`。

    `utf-8-sig` 只是**廉价保险**：实测 49 个真实 CSV **没有 BOM**（计划里"Kaggle CSV 常带
    BOM"的说法是猜的，已按实测改正）。真正的坑是**首列的空列名**：2016–2023 的 24 个 CSV
    （8 个目录 × `main_metadata`/`picks_bans`/`draft_timings`）首列都是 pandas 导出的空名
    index 列，于是"按位置取列"
    会整体错位一格，而列名本身看起来毫无异常。故本模块一律**按列名**解析（`resolve_columns`），
    不按位置。列名与取值都 strip —— 上游导出工具会在逗号后留空格。
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [n.strip() if isinstance(n, str) else n for n in (reader.fieldnames or [])]
        rows = [{k.strip() if isinstance(k, str) else k:
                 (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in reader]
    return fieldnames, rows


def resolve_columns(fieldnames: Sequence[str], spec: Mapping[str, tuple[str, ...]],
                    required: Iterable[str], path: pathlib.Path) -> dict[str, str | None]:
    """把逻辑列名解析为实际列名；必需列缺失则**指名报错**（含实际列名）。"""
    mapping = {logical: next((a for a in aliases if a in fieldnames), None)
               for logical, aliases in spec.items()}
    missing = [logical for logical in required if mapping[logical] is None]
    if missing:
        raise RuntimeError(
            f"{path} 缺少必需列 {missing}（可接受的列名："
            f"{ {m: spec[m] for m in missing} }）；实际列名 = {sorted(fieldnames)}。"
            f"规格 §17-7：Kaggle CSV 的列名与语义必须实测确认后再决定映射，猜错必须在这里炸，"
            f"而不是写一堆 NULL 或静默跳过。")
    return mapping


def read_header(path: pathlib.Path) -> list[str]:
    """只读表头（`preflight_columns` 用；不必为验列名把 66 MB 全读进来）。"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        row = next(csv.reader(f), [])
    return [n.strip() if isinstance(n, str) else n for n in row]


def actions_path_for(folder: pathlib.Path) -> pathlib.Path:
    """`<folder>/picks_bans.csv` 的路径；**文件不在就报错**（规格 §17-7 的"猜错必须炸"）。

    上一版把"缺 `picks_bans.csv`"当成"这个目录的比赛都还没有 BP 序列"（全部 pending）——
    那是把**契约违约写成了数据**：目录改名、下载中断、上游换文件名，都会静默变成几万场
    "等数据"的比赛，而 `matches.draft_state` 看起来完全正常。实测 19 个真实目录**两个文件
    成对出现**，故"成对"本身就可以断言。

    `main_metadata.csv` 缺失的目录根本不会被扫到（`load_bootstrap` 只挑有 metadata 的目录），
    所以这里只管 actions 这一侧。
    """
    path = folder / ACTIONS_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{folder} 有 {METADATA_FILENAME} 却没有 {ACTIONS_FILENAME}：每个 <folder>/ 必须两个"
            f"文件成对出现（实测 19 个真实目录都如此）。若下载被中断，重跑 "
            f"`python -m ingest.kaggle_subset`（幂等，已下好的会跳过）；若上游改了文件名，"
            f"先实测确认再改 ACTIONS_FILENAME —— **不得**当成 pending 静默入库。")
    return path


def preflight_columns(folders: Iterable[pathlib.Path], cache_dir: pathlib.Path) -> None:
    """**先**把所有输入文件的列名契约验完，再开始写。

    列名由上游决定（规格 §17-7 的未验证事项）。猜错时必须**在写第一行之前**炸：否则会跑完
    19 个目录里的前 18 个、写进几万场之后才因最后一个目录的列名报错 —— 虽然幂等重跑能收敛，
    但"看起来成功了一半"的运行本身就是误导。整个 preflight 只读表头，代价可忽略。

    文件**存在性**也在这一步验（`actions_path_for`）：缺/改名 `picks_bans.csv` 的目录必须在这里
    报错，而不是被当成"这场比赛还没有 BP 数据"。
    """
    leagues_path = cache_dir / LEAGUES_FILENAME
    if leagues_path.is_file():
        resolve_columns(read_header(leagues_path), LEAGUES_COLUMNS,
                        ("league_id", "name"), leagues_path)
    for folder in folders:
        targets = [(folder / METADATA_FILENAME, METADATA_COLUMNS, REQUIRED_METADATA),
                   (actions_path_for(folder), ACTIONS_COLUMNS, tuple(ACTIONS_COLUMNS))]
        for path, spec, required in targets:
            resolve_columns(read_header(path), spec, required, path)


def read_leagues(cache_dir: pathlib.Path) -> dict[int, tuple[str, str | None]]:
    """`Constants/Constants.Leagues.csv` → `{league_id: (name, tier)}`（缺失则返回空表）。"""
    path = cache_dir / LEAGUES_FILENAME
    if not path.is_file():
        return {}
    fieldnames, rows = read_csv(path)
    mapping = resolve_columns(fieldnames, LEAGUES_COLUMNS, ("league_id", "name"), path)
    leagues: dict[int, tuple[str, str | None]] = {}
    for row in rows:
        league_id = _cell(row, mapping, "league_id")
        name = _cell(row, mapping, "name")
        if league_id is None or name is None:
            continue
        leagues[parse_number(league_id)] = (name, _cell(row, mapping, "tier"))
    return leagues


def _cell(row: Mapping[str, str], mapping: Mapping[str, str | None], logical: str) -> str | None:
    column = mapping.get(logical)
    if column is None:
        return None
    value = row.get(column)
    return value if isinstance(value, str) and value.strip() != "" else None


def _int_cell(row, mapping, logical, *, path, match_id) -> int | None:
    value = _cell(row, mapping, logical)
    if value is None:
        return None
    try:
        return parse_number(value)
    except ValueError as exc:
        raise RuntimeError(
            f"{path} 的 match_id={match_id} 的 {logical}={value!r} 不是整数") from exc


def detect_ord_origin(orders: Iterable[int]) -> int:
    """判定 `picks_bans.csv` 的 `order` 起点：返回 0（原样）或 1（需减 1）。

    规格 §17-7：模板映射对起点极敏感，错一位就整体错位。判定依据是**整个文件**的
    `min(order)` —— 按单行判定会在 1-based 文件里被"恰好等于 1 的那一行"骗过。
    实测（2016/2018/2025）三个年度都是 0 起。
    """
    values = [parse_number(o) for o in orders]
    if not values:
        raise ValueError("空的 picks_bans：无法判定 order 起点（规格 §17-7）")
    low = min(values)
    if low == 0:
        return 0
    if low == 1:
        return 1
    raise ValueError(
        f"order 的最小值是 {low}（最大 {max(values)}）：既不是 0 起也不是 1 起，"
        f"规格 §17-7 的模板映射无从对齐，必须先确认数据集语义。")


def normalize_actions(rows: Sequence[Mapping[str, object]], *, ord_origin: int = 0
                      ) -> tuple[list[dict], dict[str, int]]:
    """把 CSV 手数行归一化为 `{"ord", "is_pick", "team", "hero_id"}`，并统计两类脏数据。

    - 越界手（归一化后不在 0..23）**丢弃并计数**：`draft_actions.ord` 有 CHECK，
      直接插会整场失败，而规格 §5.3 要求"不阻断入库、显式记录"。
    - 同一 `ord` 出现多次时**以最后一条为准**并计数（`(match_id, ord)` 是主键，
      重复直接插会以 UniqueViolation 炸掉整场）。取最后一条是约定，不是事实。
    """
    by_ord: dict[int, dict] = {}
    problems = {"ord_out_of_range": 0, "duplicate_ord": 0}
    for row in rows:
        ord_ = parse_number(row["order"]) - ord_origin
        if not 0 <= ord_ <= ORD_MAX:
            problems["ord_out_of_range"] += 1
            continue
        if ord_ in by_ord:
            problems["duplicate_ord"] += 1
        by_ord[ord_] = {"ord": ord_, "is_pick": parse_bool(row["is_pick"]),
                        "team": parse_number(row["team"]),
                        "hero_id": parse_number(row["hero_id"])}
    return [by_ord[k] for k in sorted(by_ord)], problems


# ------------------------------------------------- 顺序族（§5.3）：见 ingest.order_families

def matching_order(actions: Sequence[Mapping[str, object]],
                   first_pick_team: int | None) -> str | None:
    """**兼容别名**：`ingest.order_families.family_for` 的历史名字。

    新代码请直接调用 `family_for(n_actions, first_pick_team, actions)`（契约见该模块：
    族是**派生量、不落库**，下游必须走那个 helper，不要各自抄一份 `DRAFT_ORDERS`）。
    """
    return family_for(len(actions), first_pick_team, actions)


def closest_order_family(actions: Sequence[Mapping[str, object]],
                         first_pick_team: int | None) -> str | None:
    """**兼容别名**：`ingest.order_families.closest_family` 的历史名字。"""
    return closest_family(len(actions), first_pick_team, actions)


def detect_anomaly(actions: Sequence[Mapping[str, object]], first_pick_team: int | None, *,
                   ord_out_of_range: int = 0, duplicate_ord: int = 0) -> dict | None:
    """规格 §5.3：draft 结构异常 → 返回 `{"n_actions", "kinds", "detail"}`；否则 None。

    异常 = **不符合任何一种已登记的合法 CM 顺序**（`ingest.order_families.DRAFT_ORDERS`），
    或手数不落在任何已登记族的手数（`HAND_COUNTS` = {20, 22, 24}）里，或推不出先手方，
    或有被丢弃/重复的手。0 手**不是**异常：规格 §5.2 里 `picks_bans` 不存在就是 `pending`
    （状态机还在等数据）。**时代不同但合法的顺序不是异常**（20/22 手的历史族同样登记在册）。

    `kinds` 的两档（命名刻意不暗示"手太少"——20 手是合法手数）：

    - `type_deviation`：手数已登记（20/22/24）但逐手对不上**任何**一族 —— 是这一场的顺序问题；
    - `unregistered_hand_count`：手数本身没登记（10–19、21、23 手等）—— 是手数的问题。

    `detail["order_family"]` 记录命中的族（异常行为 None），`detail["deviations"]` 记录与
    **最接近的族**的逐手偏差 —— 只报"异常"而不报"差在哪"等于没报。
    """
    if not actions and not ord_out_of_range and not duplicate_ord:
        return None

    n_actions = len(actions)
    kinds: list[str] = []
    detail: dict[str, object] = {"n_actions": n_actions}

    family = family_for(n_actions, first_pick_team, actions) if actions else None
    detail["order_family"] = family

    if actions and first_pick_team not in (0, 1):
        # 推不出先手方时**不做类型判定**：没有 F 就没有"应当是哪一队"，硬判会造出假偏差
        kinds.append("missing_ord_zero")
    elif actions and family is None:
        if n_actions > 24:
            kinds.append("long_draft")
        elif n_actions in HAND_COUNTS:
            kinds.append("type_deviation")
        else:
            kinds.append("unregistered_hand_count")
        nearest = closest_family(n_actions, first_pick_team, actions)
        if nearest is not None:
            per_action = family_deviations(actions, first_pick_team, nearest)
            detail["closest_order_family"] = nearest
            detail["n_deviations"] = len(per_action)
            detail["deviations"] = per_action[:20]     # 只留前 20 条，避免 detail 无界增长

    if ord_out_of_range:
        kinds.append("ord_out_of_range")
        detail["ord_out_of_range"] = ord_out_of_range
    if duplicate_ord:
        kinds.append("duplicate_ord")
        detail["duplicate_ord"] = duplicate_ord

    if not kinds:
        return None
    return {"n_actions": n_actions, "kinds": kinds, "detail": detail}


# ------------------------------------------------------------------ 版本归属（§3.2）

def patch_ids_by_version(conn) -> dict[str, int]:
    """`{version_name: patch_id}`：一次读全表，避免每场一次 SELECT。"""
    return dict(conn.execute("SELECT version_name, patch_id FROM patches").fetchall())


def opendota_patch_by_id(conn) -> dict[int, int | None]:
    """`{patch_id: opendota_patch}`：`patch` 列交叉校验用。"""
    return dict(conn.execute("SELECT patch_id, opendota_patch FROM patches").fetchall())


def patch_id_for(conn, start_time: int, *, patch_ids: Mapping[str, int] | None = None) -> int | None:
    """给定 `start_time`（Unix 秒），返回 `matches.patch_id`（或 None）。

    规则（规格 §3.2/§15 + Task 10 的 R7）：

    - `start_time < 1517472000`（7.08 = 2018-02-01）→ **NULL**。该年代的版本名来自
      patchdates 独有的 6.70–7.07 段，`patches` 里没有这些行。此处**不能**退化成
      "取最早的版本"，那会把 2016–2017 的场次静默错标成 7.08。
    - 之后必须命中：名字来自 `subpatch_for_timestamp`（Valve 优先的时间线），若查不到行，
      说明常量层与归属时间线不一致（loader 少写一行 / 快照换了），**报错**而不是写 NULL。
    """
    if int(start_time) < PATCH_ATTRIBUTION_MIN_START_TIME:
        return None
    name = subpatch_for_timestamp(int(start_time))
    ids = patch_ids if patch_ids is not None else patch_ids_by_version(conn)
    if name not in ids:
        raise RuntimeError(
            f"start_time={start_time} 归属到版本 {name!r}，但 patches 表里没有这一行："
            f"归属边界（>= {PATCH_ATTRIBUTION_MIN_START_TIME}，即 2018-02-01 起）之后必须覆盖。"
            f"先确认 constants.load.load_constants 已跑过且快照与 Task 10 的时间线一致。")
    return ids[name]


# ------------------------------------------------------------------------ 入库

def _require_constants(conn) -> int:
    """常量层非空且归属边界一致才允许动手（在任何 INSERT 之前）。"""
    n_heroes = conn.execute("SELECT count(*) FROM heroes").fetchone()[0]
    n_patches, earliest = conn.execute(
        "SELECT count(*), min(extract(epoch FROM released_at)) FROM patches").fetchone()
    if not n_heroes or not n_patches:
        raise RuntimeError(
            "常量层为空：必须先跑 constants.load.load_constants(conn)"
            "（draft_actions.hero_id → heroes、matches.patch_id → patches 都是外键，"
            "版本归属也要读 patches）。")
    if int(earliest) != PATCH_ATTRIBUTION_MIN_START_TIME:
        raise RuntimeError(
            f"patches 最早的版本时间是 {int(earliest)}，与归属边界 "
            f"{PATCH_ATTRIBUTION_MIN_START_TIME}（7.08，2018-02-01）不一致："
            f"先确认常量快照（Task 10 的 R7），再调整本模块的边界常量。")
    return n_patches


def _metadata_row(row: Mapping[str, str], mapping: Mapping[str, str | None],
                  path: pathlib.Path) -> dict:
    match_id = _int_cell(row, mapping, "match_id", path=path, match_id="?")
    raw_start = _cell(row, mapping, "start_time")
    if match_id is None or raw_start is None:
        raise RuntimeError(f"{path} 里有一行的 match_id/start_time 为空：主键与时间不可为空")
    try:
        start_time = parse_timestamp(raw_start)
    except ValueError as exc:
        raise RuntimeError(
            f"{path} 的 match_id={match_id} 的 start_time={raw_start!r} 无法解析") from exc
    radiant_win = _cell(row, mapping, "radiant_win")
    return {
        "match_id": match_id,
        "start_time": start_time,
        "duration_s": _int_cell(row, mapping, "duration_s", path=path, match_id=match_id),
        "league_id": _int_cell(row, mapping, "league_id", path=path, match_id=match_id),
        "league_name": _cell(row, mapping, "league_name"),
        "series_id": _int_cell(row, mapping, "series_id", path=path, match_id=match_id),
        "series_type": _int_cell(row, mapping, "series_type", path=path, match_id=match_id),
        "radiant_team_id": _int_cell(row, mapping, "radiant_team_id", path=path, match_id=match_id),
        "radiant_team_name": _cell(row, mapping, "radiant_team_name"),
        "dire_team_id": _int_cell(row, mapping, "dire_team_id", path=path, match_id=match_id),
        "dire_team_name": _cell(row, mapping, "dire_team_name"),
        "radiant_win": parse_bool(radiant_win) if radiant_win is not None else None,
        "lobby_type": _int_cell(row, mapping, "lobby_type", path=path, match_id=match_id),
        "opendota_patch": _int_cell(row, mapping, "opendota_patch", path=path, match_id=match_id),
    }


def _action_row(row: Mapping[str, str], mapping: Mapping[str, str | None],
                path: pathlib.Path) -> dict:
    match_id = _int_cell(row, mapping, "match_id", path=path, match_id="?")
    for logical in ("order", "team", "hero_id"):
        if _cell(row, mapping, logical) is None:
            raise RuntimeError(f"{path} 的 match_id={match_id} 缺少 {logical}")
    return {
        "match_id": match_id,
        "order": _int_cell(row, mapping, "order", path=path, match_id=match_id),
        "is_pick": parse_bool(_cell(row, mapping, "is_pick")),
        "team": _int_cell(row, mapping, "team", path=path, match_id=match_id),
        "hero_id": _int_cell(row, mapping, "hero_id", path=path, match_id=match_id),
    }


def _league_rows(rows: Sequence[Mapping], names: Mapping[int, tuple[str, str | None]]
                 ) -> tuple[dict[int, tuple[str, str | None]], int]:
    """`{league_id: (name, tier)}` 与"有 id 但没名字"的计数（缺名字的联赛**不**编造名字）。

    名字优先取 `Constants/Constants.Leagues.csv`（实测 `main_metadata.csv` 根本没有这一列），
    回落到 metadata 的 `league_name`（若上游某天补上）。
    """
    leagues: dict[int, tuple[str, str | None]] = {}
    unnamed = 0
    for row in rows:
        league_id = row["league_id"]
        if league_id is None:
            continue
        if int(league_id) in names:
            leagues[int(league_id)] = names[int(league_id)]
            continue
        name = row["league_name"]
        if not name:
            unnamed += 1
            continue
        leagues[int(league_id)] = (name, None)
    return leagues, unnamed


def _team_rows(rows: Sequence[Mapping]) -> tuple[dict[int, str], int]:
    """实测 `main_metadata.csv` **没有队名列**，2016–2022 连队 id 都是空的（2023+ 才有 id）。

    Phase A 的引导子集不含 `*/teams.csv`（55.4 MB，且实测只覆盖约 66% 的 team id），
    故这里只在上游确实给了队名时才建 `teams` 行；否则把 FK 写 NULL 并**报出数量** ——
    编造队名比留空更糟。
    """
    teams: dict[int, str] = {}
    unnamed = 0
    for row in rows:
        for side in ("radiant", "dire"):
            team_id = row[f"{side}_team_id"]
            if team_id is None:
                continue
            name = row[f"{side}_team_name"]
            if not name:
                unnamed += 1
                continue
            teams[int(team_id)] = name
    return teams, unnamed


MATCH_UPSERT = """
INSERT INTO matches (match_id, data_source, patch_id, started_at, duration_s, league_id,
                     series_id, series_type, first_pick_team, radiant_team_id, dire_team_id,
                     radiant_win, lobby_type, draft_state, n_draft_actions, anomaly)
VALUES (%s, 'pro_match', %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (match_id) DO UPDATE SET
    data_source = EXCLUDED.data_source, patch_id = EXCLUDED.patch_id,
    started_at = EXCLUDED.started_at, duration_s = EXCLUDED.duration_s,
    league_id = EXCLUDED.league_id, series_id = EXCLUDED.series_id,
    series_type = EXCLUDED.series_type, first_pick_team = EXCLUDED.first_pick_team,
    radiant_team_id = EXCLUDED.radiant_team_id, dire_team_id = EXCLUDED.dire_team_id,
    radiant_win = EXCLUDED.radiant_win, lobby_type = EXCLUDED.lobby_type,
    draft_state = EXCLUDED.draft_state, n_draft_actions = EXCLUDED.n_draft_actions,
    anomaly = EXCLUDED.anomaly
"""

ANOMALY_UPSERT = """
INSERT INTO draft_anomalies (match_id, n_actions, kinds, detail)
VALUES (%s, %s, %s, %s)
ON CONFLICT (match_id) DO UPDATE SET
    n_actions = EXCLUDED.n_actions, kinds = EXCLUDED.kinds,
    detail = EXCLUDED.detail, detected_at = now()
"""


def _write_match(conn, row: Mapping, actions: Sequence[Mapping], problems: Mapping[str, int],
                 teams: Mapping[int, str], leagues: Mapping[int, tuple[str, str | None]],
                 patch_ids: Mapping[str, int], patch_numbers: Mapping[int, int | None],
                 stats: dict) -> None:
    match_id = row["match_id"]
    first_pick_team = None
    if actions:
        try:
            first_pick_team = first_pick_team_from_actions(actions)   # 规格 §8①
        except ValueError:
            first_pick_team = None                # 缺 ord=0：记 missing_ord_zero，不中断整场
    anomaly = detect_anomaly(actions, first_pick_team,
                             ord_out_of_range=problems.get("ord_out_of_range", 0),
                             duplicate_ord=problems.get("duplicate_ord", 0))
    patch_id = patch_id_for(conn, row["start_time"], patch_ids=patch_ids)
    draft_state = "complete" if actions else "pending"

    conn.execute(MATCH_UPSERT, (
        match_id, patch_id, row["start_time"], row["duration_s"],
        row["league_id"] if row["league_id"] in leagues else None,
        row["series_id"], row["series_type"], first_pick_team,
        row["radiant_team_id"] if row["radiant_team_id"] in teams else None,
        row["dire_team_id"] if row["dire_team_id"] in teams else None,
        row["radiant_win"], row["lobby_type"], draft_state,
        len(actions) if actions else None, anomaly is not None,
    ))

    # 规格 §5.2：先按 match_id 删再整体插入，避免上一次运行残留旧手
    conn.execute("DELETE FROM draft_actions WHERE match_id = %s", (match_id,))
    if actions:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO draft_actions (match_id, ord, is_pick, team, hero_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(match_id, a["ord"], a["is_pick"], a["team"], a["hero_id"]) for a in actions])

    if anomaly is not None:
        conn.execute(ANOMALY_UPSERT, (match_id, anomaly["n_actions"], anomaly["kinds"],
                                      json.dumps(anomaly["detail"], ensure_ascii=False)))
        for kind in anomaly["kinds"]:
            stats["anomaly_kinds"][kind] += 1
    else:
        conn.execute("DELETE FROM draft_anomalies WHERE match_id = %s", (match_id,))

    stats["matches"] += 1
    stats["draft_actions"] += len(actions)
    stats["anomalies"] += anomaly is not None
    stats["pending"] += not actions
    if anomaly is None and actions:
        stats["order_families"][family_for(len(actions), first_pick_team, actions)] += 1
    if row["start_time"] < PATCH_ATTRIBUTION_MIN_START_TIME:
        stats["pre_2018"] += 1
        stats["pre_2018_null_patch"] += patch_id is None
        if row["opendota_patch"] is not None:
            stats["patch_column_pre_2018"] += 1
    else:
        stats["post_2018_null_patch"] += patch_id is None
        if row["opendota_patch"] is None:
            stats["patch_column_missing"] += 1
        elif patch_id is not None:
            # 交叉校验：CSV 自带的 OpenDota 粗粒度 patch id 与本模块的归属是否一致
            if patch_numbers.get(patch_id) == row["opendota_patch"]:
                stats["patch_column_agree"] += 1
            else:
                stats["patch_column_mismatch"] += 1


def load_bootstrap(conn, *, cache_dir=DEFAULT_CACHE_DIR, commit: bool = True,
                   batch_matches: int = 2000, log=print) -> dict:
    """把 `<cache_dir>/<folder>/*.csv` 灌进数据库；返回本次运行的统计（含 pre-2018 占比）。

    `commit=False` 给测试事务隔离用（`tests/conftest.py::db` 在测试结束时 `rollback()`）；
    `commit=True` 时每 `batch_matches` 场提交一次 —— 引导入库很慢（506 MB / 19 个目录），
    中断后必须能续跑，而全部写入都是幂等的，故续跑安全。

    幂等：`leagues`/`teams`/`matches` 主键 upsert，`draft_actions` 先删后插，
    `draft_anomalies` 不再异常的场次删行（规格 §5.2）。
    """
    cache_dir = pathlib.Path(cache_dir)
    _require_constants(conn)

    folders = sorted(p for p in cache_dir.iterdir() if (p / METADATA_FILENAME).is_file()) \
        if cache_dir.is_dir() else []
    if not folders:
        raise FileNotFoundError(
            f"{cache_dir} 下没有任何 <folder>/{METADATA_FILENAME}（也没有 {ACTIONS_FILENAME}）："
            f"先跑 `python -m ingest.kaggle_subset`（该数据集是 CC0 公共数据集，"
            f"**无凭证也能下载**；有凭证时自动走已认证路径），或把 CSV 放到该目录下。")

    # `leagues`/`teams` 是**表里的行数**（结束时直查），不是逐目录累加和：跨目录重复的
    # league_id 会让累加和大出 ~16%（实测 1,808 vs 1,557），报出去就是错的信息。
    stats: dict = {"folders": [p.name for p in folders], "matches": 0, "draft_actions": 0,
                   "leagues": 0, "teams": 0, "leagues_upserted": 0, "teams_upserted": 0,
                   "anomalies": 0, "pending": 0, "actions_dropped": 0, "duplicate_ord": 0,
                   "orphan_action_matches": 0, "unnamed_leagues": 0, "unresolved_team_refs": 0,
                   "league_ids_missing_from_constants": set(), "league_id_null_matches": 0,
                   "total": 0, "pre_2018": 0, "pre_2018_null_patch": 0, "post_2018_null_patch": 0,
                   "pre_2018_share": 0.0, "patch_column_agree": 0, "patch_column_mismatch": 0,
                   "patch_column_missing": 0, "patch_column_pre_2018": 0,
                   "order_families": defaultdict(int), "anomaly_kinds": defaultdict(int)}
    preflight_columns(folders, cache_dir)        # 列名契约先整体验完，再写第一行
    patch_ids = patch_ids_by_version(conn)
    patch_numbers = opendota_patch_by_id(conn)
    league_names = read_leagues(cache_dir)
    since_commit = 0

    for folder in folders:
        meta_path = folder / METADATA_FILENAME
        fieldnames, raw_rows = read_csv(meta_path)
        mapping = resolve_columns(fieldnames, METADATA_COLUMNS, REQUIRED_METADATA, meta_path)
        rows = [_metadata_row(r, mapping, meta_path) for r in raw_rows]

        actions_by_match: dict[int, tuple[list[dict], dict[str, int]]] = {}
        actions_path = actions_path_for(folder)          # 缺文件在 preflight 已炸；这里再兜一次
        a_fields, a_raw = read_csv(actions_path)
        a_mapping = resolve_columns(a_fields, ACTIONS_COLUMNS, tuple(ACTIONS_COLUMNS),
                                    actions_path)
        parsed = [_action_row(r, a_mapping, actions_path) for r in a_raw]
        ord_origin = detect_ord_origin(a["order"] for a in parsed)
        grouped: dict[int, list[dict]] = defaultdict(list)
        for action in parsed:
            grouped[action["match_id"]].append(action)
        for match_id, group in grouped.items():
            actions_by_match[match_id] = normalize_actions(group, ord_origin=ord_origin)

        leagues, unnamed_leagues = _league_rows(rows, league_names)
        teams, unresolved_teams = _team_rows(rows)
        stats["unnamed_leagues"] += unnamed_leagues
        stats["unresolved_team_refs"] += unresolved_teams
        for row in rows:
            league_id = row["league_id"]
            if league_id is None:
                continue
            if int(league_id) not in league_names:
                # `Constants.Leagues.csv` 是联赛名的唯一来源（实测 metadata 没有该列）：
                # 查不到就是查不到 —— 记下 id 并报出去，而不是安静地写 NULL。
                stats["league_ids_missing_from_constants"].add(int(league_id))
            if int(league_id) not in leagues:
                stats["league_id_null_matches"] += 1

        if leagues:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO leagues (league_id, name, tier) VALUES (%s, %s, %s)
                                   ON CONFLICT (league_id) DO UPDATE
                                   SET name = EXCLUDED.name, tier = EXCLUDED.tier""",
                                [(lid, name, tier) for lid, (name, tier) in sorted(leagues.items())])
            stats["leagues_upserted"] += len(leagues)
        if teams:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO teams (team_id, name) VALUES (%s, %s)
                                   ON CONFLICT (team_id) DO UPDATE SET name = EXCLUDED.name""",
                                sorted(teams.items()))
            stats["teams_upserted"] += len(teams)

        known_matches = {row["match_id"] for row in rows}
        stats["orphan_action_matches"] += len(set(actions_by_match) - known_matches)
        for row in rows:
            actions, problems = actions_by_match.get(row["match_id"], ([], {}))
            stats["actions_dropped"] += problems.get("ord_out_of_range", 0)
            stats["duplicate_ord"] += problems.get("duplicate_ord", 0)
            _write_match(conn, row, actions, problems, teams, leagues, patch_ids, patch_numbers,
                         stats)
            since_commit += 1
            if commit and since_commit >= batch_matches:
                conn.commit()
                since_commit = 0

        log(f"  {folder.name}: 累计 matches={stats['matches']} "
            f"draft_actions={stats['draft_actions']}")

    if commit:
        conn.commit()

    stats["total"] = stats["matches"]
    stats["pre_2018_share"] = stats["pre_2018"] / stats["total"] if stats["total"] else 0.0
    stats["order_families"] = dict(stats["order_families"])
    stats["anomaly_kinds"] = dict(stats["anomaly_kinds"])
    stats["league_ids_missing_from_constants"] = sorted(stats["league_ids_missing_from_constants"])
    # 表行数（distinct），不是逐目录累加和 —— 同一 league_id 会在多个目录里出现。
    stats["leagues"] = conn.execute("SELECT count(*) FROM leagues").fetchone()[0]
    stats["teams"] = conn.execute("SELECT count(*) FROM teams").fetchone()[0]
    log(f"引导入库完成：folders={len(folders)} matches={stats['matches']} "
        f"draft_actions={stats['draft_actions']} leagues={stats['leagues']}（逐目录累计 "
        f"{stats['leagues_upserted']}，跨目录重复） teams={stats['teams']}（逐目录累计 "
        f"{stats['teams_upserted']}） anomalies={stats['anomalies']} pending={stats['pending']} "
        f"丢弃的越界手={stats['actions_dropped']} 重复 ord={stats['duplicate_ord']} "
        f"无 metadata 的 picks_bans 场次={stats['orphan_action_matches']}")
    # 这两项上一版只算不报（评审：计划说 unresolved_team_refs「日志可见」，实际日志里没有）。
    log(f"名字缺失的引用：联赛 {stats['unnamed_leagues']} 处、队 {stats['unresolved_team_refs']} 处"
        f"（两处都写 NULL，不编造名字 —— teams/leagues 是外键目标）")
    log(f"{LEAGUES_FILENAME} 里查不到的 league_id："
        f"{stats['league_ids_missing_from_constants']} —— 这些场次的 matches.league_id 写 NULL："
        f"{stats['league_id_null_matches']} 场")
    pre, total = stats["pre_2018"], stats["total"]
    log(f"pre-2018 场次 {pre}/{total}（{stats['pre_2018_share']:.1%}），"
        f"其中 patch_id IS NULL 的 {stats['pre_2018_null_patch']} 场；"
        f"start_time >= {PATCH_ATTRIBUTION_MIN_START_TIME} 的场次中 patch_id IS NULL 的 "
        f"{stats['post_2018_null_patch']} 场（规格 §3.2：必须为 0）")
    log(f"顺序族分布（仅统计 anomaly=false 的场次）：{stats['order_families']}")
    log(f"patch 列交叉校验（>= 2018-02-01）：吻合 {stats['patch_column_agree']} / "
        f"不吻合 {stats['patch_column_mismatch']} / CSV 缺该列值 {stats['patch_column_missing']}；"
        f"pre-2018 有该列值的 {stats['patch_column_pre_2018']} 场（patches 表无对应行，不参与比对）")
    return stats
