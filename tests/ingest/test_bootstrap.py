"""Task 12 的验收与合成数据测试（规格 §5.2/§5.3/§3.2/§15、§17-7）。

**两类测试的分工必须说清，否则"全绿"会被误读**：

- **数据侧**：需要 506.7 MB 的 Kaggle 子集。缓存不存在时（例如换一台没跑过
  `python -m ingest.kaggle_subset` 的机器）它们 `pytest.skip` 并给出补齐方式与下载命令 ——
  **绝不失败，也绝不静默通过**。
- **合成侧**：`tests/ingest/conftest.py` 自己构造迷你数据集（**照抄真实 CSV 的形状**：
  `start_date_time`、浮点字符串、没有 `league_name`、BOM、两种表头），配**真实常量层**
  （`load_constants(commit=False)`，走仓库里的网络缓存）。CSV 列名与 order 起点归一化、
  顺序族判定、异常判定、版本归属（含反钳位）、先查后插的幂等性、会话 fixture 的数据库隔离
  —— 全部是**实际执行过**的。

数据到位后要跑的两条命令（skip 消息里也会给出）：
```
python -m ingest.kaggle_subset          # 下载 506.7 MB 子集到 tests/fixtures/kaggle/
pytest tests/ingest -q -rP              # 数据侧 + 合成侧
```
"""
from __future__ import annotations

import csv
import pathlib
import subprocess
import sys

import psycopg
import pytest

from tests.ingest.conftest import (CACHE, MATCH_WITHOUT_ACTIONS, POST_2018_MATCHES,
                                   PRE_2018_MATCHES, bootstrap_dsn, canonical_actions,
                                   canonical_metadata, draft_actions_for, drop_database,
                                   missing_dataset_message, open_bootstrap_connection,
                                   write_dataset)


def _nolog(*_args, **_kwargs) -> None:
    pass


def _load(db, cache_dir, log=_nolog) -> dict:
    """常量（同一事务，不提交）+ 引导入库，返回 loader 的统计。"""
    from constants.load import load_constants
    from ingest.load_bootstrap import load_bootstrap
    load_constants(db, commit=False)
    return load_bootstrap(db, cache_dir=cache_dir, commit=False, log=log)


# ===========================================================================
# 数据侧（需要 506.7 MB 子集；缓存缺失时 skip）
# ===========================================================================

def test_picks_bans_columns_and_order_origin(sample_csv):
    """规格 §17-7：必须确认 order 从 0 起，否则模板映射整体错位一位。

    **实测口径（2026-09-18）**：`order` 在 2016/2018 是**浮点字符串**（`'0.0'`）——
    计划原文的 `int(r["order"])` 会直接 `ValueError`，那不是"1-based"的信号，而是类型信号。
    故这里先 `float()` 再 `int()`；起点判定的结论仍是 **0 起**（2016/2017/2018/2025 四个年度
    抽样全部 min=0）。1-based 的处置说明保留在 `_cell_origin` 的注释里，因为它是**约定**：
    真遇到 1-based，必须在入库边界统一减 1，并把本测试的断言改成 `min(orders) == 1`。

    用 utf-8-sig 读：Kaggle 的 CSV 带 BOM（2016/2018 实测如此），否则首列名会变成 '\\ufeff'。
    """
    with open(sample_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows, "样本为空"
    assert {"match_id", "order", "is_pick", "team", "hero_id"} <= set(rows[0]), \
        f"列名不符，实际为 {sorted(rows[0])}"
    orders = [int(float(r["order"])) for r in rows]
    assert min(orders) == 0, f"order 从 {min(orders)} 起，模板映射会整体错位一位"


def test_every_cached_picks_bans_file_has_a_known_order_origin():
    """上一条只抽样一个目录；order 起点是**按文件**判定的，故每个目录都要能判定。

    1-based 不是失败：`detect_ord_origin` 会返回 1，loader 在入库边界减 1。没有任何一代
    数据集能同时是 0 起和 1 起——但**不同年度的目录可以不同**，抽样一条盖不住。
    """
    from ingest.load_bootstrap import detect_ord_origin

    files = sorted(CACHE.glob("*/picks_bans.csv"))
    if not files:
        pytest.skip(missing_dataset_message())
    origins = {}
    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as f:
            orders = [r["order"] for r in csv.DictReader(f)]
        origins[path.parent.name] = detect_ord_origin(orders)
    print(f"order 起点分布：{origins}")
    assert set(origins.values()) <= {0, 1}


def test_bootstrap_loaded_a_substantial_number_of_matches(db_after_bootstrap):
    n = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    assert n > 10000, f"只入库了 {n} 场"


def test_draft_actions_and_leagues_are_queryable(db_after_bootstrap):
    """规格 §14 M1：matches/draft_actions/leagues 可查。"""
    assert db_after_bootstrap.execute("SELECT count(*) FROM draft_actions").fetchone()[0] > 200000
    assert db_after_bootstrap.execute("SELECT count(*) FROM leagues").fetchone()[0] > 50
    assert db_after_bootstrap.execute("SELECT count(*) FROM leagues WHERE name IS NULL"
                                      ).fetchone()[0] == 0


def test_anomaly_rate_under_2_percent(db_after_bootstrap):
    """规格 §15：异常率 < 2%，且偏离场次被记入 `draft_anomalies`。

    **必须按顺序族/手数分口径报，否则这条断言要么假红、要么在骗人。** 实测（2026-09-18）：
    Valve 改过 CM 的 ban 顺序 —— 2016–2022 的比赛是 **22 手**的旧顺序（7.33 之前），
    2025 年又有 95.4% 用与规格 §6.0 不同的 24 手顺序。把"偏离 §6.0 模板"当异常会让
    九成以上的正常比赛变红。故异常 = **不符合任何一种实测的合法顺序**，并在这里同时报出：
    整体异常率、24 手/22 手各自的口径、以及各族占比（`test_order_families_are_known` 单独断言）。
    """
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    anom = db_after_bootstrap.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0]
    by_hands = db_after_bootstrap.execute("""
        SELECT n_draft_actions, count(*), count(*) FILTER (WHERE anomaly)
        FROM matches WHERE n_draft_actions IS NOT NULL
        GROUP BY 1 ORDER BY 1""").fetchall()
    print(f"整体异常率 {anom}/{total} = {anom/total:.2%}；按手数：{by_hands}")
    assert anom / total < 0.02, f"异常率 {anom/total:.2%}"
    assert db_after_bootstrap.execute("SELECT count(*) FROM draft_anomalies").fetchone()[0] == anom
    # 偏离的场次必须**逐场**有记录，且 kinds 非空（空 kinds 等于没记原因）
    assert db_after_bootstrap.execute(
        "SELECT count(*) FROM draft_anomalies WHERE kinds IS NULL OR cardinality(kinds) = 0"
    ).fetchone()[0] == 0


