"""Task 12（Kaggle 引导数据集）独有的 fixture、数据库隔离助手与**合成数据集**构造器。

**合成数据刻意照抄真实 CSV 的形状**（2026-09-18 实测，见 `ingest/load_bootstrap.py` 的
docstring）：`start_date_time` 朴素字符串、`order`/`team`/`hero_id` 是**浮点字符串**
（`'0.0'`）、`main_metadata.csv` **没有** `league_name`/队名列、联赛名只在
`Constants/Constants.Leagues.csv` 里、`picks_bans.csv` 在 2016/2018 带 BOM + 首列空名。
照着计划里的列名（`start_time`/`league_name`）造合成数据能全绿，却与真实数据毫无关系 ——
那正是 Task 12 要避免的"写了没验"。

**为什么会话级 fixture 必须独占一个数据库**：计划原文的 `db_after_bootstrap` 直接在共享的
`dota_test` 上跑 `load_constants(conn)`（默认 `commit=True`）+ 引导入库。两条路都不通：

1. 原样（提交）：常量与几万场比赛被**提交**进共享测试库，后面 `tests/db` 的 `seeded`
   fixture 在 `constants_snapshot(snapshot_version=1)` / `heroes(80)` 上主键冲突 ——
   Task 11 已实测 145 passed / 18 errors，18 个 error 全是跨套件污染。
2. 改成 `commit=False`：psycopg 的未提交事务会**一直持有** `heroes`/`patches`/`matches` 行的
   写锁到 session 结束；同一 session 里 `tests/constants/test_load.py` 会去 upsert 同一批
   `heroes`/`patches` 行 —— 直接**锁等待挂死**（PostgreSQL 默认无 statement_timeout，
   顺序执行也会挂）。挂死比变红更糟：没有失败信息，CI 只是永远不结束。

故本 fixture 走第三条路：引导入库写进**自己拥有的兄弟库** `<db>_test_bootstrap`
（DROP + CREATE + 跑迁移 + 一次性提交），共享的 `dota_test` 一个字节都不写、一把锁都不加。
隔离性不是靠注释保证的：`tests/ingest/test_bootstrap.py` 里
`test_session_bootstrap_owns_its_own_database_and_leaves_the_shared_test_db_clean`
用合成数据实际跑一次 `open_bootstrap_connection()`，再从另一个连接确认共享库 11 张表全为 0 行。
"""
from __future__ import annotations

import csv
import pathlib

import psycopg
import pytest

REPO = pathlib.Path(__file__).parents[2]
CACHE = REPO / "tests" / "fixtures" / "kaggle"

#: 派生库后缀。共享库必须叫 `<...>_test`，派生库因此叫 `<...>_test_bootstrap`。
BOOTSTRAP_SUFFIX = "_bootstrap"

# --- 真实 2016/2018 形状：首列是空名（pandas 的 index 列）、列里没有 league_name/队名 ---
METADATA_HEADER = ["", "match_id", "duration", "leagueid", "lobby_type", "radiant_win",
                   "start_date_time", "series_id", "series_type", "patch", "region",
                   "dire_team_id", "radiant_team_id"]
# --- 真实 2016/2018 形状：带 OpenDota 自己的 ord 列，值是浮点字符串 ---
ACTIONS_HEADER_OLD = ["", "is_pick", "hero_id", "team", "order", "ord", "match_id", "leagueid"]
# --- 真实 2025 形状：没有首列、没有 ord 列，值是整数串 ---
ACTIONS_HEADER_NEW = ["is_pick", "hero_id", "team", "order", "match_id", "leagueid"]

LEAGUES_HEADER = ["leagueid", "leaguename", "tier"]
SYNTHETIC_LEAGUES = {4194: ("Synthetic League One", "professional"),
                     9584: ("Synthetic League Two", "premium")}

#: 目录形状：2016 用旧表头 + BOM + 1-based order；2025 用新表头 + 整数串。
FOLDER_STYLE = {"2016": {"header": ACTIONS_HEADER_OLD, "bom": True, "one_based": True},
                "2018": {"header": ACTIONS_HEADER_OLD, "bom": False, "one_based": False},
                "2025": {"header": ACTIONS_HEADER_NEW, "bom": False, "one_based": False}}

