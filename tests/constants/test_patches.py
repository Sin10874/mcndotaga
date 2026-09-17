"""`constants/patches.py`：Valve 权威清单 + 子版本还原（规格 §3.2、§15「版本归属测试」）。

规格 §15 要求的三类用例，缺一不可：
① 边界 —— 7.41e / 7.41f 两侧落到不同子版本；
② 字母映射 —— `add` 存在（7.41）与 `add` 缺失（7.32）两个方向都断言 `dates[0] -> 'b'`；
③ 交叉校验 —— `patches` 的行集来自 Valve（118 版本 / 84 字母版本），不是 patchdates（141 槽）。

三处控制器裁决（2026-09-17 对两个上游实测后裁定，见计划 Task 10 的更正注记）固化在本文件：
R1 **Valve 时间戳优先**：patchdates 的 7.41f 比 Valve 晚 11.8 h，边界取 Valve 的 1789455600；
R2 **7.25 不移位**：位置映射给出的 b/c 已经正确，移位会造出 Valve 并不存在的 7.25d；
R3 **7.22 不排序**：位置映射复现规格的 34 精确 / 48 ±1–2 天 / 1 例外；排序会变成 35 / 46 / 2。
"""
import datetime
import re

import pytest

import constants.patches as patches
from constants.patches import (fetch_valve_patches, fetch_patchdates,
                               restore_subpatch_dates, subpatch_for_timestamp)

UTC = datetime.timezone.utc


def _day(ts: int) -> datetime.date:
    return datetime.datetime.fromtimestamp(int(ts), UTC).date()


def _sorted_slots(entry: dict) -> dict:
    """计划里被否决的「先排序再分配字母」读法 —— 只用于反证（R3）。"""
    out = {}
    if entry.get("add"):
        out[entry["code"] + "a"] = entry["add"]
    for i, ts in enumerate(sorted(entry.get("dates") or [])):
        out[entry["code"] + chr(ord("b") + i)] = ts
    return out


def _deviation_histogram(*, sorted_dates: bool) -> dict:
    """把 patchdates 映射成字母版本后与 Valve 比**日历天**偏差。

    sorted_dates=False 走生产实现 `restore_subpatch_dates`（位置映射）。
    - 只统计**字母槽**（规格 §3.2 的 83 个可核验字母槽；基础版本不在校准口径内）；
    - 只有两侧都有的槽位才可核验（patchdates 独有的 6.70–7.07 段无从比较）。
    """
    valve = {p["patch_number"]: p["patch_timestamp"] for p in fetch_valve_patches()}
    valve_bases = {re.sub(r"[a-z]+$", "", name) for name in valve}
    devs = []
    for entry in fetch_patchdates().values():
        if entry["code"] not in valve_bases:
            continue
        slots = _sorted_slots(entry) if sorted_dates else restore_subpatch_dates(entry)
        for name, ts in slots.items():
            if name in valve and name[-1].isalpha():
                devs.append((name, (_day(ts) - _day(valve[name])).days))
    return {
        "n": len(devs),
        "exact": sum(1 for _, d in devs if d == 0),
        "within_1_2": sum(1 for _, d in devs if abs(d) in (1, 2)),
        "outliers": sorted([(n, d) for n, d in devs if abs(d) > 2]),
    }


def test_valve_list_has_118_versions_and_84_lettered():
    """用例③：权威清单来自 Valve，不是 patchdates（后者有 141 个槽位）。"""
    ps = fetch_valve_patches()
    assert len(ps) == 118
    assert sum(1 for p in ps if any(c.isalpha() for c in p["patch_number"])) == 84


def test_patchdates_has_141_letter_slots():
    pd = fetch_patchdates()
    assert sum(len(v.get("dates") or []) + (1 if v.get("add") else 0)
               for v in pd.values()) == 141


def test_dates_start_at_b_not_a():
    """用例②-a：add 存在时，dates[0] 映射到 'b'。"""
    m = restore_subpatch_dates({"code": "7.41", "main": 1, "add": 2, "dates": [3, 4, 5]})
    assert set(m) == {"7.41", "7.41a", "7.41b", "7.41c", "7.41d"}
    assert m["7.41a"] == 2 and m["7.41b"] == 3


def test_dates_start_at_b_even_without_add():
    """用例②-b：add 缺失时 dates[0] 仍是 'b' —— 错位 bug 最容易漏的分支。"""
    m = restore_subpatch_dates({"code": "7.32", "main": 1, "dates": [3, 4, 5]})
    assert "7.32a" not in m
    assert m["7.32b"] == 3 and m["7.32d"] == 5


def test_boundary_around_7_41e_and_f():
    """用例①：边界两侧必须落到不同子版本（规格 §15 版本归属测试 class ①）。

    参考比赛 1789301470 = 2026-09-13 12:11 UTC（实测版本 7.41e）；7.41f 取 **Valve** 的发布时刻
    1789455600 = 2026-09-15 07:00 UTC —— 不是 patchdates 的 1789498134（见下一个测试的 R1 说明）。
    """
    assert subpatch_for_timestamp(1789301470) == "7.41e"
    assert subpatch_for_timestamp(1789455600) == "7.41f"