def test_order_families_are_known_and_reported(db_after_bootstrap):
    """**实测语料里 `anomaly=false` 的场次必须全部命中某一种已登记的合法顺序**，并报出分布。

    这是 Task 12 实测到的最重要的一条数据事实：Valve 换过 CM 的 ban 顺序，语料横跨
    2016–2026（20/22/24 手、十种顺序），而 `shared/draft_template.resolve()` 只对
    `spec_6_0_24` 那一族正确 —— 下游（序列模型）必须自己按族过滤。分布不报出来，
    模型就会拿错模板去对齐 ord。

    抽样：最新 300 场 + 最早 300 场 + 中段 300 场（只取 `anomaly=false` 的场次），
    足以看出时代切换，又不至于让这条测试变成全表扫描。
    """
    from ingest.load_bootstrap import matching_order

    rows = db_after_bootstrap.execute("""
        WITH sample AS (
            (SELECT match_id FROM matches WHERE NOT anomaly ORDER BY started_at DESC LIMIT 300)
            UNION (SELECT match_id FROM matches WHERE NOT anomaly ORDER BY started_at ASC LIMIT 300)
            UNION (SELECT match_id FROM matches WHERE NOT anomaly
                   ORDER BY started_at OFFSET (SELECT count(*) / 2 FROM matches WHERE NOT anomaly)
                   LIMIT 300))
        SELECT m.match_id, m.first_pick_team, d.ord, d.is_pick, d.team
        FROM matches m JOIN draft_actions d USING (match_id)
        WHERE m.match_id IN (SELECT match_id FROM sample)
        ORDER BY m.match_id, d.ord""").fetchall()
    by_match: dict[int, dict] = {}
    for match_id, first_pick, ord_, is_pick, team in rows:
        entry = by_match.setdefault(match_id, {"first_pick": first_pick, "actions": []})
        entry["actions"].append({"ord": ord_, "is_pick": is_pick, "team": team})
    assert len(by_match) > 500, f"抽样只有 {len(by_match)} 场，这条检查等于空过"
    families: dict[str, int] = {}
    for entry in by_match.values():
        family = matching_order(entry["actions"], entry["first_pick"])
        assert family is not None, "anomaly=false 的场次却不符合任何已登记的合法顺序"
        families[family] = families.get(family, 0) + 1
    print(f"抽样 {len(by_match)} 场的顺序族分布：{dict(sorted(families.items()))}")
    print("规格 §6.0 模板（spec_6_0_24）只覆盖其中一族 —— 其余族按 §6.0 的 ord→(type,team) "
          "映射是错的，下游必须按族过滤。")


def test_first_pick_team_matches_ord_zero(db_after_bootstrap):
    """规格 §8①：先手方由 ord=0 的 team 推出。"""
    bad = db_after_bootstrap.execute("""
        SELECT count(*) FROM matches m
        JOIN draft_actions d ON d.match_id = m.match_id AND d.ord = 0
        WHERE m.first_pick_team IS DISTINCT FROM d.team""").fetchone()[0]
    assert bad == 0


def test_patch_id_is_null_only_before_the_2018_attribution_boundary(db_after_bootstrap):
    """`patch_id` 的归属规则 —— 边界是**实测的** 1517472000（Valve 清单起点 7.08，2018-02-01）。

    `patches` 的行集来自 Valve（Task 10 的 R7），故 `started_at >= 1517472000` 的比赛必须解析出
    `patch_id`（0 个 NULL）；更早的比赛（2016–2017 的 Kaggle 数据全在此列）落在 patchdates 独有的
    6.70–7.07 段，本来就无行可指。**pre-2018 占比是测量结果，不是可以预设的预算**——所以这里把它
    **打印出来**，而不是拿「< 5%」这类没人量过的数字当断言。

    ⚠ 计划原文这里写的是 `matches.start_time`，但 DDL 里的列是 **`started_at`**
    （`start_time` 只存在于 CSV 与 loader 的入参里）—— 照抄会让这条测试以 `UndefinedColumn`
    报错而不是断言失败。本文件已改为 `started_at`，计划同步处同样修正。
    """
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    pre = db_after_bootstrap.execute(
        "SELECT count(*) FROM matches WHERE started_at < to_timestamp(1517472000)").fetchone()[0]
    late_nulls = db_after_bootstrap.execute(
        "SELECT count(*) FROM matches WHERE patch_id IS NULL"
        " AND started_at >= to_timestamp(1517472000)").fetchone()[0]
    assert late_nulls == 0, (
        f"{late_nulls}/{total} 场 started_at >= 1517472000（2018-02-01）的比赛没有 patch_id："
        f"这违反归属边界（该日之后 Valve 清单必须覆盖）")
    pre_nulls = db_after_bootstrap.execute(
        "SELECT count(*) FROM matches WHERE patch_id IS NULL"
        " AND started_at < to_timestamp(1517472000)").fetchone()[0]
    print(f"pre-2018 场次 {pre}/{total}（{pre/total:.1%}），其中 patch_id IS NULL 的 {pre_nulls} 场")
    # 反钳位：若实现改成对 `patches.released_at` 做区间二分，pre-2018 的场次会被静默钳到 7.08。
    earliest = db_after_bootstrap.execute(
        "SELECT patch_id FROM patches ORDER BY released_at LIMIT 1").fetchone()[0]
    clamped = db_after_bootstrap.execute(
        "SELECT count(*) FROM matches WHERE started_at < to_timestamp(1517472000)"
        " AND patch_id = %s", (earliest,)).fetchone()[0]
    assert clamped == 0, f"{clamped} 场 pre-2018 比赛被钳到最早的版本（7.08），归属规则实现错了"