#: 8 场合成比赛，覆盖：三种顺序族、24/22/23/0 手、2018 边界当天/前一秒、pre-2018、
#: 无 picks_bans（pending）、`patch` 列吻合与不吻合。
MATCHES: list[dict] = [
    {"folder": "2018", "match_id": 900000001, "start_date_time": "2018-02-01 08:00:00",
     "duration": 2400, "leagueid": 4194, "series_id": 5001, "series_type": 1,
     "radiant_team_id": 101, "dire_team_id": 102, "radiant_win": True, "lobby_type": 1,
     "patch": 27, "family": "spec_6_0_24", "first_pick": 0},
    {"folder": "2018", "match_id": 900000002, "start_date_time": "2018-02-01 08:00:01",
     "duration": 2500, "leagueid": 4194, "series_id": 5002, "series_type": 1,
     "radiant_team_id": 103, "dire_team_id": 104, "radiant_win": False, "lobby_type": 1,
     "patch": 27, "family": "cm24_a", "first_pick": 1},
    # ord=3 的归属方写反 → type_deviation（24 手但不符合任何族）
    {"folder": "2018", "match_id": 900000003, "start_date_time": "2018-02-01 08:00:02",
     "duration": 2600, "leagueid": 9584, "series_id": 5003, "series_type": 1,
     "radiant_team_id": 101, "dire_team_id": 102, "radiant_win": True, "lobby_type": 0,
     "patch": 27, "family": "cm24_a", "first_pick": 0, "flip_ord": 3},
    # pre-2018：22 手 cm22_a，patch_id 必须 NULL
    {"folder": "2018", "match_id": 900000004, "start_date_time": "2016-01-01 00:00:00",
     "duration": 2700, "leagueid": 4194, "series_id": 5004, "series_type": 1,
     "radiant_team_id": 101, "dire_team_id": 102, "radiant_win": True, "lobby_type": 1,
     "patch": 16, "family": "cm22_a", "first_pick": 1},
    # 边界**前一秒**：22 手 cm22_c，patch_id 必须 NULL（反钳位）
    {"folder": "2018", "match_id": 900000005, "start_date_time": "2018-02-01 07:59:59",
     "duration": 2800, "leagueid": 9584, "series_id": 5005, "series_type": 1,
     "radiant_team_id": 101, "dire_team_id": 102, "radiant_win": False, "lobby_type": 0,
     "patch": 27, "family": "cm22_c", "first_pick": 0},
    # 23 手 → short_draft；patch 列刻意写错 → 交叉校验要能报出不吻合
    {"folder": "2018", "match_id": 900000006, "start_date_time": "2018-02-01 08:00:03",
     "duration": 2900, "leagueid": 4194, "series_id": 5006, "series_type": 1,
     "radiant_team_id": 103, "dire_team_id": 104, "radiant_win": True, "lobby_type": 1,
     "patch": 999, "family": "cm24_a", "first_pick": 1, "drop_last": 1},
    # 只有 metadata、没有 picks_bans → pending，不是 anomaly（规格 §5.2）
    {"folder": "2018", "match_id": 900000007, "start_date_time": "2018-02-01 08:00:04",
     "duration": 3000, "leagueid": 4194, "series_id": 5007, "series_type": 1,
     "radiant_team_id": 103, "dire_team_id": 104, "radiant_win": True, "lobby_type": 1,
     "patch": 27, "family": None, "first_pick": None},
    # 1-based + BOM 的旧表头目录
    {"folder": "2016", "match_id": 900000008, "start_date_time": "2016-01-01 00:00:01",
     "duration": 3100, "leagueid": 4194, "series_id": 5008, "series_type": 1,
     "radiant_team_id": 101, "dire_team_id": 102, "radiant_win": True, "lobby_type": 1,
     "patch": 16, "family": "cm22_a", "first_pick": 0},
]

MATCH_WITHOUT_ACTIONS = 900000007
POST_2018_MATCHES = (900000001, 900000002, 900000003, 900000006, 900000007)
PRE_2018_MATCHES = (900000004, 900000005, 900000008)


def missing_dataset_message() -> str:
    """数据/凭证缺失时的 skip 文案：写明缺什么、怎么补、补完跑什么。"""
    return (
        f"缺少 Kaggle 引导数据集缓存 {CACHE}（本机也没有 Kaggle 凭证："
        f"KAGGLE_USERNAME/KAGGLE_KEY 未设置、~/.kaggle/kaggle.json 不存在）。"
        f"补齐凭证（kaggle.com → Account → Create New API Token）后运行 "
        f"`python -m ingest.kaggle_subset` 下载 506.7 MB 子集；"
        f"或把已有的 CSV 放到 {CACHE}/<年度>/。"
    )


