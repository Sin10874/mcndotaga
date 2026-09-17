"""把常量写进数据库。幂等：全部使用 ON CONFLICT DO UPDATE/NOTHING。

必须在 Kaggle 引导数据入库之前跑——draft_actions.hero_id 是
heroes(hero_id) 的外键，matches.patch_id 是 patches(patch_id) 的外键。

`patches` 的行集以 **Valve 的 `patchnoteslist`** 为准（118 版本 / 84 字母版本，规格 §3.2、
§15③）：`declare_lettered_versions()` 已经按这条口径裁好，`patchdates` 独有的 6.70–7.07 段
（`patchdates_only_versions()`，83 行）**不写入本表** —— 它只供 `subpatch_for_timestamp`
归属 Valve 无记录的年代。`opendota_patch` 逐行取自 patchdates 的键，字母行继承其基础版本的
id；写入时直接取 `p["opendota_patch"]` 而不是 `p.get(...)`：键缺失要在 Task 10 的接口测试里
炸，而不是在这里静默写 NULL（该列可空，没有任何约束拦得住）。
"""
from __future__ import annotations
import json
import psycopg
from .dotaconstants import fetch_heroes, fetch_items, derive_token_index
from .archetypes import RULES
from .patches import declare_lettered_versions

SNAPSHOT_VERSION = 1

def load_constants(conn: psycopg.Connection, *, commit: bool = True) -> None:
    """把六张常量表填满（可重复执行，见模块 docstring）。

    `commit` 是**关键字参数且默认 True**：生产上这是一次性加载器，调用方拿到的是一个
    已提交、立刻对其它会话可见的常量层，所以默认必须提交，不能靠调用方记得再 commit。

    `commit=False` 是给测试的事务隔离用的：`tests/conftest.py` 的 `db` fixture 给每个测试
    一个事务并在结束时 `rollback()`。若加载器无条件 `commit()`，测试里的加载会把真实常量
    提交进共享的 `dota_test` 库，污染同一次 pytest 会话中后面所有测试 —— 实测后果是
    pytest 默认顺序（`tests/constants/` 先于 `tests/db/`）下 `seeded` fixture 在
    `constants_snapshot(snapshot_version=1)` 与 `heroes(80)` 上主键冲突、
    `tests/db/test_constraints.py::test_hero_token_index_rejects_duplicate_dense_index`
    插 hero 81 时冲突，18 个 error 全部是跨套件污染而非约束本身有问题。
    传 `commit=False` 时加载落在这个事务里，随 fixture 的 `rollback()` 一起消失。
    """
    heroes, items = fetch_heroes(), fetch_items()
    token_index = derive_token_index(heroes)

    # 「冻结快照守护」必须在**任何 INSERT 之前**（规格 §5.1:214–220）。hero_token_index 是
    # 写死在 SNAPSHOT_VERSION 上的映射，下面那条 INSERT 是 `ON CONFLICT DO NOTHING`：
    # 上游一旦增删英雄（尤其是用 1..155 里的空位补人），其后所有 hero_id 的 dense_index 会
    # 整体位移，`heroes` 与 `constants_snapshot.n_heroes` 跟着变，而 hero_token_index 仍是
    # 旧映射 —— 本函数却返回成功，这正是规格禁止的「静默重映射」（模型 token 与英雄的对应
    # 关系悄悄错了，且没有任何报错）。§5.1 要求 3 的「快照 N vs N+1 逐 hero_id 比对」由
    # Plan 2 拥有，且只覆盖**快照 N 里已存在的 hero_id**：快照 N 里本来没有的新英雄在它面前
    # 是空集，检查空过。故真正的哨兵只能放在这里：读一次现有行，与本次派生结果逐 hero_id
    # 比对，不一致就停下，要求**显式提升 SNAPSHOT_VERSION**（重派生 dense_index = 重训模型）。
    # `existing` 为空 = 首次加载，放行。（SNAPSHOT_VERSION 是模块常量，不得从库里反推。）
    existing = dict(conn.execute(
        "SELECT hero_id, dense_index FROM hero_token_index WHERE snapshot_version = %s",
        (SNAPSHOT_VERSION,)).fetchall())
    if existing and existing != token_index:
        raise RuntimeError(
            f"快照 {SNAPSHOT_VERSION} 的 hero_token_index 与本次派生不一致"
            f"（库中 {len(existing)} 行 / 派生 {len(token_index)} 行）：上游英雄集合变了。"
            f"按规格 §5.1：必须显式提升 SNAPSHOT_VERSION 并重派生 dense_index（重训模型），"
            f"禁止静默重映射。")

    conn.execute("""INSERT INTO constants_snapshot
                    (snapshot_version, n_heroes, n_items) VALUES (%s, %s, %s)
                    ON CONFLICT (snapshot_version) DO UPDATE
                    SET n_heroes = EXCLUDED.n_heroes, n_items = EXCLUDED.n_items""",
                 (SNAPSHOT_VERSION, len(heroes), len(items)))

    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO heroes(hero_id, name, localized_name, primary_attr, roles, cm_enabled)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (hero_id) DO UPDATE
                           SET name = EXCLUDED.name, localized_name = EXCLUDED.localized_name,
                               primary_attr = EXCLUDED.primary_attr, roles = EXCLUDED.roles,
                               cm_enabled = EXCLUDED.cm_enabled""",
                        [(h["id"], h["name"], h["localized_name"], h.get("primary_attr"),
                          h.get("roles"), h.get("cm_enabled")) for h in heroes])
        cur.executemany("""INSERT INTO items(item_id, name, dname, cost) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (item_id) DO UPDATE
                           SET name = EXCLUDED.name, dname = EXCLUDED.dname, cost = EXCLUDED.cost""",
                        [(i["id"], i["key"],
                          i.get("dname"), i.get("cost")) for i in items])
        cur.executemany("""INSERT INTO hero_token_index(snapshot_version, hero_id, dense_index)
                           VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                        [(SNAPSHOT_VERSION, hid, di) for hid, di in token_index.items()])

    # 版本表：Valve 为准；opendota_patch 由 declare_lettered_versions() 从 patchdates 的键导出
    for p in declare_lettered_versions():
        conn.execute("""INSERT INTO patches(version_name, base_version, released_at, opendota_patch)
                        VALUES (%s,%s,to_timestamp(%s),%s)
                        ON CONFLICT (version_name) DO UPDATE
                        SET released_at = EXCLUDED.released_at,
                            base_version = EXCLUDED.base_version,
                            opendota_patch = EXCLUDED.opendota_patch""",
                     (p["version_name"], p["base_version"], p["released_at"], p["opendota_patch"]))

    conn.execute("""INSERT INTO app_config_kv(key, value, note) VALUES
        ('archetype_role_map',    %s, '规格 §7 维度 6 的**代码出处**（不是可执行的映射表）：值指向 python:constants.archetypes.RULES；§7 的「可调而不改代码」未实现 —— 规则带取反与嵌套 OR，扁平 KV 表表达不了，见计划「显式延迟」第 5 项'),
        ('robustness_lambda',     '1.0'::jsonb, '规格 §6.6 penalized_score 的 λ'),
        ('op_decision_min_delta', '0.02'::jsonb, '规格 §9.1 三选一判定的最小差异阈值'),
        ('min_sample_n',          '30'::jsonb, '规格 §7.3/§9.1 的样本量门槛')
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value, note = EXCLUDED.note, updated_at = now()""",
        (json.dumps({name: "python:constants.archetypes.RULES" for name, _ in RULES}),))
    if commit:
        conn.commit()
