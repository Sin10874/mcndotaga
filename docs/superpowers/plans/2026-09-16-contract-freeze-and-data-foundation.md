# 契约冻结与数据地基 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 `contracts/openapi.yaml` 与 16 个边界 fixtures（解锁三条并行 worktree），并建成可查询的数据地基（常量层、含字母子版本的版本表、引导数据集入库）。

**Architecture:** 单一 Postgres 实例承载全部关系数据；契约以 OpenAPI 3.1 为唯一权威源，fixtures 与 TS 类型均从它生成并由测试校验。常量层拆为「规范实体（`heroes`/`items`）+ 版本化 token 索引（`hero_token_index`）」，token 索引按快照冻结以防常量刷新时静默重映射。数据来源三类：Valve 官方补丁 feed（权威版本清单）、dotaconstants（英雄/道具）、Kaggle CC0 数据集（历史 BP 引导数据，仅下载 506 MB 子集）。

**Tech Stack:** Python 3.12 · PostgreSQL 16 · Docker Compose · pytest · jsonschema + referencing · httpx · openapi-spec-validator

**Spec:** `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md`

---

## 前置说明

**本计划只做 M0 + M1。** 采集器（M2）、回放导入（M2b）、画像引擎（M3–M5）、可视化（M6）、序列模型（M7–M9）、部署（M10）各由独立计划覆盖。

**任务顺序说明**：Chunk 1 是**契约冻结**（M0），Chunk 2 是**数据地基**（M1）。两个 chunk 无相互依赖，可交换顺序；契约冻结优先，因为前端 worktree 只依赖它、不等数据库。

**交付后即可并行的三条 worktree：**
- 线 A（画像引擎）→ `analysis/`：依赖 `db/` + `contracts/`
- 线 C（序列模型）→ `models/`：依赖 `contracts/` + `shared/`（无需数据库）
- 前端 → `web/`：依赖 `contracts/fixtures/`，**不需要数据库**

---

## Chunk 1: 骨架、数据模型与共享模板（M0 前半）

### Task 1: 仓库骨架、依赖安装与目录边界

**Files:**
- Create: `compose.yaml`, `.gitattributes`, `.env.example`, `pyproject.toml`, `Makefile`
- Create: `db/.gitkeep`, `contracts/.gitkeep`, `analysis/.gitkeep`, `models/.gitkeep`, `web/.gitkeep`

- [ ] **Step 1: 写 `compose.yaml`**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-dota}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-dota}
      POSTGRES_DB: ${POSTGRES_DB:-dota}
    ports: ["${POSTGRES_PORT:-5432}:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dota} -d ${POSTGRES_DB:-dota}"]
      interval: 3s
      timeout: 3s
      retries: 20
volumes:
  pgdata:
```

用**命名卷** `pgdata`（规格 §13）。

- [ ] **Step 2: 写 `.gitattributes`（规格 §13 明确要求强制 LF）**

```
* text=auto eol=lf
*.png binary
*.dem binary
```

CRLF 会让容器内的 shell 脚本报 `no such file or directory`。

- [ ] **Step 3: 写 `.env.example`**

```
POSTGRES_USER=dota
POSTGRES_PASSWORD=dota
POSTGRES_DB=dota
POSTGRES_PORT=5432
DATABASE_URL=postgresql://dota:dota@localhost:5432/dota
TEST_DATABASE_URL=postgresql://dota:dota@localhost:5432/dota_test
KAGGLE_USERNAME=
KAGGLE_KEY=
LIQUIPEDIA_USER_AGENT=Dota2DraftAnalysis/0.1 (https://github.com/yourname/mcndotaga; you@example.com)
```

- [ ] **Step 4: 写 `pyproject.toml`**

```toml
[project]
name = "mcndotaga"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "psycopg[binary]>=3.2",
  "httpx>=0.27",
  "jsonschema>=4.23",
  "referencing>=0.35",
  "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "openapi-spec-validator>=0.7"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

> **`pythonpath = ["."]` 是必需的**，两个原因：① 否则 `pytest` 无法 import 顶层包；② 更隐蔽的是 `tests/db/test_*.py` 里写 `import db` 会解析到**测试目录本身** `tests/db`（namespace package），于是 `db/sources.py` 实现之后报错信息**仍然是** `No module named 'db.sources'`——红/绿检查点会完全失效。同一问题影响 `tests/contracts/`。

- [ ] **Step 5: 安装依赖（可编辑安装，使顶层包可导入）**

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```
Expected: `Successfully installed mcndotaga-0.1.0 ...`

> Makefile 里的 `python`/`pytest` 依赖此虚拟环境已激活。若用 `uv`/`poetry` 请等效替换，但**必须**保证顶层包可导入。

- [ ] **Step 6: 写 `Makefile`**

```makefile
.PHONY: up down db-reset migrate test contract contract-ts lint-spec

up:          ; docker compose up -d --wait db
down:        ; docker compose down
db-reset:    ; docker compose down -v && $(MAKE) up && $(MAKE) migrate
migrate:     ; python -m db.migrate
test:        ; pytest -q
contract:    ; python -m contracts.tools.validate_fixtures
contract-ts: ; python -m contracts.tools.gen_ts_types
lint-spec:   ; python -m openapi_spec_validator contracts/openapi.yaml
```

`up -d --wait` 等待 healthcheck 通过，替代不可靠的 `sleep`。

- [ ] **Step 7: 建立三条线的目录边界（M0 验收第 3 条）**

```bash
mkdir -p db/migrations db/views contracts/schemas contracts/fixtures contracts/tools \
         analysis models shared web/src/types tests/db tests/contracts tests/shared
for d in db contracts analysis models web; do touch "$d/.gitkeep"; done
```

规格 §11 要求三条 worktree 有明确的可写目录边界：线 A → `db/` + `analysis/`，线 C → `models/`，前端 → `web/`。`shared/` 存放两端共用、不属于任何一条线的常量（见 Task 4）。

- [ ] **Step 8: 验证**

Run: `make up && docker compose ps`
Expected: `db` 状态 `running (healthy)`

- [ ] **Step 9: Commit**

```bash
git add compose.yaml .gitattributes .env.example pyproject.toml Makefile \
        db contracts analysis models shared web tests
git commit -m "chore: 仓库骨架、依赖、Compose、三条线的目录边界"
```

---

### Task 2: §5.1 的 DDL 作为迁移 001 + 约束测试

规格 §16.1 已证明这段 DDL 在 PostgreSQL 16 上可执行（22 张表）。本任务把它变成可重复执行的迁移，并给那 15 条约束探针补上回归保护。

**Files:**
- Create: `db/migrations/001_schema.sql`, `db/migrate.py`, `db/__init__.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/db/test_constraints.py`

- [ ] **Step 1: 从规格提取 SQL（避免转录错误）**

```bash
python - <<'PY'
import re, pathlib
spec = pathlib.Path("docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md").read_text(encoding="utf-8")
sql = re.search(r"```sql\n(.*?)\n```", spec, re.S).group(1)
pathlib.Path("db/migrations/001_schema.sql").write_text(sql + "\n", encoding="utf-8")
print(f"wrote {len(sql.splitlines())} lines")
PY
```
Expected: `wrote 298 lines`

- [ ] **Step 2: 写 `db/migrate.py`**

```python
"""Apply SQL migrations then views. Idempotent via schema_migrations."""
from __future__ import annotations
import os, pathlib, sys
import psycopg

HERE = pathlib.Path(__file__).parent
MIGRATIONS = HERE / "migrations"
VIEWS = HERE / "views"

def dsn_from_env() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota") \
        .replace("postgresql+psycopg://", "postgresql://")

def apply(dsn: str | None = None) -> None:
    with psycopg.connect(dsn or dsn_from_env()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name}"); continue
            print(f"apply {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
        # 视图用 CREATE OR REPLACE，每次重跑以拾取变更
        for path in sorted(VIEWS.glob("*.sql")):
            print(f"view  {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()

if __name__ == "__main__":
    apply()
```

`apply(dsn)` 可传测试库连接串，供 `conftest.py` 建库后调用。

- [ ] **Step 3: 写 `tests/conftest.py`（自动创建并迁移测试库）**

```python
from __future__ import annotations
import os
from contextlib import contextmanager
import psycopg, pytest

def _base_dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota") \
        .replace("postgresql+psycopg://", "postgresql://")

@pytest.fixture(scope="session")
def dsn() -> str:
    """建 <db>_test 并跑迁移，使测试不污染开发库（规格 §15）。"""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        head, dbname = _base_dsn().rsplit("/", 1)
        base = f"{head}/{dbname}_test"
    head, test_name = base.rsplit("/", 1)
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (test_name,)).fetchone():
            c.execute(f'CREATE DATABASE "{test_name}"')
    from db.migrate import apply
    apply(base)
    return base

@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn) as conn:
        yield conn
        conn.rollback()

@contextmanager
def expect_violation(conn):
    """断言语句违反约束，并回滚到保存点使事务可继续。

    必需：psycopg 在约束违规后事务进入 aborted 状态，
    后续任何语句都会以 'current transaction is aborted' 失败。
    """
    conn.execute("SAVEPOINT sp")
    try:
        yield
    except psycopg.Error:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
    else:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        pytest.fail("期望约束违规，但语句执行成功了")

@pytest.fixture
def seeded(db):
    """最小可用数据集。

    注意 constants_snapshot 必须写**显式列名**：该表实际列序是
    (snapshot_version, fetched_at, n_heroes, n_items, source)，
    VALUES (1,127,501) 会被当成给 fetched_at 传整数而报类型错误。
    """
    db.execute("""INSERT INTO constants_snapshot
                  (snapshot_version, n_heroes, n_items) VALUES (1, 127, 501)""")
    db.execute("""INSERT INTO heroes(hero_id, name, localized_name)
                  VALUES (80, 'npc_dota_hero_lone_druid', 'Lone Druid')""")
    db.execute("INSERT INTO hero_token_index VALUES (1, 80, 73)")
    db.execute("INSERT INTO leagues(league_id, name) VALUES (1, 'L')")
    db.execute("""INSERT INTO matches(match_id, data_source, started_at, first_pick_team, draft_state)
                  VALUES (1, 'pro_match', now(), 0, 'complete')""")
    return 1
```

- [ ] **Step 4: 写 `tests/db/test_constraints.py`（覆盖规格 §16.1 全部探针）**

```python
from tests.conftest import expect_violation

def test_full_ten_player_match_inserts(db, seeded):
    rows = [(1, s, None, 0 if s < 5 else 1, 80) for s in range(10)]
    with db.cursor() as cur:
        cur.executemany("""INSERT INTO match_players(match_id, player_slot, account_id, team, hero_id)
                           VALUES (%s,%s,%s,%s,%s)""", rows)
    assert db.execute("SELECT count(*) FROM match_players WHERE match_id=1").fetchone()[0] == 10

def test_anonymous_players_do_not_collide(db, seeded):
    """规格 §16.1：两个匿名选手同场必须都入库（主键是 player_slot 而非 account_id）。"""
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80), (1,8,NULL,1,80)""")
    n = db.execute("SELECT count(*) FROM match_players WHERE match_id=1 AND account_id IS NULL").fetchone()[0]
    assert n == 2

def test_raw_mod_128_normalization_is_rejected(db, seeded):
    """规格 §16.2：raw%128 把 Dire 的 128..132 塌缩成 0..4。"""
    with expect_violation(db):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,0,NULL,1,80)""")

