"""版本表：Valve 权威清单 + patchdates 子版本还原（规格 §3.2、§15「版本归属测试」）。

**唯一权威是 Valve 的 `patchnoteslist`**：118 个版本、其中 84 个字母版本、从 7.08 起
（规格 §16.3）。`patchdates.json` 只做两件事：

1. 补出 Valve 未收录的序列（6.70–7.07 段）用于**归属**（不写入 `patches` 表）；
2. 与 Valve 交叉校验：偏差超过 ±2 天记入 `outliers`，且**一律以 Valve 为准**。

字母映射规则（规格 §3.2，固化在 `restore_subpatch_dates`）::

    main      -> <code>            (基础版本，如 '7.41')
    add       -> <code> + 'a'      (若存在)
    dates[0]  -> <code> + 'b'      ← 从 'b' 起，不是 'a'
    dates[1]  -> <code> + 'c'
    ...

映射结果进入时间线/行集之前还要过一道**时间线不倒挂不变式**（`_drop_backdated_slots`）：槽位不得早于
前一个序列的 `main`。实测它丢掉 7.06 的一个错档槽位（84 → 83 行）—— 那个槽位实际属于 6.88 时代。

**归属边界（Task 12 必须按它处理 `patch_id`）**：Valve 清单从 7.08 = 1517472000（2018-02-01）起，
patchdates 独有的 6.70–7.07 段**全部**排在其之前。故 `subpatch_for_timestamp(ts)` 返回的名字
**只有**对 `ts >= 1517472000` 才保证出现在 `patches` 行集里。

三处已知例外（`KNOWN_EXCEPTIONS`）：**7.22 不排序、7.25 不移位、7.06 错档槽位丢弃** —— 详见该常量。

性能：两个上游响应用 `lru_cache` **按进程缓存**（一次进程只读一次磁盘；`REFRESH_NETWORK=1` 时也只刷新
一次——否则每条测试都会重拉，patchdates 的 raw 优先策略会连续 timeout）；`_version_timeline()` 每次重建。
`lru_cache` 一旦填充，`MCNDOTAGA_CACHE_DIR` / `REFRESH_NETWORK` 的改动就只在 `cache_clear()` 之后生效。
`fetch_valve_patches()` 返回**防御性拷贝**（list 与每行 dict 都是新的）；`fetch_patchdates()` 返回的仍是
缓存里的**同一个对象**，调用方不得原地修改。
"""
from __future__ import annotations

import bisect
import datetime
import functools
import string
from typing import Any, Mapping

import httpx

from . import _http

VALVE_PATCHNOTESLIST_URL = "https://www.dota2.com/datafeed/patchnoteslist?language=english"
# 计划只写了仓库名（"D2-LRG-Metadata/patchdates.json（无认证）"）而没钉 URL，这里钉 canonical raw URL。
# 2026-09-17 实测：7686 B、61 个键、键是 OpenDota 粗粒度 patch id 的**字符串**。
PATCHDATES_URL = "https://raw.githubusercontent.com/leamare/D2-LRG-Metadata/master/patchdates.json"
# 同一文件的 jsDelivr 镜像（仅为可用性回退）：本环境 raw.githubusercontent.com 的 TLS 握手会超时
# （curl 与 httpx 均 60 s 无响应），镜像返回同样 7686 B。回退只在 raw 抛 httpx.HTTPError 时发生。
PATCHDATES_MIRROR_URL = (
    "https://cdn.jsdelivr.net/gh/leamare/D2-LRG-Metadata@master/patchdates.json")

VALVE_CACHE_NAME = "dota2_patchnoteslist.json"
PATCHDATES_CACHE_NAME = "d2lrg_patchdates.json"

# 规格 §3.2：「偏差超过 ±2 天则记录告警并以 Valve 为准」。按**日历天**计（见 cross_check_against_valve）。
MAX_TOLERATED_DEVIATION_DAYS = 2

_LETTERS = string.ascii_lowercase