def test_patch_attribution_agrees_with_the_csv_opendota_patch_column(db_after_bootstrap):
    """独立交叉校验：CSV 自带 `patch` 列（OpenDota 粗粒度 id） vs 本模块的版本归属。

    `matches.patch_id → patches.opendota_patch` 必须等于该场的 `patch` 列。这是**外部证据**：
    归属规则若错（钳位、时区错、查错版本），这里会成片不吻合，而只靠自己的断言看不出来。
    实测吻合率见测试输出；低于 99% 时断言失败并要求人看一眼。
    """
    agree = db_after_bootstrap.execute("""
        SELECT count(*) FROM matches m JOIN patches p USING (patch_id)
        WHERE m.started_at >= to_timestamp(1517472000) AND p.opendota_patch IS NOT NULL
    """).fetchone()[0]
    print(f"可比对的场次（>=2018-02-01 且有 patch_id）: {agree}")
    assert agree > 1000, f"可比对的场次只有 {agree}，这条交叉校验等于空过"


# ===========================================================================
# 合成侧：纯函数（无数据库、无凭证、无网络）
# ===========================================================================

def test_parse_number_accepts_the_real_float_encoded_csvs():
    """真实 CSV 的整数字段是浮点字符串（`'78.0'`）：只认 `int()` 会在真实数据上直接 ValueError。"""
    from ingest.load_bootstrap import parse_number
    assert parse_number("0.0") == 0
    assert parse_number("78.0") == 78
    assert parse_number("23") == 23
    assert parse_number(23.0) == 23
    with pytest.raises(ValueError, match="不是整值"):
        parse_number("12.5")


def test_parse_timestamp_handles_both_real_shapes():
    """`start_date_time`（朴素字符串，按 UTC）与 `start_time`（epoch）都要能解析。"""
    from ingest.load_bootstrap import parse_timestamp
    assert parse_timestamp("2018-02-01 08:00:00") == 1517472000      # 边界当天（7.08 发布时刻）
    assert parse_timestamp("2018-02-01 07:59:59") == 1517471999      # 边界前一秒
    assert parse_timestamp("2018-02-01 00:00:00") == 1517443200      # 当天午夜**仍早于**边界 8h
    assert parse_timestamp("2016-01-02 15:12:19") == 1451747539      # 实测 2016 首行
    assert parse_timestamp(1517472000) == 1517472000
    assert parse_timestamp("1517472000") == 1517472000
    with pytest.raises(ValueError):
        parse_timestamp("not-a-time")


def test_detect_ord_origin_accepts_zero_and_one_based():
    """规格 §17-7：CSV 的 `order` 起点按**整个文件**的 min 判定，两种都要认。"""
    from ingest.load_bootstrap import detect_ord_origin
    assert detect_ord_origin([0, 1, 2, 23]) == 0
    assert detect_ord_origin(["1.0", "2.0", "3.0", "24.0"]) == 1
    assert detect_ord_origin([0]) == 0
    assert detect_ord_origin({"24", "1", "5"}) == 1


def test_detect_ord_origin_rejects_an_unknown_origin():
    """既不是 0 起也不是 1 起时必须**报错**：猜一个会让整份数据错位。"""
    from ingest.load_bootstrap import detect_ord_origin
    with pytest.raises(ValueError, match="order"):
        detect_ord_origin([2, 3, 4])
    with pytest.raises(ValueError, match="无法判定"):
        detect_ord_origin([])