# ---------------------------------------------------------------- 合成数据集

#: 合成手数用的 hero_id：**必须都是 heroes 里真实存在的 id**（`draft_actions.hero_id` 有外键）。
#: 实测空位 = 24 / 115–118 / 122 / 124 / 125（Task 11 记录），故 1..23 + 25 全部有效。
SYNTHETIC_HERO_IDS = [*range(1, 24), 25]


def draft_actions_for(family: str, first_pick: int) -> list[tuple[int, bool, int, int]]:
    """按**实测顺序族**生成 `(order, is_pick, team, hero_id)`（order 一律 0-based）。"""
    from ingest.load_bootstrap import DRAFT_ORDERS, resolve_in
    return [(ord_, *resolve_in(family, ord_, first_pick), SYNTHETIC_HERO_IDS[ord_])
            for ord_ in range(len(DRAFT_ORDERS[family]))]


def canonical_metadata() -> list[dict]:
    return [dict(row) for row in MATCHES]


def canonical_actions() -> dict[int, list[tuple[int, bool, int, int]]]:
    """match_id → 动作行（`order` 0-based；1-based 由 `write_dataset` 统一 +1）。"""
    out: dict[int, list[tuple[int, bool, int, int]]] = {}
    for row in MATCHES:
        family = row["family"]
        if family is None:
            out[row["match_id"]] = []
            continue
        actions = draft_actions_for(family, row["first_pick"])
        for ord_ in ([row["flip_ord"]] if "flip_ord" in row else []):
            actions[ord_] = (actions[ord_][0], not actions[ord_][1],
                             actions[ord_][2], actions[ord_][3])
        drop = row.get("drop_last", 0)
        out[row["match_id"]] = actions[:len(actions) - drop] if drop else actions
    return out


