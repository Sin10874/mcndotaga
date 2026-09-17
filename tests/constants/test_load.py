"""`constants/load.py`：把常量写进六张表（规格 §5.1、§15「常量层验收」）。

**`commit=False` 是测试隔离的硬要求，不是顺手加的开关。** `db` fixture（`tests/conftest.py`）
给每个测试一个事务并在结束时 `rollback()`；而 `load_constants` 作为生产用的一次性加载器
必须在末尾 `commit()`。两者直接相遇时，测试里的加载会把真实常量**提交**进共享的
`dota_test`，于是 pytest 按默认顺序（`tests/constants/` 先于 `tests/db/`）跑到
`seeded` fixture 时就在 `constants_snapshot(snapshot_version=1)` / `heroes(80)` 上主键冲突，
`tests/db/test_constraints.py` 里插 hero 81 的那条也会冲突 —— 18 个 error 全部是跨套件污染，
不是约束测试本身有问题。故本文件**每一处**都写 `load_constants(db, commit=False)`。
"""
from __future__ import annotations

import pytest


def test_load_constants_populates_all_tables(db):
    from constants.load import load_constants
    load_constants(db, commit=False)

    assert db.execute("SELECT count(*) FROM constants_snapshot").fetchone()[0] == 1
    snap = db.execute("SELECT snapshot_version, n_heroes, n_items FROM constants_snapshot").fetchone()
    assert snap[1] == 127 and snap[2] == 501

    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 501
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM patches").fetchone()[0] == 118
    assert db.execute("SELECT count(*) FROM patches WHERE version_name ~ '[a-z]$'").fetchone()[0] == 84

    # opendota_patch 必须**真的有值**：这一列在 R8 之前会被静默写成 NULL（列本身可空，
    # 没有任何约束拦得住），而 §3.2 的版本归属要用它对齐 OpenDota 的粗粒度 id。
    # 只在 Python 层断言 `declare_lettered_versions()` 的键存在不够 —— 入库路径丢键、
    # `ON CONFLICT` 只更新 released_at、`VALUES` 少一列，都能让行数/字母数全绿而此列为空。
    assert db.execute("SELECT count(*) FROM patches WHERE opendota_patch IS NULL").fetchone()[0] == 0
    spot = dict(db.execute("""SELECT version_name, opendota_patch FROM patches
                              WHERE version_name IN ('7.41f', '7.22', '7.08')""").fetchall())
    assert spot == {"7.41f": 60, "7.22": 41, "7.08": 27}


def test_token_index_satisfies_the_derivation_rule(db):
    """规格 §5.1：dense_index == row_number() OVER (ORDER BY hero_id) - 1。"""
    from constants.load import load_constants
    load_constants(db, commit=False)
    # 先钉住行数：空表时下面的 `bad == 0` 会**空过**（0 行里当然没有违反者）。
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    bad = db.execute("""
        SELECT count(*) FROM (
          SELECT dense_index, row_number() OVER (ORDER BY hero_id) - 1 AS expected
          FROM hero_token_index) t
        WHERE dense_index <> expected""").fetchone()[0]
    assert bad == 0


def test_hero_135_dense_index_is_121(db):
    from constants.load import load_constants
    load_constants(db, commit=False)
    got = db.execute("SELECT dense_index FROM hero_token_index WHERE hero_id = 135").fetchone()[0]
    assert got == 121


def test_load_refuses_to_silently_remap_tokens(db, monkeypatch):
    """规格 §5.1:214–220：上游英雄集合变了必须报错，禁止静默重映射 token。

    场景：先按当前上游加载一次，再模拟**上游用 1..155 里的空位补了一个新英雄**。这会把它
    之后所有 hero_id 的 dense_index 整体位移，而 `hero_token_index` 的 `ON CONFLICT DO
    NOTHING` 会原样保留旧映射、`heroes` / `n_heroes` 却已经变了 —— 没有守护时加载器照样
    返回成功，模型 token 与英雄的对应关系就悄悄错了（§5.1 要求 3 的那条 N vs N+1 检查由
    Plan 2 拥有，只覆盖快照 1 里**已存在**的 hero_id，故覆盖不到这个新英雄）。

    id 用 **24**：它是实测 1..126 里的真实空位（实测空位 = 24 / 115–118 / 122 / 124 / 125），
    故追加后派生集合是 128 行、且其后每个英雄的 dense_index 都位移 1。
    """
    import constants.load as load_mod
    load_mod.load_constants(db, commit=False)

    heroes = [*load_mod.fetch_heroes(),
              {"id": 24, "name": "npc_dota_hero_synthetic_gap",
               "localized_name": "Synthetic Gap", "primary_attr": "agi",
               "roles": ["Carry"], "cm_enabled": True}]
    monkeypatch.setattr(load_mod, "fetch_heroes", lambda: heroes)

    with pytest.raises(RuntimeError, match=f"快照 {load_mod.SNAPSHOT_VERSION}"):
        load_mod.load_constants(db, commit=False)

    # 守护在**任何 INSERT 之前**，故这次加载一行都没写：heroes 仍是 127，快照里的 n_heroes
    # 也不能变成 128（后者能抓住"把守护挪到快照 upsert 之后"这种半吊子实现）。
    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT n_heroes FROM constants_snapshot WHERE snapshot_version = %s",
                      (load_mod.SNAPSHOT_VERSION,)).fetchone()[0] == 127