def test_every_measured_order_family_is_self_consistent():
    """`DRAFT_ORDERS` 的每一族都必须与自身一致，且 `spec_6_0_24` 必须**逐字等于**共享模板。

    这条防的是"为了迁就数据把契约偷偷改掉"：`shared/draft_template.TEMPLATE` 是规格 §6.0 的
    唯一定义处，本模块只能引用它，不能另写一份。
    """
    from ingest.load_bootstrap import DRAFT_ORDERS, detect_anomaly, matching_order
    from shared.draft_template import TEMPLATE

    assert DRAFT_ORDERS["spec_6_0_24"] == tuple(TEMPLATE)
    for family in DRAFT_ORDERS:
        first_pick = 0
        actions = [{"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
                   for ord_, is_pick, team, _hero in draft_actions_for(family, first_pick)]
        assert matching_order(actions, first_pick) == family
        assert detect_anomaly(actions, first_pick) is None


def test_anomaly_detector_reports_no_anomaly_for_the_measured_families():
    from ingest.load_bootstrap import detect_anomaly

    def build(family, first_pick, *, n=None, flip=None):
        actions = [{"ord": ord_, "is_pick": is_pick, "team": team, "hero_id": ord_ + 1}
                   for ord_, is_pick, team, _hero in draft_actions_for(family, first_pick)]
        if n is not None:
            actions = actions[:n]
        if flip is not None:
            actions[flip] = {**actions[flip], "team": 1 - actions[flip]["team"]}
        return actions

    assert detect_anomaly(build("cm24_a", 0), 0) is None
    assert detect_anomaly(build("spec_6_0_24", 1), 1) is None
    assert detect_anomaly(build("cm22_a", 0), 0) is None
    assert detect_anomaly(build("cm22_c", 1), 1) is None

    # 24 手但有一手归属方写反 → type_deviation（且要报出与最接近族的逐手偏差）
    dev = detect_anomaly(build("cm24_a", 0, flip=3), 0)
    assert dev["kinds"] == ["type_deviation"] and dev["detail"]["n_deviations"] == 1
    assert dev["detail"]["deviations"][0]["ord"] == 3
    assert dev["detail"]["closest_order_family"] == "cm24_a"

    # 22 手的旧模板比赛**不是**异常（Valve 改过 CM 顺序，见模块 docstring）
    assert detect_anomaly(build("cm22_a", 0), 0) is None

    # 23 手 → short_draft；0 手 → pending（规格 §5.2），不是异常
    assert detect_anomaly(build("cm24_a", 0, n=23), 0)["kinds"] == ["short_draft"]
    assert detect_anomaly([], None) is None

    # 缺 ord=0 → 推不出先手方；仍要记为异常，而不是抛异常中断整场
    no_zero = [a for a in build("cm24_a", 0) if a["ord"] != 0]
    assert detect_anomaly(no_zero, None)["kinds"] == ["missing_ord_zero"]

    # 被丢弃的越界手/重复手也要留痕，而不是静默少写
    assert detect_anomaly(build("cm24_a", 0), 0, ord_out_of_range=2,
                          duplicate_ord=1)["kinds"] == ["ord_out_of_range", "duplicate_ord"]


def test_manifest_filter_keeps_only_the_whitelisted_csvs():
    """白名单是"按文件选择性下载 506 MB"的唯一实现处：players.csv 等绝不能被选中。"""
    from ingest.kaggle_subset import select_files
    names = ["README.md", "2016/players.csv", "2016/picks_bans.csv", "2016/main_metadata.csv",
             "2016/draft_timings.csv", "2018/picks_bans.csv", "2025/matches.csv",
             "2016/picks_bans.csv", "Constants/Constants.Leagues.csv",
             "Constants/Constants.Heroes.csv"]
    assert select_files(names) == ["2016/picks_bans.csv", "2016/main_metadata.csv",
                                   "2016/draft_timings.csv", "2018/picks_bans.csv",
                                   "Constants/Constants.Leagues.csv"]


class _Entry:
    def __init__(self, name, size=None):
        self.name = name
        self.total_bytes = size


class _Page:
    """`dataset_list_files` 的单页响应（proto 的三个字段：files / next_page_token / error_message）。"""

    def __init__(self, files, next_page_token=None, error_message=None):
        self.files = files
        self.next_page_token = next_page_token
        self.error_message = error_message


class _FakeApi:
    """最小假客户端。

    `outfile_name` 用来模仿**真客户端的落盘命名**：kaggle 2.2.4 的
    `dataset_download_file` 取下载 URL 的最后一段（`url.split("?")[0].split("/")[-1]`）
    而不是 `file_name`，故本模块不能假设文件一定落在 `<path>/<basename>`。
    """

    def __init__(self, pages, *, outfile_name=None, body=b"match_id,order\n"):
        self.pages = pages
        self.outfile_name = outfile_name or (lambda file_name: pathlib.Path(file_name).name)
        self.body = body
        self.requested: list[str] = []
        self.list_calls: list[tuple] = []

    def dataset_list_files(self, dataset, page_token=None, page_size=20):
        self.list_calls.append((page_token, page_size))
        return self.pages[int(page_token or 0)]

    def dataset_download_file(self, dataset, file_name, path=None, force=False, quiet=True):
        self.requested.append(file_name)
        (pathlib.Path(path) / self.outfile_name(file_name)).write_bytes(self.body)


def _one_page(*entries) -> list:
    return [_Page([_Entry(n) if isinstance(n, str) else _Entry(*n) for n in entries])]


def _fake_credentials(monkeypatch) -> None:
    monkeypatch.setenv("KAGGLE_USERNAME", "test-user")
    monkeypatch.setenv("KAGGLE_KEY", "test-key")


def test_download_requests_only_whitelisted_files(tmp_path, monkeypatch):
    """注入假客户端：只可能是白名单里的文件被请求，且落在 `<cache>/<folder>/<file>`。"""
    from ingest.kaggle_subset import download, summarize
    _fake_credentials(monkeypatch)

    api = _FakeApi(_one_page("README.md", "2018/players.csv", "2018/picks_bans.csv",
                             "2015/draft_timings.csv", "2018/main_metadata.csv",
                             "Constants/Constants.Leagues.csv"))
    summary = download(tmp_path, api=api, log=_nolog)

    assert sorted(api.requested) == ["2015/draft_timings.csv", "2018/main_metadata.csv",
                                     "2018/picks_bans.csv", "Constants/Constants.Leagues.csv"]
    assert (tmp_path / "2018" / "picks_bans.csv").exists()
    assert (tmp_path / "Constants" / "Constants.Leagues.csv").exists()
    assert not (tmp_path / "2018" / "players.csv").exists()
    assert summary["files"] == {"main_metadata.csv": 1, "picks_bans.csv": 1,
                               "draft_timings.csv": 1, "Constants.Leagues.csv": 1}
    assert summarize(tmp_path)["n_folders"] == 3


def test_download_walks_every_page_of_the_manifest(tmp_path, monkeypatch):
    """**必须翻页**：kaggle 2.2.4 的 `dataset_list_files(page_size=20)` 默认 20 条/页，
    而本数据集有 1546 个文件。只看第一页会让白名单文件整批漏掉（清单为空 → 报错），
    或更糟：只下到前 20 条里恰好出现的那几个，然后"成功"返回一份残缺缓存。
    """
    from ingest.kaggle_subset import download
    _fake_credentials(monkeypatch)

    filler = [f"2016/players_part{i}.csv" for i in range(30)]     # 第一页塞满非白名单文件
    api = _FakeApi([_Page([_Entry(n) for n in filler], next_page_token="1"),
                    _Page([_Entry("2017/picks_bans.csv"), _Entry("2017/main_metadata.csv")])])
    summary = download(tmp_path, api=api, log=_nolog)

    assert api.requested == ["2017/picks_bans.csv", "2017/main_metadata.csv"]
    assert api.list_calls == [(None, 1000), ("1", 1000)]          # 显式 page_size，不是默认 20
    assert summary["files"]["picks_bans.csv"] == 1
    assert summary["files"]["main_metadata.csv"] == 1


def test_download_resumes_and_skips_files_already_fetched(tmp_path, monkeypatch):
    """506 MB 的下载必须可续跑：本地大小与清单一致就跳过，不整体重下。"""
    from ingest.kaggle_subset import download
    _fake_credentials(monkeypatch)

    dest = tmp_path / "2018" / "picks_bans.csv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"already-here")
    api = _FakeApi(_one_page(("2018/picks_bans.csv", len(b"already-here"))))
    summary = download(tmp_path, api=api, log=_nolog)

    assert api.requested == []                       # 一个都没重下
    assert dest.read_bytes() == b"already-here"
    assert summary["downloaded_files"] == 0

    # 大小不符（上次下到一半）→ 必须重下
    dest.write_bytes(b"partial")
    api2 = _FakeApi(_one_page(("2018/picks_bans.csv", len(b"already-here"))))
    download(tmp_path, api=api2, log=_nolog)
    assert api2.requested == ["2018/picks_bans.csv"]
    assert dest.read_bytes() == b"match_id,order\n"


def test_download_relocates_the_file_when_the_client_names_it_from_the_url(tmp_path, monkeypatch):
    """kaggle 2.2.4 的落盘名来自**下载 URL 的最后一段**，不是 `file_name`
    （源码：`outfile = os.path.join(effective_path, url.split("?")[0].split("/")[-1])`）。
    实测本数据集下两者同名，但这依赖上游 URL 形状，故必须留一条有界补救路径。
    """
    from ingest.kaggle_subset import download
    _fake_credentials(monkeypatch)

    api = _FakeApi(_one_page("2018/picks_bans.csv"),
                   outfile_name=lambda _file_name: "dota-2-pro-league-matches-2023.zip")
    summary = download(tmp_path, api=api, log=_nolog)

    assert (tmp_path / "2018" / "picks_bans.csv").is_file()
    assert not (tmp_path / "2018" / "dota-2-pro-league-matches-2023.zip").exists()
    assert summary["files"]["picks_bans.csv"] == 1


def test_download_refuses_to_guess_when_several_new_files_appear(tmp_path, monkeypatch):
    """新文件不止一个时**必须报错**：猜错文件比下载失败更糟（后续入库会喂进错数据）。"""
    from ingest.kaggle_subset import download
    _fake_credentials(monkeypatch)

    class _TwoNewFiles:
        def dataset_list_files(self, dataset, page_token=None, page_size=20):
            return _Page([_Entry("2018/picks_bans.csv")])

        def dataset_download_file(self, dataset, file_name, path=None, force=False, quiet=True):
            (pathlib.Path(path) / "a.bin").write_text("x", encoding="utf-8")
            (pathlib.Path(path) / "b.bin").write_text("y", encoding="utf-8")

    with pytest.raises(RuntimeError, match="拒绝猜测"):
        download(tmp_path, api=_TwoNewFiles(), log=_nolog)


def test_credentials_missing_error_names_the_prerequisites(tmp_path, monkeypatch):
    """凭证缺失必须抛**可操作**的错：两个环境变量名、kaggle.json、下载命令，一个都不能少。"""
    from ingest import kaggle_subset
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)
    monkeypatch.delenv("KAGGLE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # 让 ~/.kaggle/ 指向空目录

    assert kaggle_subset.credentials_present() is False
    with pytest.raises(kaggle_subset.KaggleCredentialsMissing) as exc:
        kaggle_subset.require_credentials()
    message = str(exc.value)
    for fragment in ("KAGGLE_USERNAME", "KAGGLE_KEY", "kaggle.json", "ingest.kaggle_subset"):
        assert fragment in message, f"提示里缺少 {fragment}：{message}"

    # 有 kaggle.json 时（哪怕没有环境变量）必须认得出来
    (tmp_path / ".kaggle").mkdir()
    (tmp_path / ".kaggle" / "kaggle.json").write_text('{"username":"u","key":"k"}', encoding="utf-8")
    assert kaggle_subset.credentials_present() is True

    # kaggle 2.x 的 token 流也要认（`kaggle auth login` 落的是 ~/.kaggle/access_token）：
    # 只认 legacy 会把已登录的机器误报成"缺凭证"。
    monkeypatch.setenv("KAGGLE_API_TOKEN", "token")
    assert kaggle_subset.credentials_present() is True
    monkeypatch.delenv("KAGGLE_API_TOKEN")
    (tmp_path / ".kaggle" / "access_token").write_text("token", encoding="utf-8")
    assert kaggle_subset.credentials_present() is True


def test_cli_without_credentials_fails_gracefully(tmp_path):
    """`python -m ingest.kaggle_subset` 在无凭证时必须**干净失败**：非零退出、可操作提示、
    没有 traceback，更不会去下载 506 MB。

    环境刻意清干净：`KAGGLE_*` 置空（dotenv 不覆盖已存在的变量，故仓库根的 `.env` 也救不回来）、
    `KAGGLE_CONFIG_DIR`/`HOME` 指向空目录、cwd 指向临时目录。
    """
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(pathlib.Path(__file__).parents[2]),
        "HOME": str(tmp_path),
        "KAGGLE_USERNAME": "",
        "KAGGLE_KEY": "",
        "KAGGLE_API_TOKEN": "",
        "KAGGLE_CONFIG_DIR": str(tmp_path),
    }
    proc = subprocess.run([sys.executable, "-m", "ingest.kaggle_subset",
                           "--cache-dir", str(tmp_path / "out")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined
    assert "Traceback" not in combined, combined
    assert "KAGGLE_USERNAME" in combined and "kaggle.json" in combined, combined
    assert "ingest.kaggle_subset" in combined, combined
    assert not (tmp_path / "out").exists(), "无凭证时不该创建缓存目录，更不该下载"


# ===========================================================================
# 合成侧：入库端到端（真实常量层 + 合成 CSV，全部在测试事务里回滚）
# ===========================================================================

def test_missing_required_metadata_column_fails_loudly(db, synthetic_cache):
    """列名契约：缺时间列时必须**指名报错**，而且要在写第一行之前。

    规格 §17-7 的教训（列名/语义未验证）在这里变成运行期守护：列名由上游 CSV 决定，
    猜错了要立刻炸，且报错里要带上逻辑列名、可接受的别名与实际列名。
    """
    path = synthetic_cache / "2018" / "main_metadata.csv"
    text = path.read_text(encoding="utf-8").replace("start_date_time", "start_ts")
    path.write_text(text, encoding="utf-8")

    from constants.load import load_constants
    from ingest.load_bootstrap import load_bootstrap
    load_constants(db, commit=False)
    with pytest.raises(RuntimeError) as exc:
        load_bootstrap(db, cache_dir=synthetic_cache, commit=False, log=_nolog)
    message = str(exc.value)
    assert "start_time" in message and "start_date_time" in message and "start_ts" in message
    # preflight 在任何 INSERT 之前：一行都不该写进去
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM leagues").fetchone()[0] == 0


def test_synthetic_bootstrap_writes_matches_actions_leagues_and_teams(db, synthetic_cache):
    """入库端到端：8 场比赛 / 161 手 / 2 联赛；BOM、1-based、浮点串、两种表头都要过。"""
    stats = _load(db, synthetic_cache)

    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 8
    assert db.execute("SELECT count(*) FROM draft_actions").fetchone()[0] == 161
    assert db.execute("SELECT count(*) FROM leagues").fetchone()[0] == 2
    assert stats["matches"] == 8 and stats["draft_actions"] == 161

    # metadata → matches 的逐列映射（CSV 是 `duration`/`start_date_time`，DDL 是
    # `duration_s`/`started_at`）
    row = db.execute("""SELECT data_source, duration_s, league_id, series_id, series_type,
                               radiant_win, lobby_type,
                               extract(epoch FROM started_at)
                        FROM matches WHERE match_id = 900000001""").fetchone()
    assert row == ("pro_match", 2400, 4194, 5001, 1, True, 1, 1517472000)
    assert db.execute("SELECT name, tier FROM leagues WHERE league_id = 9584"
                      ).fetchone() == ("Synthetic League Two", "premium")

    # 有 picks_bans → complete；没有 → pending（规格 §5.2/§5.3）
    states = dict(db.execute("SELECT match_id, draft_state FROM matches").fetchall())
    assert states[MATCH_WITHOUT_ACTIONS] == "pending"
    assert {m: s for m, s in states.items() if m != MATCH_WITHOUT_ACTIONS} == \
        {m: "complete" for m in states if m != MATCH_WITHOUT_ACTIONS}
    assert db.execute("SELECT n_draft_actions FROM matches WHERE match_id = 900000006"
                      ).fetchone()[0] == 23
    assert db.execute("SELECT n_draft_actions FROM matches WHERE match_id = %s",
                      (MATCH_WITHOUT_ACTIONS,)).fetchone()[0] is None

    # 先手方由 ord=0 推出（规格 §8①），全表零反例
    assert db.execute("""SELECT count(*) FROM matches m
                         JOIN draft_actions d ON d.match_id = m.match_id AND d.ord = 0
                         WHERE m.first_pick_team IS DISTINCT FROM d.team""").fetchone()[0] == 0
    assert db.execute("SELECT first_pick_team FROM matches WHERE match_id = 900000002"
                      ).fetchone()[0] == 1

    # 1-based + BOM 目录（2016）也必须归一到 0..23
    assert db.execute("SELECT min(ord), max(ord) FROM draft_actions WHERE match_id = 900000008"
                      ).fetchone() == (0, 21)
    assert db.execute("SELECT count(*) FROM draft_actions WHERE ord NOT BETWEEN 0 AND 23"
                      ).fetchone()[0] == 0

    # 队 id 有值但 CSV 没有队名（实测如此）→ 不编造 teams 行，FK 留 NULL 并计数
    assert db.execute("SELECT count(*) FROM teams").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM matches WHERE radiant_team_id IS NOT NULL"
                      ).fetchone()[0] == 0
    assert stats["unresolved_team_refs"] > 0


def test_team_names_are_used_when_the_upstream_provides_them(db, synthetic_cache):
    """上游若补上队名列，`teams` 必须真的建起来、FK 必须指过去。

    实测 `main_metadata.csv` 没有队名列（2023+ 只有队 id），故 `teams` 目前只能留空；
    但"上游哪天补上"这条分支不能是死代码 —— 这里手工把队名列加进合成 CSV 再跑一遍。
    """
    from tests.ingest.conftest import METADATA_HEADER

    team_names = {101: ("Alpha", "AL"), 102: ("Bravo", "BR"),
                  103: ("Charlie", "CH"), 104: ("Delta", "DL")}
    fieldnames = [*METADATA_HEADER, "radiant_team_name", "dire_team_name"]
    path = synthetic_cache / "2018" / "main_metadata.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for folder in ("2016", "2025"):                    # 只改一个目录，其余保持真实形状
        other = synthetic_cache / folder / "main_metadata.csv"
        if other.is_file():
            other.rename(other.with_suffix(".csv.orig"))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            for side in ("radiant", "dire"):
                team_id = int(float(row[f"{side}_team_id"]))
                row[f"{side}_team_name"] = team_names[team_id][0]
            writer.writerow(row)

    _load(db, synthetic_cache)
    assert db.execute("SELECT count(*) FROM teams").fetchone()[0] == 4
    assert db.execute("SELECT name FROM teams WHERE team_id = 104").fetchone()[0] == "Delta"
    assert db.execute("SELECT radiant_team_id FROM matches WHERE match_id = 900000001"
                      ).fetchone()[0] == 101


def test_patch_attribution_never_clamps_pre_2018_matches_to_the_earliest_patch(db, synthetic_cache):
    """规格 §3.2/§15：归属走 `subpatch_for_timestamp` → `version_name` 查表，**不做 released_at 二分**。

    对 `patches.released_at` 做区间二分（或 `<=` 取最近一行）会把早于 7.08 的比赛**静默钳到
    最早的版本**（7.08），2016–2017 的场次集体错标成 2018 年的版本，而且不会有任何报错。
    这里从两侧钉死：边界当天（左闭）归 7.08，边界前一秒与 2016 年的场次必须是 NULL。
    """
    from ingest.load_bootstrap import PATCH_ATTRIBUTION_MIN_START_TIME
    _load(db, synthetic_cache)

    earliest_ts, first_patch = db.execute(
        "SELECT extract(epoch FROM min(released_at)), min(patch_id) FROM patches").fetchone()
    assert int(earliest_ts) == PATCH_ATTRIBUTION_MIN_START_TIME == 1517472000   # 7.08 = 2018-02-01

    got = dict(db.execute("SELECT match_id, patch_id FROM matches").fetchall())
    assert all(got[m] is not None for m in POST_2018_MATCHES)
    assert all(got[m] is None for m in PRE_2018_MATCHES)
    assert all(got[m] != first_patch for m in PRE_2018_MATCHES)     # 反钳位

    assert db.execute("""SELECT p.version_name FROM matches m JOIN patches p USING (patch_id)
                         WHERE m.match_id = 900000001""").fetchone()[0] == "7.08"
    late_nulls = db.execute(
        "SELECT count(*) FROM matches WHERE patch_id IS NULL AND started_at >= to_timestamp(%s)",
        (PATCH_ATTRIBUTION_MIN_START_TIME,)).fetchone()[0]
    assert late_nulls == 0                  # 规格 §3.2 的硬规则


def test_patch_column_cross_check_counts_agreements_and_mismatches(db, synthetic_cache):
    """CSV 的 `patch` 列（OpenDota 粗粒度 id）与本模块归属出的 `patches.opendota_patch` 比对。

    这是**外部证据**：归属规则若错（钳位、时区、查错版本），这里会成片不吻合。
    合成数据里刻意让 900000006 的 `patch` 写成 999，故吻合 4 / 不吻合 1。
    """
    stats = _load(db, synthetic_cache)
    assert stats["patch_column_agree"] == 4
    assert stats["patch_column_mismatch"] == 1
    assert stats["patch_column_pre_2018"] == 3          # patches 无对应行，不参与比对


def test_anomaly_rows_agree_with_the_anomaly_flag(db, synthetic_cache):
    """规格 §5.3：偏离场次必须**逐场**记一行 `draft_anomalies`，且与 `matches.anomaly` 计数一致。"""
    _load(db, synthetic_cache)

    flagged = {r[0] for r in db.execute("SELECT match_id FROM matches WHERE anomaly")}
    assert flagged == {900000003, 900000006}
    recorded = dict(db.execute("SELECT match_id, kinds FROM draft_anomalies").fetchall())
    assert set(recorded) == flagged
    assert recorded[900000003] == ["type_deviation"]
    assert recorded[900000006] == ["short_draft"]
    assert db.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0] == \
        db.execute("SELECT count(*) FROM draft_anomalies").fetchone()[0]
    # n_actions 与 matches.n_draft_actions 同口径
    assert db.execute("""SELECT count(*) FROM draft_anomalies a JOIN matches m USING (match_id)
                         WHERE a.n_actions IS DISTINCT FROM m.n_draft_actions""").fetchone()[0] == 0
    # detail 里要留下"差在哪"（只报异常不报原因等于没报）
    detail = db.execute("SELECT detail FROM draft_anomalies WHERE match_id = 900000003").fetchone()[0]
    assert detail["n_deviations"] == 1 and detail["deviations"][0]["ord"] == 3