# 三个已知例外必须显式声明 —— 每个例外的性质不同，逐条说清（**不要**用一句「偏差都在 ±2 天阈值之内」
# 概括：那是错的，7.22 的唯一例外槽位偏差 -34 天，本来就超阈值）：
# - 7.22：偏差 -34 天，**超出** ±2 天阈值，偏差检测能抓到。声明它是为了固化「不排序」的读法，并让
#   cross_check 的 max_deviation_days 排除它（否则那条 `<= 2` 哨兵会永远红）；例外仍出现在 outliers 里。
# - 7.25：偏差 +1 天，落在阈值**之内**，偏差检测抓不到，只能靠显式声明。
# - 7.06：不是「偏差」问题，而是槽位**错档** —— 该槽位早于前一个序列的 main，由时间线不倒挂不变式
#   整条丢弃（丢弃后该窗口归 6.88c）；原始 patchdates 字节不变，故仍需要声明。
# 键用 float 是为了让 `7.22 in KNOWN_EXCEPTIONS` 这种写法直接可用（规格 §3.2 的版本号记法）。
KNOWN_EXCEPTIONS: dict[float, str] = {
    7.06: ("patchdates 的 dates[4]=1471651200（2016-08-20）**错档**：它实际是 6.88c 的公告时刻"
           "（patchdates 的 6.88c=1471564800 / 2016-08-19，6.88d=1472774400 / 2016-09-02），"
           "却记在 7.06 名下。位置映射把它变成 7.06f（比 7.06 自己的 main 1494856800 早 268 天），"
           "使 [1471651200, 1472774400) 这 12 天整段被错标成 7.06f。**该槽位整条丢弃**："
           "由 _drop_backdated_slots 的不倒挂不变式（槽位不得早于前一个序列的 main；7.06 的前一个"
           "序列是 7.05，main=1491750000）拦下，丢弃后该窗口正确归属 6.88c/6.88d。"
           "这是通用不变式，不是为 7.06 写的点修 —— 下次数据刷新再出现错档，同样会被丢弃。"),
    7.22: ("patchdates 的 dates[] 未排序（unsorted）：dates[2]=1558915200 被位置映射成 7.22d，"
           "比 Valve 的 7.22d=1561878000 早 34 天。**不排序**：按给定顺序的位置映射才能复现规格 §3.2 的"
           "「83 槽 / 34 精确 / 48 ±1–2 天 / 1 例外」；排序后 7.22c 与 7.22d 会同时错位（35 / 46 / 2）。"
           "该槽位以 Valve 的日期为准，并由 cross_check_against_valve 记为 outlier。"),
    7.25: ("patchdates 缺 add 字段（no add），而 Valve 有 7.25a=1584514800；其 dates[0]=1585107278 "
           "实际对应 Valve 的 7.25b=1585033200（+0.86 天）。**不移位**：位置映射 dates[0] -> 'b' 已经"
           "给出正确的 b/c；再把 dates[i] 映射到 'c'+i 会凭空造出 Valve 并不存在的 7.25d。"
           "偏差只有 +1 天，±2 天阈值抓不到，故必须显式声明。"),
}


@functools.lru_cache(maxsize=1)
def _fetch_valve_patches_cached() -> list[dict]:
    """Valve 官方补丁清单（免密钥）。返回 `data["patches"]`（118 条，按发布时间升序）。

    **私有**：返回值就是进程级缓存对象本身，只许 `fetch_valve_patches()` 读它。
    """
    data = _http.fetch_json(VALVE_PATCHNOTESLIST_URL, VALVE_CACHE_NAME)
    if not isinstance(data, dict) or "patches" not in data:
        raise RuntimeError(
            "Valve patchnoteslist 响应形状异常（期望 {'patches': [...], 'success': ...}，"
            f"实际 {type(data).__name__}）：{str(data)[:200]}")
    # 规格 §3.2：该 API **出错也返回 HTTP 200**，必须检查 success 字段。
    if data.get("success") is False:
        raise RuntimeError("Valve patchnoteslist 返回 success=false：上游拒绝或参数错误")
    return data["patches"]


def fetch_valve_patches() -> list[dict]:
    """`_fetch_valve_patches_cached()` 的**防御性拷贝**（列表与每行 dict 都是新的）。

    拷贝必须发生在 `lru_cache` **之外**：写在被缓存的函数里等于把拷贝本身也缓存起来，
    调用方一次 `append`/`sort`/改字段照样污染整个进程（返回值全是 int/str，故逐行浅拷贝即可）。
    磁盘缓存仍是一次进程只读一次。
    """
    return [dict(p) for p in _fetch_valve_patches_cached()]