def test_timeline_after_the_first_loaded_patch_resolves_to_loaded_versions(db):
    """Task 12 归属契约：时间线里 >= 最早入库 `released_at` 的版本名必须**全部**已入库。

    Task 12 的规则「2018 分界之后 `matches.patch_id` 0 个 NULL」靠的就是这条闭合性：
    `subpatch_for_timestamp(ts)` 只保证返回一个名字，名字能否对上 `patches` 行要靠这里断言。
    若 loader 少写一行、或时间线多出一个 patchdates 独有的槽位（它们**全部**早于该分界），
    Task 12 就会在写 `patch_id` 时炸 —— 或更糟：静默写 NULL。
    """
    from constants.load import load_constants
    from constants.patches import _version_timeline
    load_constants(db, commit=False)

    min_released_at = int(db.execute(
        "SELECT extract(epoch FROM min(released_at)) FROM patches").fetchone()[0])
    assert min_released_at == 1517472000          # 7.08 = 2018-02-01，Task 12 的分界
    loaded = {r[0] for r in db.execute("SELECT version_name FROM patches")}
    reachable = {name for ts, name in _version_timeline() if ts >= min_released_at}
    assert len(reachable) == 118                  # 非空性：空集会让下面的差集断言空过
    assert reachable - loaded == set()


def test_app_config_kv_has_the_four_required_keys(db):
    """规格 §5.1：M1 必须种入这四个键，否则 §6.6/§7/§9.1 的相关功能不可用。

    三个数值必须按**值**断言，不能只查键存在：把 `'0.02'::jsonb` 写成 `'0.2'::jsonb`
    会让键集断言全绿，而 §9.1 的最小差异阈值悄悄变成 10 倍。JSONB 经 psycopg 3 解码后
    已是 Python 数字（`1.0` / `0.02` / `30`），直接比较即可。
    """
    from constants.load import load_constants
    load_constants(db, commit=False)
    rows = dict(db.execute("SELECT key, value FROM app_config_kv").fetchall())
    assert {"archetype_role_map", "robustness_lambda",
            "op_decision_min_delta", "min_sample_n"} <= set(rows)
    assert rows["robustness_lambda"] == 1.0
    assert rows["op_decision_min_delta"] == 0.02
    assert rows["min_sample_n"] == 30


def test_archetype_role_map_names_agree_with_the_rules(db):
    """值只是**代码出处指针**：这里能断言的只有"六个原型名与 RULES 一致"。

    规格 §7:1053 说该映射「存于 `app_config_kv`，可调而不改代码」——**这一点没有实现**：
    RULES 的规则带取反（`"Pusher" not in r`）与嵌套 OR，扁平 KV 表表达不了。库里存的是
    `{原型名: "python:constants.archetypes.RULES"}`，记录"§7 维度 6 由哪段代码算"。
    规则本身的一致性由 `tests/constants/test_archetypes.py` 的完备性/分布测试守护。
    """
    import json
    from constants.load import load_constants
    from constants.archetypes import RULES
    load_constants(db, commit=False)
    raw = db.execute("SELECT value FROM app_config_kv WHERE key='archetype_role_map'").fetchone()[0]
    # 计划原文写的是 `json.loads(raw)` —— 对 TEXT 列成立，但 `value` 是 JSONB，
    # psycopg 3 默认已把它解码成 Python 对象，再 loads 会抛
    # `TypeError: the JSON object must be str, bytes or bytearray, not dict`。
    # 两种可能都接住，断言只关心"存进去的键集与代码一致"。
    stored = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    assert set(stored) == {name for name, _ in RULES}


def test_load_is_idempotent(db):
    """二次加载既不新增行也不改值 —— 六张表都要过这一关。

    `patches` 单独断言：它的 `ON CONFLICT` 必须覆盖 `base_version` / `opendota_patch`
    （只更新 `released_at` 时，第一版写进去的 NULL 永远回填不了，见 R8），
    故这里比对整行而不是只比行数。

    `app_config_kv` 的 `note` 也必须参与 upsert：只写 `value = EXCLUDED.value` 时，
    修正后的说明永远传播不出去。故先把 note 改脏、再加载一次，断言复原 —— 这是
    `note = EXCLUDED.note` 的直接守护。
    """
    from constants.load import load_constants
    load_constants(db, commit=False)
    patches_before = db.execute("""SELECT version_name, base_version, released_at, opendota_patch
                                   FROM patches ORDER BY version_name""").fetchall()
    kv_before = db.execute("SELECT key, value, note FROM app_config_kv ORDER BY key").fetchall()

    load_constants(db, commit=False)

    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 501
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM constants_snapshot").fetchone()[0] == 1

    patches_after = db.execute("""SELECT version_name, base_version, released_at, opendota_patch
                                  FROM patches ORDER BY version_name""").fetchall()
    assert len(patches_after) == 118
    assert patches_after == patches_before
    assert db.execute("SELECT key, value, note FROM app_config_kv ORDER BY key").fetchall() == kv_before

    db.execute("UPDATE app_config_kv SET note = 'stale'")
    load_constants(db, commit=False)
    assert db.execute("SELECT key, value, note FROM app_config_kv ORDER BY key").fetchall() == kv_before