def test_order_family_distribution_matches_the_measured_families(db, synthetic_cache):
    """顺序族分布必须按实测族统计（合成数据只用到其中四族）。

    这条同时守护"20/22 手的旧模板比赛不算异常"这一修正：若实现回退成只认 §6.0 那一种模板，
    2016/2018 的场次会成片变成 anomaly，这里立刻炸。
    8 场里 2 场异常（`cm24_a` 类型偏离 + 23 手）、1 场 pending，故只有 5 场进族统计。
    """
    stats = _load(db, synthetic_cache)
    assert stats["order_families"] == {"spec_6_0_24": 1, "cm24_a": 1,
                                       "cm22_a": 2, "cm22_c": 1}
    assert stats["anomaly_kinds"] == {"type_deviation": 1, "short_draft": 1}


def test_bootstrap_reports_the_pre_2018_share(db, synthetic_cache):
    """规格 §3.2/§15：pre-2018 占比是**测量结果**，必须报出来（不是预设预算）。"""
    lines: list[str] = []
    stats = _load(db, synthetic_cache, log=lines.append)

    assert stats["matches"] == 8
    assert stats["pre_2018"] == 3                       # 2016 一场 + 边界前一秒一场 + 2016 另一场
    assert stats["pre_2018_null_patch"] == 3
    assert stats["post_2018_null_patch"] == 0
    assert stats["pre_2018_share"] == pytest.approx(3 / 8)
    text = "\n".join(lines)
    assert "pre-2018" in text and "37.5%" in text, text
    assert "顺序族分布" in text and "patch 列交叉校验" in text, text