def test_slot_team_mismatch_is_rejected(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,5,NULL,0,80)""")

def test_metric_weights_rejects_undefined_metric(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO metric_weights VALUES ('laning','pro_match',1.0,NULL)")

def test_metric_weights_accepts_all_six_spec_keys(db, seeded):
    for m in ["patch_strength","hero_pool","system_pref","bp_tendency","tempo","map_vision"]:
        db.execute("INSERT INTO metric_weights VALUES (%s,'pro_match',1.0,NULL)", (m,))

def test_data_source_rejects_undefined_value(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'ranked',now(),'complete')""")

def test_draft_state_rejects_undefined_value(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'pro_match',now(),'partial')""")

def test_draft_actions_rejects_unknown_hero(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,999)")

def test_draft_actions_rejects_ord_out_of_range(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO draft_actions VALUES (1,24,false,0,80)")

def test_rosters_allows_null_joined_at(db, seeded):
    db.execute("INSERT INTO players(account_id,name) VALUES (111,'p')")
    db.execute("INSERT INTO teams(team_id,name) VALUES (10,'A')")
    db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,NULL,'liquipedia')")
    with expect_violation(db):
        db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,'2020-01-01','liquipedia')")

def test_hero_token_index_rejects_duplicate_dense_index(db, seeded):
    """同快照内两个英雄不能占用同一 dense_index。"""
    db.execute("""INSERT INTO heroes(hero_id,name,localized_name) VALUES (81,'npc_dota_hero_x','X')""")
    with expect_violation(db):
        db.execute("INSERT INTO hero_token_index VALUES (1, 81, 73)")

def test_hero_token_index_rejects_double_index_for_same_hero(db, seeded):
    """同一英雄在同快照内不能有两个索引。"""
    with expect_violation(db):
        db.execute("INSERT INTO hero_token_index VALUES (1, 80, 121)")

def test_hero_token_index_is_versioned(db, seeded):
    db.execute("INSERT INTO constants_snapshot (snapshot_version,n_heroes,n_items) VALUES (2,128,501)")
    db.execute("INSERT INTO hero_token_index VALUES (2, 80, 73)")   # 新快照可复用索引

def test_reinsert_is_idempotent(db, seeded):
    """规格 §16.1：重复灌入不产生重复行。"""
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80)")
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80) ON CONFLICT DO NOTHING")
    assert db.execute("SELECT count(*) FROM draft_actions WHERE match_id=1").fetchone()[0] == 1
```

- [ ] **Step 5: 反向验证测试真的在测东西**

本任务的 DDL 直接来自规格且已在 PostgreSQL 16 上验证（规格 §16.1），因此这里的目的**不是** TDD 式实现，而是**给已验证的 DDL 补回归保护**——直接跑就是绿的，没有天然红态。

真正的红/绿验证方式是**故意破坏再修复**：

```bash
# 1. 临时注释掉 db/migrations/001_schema.sql 里的这一行
#    CONSTRAINT slot_team_agree CHECK ((player_slot < 5) = (team = 0))
make db-reset && pytest tests/db/test_constraints.py -q
# 2. 预期：test_raw_mod_128_normalization_is_rejected 与
#          test_slot_team_mismatch_is_rejected 变红（2 failed）
# 3. 恢复该行，重跑，预期回到 15 passed
```

**请实际执行一次这个反向验证再继续。**

- [ ] **Step 6: 运行确认通过**

Run: `make db-reset && pytest tests/db/test_constraints.py -q`
Expected: **15 passed**

- [ ] **Step 7: Commit**

```bash
git add db/ tests/
git commit -m "feat(db): §5.1 DDL 作为迁移 001 + 15 条约束测试（含 raw%128 与匿名选手回归保护）"
```

---

### Task 3: 对手侧隔离视图与负向测试

规格 §4.1 要求「`scrim` 数据永不进入对手画像」，强制机制是只读视图 + `resolve_sources()` 白名单 + 一条**可观测量**的负向测试。

**Files:**
- Create: `db/views/opponent_profile.sql`, `db/sources.py`
- Modify: `db/migrate.py`（若 Task 2 Step 2 的视图循环未生效，在此补上，并把它加入本任务的 `git add`）
- Test: `tests/db/test_isolation.py`

- [ ] **Step 1: 写失败的测试**

```python
import pytest
from db.sources import resolve_sources, SourceNotAllowed

def test_opponent_view_excludes_scrim_and_pub(db, seeded):
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                  VALUES (2,'scrim',now(),'complete'), (3,'pub_match',now(),'complete')""")
    rows = db.execute("SELECT match_id FROM opponent_profile_matches ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == [1], "对手画像视图只能看到 pro_match"

def test_pub_view_excludes_scrim(db, seeded):
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                  VALUES (2,'scrim',now(),'complete'), (3,'pub_match',now(),'complete')""")
    rows = db.execute("SELECT match_id FROM opponent_profile_matches_with_pub ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == [1, 3]

def test_resolve_sources_rejects_scrim_for_opponent_paths():
    with pytest.raises(SourceNotAllowed):
        resolve_sources(["pro_match", "scrim"], allow_scrim=False)

def test_resolve_sources_accepts_scrim_when_explicitly_allowed():
    assert resolve_sources(["pro_match", "scrim"], allow_scrim=True) == ["pro_match", "scrim"]

def test_resolve_sources_rejects_unknown():
    with pytest.raises(SourceNotAllowed):
        resolve_sources(["ranked"])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_isolation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.sources'`

> 若报的是 `No module named 'db'`，或路径指向 `tests/db`，说明 Task 1 Step 4 的 `pythonpath = ["."]` 没生效。

- [ ] **Step 3: 写 `db/views/opponent_profile.sql`**

```sql
-- 规格 §4.1：对手侧数据路径的唯一合法来源。刻意不含 scrim。
CREATE OR REPLACE VIEW opponent_profile_matches AS
SELECT * FROM matches WHERE data_source = 'pro_match';

-- 显式开启 pub_match 时的放宽版；仍不含 scrim。
CREATE OR REPLACE VIEW opponent_profile_matches_with_pub AS
SELECT * FROM matches WHERE data_source IN ('pro_match','pub_match');
```

- [ ] **Step 4: 写 `db/sources.py`**

```python
"""规格 §4.1 的隔离规则。

对手侧模块（playbook / value 的对手侧 / profile / policy / advise）
调用时必须 allow_scrim=False（默认值）。
"""
from __future__ import annotations

ALLOWED = {"pro_match", "pub_match", "scrim"}

class SourceNotAllowed(ValueError):
    pass

def resolve_sources(requested: list[str], *, allow_scrim: bool = False) -> list[str]:
    unknown = set(requested) - ALLOWED
    if unknown:
        raise SourceNotAllowed(f"unknown sources: {sorted(unknown)}")
    if "scrim" in requested and not allow_scrim:
        raise SourceNotAllowed("scrim data may never reach opponent-side paths (spec §4.1)")
    return sorted(set(requested))
```

- [ ] **Step 5: 确认视图已生效**

Run: `make db-reset && psql "$DATABASE_URL" -c "\dv opponent_profile*"`
Expected: 两张视图均存在。若不存在，说明 `db/migrate.py` 的视图循环未生效——回到 Task 2 Step 2 补上。

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/db -q`
Expected: **20 passed**（15 约束 + 5 隔离）

- [ ] **Step 7: Commit**

```bash
git add db/views db/sources.py tests/db/test_isolation.py
git commit -m "feat(db): 对手侧隔离视图 + resolve_sources 白名单 + 负向测试"
```

---

### Task 4: 共享模板模块（引擎、模型与校验器共用）

规格 §6.0 的 24 手模板是本项目最不能出错的东西。它**不能**住在 `contracts/tools/`（那是校验器包，生产引擎不该 import 它），但**必须只有一份定义**。

**Files:**
- Create: `shared/__init__.py`, `shared/draft_template.py`
- Create: `tests/shared/__init__.py`
- Test: `tests/shared/test_draft_template.py`

- [ ] **Step 1: 写失败的测试**

```python
import pytest
from shared.draft_template import TEMPLATE, resolve, first_pick_team_from_actions

def test_template_shape_matches_spec():
    assert len(TEMPLATE) == 24
    assert sum(1 for is_pick, _ in TEMPLATE if not is_pick) == 14
    assert sum(1 for is_pick, _ in TEMPLATE if is_pick) == 10

def test_sides_are_balanced():
    assert sum(1 for ip, w in TEMPLATE if not ip and w == "F") == 7
    assert sum(1 for ip, w in TEMPLATE if not ip and w == "O") == 7
    assert sum(1 for ip, w in TEMPLATE if ip and w == "F") == 5
    assert sum(1 for ip, w in TEMPLATE if ip and w == "O") == 5

def test_phase_structure_is_7_2_3_6_4_2():
    """规格 §8① 的固定阶段结构。"""
    phases, cur, cnt = [], TEMPLATE[0][0], 0
    for is_pick, _ in TEMPLATE:
        if is_pick == cur:
            cnt += 1
        else:
            phases.append(cnt); cur, cnt = is_pick, 1
    phases.append(cnt)
    assert phases == [7, 2, 3, 6, 4, 2]

def test_resolve_ord_13_is_first_pick_team_when_they_lead():
    """规格 §6.3 示例：first_pick_team=0 时 ord 13 属于 team 0 的 pick。"""
    assert resolve(13, 0) == (True, 0)
    assert resolve(13, 1) == (True, 1)

def test_resolve_ord_0_is_a_ban_by_first_pick_team():
    """规格 §8① 已验证：ord 0 的队伍就是先手方。"""
    assert resolve(0, 0) == (False, 0)
    assert resolve(0, 1) == (False, 1)

def test_resolve_ord_12_is_opponent_pick():
    assert resolve(12, 0) == (True, 1)

def test_first_pick_team_from_actions():
    assert first_pick_team_from_actions([{"ord": 0, "team": 1}, {"ord": 1, "team": 1}]) == 1

def test_resolve_rejects_out_of_range():
    with pytest.raises(ValueError):
        resolve(24, 0)

def test_first_pick_team_from_actions_requires_ord_zero():
    with pytest.raises(ValueError):
        first_pick_team_from_actions([{"ord": 1, "team": 0}])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/shared -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.draft_template'`

- [ ] **Step 3: 写 `shared/draft_template.py`**

```python
"""规格 §6.0 / §8① 的 24 手 CM 模板。

**唯一定义处** —— 分析引擎、序列模型、契约校验器一律 import 这里。
两份副本必然漂移，而模板是本项目最不能出错的东西。

已验证（规格 §16.3）：OpenDota 全库 1,014 场中
`first_ban_team == first_pick_team` 成立 1014/1014。
"""
from __future__ import annotations

# (is_pick, 归属方)；'F' = 先手方, 'O' = 后手方
TEMPLATE: list[tuple[bool, str]] = [
    (False,'F'),(False,'F'),(False,'O'),(False,'O'),(False,'F'),(False,'O'),(False,'O'),  # 0-6   ban
    (True,'F'),(True,'O'),                                                                  # 7-8   pick
    (False,'F'),(False,'F'),(False,'O'),                                                    # 9-11  ban
    (True,'O'),(True,'F'),(True,'F'),(True,'O'),(True,'O'),(True,'F'),                      # 12-17 pick
    (False,'F'),(False,'O'),(False,'F'),(False,'O'),                                        # 18-21 ban
    (True,'F'),(True,'O'),                                                                  # 22-23 pick
]

def resolve(ord_: int, first_pick_team: int) -> tuple[bool, int]:
    """返回该手的 (is_pick, team)。team: 0=Radiant 1=Dire。

    手数与类型**不由模型预测**——由本模板 + first_pick_team 确定性推出。
    """
    if not 0 <= ord_ < len(TEMPLATE):
        raise ValueError(f"ord 必须在 0..23，收到 {ord_}")
    if first_pick_team not in (0, 1):
        raise ValueError(f"first_pick_team 必须是 0 或 1，收到 {first_pick_team}")
    is_pick, who = TEMPLATE[ord_]
    team = first_pick_team if who == "F" else 1 - first_pick_team
    return is_pick, team

def first_pick_team_from_actions(actions: list[dict]) -> int:
    """由 ord=0 的 team 推出先手方（规格 §8①）。"""
    for a in actions:
        if a["ord"] == 0:
            return int(a["team"])
    raise ValueError("actions 中缺少 ord=0，无法推出先手方")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/shared -q`
Expected: **9 passed**

- [ ] **Step 5: Commit**

```bash
git add shared/ tests/shared/
git commit -m "feat(shared): 24 手模板唯一定义处，供引擎、模型与校验器共用"
```

---

## Chunk 2: 契约本身（M0 后半）

本 chunk 逐字段冻结 `contracts/openapi.yaml`。完成后 M0 达成，
三条 worktree 即可并行启动。

### Task 5: 契约公共组件（枚举 / 降级形态 / 错误信封）

**Files:**
- Create: `contracts/schemas/common.yaml`
- Create: `contracts/tools/__init__.py`, `contracts/tools/build_openapi.py`
- Create: `tests/contracts/__init__.py`, `tests/contracts/conftest.py`
- Test: `tests/contracts/test_common_schema.py`

- [ ] **Step 1: 写 `tests/contracts/conftest.py`（根锚定校验器）**

```python
"""提供 validator_for() —— 让子 schema 的 $ref 能正确解析。

直接 validate(instance, subschema) 会因为 #/components/... 的 $ref
在子 schema 内无根可依而抛 referencing 的 PointerToNowhere，
且该异常**不是** jsonschema.ValidationError，常规 except 与
pytest.raises(ValidationError) 都抓不到。必须根锚定。
"""
from __future__ import annotations
import pathlib
import pytest, yaml
from referencing import Registry, Resource
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).parents[2] / "contracts"

@pytest.fixture(scope="session")
def contract_doc() -> dict:
    return yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))

@pytest.fixture(scope="session")
def validator_for(contract_doc):
    registry = Registry().with_resource("", Resource.from_contents(contract_doc))
    def _make(name: str) -> Draft202012Validator:
        return Draft202012Validator(
            {"$ref": f"#/components/schemas/{name}"}, registry=registry)
    return _make

@pytest.fixture(scope="session")
def common(contract_doc) -> dict:
    return contract_doc["components"]["schemas"]
```

- [ ] **Step 2: 写 `contracts/schemas/common.yaml`**

```yaml
openapi: 3.1.0
info: {title: MCNDOTAGA Contract, version: "1.0.0"}
components:
  schemas:
    Team:   {type: integer, enum: [0, 1], description: "0=Radiant, 1=Dire；仅数值字段"}
    Side:   {type: string, enum: [us, them], description: "仅 Playbook 语义字段"}
    Confidence: {type: string, enum: [low, medium, high]}
    Recommendation: {type: string, enum: [pick, ban, leave_and_counter, insufficient_data]}
    TheirOpening:
      type: string
      enum: [teamfight, push, pickoff, splitpush, protect, initiate, unknown]
    NoteKind:
      type: string
      enum: [ward, timing, lane, smoke, combat, resource, communication]
    UnavailableReason:
      type: string
      enum: [needs_replay, insufficient_samples, source_not_allowed, stat_unavailable]
    ErrorCode:
      type: string
      enum: [insufficient_data, invalid_request, source_not_allowed, not_found, upstream_unavailable]
    Degraded:
      type: object
      required: [value, reason]
      properties:
        value:  {type: "null"}
        reason: {$ref: "#/components/schemas/UnavailableReason"}
        needs:  {type: string, enum: ["Phase B"]}
      allOf:
        - if:   {properties: {reason: {const: needs_replay}}, required: [reason]}
          then: {required: [needs]}
        - if:   {properties: {reason: {not: {const: needs_replay}}}, required: [reason]}
          then: {not: {required: [needs]}}
    ErrorEnvelope:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, message]
          properties:
            code:    {$ref: "#/components/schemas/ErrorCode"}
            message: {type: string}
            detail:  {type: object}
```

- [ ] **Step 3: 写 `contracts/tools/build_openapi.py`**

```python
"""合并 contracts/schemas/*.yaml 为单一 openapi.yaml。重名冲突直接报错。"""
from __future__ import annotations
import copy, pathlib, sys
import yaml

HERE = pathlib.Path(__file__).parents[1]
SCHEMAS = HERE / "schemas"
OUT = HERE / "openapi.yaml"

def build() -> dict:
    merged = {"openapi": "3.1.0",
              "info": {"title": "MCNDOTAGA Contract", "version": "1.0.0"},
              "paths": {},
              "components": {"schemas": {}}}
    for path in sorted(SCHEMAS.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, schema in (doc.get("components", {}).get("schemas") or {}).items():
            if name in merged["components"]["schemas"]:
                raise ValueError(
                    f"schema {name!r} 在 {path.name} 与其它文件重复定义——"
                    "同名不同义会让前端与后端产生分歧")
            merged["components"]["schemas"][name] = copy.deepcopy(schema)
    return merged

def main() -> int:
    doc = build()
    OUT.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {OUT} with {len(doc['components']['schemas'])} schemas")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 写 `tests/contracts/test_common_schema.py`**

```python
import pytest, jsonschema

def test_degraded_requires_needs_only_for_needs_replay(validator_for):
    v = validator_for("Degraded")
    v.validate({"value": None, "reason": "needs_replay", "needs": "Phase B"})
    v.validate({"value": None, "reason": "insufficient_samples"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "insufficient_samples", "needs": "Phase B"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "needs_replay"})

def test_degraded_rejects_truthy_value(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Degraded").validate({"value": 0, "reason": "insufficient_samples"})

def test_error_codes_are_closed(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("ErrorEnvelope").validate({"error": {"code": "boom", "message": "x"}})

def test_all_six_archetypes_are_defined(common):
    assert set(common["TheirOpening"]["enum"]) == {
        "teamfight","push","pickoff","splitpush","protect","initiate","unknown"}
```

- [ ] **Step 5: 生成并运行**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: `wrote .../openapi.yaml with 10 schemas`；**4 passed**

- [ ] **Step 6: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): §6.0 公共组件 + 根锚定校验器（修 ref 解析）"
```

---

### Task 6: 五个接口的 schema 与可执行不变式

**Files:**
- Create: `contracts/schemas/value.yaml`, `policy.yaml`, `playbook.yaml`, `profile.yaml`, `advise.yaml`
- Create: `contracts/tools/invariants.py`
- Test: `tests/contracts/test_invariants.py`

- [ ] **Step 1: 写 `contracts/tools/invariants.py`**

```python
"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。"""
from __future__ import annotations
from shared.draft_template import TEMPLATE

TOL = 1e-3

def check_resolve(next_ord: int, team: int, is_pick: bool, first_pick_team: int) -> list[str]:
    exp_pick, who = TEMPLATE[next_ord]
    exp_team = first_pick_team if who == "F" else 1 - first_pick_team
    errs = []
    if bool(exp_pick) != bool(is_pick):
        errs.append(f"ord {next_ord} 类型应为 {'pick' if exp_pick else 'ban'}")
    if exp_team != team:
        errs.append(f"ord {next_ord} 归属应为 team {exp_team}，实际 {team}")
    return errs

def check_value(body: dict, requested_sources: list[str] | None = None) -> list[str]:
    errs = []
    total = sum(c["delta"] for c in body["contributions"])
    want = body["radiant_win_prob"] - 0.5
    if abs(total - want) > TOL:
        errs.append(f"contributions 求和 {total:.4f} != radiant_win_prob-0.5 {want:.4f}")
    if requested_sources is not None:
        extra = set(body.get("sources_used", [])) - set(requested_sources)
        if extra:
            errs.append(f"sources_used 含未请求的来源: {sorted(extra)}")
    return errs

def check_policy(body: dict) -> list[str]:
    errs = []
    s = sum(c["prob"] for c in body["candidates"]) + body["other_prob"]
    if abs(s - 1.0) > TOL:
        errs.append(f"sum(candidates.prob)+other_prob = {s:.4f} != 1.0")
    if len(body["candidates"]) > body["top_n"]:
        errs.append(f"len(candidates)={len(body['candidates'])} > top_n={body['top_n']}")
    if any(not c.get("reasons") for c in body["candidates"]):
        errs.append("存在 reasons 为空的候选")
    return errs

def check_playbook(body: dict) -> list[str]:
    errs = []
    fpt = body["matchup"]["first_pick_team"]
    us_first = body["matchup"]["side_map"]["us"] == fpt
    want_first = "us" if us_first else "them"
    for br in body["branches"]:
        if br["condition"]["first_pick"] != want_first:
            errs.append(f"分支 {br['branch_id']} 的 first_pick 应为 {want_first}")
        for pl in br["plans"]:
            if not pl.get("fallback"):
                errs.append(f"plan {pl['label']} 的 fallback 为空")
            for kp in pl["key_picks"]:
                _, who = TEMPLATE[kp["by_ord"]]
                owner_is_us = (who == "F") if us_first else (who == "O")
                if not owner_is_us:
                    errs.append(f"plan {pl['label']}: by_ord {kp['by_ord']} 属于对手，"
                                f"与分支 first_pick={want_first} 不符")
    for oh in body.get("op_hero_decision", []):
        lv = oh["if_we_leave"]
        if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
            errs.append(f"hero {oh['hero_id']}: our_wr + their_wr != 1.0")
    return errs

def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    """支持两种 mode：realtime 返回 options[]，offline 返回 branches[].plans[]。"""
    errs = []
    if "options" in body:
        items = body["options"]
    elif "branches" in body:
        items = [pl for br in body["branches"] for pl in br["plans"]]
    else:
        return ["advise 响应既无 options 也无 branches"]
    scores = [o["penalized_score"] for o in items]
    if scores != sorted(scores, reverse=True):
        errs.append("结果未按 penalized_score 降序")
    for o in items:
        key = o.get("hero_id", o.get("label"))
        exp = o["expected_wr"] - lam * max(0.0, o["robustness_delta"] - 0.10)
        if abs(exp - o["penalized_score"]) > TOL:
            errs.append(f"{key}: penalized_score {o['penalized_score']} != {exp:.4f}")
        if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
            errs.append(f"{key}: robustness_delta > 0.10 但缺 risk_note")
        if not o.get("fallback"):
            errs.append(f"{key}: fallback 为空")
    return errs

_ARCHETYPES = {"initiate","protect","push","teamfight","pickoff","splitpush"}

def check_profile(body: dict) -> list[str]:
    errs = []
    for p in body["players"]:
        ha = p["dimensions"]["hero_archetype"]
        if set(ha) != _ARCHETYPES:
            errs.append(f"player {p['account_id']}: hero_archetype 键集不完整")
        elif abs(sum(ha.values()) - 1.0) > TOL:
            errs.append(f"player {p['account_id']}: hero_archetype 和 != 1.0")
    return errs
```

- [ ] **Step 2: 写失败测试**

```python
from contracts.tools.invariants import (check_value, check_policy, check_advise,
                                        check_playbook, check_profile)

SPEC_VALUE = {"radiant_win_prob": 0.530, "contributions": [
    {"factor":"patch_strength","delta":0.011},{"factor":"counter_matchup","delta":-0.014},
    {"factor":"player_comfort","delta":0.024},{"factor":"first_pick","delta":0.009}]}

def test_spec_value_example_passes():
    assert check_value(SPEC_VALUE) == []

def test_value_detects_the_original_round1_defect():
    """规格一轮抓到的原始错误：求和 0.054 而 radiant_win_prob-0.5 = 0.030。"""
    bad = {**SPEC_VALUE, "contributions": [
        {"factor":"patch_strength","delta":0.021},{"factor":"counter_matchup","delta":-0.014},
        {"factor":"player_comfort","delta":0.038},{"factor":"first_pick","delta":0.009}]}
    assert check_value(bad) != []

def test_value_detects_unrequested_source():
    body = {**SPEC_VALUE, "sources_used": ["pro_match", "pub_match"]}
    assert check_value(body, requested_sources=["pro_match"]) != []

def test_spec_policy_example_passes():
    assert check_policy({"candidates":[{"hero_id":112,"prob":0.180,"reasons":["x"]}],
                         "top_n":10,"other_prob":0.820}) == []

def test_spec_profile_example_passes():
    body = {"players":[{"account_id":1,"dimensions":{"hero_archetype":{
        "initiate":0.24,"protect":0.18,"push":0.17,"teamfight":0.19,"pickoff":0.13,"splitpush":0.09}}}]}
    assert check_profile(body) == []

def test_profile_rejects_five_category_distribution():
    """规格四轮抓到的原始缺陷：只有 5 类且凑巧和为 1.0。"""
    body = {"players":[{"account_id":1,"dimensions":{"hero_archetype":{
        "teamfight":0.32,"push":0.18,"pickoff":0.21,"splitpush":0.11,"protect":0.18}}}]}
    assert check_profile(body) != []

def test_spec_advise_example_passes():
    opts = [{"hero_id":105,"expected_wr":0.552,"robustness_delta":0.04,"penalized_score":0.552,"fallback":[67]},
            {"hero_id":67,"expected_wr":0.548,"robustness_delta":0.03,"penalized_score":0.548,"fallback":[19]},
            {"hero_id":19,"expected_wr":0.556,"robustness_delta":0.22,"penalized_score":0.436,"fallback":[67],"risk_note":"r"}]
    assert check_advise({"options": opts}) == []

def test_advise_handles_offline_branches_shape():
    """offline 模式返回 branches[].plans[]，不含 options —— 不得 KeyError。"""
    body = {"branches": [{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                          "plans":[{"label":"A1","expected_wr":0.52,"robustness_delta":0.04,
                                    "penalized_score":0.52,"fallback":[67,19]}]}]}
    assert check_advise(body) == []

def test_playbook_flags_opponent_by_ord_in_own_branch():
    body = {"matchup":{"first_pick_team":0,"side_map":{"us":0,"them":1}},
            "branches":[{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                         "plans":[{"label":"A1","fallback":[1],"key_picks":[{"by_ord":12}]}]}],
            "op_hero_decision":[]}
    assert check_playbook(body) != []   # ord 12 属于后手方
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contracts.tools.invariants'`

- [ ] **Step 4: 写五个 schema 文件**

按规格 §6.1–§6.6 的响应示例逐字段落成 JSON Schema。硬性要求：

1. **组件名必须恰好是** `Value` / `Policy` / `Playbook` / `Profile` / `Advise`——`validate_fixtures.py` 用 `resource.capitalize()` 查找。
2. **所有降级字段必须 `$ref` 到 `Degraded`**，不得内联同形对象。否则规格 §15 的「降级契约测试」对它们不生效，`playbook__op_insufficient.json` 的反向保护（`needs` 必须缺席）也失去约束。
3. 规格标 `// optional` 的字段不进 `required`。

- [ ] **Step 5: 运行确认通过**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: **15 passed**（4 公共 + 11 不变式）

- [ ] **Step 6: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): 五资源 schema + 可执行不变式（降级字段一律 ref Degraded）"
```

---

### Task 7: 16 个边界 fixtures 与校验工具

**Files:**
- Create: `contracts/fixtures/*.json`（16 个）、`contracts/fixtures/README.md`
- Create: `contracts/tools/validate_fixtures.py`
- Test: `tests/contracts/test_fixtures.py`

- [ ] **Step 1: 先写测试（保证有红态）**

```python
from contracts.tools.validate_fixtures import main

def test_all_fixtures_conform():
    assert main() == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/contracts/test_fixtures.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contracts.tools.validate_fixtures'`

- [ ] **Step 3: 写 `contracts/tools/validate_fixtures.py`**

```python
"""校验每个 fixture：① 过其资源 schema（根锚定，$ref 可解析）；② 过该资源不变式。"""
from __future__ import annotations
import json, pathlib, sys
from referencing import Registry, Resource
from jsonschema import Draft202012Validator
import yaml
from . import invariants as inv

ROOT = pathlib.Path(__file__).parents[1]
FIX = ROOT / "fixtures"

ROUTES = {
    "value":    inv.check_value,
    "policy":   inv.check_policy,
    "playbook": inv.check_playbook,
    "profile":  inv.check_profile,
    "advise":   inv.check_advise,
}

def main() -> int:
    doc = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    registry = Registry().with_resource("", Resource.from_contents(doc))

    def validator(name: str) -> Draft202012Validator:
        return Draft202012Validator({"$ref": f"#/components/schemas/{name}"}, registry=registry)

    failures, files = [], sorted(FIX.glob("*.json"))
    for f in files:
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
            resource = f.name.split("__")[0]
            if resource == "error":
                validator("ErrorEnvelope").validate(body)
                continue
            validator(resource.capitalize()).validate(body)
            for err in ROUTES[resource](body):
                failures.append(f"{f.name}: invariant — {err}")
        except Exception as e:                      # schema 错误计入失败而非让工具崩溃
            msg = getattr(e, "message", None) or str(e)
            failures.append(f"{f.name}: schema — {type(e).__name__}: {msg}")
    for m in failures:
        print("FAIL", m)
    print(f"{len(files)} fixtures, {len(failures)} failures")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 写 16 个 fixture 与 `README.md`**

| 文件 | 必须覆盖 |
|---|---|
| `value__spec_example.json` | 规格 §6.1 示例（真实比赛数据） |
| `value__low_confidence.json` | `confidence:"low"`、`n_samples < 30` |
| `policy__spec_example.json` | 规格 §6.2 示例（前 12 手真实 BP） |
| `policy__no_model.json` | `baseline.model_top1 = null` |
| `playbook__spec_example.json` | 规格 §6.3 示例 |
| `playbook__draft_incomplete.json` | `data_quality.n_pending_draft > 0` 且 `n_unavailable_draft > 0` |
| `playbook__anomalous.json` | `data_quality.n_anomalous_draft > 0` |
| `playbook__positions_phase_b.json` | `notes[].value=null` + `reason="needs_replay"` + **有** `needs` |
| `playbook__op_insufficient.json` | `recommendation="insufficient_data"`、`if_we_ban` 降级且**无** `needs` |
| `profile__spec_example.json` | 规格 §6.4 示例（`window_games=48`） |
| `profile__map_vision_counts_only.json` | `map_vision` 返回真实 `percentile`（非降级），同时 ward 坐标条目降级 |
| `profile__position_unknown.json` | 五个位置维度降级，**`hero_archetype` 仍返回六项且和为 1.0** |
| `advise__realtime.json` | `options[]` 形状 |
| `advise__offline.json` | `mode:"offline"` → `branches[].plans[]`（非 `options[]`） |
| `advise__robustness_penalized.json` | 含 `robustness_delta > 0.10` 的项，`penalized_score < expected_wr`、带 `risk_note`、排序被降 |
| `error__insufficient_data.json` | `error.code="insufficient_data"` |
| `error__source_not_allowed.json` | `error.code="source_not_allowed"` |

**`contracts/fixtures/README.md` 必须写明 HTTP 状态码映射**——fixture 文件体只承载响应体，JSON 无法表达状态码：

| fixture 前缀 | HTTP 状态 |
|---|---|
| `value__*` / `policy__*` / `playbook__*` / `profile__*` / `advise__*` | 200 |
| `error__insufficient_data` | **200**（业务不足，非传输错误） |
| `error__source_not_allowed` | 403 |

机器校验状态码需上移到集成测试（计划 2+）。

- [ ] **Step 5: 运行校验**

Run: `make contract && pytest tests/contracts/test_fixtures.py -q`
Expected: `16 fixtures, 0 failures`；**1 passed**

- [ ] **Step 6: Commit**

```bash
git add contracts/
git commit -m "feat(contracts): 16 个边界 fixtures + 校验工具 + 状态码映射说明"
```

---

### Task 8: TS 类型生成与 OpenAPI 文档校验（M0 收尾）

**Files:**
- Create: `contracts/tools/gen_ts_types.py`
- Create: `web/src/types/contract.ts`（生成物，提交入库）
- Test: `tests/contracts/test_ts_types_fresh.py`

- [ ] **Step 1: 写 `contracts/tools/gen_ts_types.py`**

`datamodel-code-generator` **没有 TypeScript 后端**（只生成 Pydantic/dataclasses/TypedDict/msgspec），故手写生成器。契约结构简单（枚举 + 固定对象），手写更可控且无额外依赖。

```python
"""从 openapi.yaml 生成 web/src/types/contract.ts。覆盖全部枚举与资源名。"""
from __future__ import annotations
import pathlib, sys
import yaml

ROOT = pathlib.Path(__file__).parents[1]
OUT = ROOT.parent / "web" / "src" / "types" / "contract.ts"

HEADER = ("// AUTO-GENERATED from contracts/openapi.yaml — do not edit.\n"
          "// Regenerate: make contract-ts\n\n")

# 契约里必须存在、且前端需要引用的枚举；缺失即报错
# （防止契约删字段而前端静默退化）
REQUIRED_ENUMS = ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                  "UnavailableReason", "ErrorCode", "Side"]

def ts_type(name: str, schema: dict) -> str | None:
    enum = schema.get("enum")
    if not enum:
        return None
    if schema.get("type") == "string":
        body = " | ".join(f'"{v}"' for v in enum)
        return f"export type {name} = {body};\n"
    if schema.get("type") == "integer":
        return f"export type {name} = {' | '.join(str(v) for v in enum)};\n"
    return None

def main() -> int:
    doc = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    missing = [n for n in REQUIRED_ENUMS if n not in schemas]
    if missing:
        raise SystemExit(f"契约缺少必需枚举: {missing}")
    parts = [HEADER]
    for name in sorted(schemas):
        t = ts_type(name, schemas[name])
        if t:
            parts.append(t)
    parts.append("\n/** 资源名 → 契约组件名 */\n"
                 "export type Resource = 'Value' | 'Policy' | 'Playbook' | 'Profile' | 'Advise';\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写新鲜度与完整性测试**

```python
import pathlib, subprocess, sys

def test_generated_ts_is_up_to_date():
    p = pathlib.Path("web/src/types/contract.ts")
    before = p.read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", "contracts.tools.gen_ts_types"], check=True)
    assert before == p.read_text(encoding="utf-8"), "contract.ts 已过期，运行 make contract-ts 并提交"

def test_ts_contains_all_required_enums():
    """生成器漏掉 §6.0 的枚举时失败。"""
    ts = pathlib.Path("web/src/types/contract.ts").read_text(encoding="utf-8")
    for name in ["Confidence","Recommendation","TheirOpening","NoteKind",
                 "UnavailableReason","ErrorCode","Side"]:
        assert f"export type {name} =" in ts, f"{name} 未生成"
```

- [ ] **Step 3: 生成并运行**

Run: `make contract-ts && pytest tests/contracts -q`
Expected: `wrote .../web/src/types/contract.ts`；**17 passed**

- [ ] **Step 4: 校验 OpenAPI 文档本身（M0 验收第 1 条）**

Run: `make lint-spec`
Expected: 无输出（通过即静默）

- [ ] **Step 5: 全量测试**

Run: `make db-reset && make test`
Expected: **全部 passed**（约 46 个）

- [ ] **Step 6: Commit（M0 完成）**

```bash
git add contracts/ web/ tests/
git commit -m "feat(contracts): TS 类型生成 + 完整性测试 + OpenAPI 校验 —— M0 完成"
```

**M0 验收对照**：

| 规格 §14 M0 条款 | 落地位置 |
|---|---|
| `contracts/openapi.yaml` 提交且通过 schema 校验 | Task 8 Step 4 `make lint-spec` |
| 16 个 fixtures 各自通过契约校验 | Task 7 Step 5 `make contract` |
| 三条线的目录边界建立 | Task 1 Step 7（`analysis/`、`models/`、`web/` 均已创建） |
## Chunk 3: 数据地基（M1）

### Task 9: 常量层——英雄、道具与原型映射

**Files:**
- Create: `constants/__init__.py`, `constants/dotaconstants.py`, `constants/archetypes.py`
- Create: `constants/_http.py`（共用带自定义 UA 的 httpx 客户端）
- Create: `tests/constants/__init__.py`
- Test: `tests/constants/test_dotaconstants.py`, `tests/constants/test_archetypes.py`

- [ ] **Step 1: 写失败测试（断言规格 §16.3 的实测数字）**

```python
from constants.dotaconstants import fetch_heroes, fetch_items, derive_token_index

def test_hero_count_is_127():
    assert len(fetch_heroes()) == 127

def test_item_count_is_501():
    assert len(fetch_items()) == 501

def test_exactly_nine_hero_ids_exceed_126():
    ids = sorted(h["id"] for h in fetch_heroes())
    over = [i for i in ids if i > 126]
    assert over == [128,129,131,135,136,137,138,145,155]

def test_token_index_is_dense_and_zero_based():
    m = derive_token_index(fetch_heroes())
    assert sorted(m.values()) == list(range(127))

def test_hero_135_maps_to_121():
    """规格 §16.2 用真实比赛验证过的具体映射。"""
    assert derive_token_index(fetch_heroes())[135] == 121

def test_ten_items_have_null_dname():
    items = fetch_items()
    assert sum(1 for i in items if not i.get("dname")) == 10

def test_archetype_mapping_is_total():
    """规格 §16.3：127 个英雄零遗漏。"""
    from constants.archetypes import archetypes_for
    heroes = fetch_heroes()
    missing = [h["localized_name"] for h in heroes if not archetypes_for(h["roles"])]
    assert missing == [], f"零命中英雄: {missing}"

def test_eight_historical_heroes_now_hit_teamfight():
    from constants.archetypes import archetypes_for
    by_id = {h["id"]: h for h in fetch_heroes()}
    for hid in [11,22,35,72,76,105,135,138]:
        assert "teamfight" in archetypes_for(by_id[hid]["roles"])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants -q`
Expected: FAIL — `No module named 'constants.dotaconstants'`

- [ ] **Step 3: 实现**

`constants/_http.py`：一个 httpx 客户端工厂，**必须带自定义 User-Agent**——OpenDota 对 `python-urllib`/默认 httpx UA 返回 403（实测）。UA 从 `LIQUIPEDIA_USER_AGENT` 同风格的常量取，或写死 `dota2-draft-analysis/0.1`。

`constants/dotaconstants.py`：
- `fetch_heroes()` → `GET /api/constants/heroes`（返回以 hero_id 为键的 dict，转成 list）
- `fetch_items()` → `GET /api/constants/items`
- `derive_token_index(heroes)` → **按 `hero_id` 升序**排序取下标（规格 §5.1 的 dense_index 派生规则）

`constants/archetypes.py`：实现规格 §7 维度 6 的映射表。**注意 roles 词表实测只有 8 项**（`Carry, Disabler, Durable, Escape, Initiator, Nuker, Pusher, Support`），**没有 `Jungler`**，也没有任何一项等于六类原型名。`teamfight` 必须是**析取式**：

```python
RULES = [
    ("initiate",   lambda r: "Initiator" in r),
    ("push",       lambda r: "Pusher" in r),
    ("pickoff",    lambda r: "Escape" in r and "Nuker" in r),
    ("splitpush",  lambda r: "Carry" in r and "Escape" in r and "Pusher" not in r),
    ("protect",    lambda r: "Support" in r and ("Nuker" in r or "Durable" in r)),
    ("teamfight",  lambda r: "Durable" in r or "Disabler" in r
                             or ("Carry" in r and "Nuker" in r)),
]

def archetypes_for(roles: list[str]) -> list[str]:
    r = set(roles)
    return [name for name, pred in RULES if pred(r)]
```

**上一版用的是合取式**（`teamfight = Durable AND (Nuker OR Disabler)`），会让 8 个英雄零命中（11/22/35/72/76/105/135/138），其中 105 与 135 分别出现在契约示例与参考比赛里，且 `Σ=0` 会导致归一化除零。

`derive_token_index(heroes)`：按 `hero_id` 升序排序取下标。

`constants/archetypes.py`：实现规格 §7 维度 6 的映射表（析取式 `teamfight`）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/constants -q`
Expected: **8 passed**

- [ ] **Step 5: Commit**

```bash
git add constants/ tests/constants/
git commit -m "feat(constants): 英雄/道具常量 + token 索引派生 + 原型映射（实测零遗漏）"
```

---

### Task 10: 版本表——Valve 权威清单 + 子版本还原

规格 §16.3 记录了一个**用错规则会导致最大 47 天偏差**的陷阱。本任务把它变成受测代码。

**Files:**
- Create: `constants/patches.py`
- Test: `tests/constants/test_patches.py`

- [ ] **Step 1: 写失败测试（锁死字母映射规则）**

```python
from constants.patches import fetch_valve_patches, restore_subpatch_dates

def test_valve_list_has_118_versions_and_84_lettered():
    ps = fetch_valve_patches()
    assert len(ps) == 118
    assert sum(1 for p in ps if any(c.isalpha() for c in p["patch_number"])) == 84

def test_dates_start_at_b_not_a():
    """规格 §3.2：dates[] 从 'b' 起。用 add 存在的 7.41 序列验证。"""
    m = restore_subpatch_dates({"code":"7.41","main":1,"add":2,"dates":[3,4,5]})
    assert set(m) == {"7.41","7.41a","7.41b","7.41c","7.41d"}
    assert m["7.41a"] == 2 and m["7.41b"] == 3

def test_dates_start_at_b_even_without_add():
    """add 缺失时 dates[0] 仍是 'b' —— 这是错位 bug 最容易漏掉的分支。"""
    m = restore_subpatch_dates({"code":"7.32","main":1,"dates":[3,4,5]})
    assert "7.32a" not in m
    assert m["7.32b"] == 3 and m["7.32d"] == 5

def test_reference_match_subpatch_is_7_41e():
    """规格 §16.3：start_time 1789301470 必须落在 7.41e 而非 7.41f。"""
    from constants.patches import subpatch_for_timestamp
    assert subpatch_for_timestamp(1789301470) == "7.41e"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants/test_patches.py -q`
Expected: FAIL — `No module named 'constants.patches'`

- [ ] **Step 3: 实现并入库**

实现三个函数，跑 `valve × patchdates` 交叉校验：偏差 > ±2 天记录告警并以 Valve 为准；`7.22` 与 `7.25` 两个已知例外单独处理。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/constants/test_patches.py -q`
Expected: **4 passed**

- [ ] **Step 5: Commit**

```bash
git add constants/patches.py tests/constants/test_patches.py
git commit -m "feat(constants): 版本表（Valve 权威 + 子版本还原，锁死 dates 从 b 起的规则）"
```

---

### Task 11: Kaggle 引导数据集（选择性下载 506 MB 而非 48.5 GB）

**Files:**
- Create: `ingest/kaggle_subset.py`
- Create: `ingest/load_bootstrap.py`
- Test: `tests/ingest/test_bootstrap.py`

- [ ] **Step 1: 先验证列名（规格 §17 第 7 项的唯一前置）**

```python
def test_picks_bans_columns_and_order_origin(sample_csv):
    """规格 §17-7：必须确认 order 从 0 起，否则模板映射整体错位一位。"""
    import csv
    with open(sample_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {"match_id","order","is_pick","team","hero_id"} <= set(rows[0])
    assert min(int(r["order"]) for r in rows) == 0, "order 从 1 起，模板映射会错位"
```

若该断言失败，**停下来**：说明数据集用 1-based，全部 `ord` 需减 1，且要回到规格 §6.0 确认模板语义。

- [ ] **Step 2: 写下载脚本（按文件白名单）**

只下 `draft_timings.csv`、`picks_bans.csv`、`main_metadata.csv`（合计 506 MB）。**绝不整包下载**（`players.csv` 单文件 41 GB）。需 `KAGGLE_USERNAME`/`KAGGLE_KEY`，从 `.env` 读。

- [ ] **Step 3: 写入库脚本**

注意：`draft_timings.csv` 只在 19 个年度目录中的 10 个存在。**以 `picks_bans.csv` 为 BP 序列的权威来源**。

- [ ] **Step 4: 写异常率测试**

```python
def test_anomaly_rate_under_2_percent(db_after_bootstrap):
    """规格 §15：异常率 < 2%，且偏离的场次被记入 draft_anomalies。"""
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    anom = db_after_bootstrap.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0]
    assert total > 10000
    assert anom / total < 0.02
    assert db_after_bootstrap.execute("SELECT count(*) FROM draft_anomalies").fetchone()[0] == anom
```

- [ ] **Step 5: 运行**

Run: `python -m ingest.kaggle_subset && python -m ingest.load_bootstrap && pytest tests/ingest -q`
Expected: 全部 passed

- [ ] **Step 6: Commit**

```bash
git add ingest/ tests/ingest/
git commit -m "feat(ingest): Kaggle 子集下载（506MB）+ 引导入库 + 异常率校验"
```

---

### Task 12: M1 验收

- [ ] **Step 1: 写验收脚本 `tests/test_m1_acceptance.py`**

```python
def test_m1_acceptance(db_after_bootstrap):
    # 英雄 = 127，道具 = 501
    assert db_after_bootstrap.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db_after_bootstrap.execute("SELECT count(*) FROM items").fetchone()[0] == 501
    # 版本表含 84 个字母子版本（来自 Valve）
    n = db_after_bootstrap.execute(
        "SELECT count(*) FROM patches WHERE version_name ~ '[a-z]$'").fetchone()[0]
    assert n == 84, f"字母子版本数 {n} != 84（规格 §16.3：该数字只能来自 Valve）"
    # 引导数据集入库且异常率 < 2%
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    assert total > 10000
    # token 索引完备
    assert db_after_bootstrap.execute(
        "SELECT count(*) FROM hero_token_index WHERE snapshot_version = "
        "(SELECT max(snapshot_version) FROM constants_snapshot)").fetchone()[0] == 127
```

- [ ] **Step 2: 运行**

Run: `pytest tests/test_m1_acceptance.py -q`
Expected: **1 passed**

- [ ] **Step 3: Commit**

```bash
git add tests/test_m1_acceptance.py
git commit -m "test: M1 验收（127 英雄 / 501 道具 / 84 字母版本 / 异常率 / token 索引）"
```

---

## 完成后的状态

**已解锁**：三条 worktree 可并行启动
- 线 A：`db/` + `contracts/` 就绪 → 开始计划 3（画像与剧本引擎）
- 线 C：`contracts/` + 引导数据集就绪 → 开始计划 5（序列模型）
- 前端：`contracts/fixtures/` 就绪，**不需要数据库** → 开始计划 4（可视化）

**未做**（属后续计划）：常驻采集器（M2）、回放导入（M2b）、画像引擎（M3–M5）、可视化（M6）、序列模型（M7–M9）、部署（M10）。

**规格 §17 中仍未验证、但本计划已设测试保护的两项**：Kaggle CSV 的列名与 `order` 起始值（Task 10 Step 1 会直接失败并停下）。