@functools.lru_cache(maxsize=1)
def fetch_patchdates() -> dict[str, dict]:
    """`D2-LRG-Metadata/patchdates.json`：键是 OpenDota 粗粒度 patch id 的字符串，值形如
    `{"code": "7.41", "main": ts, "add"?: ts, "dates": [ts, ...]}`。"""
    try:
        data = _http.fetch_json(PATCHDATES_URL, PATCHDATES_CACHE_NAME)
    except httpx.HTTPError:
        # 只有 raw 不可达（超时/DNS/5xx）才回退；缓存损坏走 _http 的 RuntimeError，不在此吞掉。
        data = _http.fetch_json(PATCHDATES_MIRROR_URL, PATCHDATES_CACHE_NAME)
    if not isinstance(data, dict):
        raise RuntimeError(f"patchdates 响应形状异常（期望对象）：{type(data).__name__}")
    return data


def restore_subpatch_dates(entry: Mapping[str, Any]) -> dict[str, int]:
    """把一条 patchdates 记录展开为 `{版本名: 时间戳}`（**纯函数**，不做 I/O）。

    - `dates[]` 一律从 `'b'` 起，且**保持给定顺序**（7.22 的第 3 项乱序是数据事实，见 KNOWN_EXCEPTIONS）；
    - **不做任何移位**：`add` 缺失时 `dates[0]` 仍是 `'b'`（7.25 实测如此），
      `add` 存在时才额外产出 `'a'`。
    """
    code = entry["code"]
    out: dict[str, int] = {code: int(entry["main"])}
    add = entry.get("add")
    if add:
        out[code + "a"] = int(add)
    for i, ts in enumerate(entry.get("dates") or []):
        if i + 1 >= len(_LETTERS):
            raise ValueError(f"{code} 的 dates 槽位超过 {len(_LETTERS) - 1} 个，字母后缀不够用")
        out[code + _LETTERS[i + 1]] = int(ts)
    return out


def _base_version(name: str) -> str:
    """`'7.41f'` -> `'7.41'`（补丁号里的字母后缀只出现在末尾）。"""
    return name.rstrip(string.ascii_letters)


def _valve_versions() -> dict[str, int]:
    """Valve 清单：`{版本名: 时间戳}`。"""
    return {p["patch_number"]: int(p["patch_timestamp"]) for p in fetch_valve_patches()}


def _utc_day(ts: int) -> datetime.date:
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).date()


def _patchdates_series() -> list[Mapping[str, Any]]:
    """patchdates 的条目按 `main` 升序 —— 序列的**时间顺序**（不是键 "0"…"60" 的顺序）。"""
    return sorted(fetch_patchdates().values(), key=lambda e: int(e["main"]))


def _drop_backdated_slots(entry: Mapping[str, Any], previous_main: int | None
                          ) -> tuple[dict[str, int], dict[str, int]]:
    """把 `restore_subpatch_dates(entry)` 按**时间线不倒挂不变式**切成（保留, 丢弃）。

    不变式：**映射出来的槽位不得早于「前一个序列」的 `main`**；「前一个序列」= `main` 小于本条目、
    且 `main` 最大的那条 patchdates 记录。理由：字母由**位置**决定（见 `restore_subpatch_dates`），
    一个槽位一旦落到前一个序列的地盘上，位置映射必然给出一个错版本名 —— 它比前一个序列的 main
    还早这件事本身就暴露了错档。

    这是**通用不变式，不是针对某条记录的点修**：将来数据刷新若再出现错档槽位，同样会被丢弃，
    而不是悄悄把一段窗口的归属弄反。实测当前快照只丢 1 条：7.06f（见 `KNOWN_EXCEPTIONS[7.06]`）。
    """
    slots = restore_subpatch_dates(entry)
    if previous_main is None:              # 最早的那条序列没有「前一个」
        return slots, {}
    kept = {name: ts for name, ts in slots.items() if ts >= previous_main}
    dropped = {name: ts for name, ts in slots.items() if ts < previous_main}
    return kept, dropped


