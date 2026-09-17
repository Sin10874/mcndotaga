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

两处已知例外（`KNOWN_EXCEPTIONS`）：**7.22 不排序、7.25 不移位** —— 详见该常量的文本。

性能：两个上游响应用 `lru_cache` **按进程缓存**（一次进程只读一次磁盘；`REFRESH_NETWORK=1` 时也只刷新
一次——否则每条测试都会重拉，patchdates 的 raw 优先策略会连续 timeout）；`_version_timeline()` 每次重建。
两个 fetch 返回的是缓存里的**同一个对象**，调用方不得原地修改。
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

# 两个已知例外必须显式声明 —— 它们的偏差都在 ±2 天阈值**之内**，靠偏差检测抓不到。
# 键用 float 是为了让 `7.22 in KNOWN_EXCEPTIONS` 这种写法直接可用（规格 §3.2 的版本号记法）。
KNOWN_EXCEPTIONS: dict[float, str] = {
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
def fetch_valve_patches() -> list[dict]:
    """Valve 官方补丁清单（免密钥）。返回 `data["patches"]`（118 条，按发布时间升序）。"""
    data = _http.fetch_json(VALVE_PATCHNOTESLIST_URL, VALVE_CACHE_NAME)
    if not isinstance(data, dict) or "patches" not in data:
        raise RuntimeError(
            "Valve patchnoteslist 响应形状异常（期望 {'patches': [...], 'success': ...}，"
            f"实际 {type(data).__name__}）：{str(data)[:200]}")
    # 规格 §3.2：该 API **出错也返回 HTTP 200**，必须检查 success 字段。
    if data.get("success") is False:
        raise RuntimeError("Valve patchnoteslist 返回 success=false：上游拒绝或参数错误")
    return data["patches"]


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


def patchdates_only_versions() -> list[dict]:
    """Valve 未覆盖的序列（6.70–7.07 段）：patchdates 独有的基础版本 + 字母槽（27 + 57 = 84 行）。

    **不写入 `patches` 表**：规格 §3.2「`patches` 表的行集以 Valve 为准（118 版本 / 84 字母版本）」、
    §15③、以及 M1 验收（Task 13 断言字母版本数 == 84）都要求行集来自 Valve，
    而 patchdates 有 141 个字母槽。它的用途是让 `subpatch_for_timestamp` 能归属 Valve 无记录的年代。
    """
    valve_bases = {_base_version(name) for name in _valve_versions()}
    rows = []
    for entry in fetch_patchdates().values():
        if entry["code"] in valve_bases:
            continue
        for name, ts in restore_subpatch_dates(entry).items():
            rows.append({"version_name": name, "base_version": entry["code"], "released_at": ts})
    rows.sort(key=lambda r: (r["released_at"], r["version_name"]))
    return rows


def declare_lettered_versions(*, include_patchdates_only: bool = False) -> list[dict]:
    """`patches` 表的行集：Valve 的 118 个版本（含 84 个字母子版本），按时间戳升序。

    每行**恰好**三个键，对应 Task 11（`constants/load.py`）迭代的字段：
    `version_name` / `base_version` / `released_at`（int，Unix 秒，来自 Valve）。

    `include_patchdates_only=True` 时并上 `patchdates_only_versions()`（6.70–7.07 段，84 行）——
    那是**归属用**的并集（202 行 / 141 个字母槽），**不要**写进 `patches` 表（见该函数说明）。
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
    """
    valve = _valve_versions()
    valve_bases = {_base_version(name) for name in valve}
    timeline = [(ts, name) for name, ts in valve.items()]
    for entry in fetch_patchdates().values():
        if entry["code"] in valve_bases:
            continue
        timeline += [(ts, name) for name, ts in restore_subpatch_dates(entry).items()]
    timeline.sort()
    return timeline


def subpatch_for_timestamp(ts: int) -> str:
    """给定 Unix 时间戳，返回其所属版本名（如 `"7.41e"`）。

    区间语义：**左闭右开** —— 恰好在某版本发布时刻 `t` 的 `ts` 属于该新版本（`t` 之前一秒属于旧版本）。
    数据来源：Valve 优先；Valve 未覆盖的 6.70–7.07 段回落 patchdates（见 `_version_timeline`）。
    早于最早已知版本的时间戳抛 `ValueError`（静默归到某个版本会让年代错得离谱）。
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
      偏差，故 `<= 2` 的含义是「没有**意外**漂移」。例外槽位仍出现在 `outliers` 里，
      所以这个排除不可能把它藏起来；任何**新**的超差都会同时抬高该值（见测试的变异守护）。
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