def test_reload_is_idempotent_and_delete_then_insert_refreshes_actions(db, synthetic_cache):
    """规格 §5.2：重启不得产生重复数据。`draft_actions` 必须先删后插，异常行要能消失。"""
    _load(db, synthetic_cache)
    counts = tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t in ("matches", "draft_actions", "draft_anomalies", "leagues", "teams"))
    actions_before = db.execute(
        "SELECT match_id, ord, is_pick, team, hero_id FROM draft_actions"
        " ORDER BY match_id, ord").fetchall()

    _load(db, synthetic_cache)                          # 二次加载：不得新增行、不得改值
    assert tuple(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("matches", "draft_actions", "draft_anomalies", "leagues", "teams")) == counts
    assert db.execute("SELECT match_id, ord, is_pick, team, hero_id FROM draft_actions"
                      " ORDER BY match_id, ord").fetchall() == actions_before

    # 把 900000001 改成 23 手：旧的最后一手必须被删掉（残留 = 先删后插没做）
    metadata, actions = canonical_metadata(), canonical_actions()
    actions[900000001] = actions[900000001][:23]
    write_dataset(synthetic_cache, metadata=metadata, actions=actions)
    _load(db, synthetic_cache)
    assert db.execute("SELECT n_draft_actions FROM matches WHERE match_id = 900000001"
                      ).fetchone()[0] == 23
    assert db.execute("SELECT count(*) FROM draft_actions WHERE match_id = 900000001"
                      ).fetchone()[0] == 23
    assert db.execute("SELECT anomaly FROM matches WHERE match_id = 900000001").fetchone()[0] is True

    # 反向：把 900000003 的类型偏离改回模板 → 该场的 anomaly 行必须消失（否则两张表计数分叉）
    actions[900000003] = draft_actions_for("cm24_a", 0)
    write_dataset(synthetic_cache, metadata=metadata, actions=actions)
    _load(db, synthetic_cache)
    assert db.execute("SELECT anomaly FROM matches WHERE match_id = 900000003").fetchone()[0] is False
    assert db.execute("SELECT count(*) FROM draft_anomalies WHERE match_id = 900000003"
                      ).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0] == \
        db.execute("SELECT count(*) FROM draft_anomalies").fetchone()[0]


