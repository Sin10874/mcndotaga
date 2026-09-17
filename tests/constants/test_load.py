"""`constants/load.py`：把常量写进六张表（规格 §5.1、§15「常量层验收」）。

**`commit=False` 是测试隔离的硬要求，不是顺手加的开关。** `db` fixture（`tests/conftest.py`）
给每个测试一个事务并在结束时 `rollback()`；而 `load_constants` 作为生产用的一次性加载器
必须在末尾 `commit()`。两者直接相遇时，测试里的加载会把真实常量**提交**进共享的
`dota_test`，于是 pytest 按默认顺序（`tests/constants/` 先于 `tests/db/`）跑到
`seeded` fixture 时就在 `constants_snapshot(snapshot_version=1)` / `heroes(80)` 上主键冲突，
`tests/db/test_constraints.py` 里插 hero 81 的那条也会冲突 —— 16+ 个失败全部是跨套件污染，
不是约束测试本身有问题。故本文件**每一处**都写 `load_constants(db, commit=False)`。
"""
from __future__ import annotations


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


def test_app_config_kv_has_the_four_required_keys(db):
    """规格 §5.1：M1 必须种入这四个键，否则 §6.6/§7/§9.1 的相关功能不可用。"""
    from constants.load import load_constants
    load_constants(db, commit=False)
    keys = {r[0] for r in db.execute("SELECT key FROM app_config_kv")}
    assert {"archetype_role_map", "robustness_lambda",
            "op_decision_min_delta", "min_sample_n"} <= keys


def test_archetype_role_map_matches_the_python_rules(db):
    """规格 §7：映射存于 app_config_kv，可调而不改代码——故必须与代码一致。"""
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
    """
    from constants.load import load_constants
    load_constants(db, commit=False)
    patches_before = db.execute("""SELECT version_name, base_version, released_at, opendota_patch
                                   FROM patches ORDER BY version_name""").fetchall()
    kv_before = db.execute("SELECT key, value FROM app_config_kv ORDER BY key").fetchall()

    load_constants(db, commit=False)

    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 501
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM constants_snapshot").fetchone()[0] == 1

    patches_after = db.execute("""SELECT version_name, base_version, released_at, opendota_patch
                                  FROM patches ORDER BY version_name""").fetchall()
    assert len(patches_after) == 118
    assert patches_after == patches_before
    assert db.execute("SELECT key, value FROM app_config_kv ORDER BY key").fetchall() == kv_before