def test_boundary_is_exclusive_on_the_left():
    """边界左侧（发布前一秒）仍属旧版本，右侧同一秒即新版本 —— 用的是 **Valve** 的边界。

    R1（2026-09-17 裁决）：Valve 的 7.41f = 1789455600（2026-09-15 07:00），patchdates 的
    7.41f = 1789498134（同日 18:48，晚 42534 s ≈ 11.8 h）。规格 §3.2 入库规则「以 Valve 的时间戳
    为准」，故 1789455599 属 7.41e、1789455600 属 7.41f。**受影响的窗口是 [1789455600, 1789498134)
    这 11.8 h**：落在其中的比赛若改用 patchdates 的时间戳会被错标成 7.41e。
    （计划原断言 `subpatch_for_timestamp(1789498133) == "7.41e"` 与 §3.2 的入库规则矛盾，已按 R1 重写。）
    """
    assert subpatch_for_timestamp(1789455599) == "7.41e"
    assert subpatch_for_timestamp(1789455600) == "7.41f"


def test_valve_timestamp_wins_inside_the_11_8h_window():
    """R1 的实测依据：两个来源的 7.41f 相差 42534 s，窗口内必须已归 7.41f。"""
    entry = next(v for v in fetch_patchdates().values() if v["code"] == "7.41")
    assert entry["dates"][-1] == 1789498134          # patchdates 的 7.41f
    valve = {p["patch_number"]: p["patch_timestamp"] for p in fetch_valve_patches()}
    assert valve["7.41f"] == 1789455600              # Valve 的 7.41f
    assert valve["7.41f"] - entry["dates"][-1] == -42534   # ≈ -11.8 h
    assert subpatch_for_timestamp(1789498133) == "7.41f"   # patchdates 眼里「还差 1 秒发布」
    assert subpatch_for_timestamp(1789498134) == "7.41f"


def test_known_exceptions_are_declared():
    """规格 §3.2：7.22 与 7.25 是已知例外，必须显式声明而非静默错标。"""
    from constants.patches import KNOWN_EXCEPTIONS
    assert 7.22 in KNOWN_EXCEPTIONS and 7.25 in KNOWN_EXCEPTIONS
    assert "unsorted" in KNOWN_EXCEPTIONS[7.22]
    assert "no add" in KNOWN_EXCEPTIONS[7.25]


def test_7_25_is_not_shifted():
    """R2：7.25 的 dates[] **不后移** —— 位置映射已经给出正确的 b/c。

    实测 patchdates key "44"（code 7.25）：main=1584403200、**无 add**、dates=[1585107278, 1586230920]；
    Valve：7.25=1584428400、7.25a=1584514800、7.25b=1585033200、7.25c=1586156400。
    dates[0] 与 Valve 的 7.25b 差 +0.86 天，dates[1] 与 7.25c 差 +0.86 天（±2 天阈值抓不到），
    7.25a 由 Valve 提供。若按计划旧规则把 dates[i] 映射到 'c'+i，会造出 Valve 并不存在的 7.25d。
    """
    m = restore_subpatch_dates({"code": "7.25", "main": 1584403200,
                                "dates": [1585107278, 1586230920]})
    assert set(m) == {"7.25", "7.25b", "7.25c"}
    assert "7.25a" not in m and "7.25d" not in m
    assert m["7.25b"] == 1585107278 and m["7.25c"] == 1586230920
    valve = {p["patch_number"]: p["patch_timestamp"] for p in fetch_valve_patches()}
    assert valve["7.25a"] == 1584514800 and "7.25d" not in valve
    assert (_day(m["7.25b"]) - _day(valve["7.25b"])).days == 1
    assert (_day(m["7.25c"]) - _day(valve["7.25c"])).days == 1


def test_7_22_keeps_the_given_order_and_the_anomaly_is_reported():
    """R3：7.22 的 dates[] **不排序** —— 位置映射复现规格的校准，排序会多出一个错位槽。

    实测 patchdates key "41"（code 7.22）的 dates 第三项（1558915200）比第二项早 14 天，是数据事实。
    位置映射把它落为 7.22d，与 Valve 的 7.22d(1561878000) 相差 **-34 天** —— 正是规格 §3.2 里
    「仅 1 个异常（7.22d 差 34 天）」。排序会同时弄错 7.22c 与 7.22d（见下个测试）。
    """
    m = restore_subpatch_dates({"code": "7.22", "main": 1558656000,
                                "dates": [1559009567, 1560133416, 1558915200, 1563170519,
                                          1564362927, 1567790965, 1569806921]})
    assert m["7.22c"] == 1560133416      # dates[1] 原样落位
    assert m["7.22d"] == 1558915200      # dates[2] 原样落位，没有被排序换走
    assert (_day(m["7.22d"]) - _day(1561878000)).days == -34   # Valve 的 7.22d