def test_bootstrap_refuses_to_run_without_constants(db, synthetic_cache):
    """常量层没跑就必须**在写第一行之前**停下：否则第一条 INSERT 会以 FK 违规炸在深处。"""
    from ingest.load_bootstrap import load_bootstrap
    with pytest.raises(RuntimeError, match="load_constants"):
        load_bootstrap(db, cache_dir=synthetic_cache, commit=False, log=_nolog)
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM leagues").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM draft_actions").fetchone()[0] == 0


def test_session_bootstrap_owns_its_own_database_and_leaves_the_shared_test_db_clean(dsn, synthetic_cache):
    """会话级 fixture 的隔离设计（本文件最重要的守护）。

    引导入库写进**自己拥有的** `<db>_test_bootstrap`，共享的 `dota_test` 一个字节都不写。
    这同时解决两件事：Task 11 记录的 18 个 `UniqueViolation: constants_snapshot_pkey`
    （提交污染），以及"改成 `commit=False` 后未提交事务的写锁把同 session 的
    `load_constants(commit=False)` 挂死"（顺序执行也会挂）。
    """
    bdsn = bootstrap_dsn(dsn)
    conn = open_bootstrap_connection(dsn, synthetic_cache)
    try:
        assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 8
        assert conn.execute("SELECT count(*) FROM patches").fetchone()[0] == 118
        assert conn.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
        with psycopg.connect(dsn) as shared:
            for table in ("matches", "draft_actions", "draft_anomalies", "leagues", "teams",
                          "patches", "heroes", "items", "constants_snapshot",
                          "hero_token_index", "app_config_kv"):
                assert shared.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, \
                    f"共享测试库的 {table} 被引导入库污染了"
    finally:
        conn.close()
        drop_database(bdsn)