def write_dataset(root, *, metadata=None, actions=None) -> pathlib.Path:
    """把合成数据写进 `<root>`：`<folder>/main_metadata.csv`、`<folder>/picks_bans.csv`、
    `Constants/Constants.Leagues.csv`。"""
    root = pathlib.Path(root)
    metadata = canonical_metadata() if metadata is None else metadata
    actions = canonical_actions() if actions is None else actions

    constants = root / "Constants"
    constants.mkdir(parents=True, exist_ok=True)
    with open(constants / "Constants.Leagues.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LEAGUES_HEADER)
        for league_id, (name, tier) in sorted(SYNTHETIC_LEAGUES.items()):
            writer.writerow([league_id, name, tier])

    by_folder: dict[str, list[dict]] = {}
    for row in metadata:
        by_folder.setdefault(row["folder"], []).append(row)

    for folder, rows in sorted(by_folder.items()):
        style = FOLDER_STYLE.get(folder, {"header": ACTIONS_HEADER_NEW, "bom": False,
                                          "one_based": False})
        folder_path = root / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        encoding = "utf-8-sig" if style["bom"] else "utf-8"

        with open(folder_path / "main_metadata.csv", "w", newline="", encoding=encoding) as f:
            writer = csv.DictWriter(f, fieldnames=METADATA_HEADER, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # 真实 2016/2018 的浮点导出口味：整数字段带 `.0`
                writer.writerow({"": 0, **{k: (f"{v}.0" if isinstance(v, int) and not isinstance(v, bool)
                                               else v) for k, v in row.items()},
                                 "radiant_win": "True" if row["radiant_win"] else "False"})

        offset = 1 if style["one_based"] else 0
        with open(folder_path / "picks_bans.csv", "w", newline="", encoding=encoding) as f:
            writer = csv.writer(f)
            writer.writerow(style["header"])
            for row in rows:
                for (ord_, is_pick, team, hero_id) in actions.get(row["match_id"], []):
                    order = ord_ + offset
                    if style["header"] is ACTIONS_HEADER_OLD:
                        writer.writerow(["", is_pick, f"{hero_id}.0", f"{team}.0",
                                         f"{order}.0", f"{order}.0", row["match_id"],
                                         row["leagueid"]])
                    else:
                        writer.writerow([is_pick, hero_id, team, order, row["match_id"],
                                         row["leagueid"]])
    return root


@pytest.fixture
def synthetic_cache(tmp_path) -> pathlib.Path:
    """一份**完全合成**的迷你数据集（无凭证、无网络）：8 场比赛 / 三个年度目录。"""
    return write_dataset(tmp_path / "kaggle")


# ------------------------------------------------- 会话级引导库（独占数据库）

def bootstrap_dsn(test_dsn: str) -> str:
    """把 `<db>_test` 派生成 `<db>_test_bootstrap`（保留查询串）。"""
    dsn, sep, query = test_dsn.partition("?")
    head, name = dsn.rsplit("/", 1)
    if not name.endswith("_test"):
        raise RuntimeError(
            f"拒绝从 {name!r} 派生引导库：基库名必须以 '_test' 结尾"
            f"（本模块会对派生库 DROP + CREATE）。请检查 TEST_DATABASE_URL / DATABASE_URL。")
    return f"{head}/{name}{BOOTSTRAP_SUFFIX}{sep}{query}"


def _admin_exec(dsn: str, sql: str) -> None:
    dsn_only, _, _ = dsn.partition("?")
    head, name = dsn_only.rsplit("/", 1)
    ident = name.replace('"', '""')          # 标识符不能参数化，只能手工转义
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        c.execute(sql.format(ident=ident))


def reset_database(dsn: str) -> None:
    """从零重建该库并跑迁移（与 `tests/conftest.py::dsn` 同一口径：改 DDL 必须生效）。"""
    _admin_exec(dsn, 'DROP DATABASE IF EXISTS "{ident}" WITH (FORCE)')
    _admin_exec(dsn, 'CREATE DATABASE "{ident}"')
    from db.migrate import apply
    apply(dsn)


def drop_database(dsn: str) -> None:
    _admin_exec(dsn, 'DROP DATABASE IF EXISTS "{ident}" WITH (FORCE)')


def open_bootstrap_connection(test_dsn: str, cache_dir) -> psycopg.Connection:
    """在**自己拥有的** `<db>_test_bootstrap` 上跑常量 + 引导入库（提交），返回该库的连接。

    调用方负责 `conn.close()` 与 `drop_database(bootstrap_dsn(test_dsn))`。
    共享的 `dota_test` 不参与，故与 `tests/constants`、`tests/db` 的写入顺序完全无关。
    """
    from constants.load import load_constants
    from ingest.load_bootstrap import load_bootstrap

    dsn = bootstrap_dsn(test_dsn)
    reset_database(dsn)
    conn = psycopg.connect(dsn)
    try:
        load_constants(conn)                       # 自己的库：默认 commit=True 正是想要的
        load_bootstrap(conn, cache_dir=cache_dir)
    except BaseException:
        conn.close()
        raise
    return conn


@pytest.fixture(scope="session")
def db_after_bootstrap(dsn):
    """跑完常量入库 + Kaggle 引导入库的**独占数据库连接**（凭证/数据缺失则 skip）。

    会话级：引导入库很慢，不能每个测试重跑一次。**不碰共享的 `dota_test`**（理由见模块
    docstring）：这既避免 Task 11 的 18 个主键冲突，也避免未提交事务的锁把同 session 的
    `load_constants(commit=False)` 挂死。
    """
    if not any(CACHE.glob("*/picks_bans.csv")):
        pytest.skip(missing_dataset_message())
    conn = open_bootstrap_connection(dsn, CACHE)
    try:
        yield conn
    finally:
        conn.close()
        drop_database(bootstrap_dsn(dsn))


@pytest.fixture(scope="session")
def sample_csv() -> pathlib.Path:
    """缓存里的某个 `picks_bans.csv`，用于验证列名与 order 起始值（规格 §17-7）。

    计划原文钉死 `2016/picks_bans.csv`；这里改成"任一存在的目录"：数据集的目录划分由上游决定
    （19 个目录，2016 未必在其中），钉死单个目录会让一条本可运行的测试无谓地跳过。
    缓存不存在则**跳过**——凭证缺失时不应让整个测试套件变红。
    """
    found = sorted(CACHE.glob("*/picks_bans.csv"))
    if not found:
        pytest.skip(missing_dataset_message())
    return found[0]