def test_positional_rule_reproduces_the_spec_calibration():
    """R3 正证：位置映射复现规格 §3.2 的「83 槽 / 34 精确 / 48 ±1–2 天 / 1 例外」。"""
    hist = _deviation_histogram(sorted_dates=False)
    assert hist["n"] == 83
    assert (hist["exact"], hist["within_1_2"], len(hist["outliers"])) == (34, 48, 1)
    assert hist["outliers"] == [("7.22d", -34)]


def test_sorting_7_22_contradicts_the_spec_calibration():
    """R3 反证：排序后再分配字母得到 35 / 46 / 2（7.22c 与 7.22d 同时错位），与规格对不上。"""
    hist = _deviation_histogram(sorted_dates=True)
    assert (hist["exact"], hist["within_1_2"], len(hist["outliers"])) == (35, 46, 2)
    assert hist["outliers"] == [("7.22c", -12), ("7.22d", -20)]


def test_letter_mapping_agrees_with_valve_on_sampled_series():
    """用例③：交叉校验 —— 没有**意外**漂移（> ±2 天）；已声明例外单独可见（R4）。"""
    report = patches.cross_check_against_valve()
    assert report["n_compared"] >= 80
    assert report["max_deviation_days"] <= 2, report
    outlier = next(o for o in report["outliers"] if o["version_name"] == "7.22d")
    assert outlier["deviation_days"] == -34 and outlier["declared_exception"] is True
    assert report["max_deviation_days"] == 2, report   # 非例外槽位的最大值：7.31c 恰好 2 天


def test_cross_check_reports_new_drift_beyond_the_exception(monkeypatch):
    """R4：排除已声明例外**不会**掩盖新的漂移 —— 把非例外的 7.31 序列挪 3 天，max 必须破 2。"""
    tampered = {}
    for key, entry in fetch_patchdates().items():
        entry = dict(entry)
        if entry["code"] == "7.31":
            entry["dates"] = [ts + 3 * 86400 for ts in entry["dates"]]
        tampered[key] = entry
    monkeypatch.setattr(patches, "fetch_patchdates", lambda: tampered)
    report = patches.cross_check_against_valve()
    assert report["max_deviation_days"] > 2, report
    assert any(o["version_name"].startswith("7.31") for o in report["outliers"]), report


def test_declare_lettered_versions_matches_the_patches_row_set():
    """R5：Task 11 逐字 import 的 `declare_lettered_versions` 必须存在且形状可用。

    返回值就是 `patches` 表的行集：Valve 的 118 行、其中 84 个字母子版本 —— 规格 §3.2
    「`patches` 表的行集以此为准」、§15③、M1 验收（Task 13 断言字母版本数 == 84）。
    键必须**恰好**是 Task 11 迭代的三个（`version_name` / `base_version` / `released_at`）。
    """
    rows = patches.declare_lettered_versions()
    assert rows, "declare_lettered_versions() 不能为空"
    assert len(rows) == 118
    assert {frozenset(r) for r in rows} == {
        frozenset({"version_name", "base_version", "released_at"})}
    assert len({r["version_name"] for r in rows}) == 118
    assert sum(1 for r in rows if r["version_name"][-1].isalpha()) == 84
    assert all(isinstance(r["released_at"], int) and r["released_at"] > 0 for r in rows)
    assert all(r["version_name"].startswith(r["base_version"]) for r in rows)
    valve = {p["patch_number"]: p["patch_timestamp"] for p in fetch_valve_patches()}
    assert {r["version_name"]: r["released_at"] for r in rows} == valve   # 时间戳来自 Valve


def test_patchdates_only_slots_are_available_but_stay_out_of_the_row_set():
    """R5 的另一半：patchdates 补出的槽位（Valve 未覆盖的 6.70–7.07 段）必须可达且受测。

    它们**不能进 `patches` 表** —— 否则 M1 的「118 行 / 84 个字母子版本」两个验收数字都会破；
    但它们必须参与归属（否则 6.70–7.07 的比赛无法定位子版本），故以
    `include_patchdates_only=True` 的并集形式暴露：27 个基础版本 + 57 个字母槽。
    """
    extra = patches.patchdates_only_versions()
    assert len(extra) == 84
    assert sum(1 for r in extra if r["version_name"][-1].isalpha()) == 57
    bases = {r["base_version"] for r in extra}
    assert "6.86" in bases and "7.07" in bases      # Valve 的清单从 7.08 起（规格 §16.3）
    assert "7.08" not in bases
    assert {r["version_name"] for r in extra} >= {"6.86", "6.86b", "7.07d"}

    union = patches.declare_lettered_versions(include_patchdates_only=True)
    assert len(union) == 118 + 84
    assert sum(1 for r in union if r["version_name"][-1].isalpha()) == 84 + 57
    # 6.70–7.07 段也要能归属：Valve 无记录的年代由 patchdates 兜底
    assert subpatch_for_timestamp(1450224000) == "6.86"
    assert subpatch_for_timestamp(1509462000) == "7.07"


def test_subpatch_for_timestamp_refuses_timestamps_before_the_earliest_version():
    """早于最早已知版本（6.70，2010-12）的时间戳必须响亮报错，而不是静默落到某个版本。"""
    with pytest.raises(ValueError, match=r"6\.70"):
        subpatch_for_timestamp(0)