def _kept_and_dropped_slots() -> tuple[dict[str, dict[str, int]], list[dict]]:
    """一次遍历得到 `({code: 过完不倒挂不变式的槽位}, [被丢弃的槽位])`。"""
    kept_by_code: dict[str, dict[str, int]] = {}
    dropped: list[dict] = []
    previous_main: int | None = None
    for entry in _patchdates_series():
        kept, lost = _drop_backdated_slots(entry, previous_main)
        kept_by_code[entry["code"]] = kept
        dropped += [{"base_version": entry["code"], "version_name": name, "released_at": ts,
                     "previous_main": previous_main} for name, ts in lost.items()]
        previous_main = int(entry["main"])
    return kept_by_code, dropped


def _dropped_backdated_slots() -> list[dict]:
    """被不倒挂不变式丢弃的槽位（实测恰好 1 条：7.06f；`previous_main` 是前一个序列的 main）。"""
    return _kept_and_dropped_slots()[1]


def patchdates_only_versions() -> list[dict]:
    """Valve 未覆盖的序列（6.70–7.07 段）：patchdates 独有的基础版本 + 字母槽（27 + 56 = 83 行）。

    **不写入 `patches` 表**：规格 §3.2「`patches` 表的行集以 Valve 为准（118 版本 / 84 字母版本）」、
    §15③、以及 M1 验收（Task 13 断言字母版本数 == 84）都要求行集来自 Valve，
    而 patchdates 原始有 141 个字母槽。它的用途是让 `subpatch_for_timestamp` 能归属 Valve 无记录的年代。

    相对上游的原始映射有**两处收缩**，都在这里体现：字母槽只算 Valve 未覆盖的序列（141 → 57），
    再被时间线不倒挂不变式丢掉 7.06 的错档槽位 7.06f（57 → 56），行数 84 → 83。
    上游 `patchdates.json` 的 141 槽属性本身不变（规格 §3.2 / README 记的是上游）。

    **归属边界**：这些行**全部**早于 Valve 清单起点（7.08 = 1517472000，2018-02-01），
    故它们不属于 `patches` 行集；`subpatch_for_timestamp` 对早于该时刻的时间戳返回的名字
    **不在** `patches` 里。
    """
    valve_bases = {_base_version(name) for name in _valve_versions()}
    kept_by_code, _ = _kept_and_dropped_slots()
    rows = []
    for code, slots in kept_by_code.items():
        if code in valve_bases:
            continue
        for name, ts in slots.items():
            rows.append({"version_name": name, "base_version": code, "released_at": ts})
    rows.sort(key=lambda r: (r["released_at"], r["version_name"]))
    return rows


def declare_lettered_versions(*, include_patchdates_only: bool = False) -> list[dict]:
    """`patches` 表的行集：Valve 的 118 个版本（含 84 个字母子版本），按时间戳升序。

    每行**恰好**三个键，对应 Task 11（`constants/load.py`）迭代的字段：
    `version_name` / `base_version` / `released_at`（int，Unix 秒，来自 Valve）。

    `include_patchdates_only=True` 时并上 `patchdates_only_versions()`（6.70–7.07 段，83 行）——
    那是**归属用**的并集（201 行 / 140 个字母槽），**不要**写进 `patches` 表（见该函数说明）。
    """
    rows = [{"version_name": name, "base_version": _base_version(name), "released_at": ts}
            for name, ts in _valve_versions().items()]
    if include_patchdates_only:
        rows += patchdates_only_versions()
    rows.sort(key=lambda r: (r["released_at"], r["version_name"]))
    return rows


def _version_timeline() -> list[tuple[int, str]]:
    """归属时间线 `[(时间戳, 版本名)]`（升序）。

    规格 §3.2 入库规则「以 Valve 的时间戳为准；patchdates 的值仅用于填写那些 Valve 列表未覆盖的
    序列」：同一 `base_version` 只要 Valve 覆盖了，就**只用 Valve 的条目**，patchdates 的同名槽位
    一律丢弃（两个来源的 7.41f 相差 11.8 h，混用会让窗口内的比赛错标一个字母）。
    patchdates 独有的序列还要过 `_drop_backdated_slots` 的不倒挂不变式（丢掉 7.06f 这个错档槽位）。
    """
    valve = _valve_versions()
    valve_bases = {_base_version(name) for name in valve}
    timeline = [(ts, name) for name, ts in valve.items()]
    kept_by_code, _ = _kept_and_dropped_slots()
    for code, slots in kept_by_code.items():
        if code in valve_bases:
            continue
        timeline += [(ts, name) for name, ts in slots.items()]
    timeline.sort()
    return timeline


def subpatch_for_timestamp(ts: int) -> str:
    """给定 Unix 时间戳，返回其所属版本名（如 `"7.41e"`）。

    区间语义：**左闭右开** —— 恰好在某版本发布时刻 `t` 的 `ts` 属于该新版本（`t` 之前一秒属于旧版本）。
    数据来源：Valve 优先；Valve 未覆盖的 6.70–7.07 段回落 patchdates（见 `_version_timeline`）。
    早于最早已知版本的时间戳抛 `ValueError`（静默归到某个版本会让年代错得离谱）。

    **归属 ≠ 入库成功**：返回的名字**只有**对 `ts >= 1517472000`（Valve 清单起点 7.08，2018-02-01）
    才保证出现在 `patches` 行集里。更早的时间戳（2016–2017 的 Kaggle 数据全在此列）返回的是
    patchdates 独有的 6.70–7.07 名字，`patches` 里**没有**这些行 —— 调用方（Task 12）必须按这条
    边界决定 `patch_id` 写不写：仅 `start_time < 1517472000` 允许 NULL，之后必须 0 个 NULL。
    """
    timeline = _version_timeline()
    stamps = [t for t, _ in timeline]
    idx = bisect.bisect_right(stamps, int(ts)) - 1
    if idx < 0:
        first_ts, first_name = timeline[0]
        raise ValueError(
            f"时间戳 {ts} 早于最早已知版本 {first_name}（{_utc_day(first_ts)}，"
            f"Unix {first_ts}）：本模块只覆盖 patchdates 有记录的 6.70 起")
    return timeline[idx][1]


def cross_check_against_valve() -> dict:
    """Valve × patchdates 交叉校验（**本函数独占该职责**）。

    口径（三处都是有意选择，不是默认值）：

    - 只比较**字母槽**（Valve 覆盖的序列里、patchdates 映射出的同名字母版本）：规格 §3.2 的
      「83 个可核验字母槽」就是这个口径。基础版本不参与——它的名字来自 `code` 而非字母规则，
      比它并不能证明映射正确；patchdates 独有的 6.70–7.07 段则无从比较。
    - 偏差按**日历天**计：patchdates 是公告时刻，整体系比 Valve 晚约 1 天，小时级差异无意义；
      规格 §3.2 的「34 精确 / 48 ±1–2 天 / 1 例外」正是日历天口径（按秒算会把 7.31c 的 2.6 天
      误报成超差）。
    - `outliers`：|偏差| > 2 天的**全部**槽位，**包含**已声明的例外（7.22d）—— 例外必须可见。
    - `max_deviation_days`：**排除已声明例外序列**（`KNOWN_EXCEPTIONS` 里的 base_version）后的最大
      偏差 —— 排除发生在取 max **之前**：逐个条目先判 `declared`，已声明的整段跳过。故 `<= 2` 的含义
      是「没有**意外**漂移」。例外槽位仍出现在 `outliers` 里，所以这个排除不可能把它藏起来。
      **会抬高该值的只有非例外序列的漂移**；已声明序列内部再怎么漂（如 7.22d 的 −34 天）也只出现在
      `outliers` 里，不会反映到这个值上 —— 原表述「任何新的超差都会同时抬高该值」是错的。
    """
    valve = _valve_versions()
    valve_bases = {_base_version(name) for name in valve}
    n_compared = 0
    max_days = 0
    outliers: list[dict] = []
    for entry in fetch_patchdates().values():
        if entry["code"] not in valve_bases:
            continue
        declared = float(entry["code"]) in KNOWN_EXCEPTIONS
        for name, ts in restore_subpatch_dates(entry).items():
            if name not in valve or not name[-1].isalpha():
                continue
            n_compared += 1
            dev = (_utc_day(ts) - _utc_day(valve[name])).days
            if abs(dev) > MAX_TOLERATED_DEVIATION_DAYS:
                outliers.append({"version_name": name, "deviation_days": dev,
                                 "patchdates": ts, "valve": valve[name],
                                 "declared_exception": declared})
            if declared:
                continue
            max_days = max(max_days, abs(dev))
    outliers.sort(key=lambda o: (-abs(o["deviation_days"]), o["version_name"]))
    return {"n_compared": n_compared, "max_deviation_days": max_days, "outliers": outliers}
