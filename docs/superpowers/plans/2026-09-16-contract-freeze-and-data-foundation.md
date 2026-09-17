# 契约冻结与数据地基 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 `contracts/openapi.yaml` 与 17 个边界 fixtures（解锁三条并行 worktree），并建成可查询的数据地基（常量层、含字母子版本的版本表、引导数据集入库）。

**Architecture:** 单一 Postgres 实例承载全部关系数据；契约以 OpenAPI 3.1 为唯一权威源，fixtures 与 TS 类型均从它生成并由测试校验。常量层拆为「规范实体（`heroes`/`items`）+ 版本化 token 索引（`hero_token_index`）」，token 索引按快照冻结以防常量刷新时静默重映射。数据来源三类：Valve 官方补丁 feed（权威版本清单）、dotaconstants（英雄/道具）、Kaggle CC0 数据集（历史 BP 引导数据，仅下载 506 MB 子集）。

**Tech Stack:** Python 3.12 · PostgreSQL 16 · Docker Compose · pytest · jsonschema + referencing · httpx · openapi-spec-validator

**Spec:** `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md`

---

## 前置说明

**本计划只做 M0 + M1。** 采集器（M2）、回放导入（M2b）、画像引擎（M3–M5）、可视化（M6）、序列模型（M7–M9）、部署（M10）各由独立计划覆盖。

**三块结构**：

| Chunk | 内容 | 里程碑 | 任务 |
|---|---|---|---|
| 1 | 骨架、数据模型与共享模板 | M0 前半 | Task 1–4 |
| 2 | 契约本身（五资源 schema + 17 fixtures） | M0 后半 | Task 5–8 |
| 3 | 数据地基（常量入库 + 引导数据集） | M1 | Task 9–13 |

**依赖方向**：Chunk 2 只依赖 Chunk 1 的仓库骨架；Chunk 3 依赖 Chunk 1 的 DDL 与 `conftest.py`。三块中 **Chunk 1 → Chunk 2** 是关键路径（交付 fixture 后前端即可开工），Chunk 3 可与 Chunk 2 并行。

**交付后即可并行的三条 worktree：**
- 线 A（画像引擎）→ `analysis/`：依赖 `db/` + `contracts/`（即 Chunk 1 + 2 + 3）
- 线 C（序列模型）→ `models/`：依赖 `contracts/` + `shared/draft_template.py`。
  **不需要数据库**——引导数据集与模板足以训练与评估
- 前端 → `web/`：依赖 `contracts/fixtures/`（即 Chunk 1 + 2），**不需要数据库、不需要 Chunk 3**

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

# 必需：仓库是扁平布局（db/ web/ shared/ models/ analysis/ contracts/ 全在顶层），
# 不声明包发现规则时 setuptools 会以
#   error: Multiple top-level packages discovered in a flat-layout
# 中止，`pip install -e ".[dev]"` 直接失败，后续所有 import 无从谈起。
[tool.setuptools.packages.find]
include = ["db*", "shared*", "contracts*", "analysis*", "models*"]

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

规格 §16.1 已证明这段 DDL 在 PostgreSQL 16 上可执行（22 张表）。本任务把它变成可重复执行的迁移，并给那 15 条约束探针补上回归保护（实现后为 16 条测试：匿名选手那条按「唯一性」与「可共存」拆为两条）。

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

def _test_dsn_from(base: str) -> str:
    """把 <db> 派生成 <db>_test，保留查询串。

    必须先把 "?" 之后切掉再拼后缀：否则
    `.../dota?sslmode=disable` 会推出 `dota?sslmode=disable_test`，
    PG 报 `invalid sslmode value: "disable_test"`。
    """
    dsn, sep, query = base.partition("?")
    head, dbname = dsn.rsplit("/", 1)
    return f"{head}/{dbname}_test{sep}{query}"

@pytest.fixture(scope="session")
def dsn() -> str:
    """每 session 重建 <db>_test 并跑迁移，使测试不污染开发库（规格 §15）。

    必须 DROP + CREATE，不能只在库不存在时创建：否则只要 ledger 里已有
    001_schema.sql，对该迁移的任何后续修改都不会被应用，测试会**静默**跑在
    旧 schema 上（改了 DDL 却依然全绿）。Task 3 起会频繁改 schema，这个坑
    必然再踩，故每次 session 都从零重建以换取「schema 永远对应当前迁移」。

    三点取舍（有意为之，不是疏忽）：
    - WITH (FORCE) 需要 PG 13+（本机 16.14）。它会**踢掉连到该库的其他会话**，
      因此两个 pytest 会话并行跑会互相 DROP/CREATE。当前依赖**串行执行**；
      将来若要并行（pytest-xdist），必须改为每 worker 一个库名。
    - DROP/CREATE 每次 session 多花几百毫秒，换来"schema 永远对应当前迁移"。
    - **库名必须以 `_test` 结尾**，否则 pytest.exit 中止整个会话。本 fixture 会
      无条件 DROP 目标库，而 TEST_DATABASE_URL 恰恰是人手设置、最容易打错的
      旋钮：打错到真库上不会报错，只会静默销毁它。护栏宁可误拒，不可误删。
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        base = _test_dsn_from(_base_dsn())
    # 查询串（?sslmode=... 等）必须在解析库名之前切掉：否则 test_name 会变成
    # 'dota_test?sslmode=disable'，既过不了下面的 _test 护栏，也会让 ident 带上 '?'。
    dsn_only, sep, query = base.partition("?")
    head, test_name = dsn_only.rsplit("/", 1)
    # 库名是标识符，不能参数化（%s 只能用在值的位置），故手工转义双引号
    ident = test_name.replace('"', '""')
    # 护栏必须在 DROP 之前：这是配置错误，应整体停下并给出可操作提示，
    # 而不是让每个测试各报一个莫名其妙的断言失败。
    if not test_name.endswith("_test"):
        pytest.exit(
            f"拒绝操作测试库 {test_name!r}：库名必须以 '_test' 结尾。"
            f"本 fixture 会 DROP + CREATE 该库，误配会静默销毁真实数据库。"
            f"请检查 TEST_DATABASE_URL / DATABASE_URL。",
            returncode=1,
        )
    with psycopg.connect(f"{head}/postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{ident}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{ident}"')
    from db.migrate import apply
    apply(base)
    return base   # 保留查询串，供测试连接使用

@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn) as conn:
        yield conn
        conn.rollback()

@contextmanager
def expect_violation(conn, sqlstate: str | None = None,
                     constraint: str | tuple[str, ...] | None = None):
    """断言语句违反约束，并回滚到保存点使事务可继续。

    必需：psycopg 在约束违规后事务进入 aborted 状态，
    后续任何语句都会以 'current transaction is aborted' 失败。

    sqlstate / constraint：钉死"拒绝到底来自哪条约束"。都不传时接受任何
    psycopg.Error——**这是个很宽的网**：一条被别的约束拒绝的语句同样能让
    with 块通过，于是测试名声称在守护 X、实际什么都没守护。本项目已实测踩到
    这一点（一条声称守护 (match_id, player_slot) 主键的测试，实际可以是被
    FK 拒绝）。故**所有负向测试都必须显式传 sqlstate**；同一 SQLSTATE 下有
    歧义风险时再传 constraint。

    constraint 收字符串或字符串元组：PG 对具名约束报约束名，对**部分唯一
    索引**报索引名（如 rosters 的 left_at IS NULL 那条），两者形式不同。
    """
    conn.execute("SAVEPOINT sp")
    try:
        yield
    except psycopg.Error as exc:
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        if sqlstate is not None and exc.sqlstate != sqlstate:
            pytest.fail(
                f"期望 SQLSTATE {sqlstate}，实际为 {exc.sqlstate}：{exc}"
            )
        if constraint is not None:
            allowed = (constraint,) if isinstance(constraint, str) else constraint
            actual = exc.diag.constraint_name
            if actual not in allowed:
                pytest.fail(f"期望约束 {allowed}，实际为 {actual!r}：{exc}")
    except BaseException:
        # 非 psycopg 异常（例如测试里调用的辅助函数抛 KeyError）也必须回滚，
        # 否则块内的写入会留在事务里可见，污染后续断言。
        conn.execute("ROLLBACK TO SAVEPOINT sp")
        raise
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

def test_duplicate_player_slot_is_rejected(db, seeded):
    """规格 §5.1：主键是 (match_id, player_slot)。同一场同一 slot 不能有两行。

    必须钉住 constraint="match_players_pkey"，光钉 23505 不够：本测试存在多种
    都能骗过 SQLSTATE-only 版本的变形——把主键换成 UNIQUE(match_id, player_slot)、
    换成三列唯一、或给主键改名，SQLSTATE 都仍是 23505 而测试照绿，于是规格明确
    论证过的那个主键其实无人守护（实测：这几种变形下仅钉 SQLSTATE 均为 16 passed）。

    先插入 players(111) 是为了让**唯一**被违反的约束就是主键：若该 account 不存在，
    同一条语句会同时踩 FK 与主键，PG 只报其一，剔除 FK 变量才能确认拒绝来自主键。
    """
    db.execute("INSERT INTO players(account_id, name) VALUES (111, 'p')")
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80)""")
    with expect_violation(db, sqlstate="23505", constraint="match_players_pkey"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,3,111,0,80)""")   # 同 slot，不同 account

def test_two_anonymous_players_coexist(db, seeded):
    """匿名选手（account_id IS NULL）在不同 slot 上必须都能入库。"""
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80), (1,8,NULL,1,80)""")
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=1 AND account_id IS NULL"
    ).fetchone()[0] == 2

def test_raw_mod_128_normalization_is_rejected(db, seeded):
    """规格 §16.2：raw%128 把 Dire 的 128..132 塌缩成 0..4。

    已钉住约束名（见下），故此处 23514 来自 slot_team_agree。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="slot_team_agree"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,0,NULL,1,80)""")

def test_slot_team_mismatch_is_rejected(db, seeded):
    """slot 的 0-4/5-9 分段必须与 team 自洽（与 raw%128 同为 slot_team_agree 守护）。

    已钉住约束名（见下），故此处 23514 来自 slot_team_agree。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="slot_team_agree"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,5,NULL,0,80)""")

def test_metric_weights_rejects_undefined_metric(db, seeded):
    """规格 §7：只认六个指标键；'laning' 不是其中之一。

    已钉住约束名（见下），故此处 23514 来自 metric_weights_metric_check。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="metric_weights_metric_check"):
        db.execute("INSERT INTO metric_weights VALUES ('laning','pro_match',1.0,NULL)")

def test_metric_weights_accepts_all_six_spec_keys(db, seeded):
    for m in ["patch_strength","hero_pool","system_pref","bp_tendency","tempo","map_vision"]:
        db.execute("INSERT INTO metric_weights VALUES (%s,'pro_match',1.0,NULL)", (m,))

def test_data_source_rejects_undefined_value(db, seeded):
    """规格 §5.4：只有 pro_match/pub_match/scrim 三种来源，'ranked' 不合法。

    已钉住约束名（见下），故此处 23514 来自 matches_data_source_check。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="matches_data_source_check"):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'ranked',now(),'complete')""")

def test_draft_state_rejects_undefined_value(db, seeded):
    """状态机只有 pending/complete/unavailable，'partial' 不合法。

    已钉住约束名（见下），故此处 23514 来自 matches_draft_state_check。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="matches_draft_state_check"):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'pro_match',now(),'partial')""")

def test_draft_actions_rejects_unknown_hero(db, seeded):
    """hero_id 外键：不存在的英雄必须被 FK 拒绝（不是 CHECK）。

    已钉住约束名（见下），故此处 23503 来自 draft_actions_hero_id_fkey。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23503", constraint="draft_actions_hero_id_fkey"):
        db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,999)")

def test_draft_actions_rejects_ord_out_of_range(db, seeded):
    """24 手模板：ord 合法区间是 0..23。

    已钉住约束名（见下），故此处 23514 来自 draft_actions_ord_check。
    取舍：约束名由 PG 自动生成，改名会让本测试变红——接受这种耦合，
    因为「目标约束真的被触发」只有靠约束名才能验证（详见计划文档
    「每条负向测试必须钉死拒绝来源」一节）。"""
    with expect_violation(db, sqlstate="23514", constraint="draft_actions_ord_check"):
        db.execute("INSERT INTO draft_actions VALUES (1,24,false,0,80)")

def test_rosters_allows_null_joined_at(db, seeded):
    """两条断言：joined_at 可空能入库（Liquipedia 常缺 joindate），
    但同队同选手不允许第二条 left_at IS NULL。

    此处的唯一性由**部分唯一索引** rosters_team_id_account_id_idx 提供，
    故 PG 报的是索引名而非约束名——这正是 constraint 参数要收元组/索引名的原因。
    """
    db.execute("INSERT INTO players(account_id,name) VALUES (111,'p')")
    db.execute("INSERT INTO teams(team_id,name) VALUES (10,'A')")
    db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,NULL,'liquipedia')")
    with expect_violation(db, sqlstate="23505",
                          constraint="rosters_team_id_account_id_idx"):
        db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,'2020-01-01','liquipedia')")

def test_hero_token_index_rejects_duplicate_dense_index(db, seeded):
    """同快照内两个英雄不能占用同一 dense_index。"""
    db.execute("""INSERT INTO heroes(hero_id,name,localized_name) VALUES (81,'npc_dota_hero_x','X')""")
    with expect_violation(db, sqlstate="23505", constraint="hero_token_index_pkey"):
        db.execute("INSERT INTO hero_token_index VALUES (1, 81, 73)")

def test_hero_token_index_rejects_double_index_for_same_hero(db, seeded):
    """同一英雄在同快照内不能有两个索引（UNIQUE(snapshot_version, hero_id)）。"""
    with expect_violation(db, sqlstate="23505",
                          constraint="hero_token_index_snapshot_version_hero_id_key"):
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

**⚠ 原文这条路子已实测推翻，不要再照抄。** 原文说"同时删掉 PRIMARY KEY 与
slot_team_agree，预期 2 failed, 13 passed"——数字对不上，且理由也不是它说的那个。
下面三个实验的预期值**全部经过实测**（PG 16.14，`pytest tests/db -q`）：

```bash
# 实验 A（验证 slot_team_agree）——链式反应：它同时守护 raw%128 探针。
#   删掉 `CONSTRAINT slot_team_agree CHECK (...)` 这一行，
#   并把 `PRIMARY KEY (match_id, player_slot),` 的尾逗号去掉（它成为最后一个约束）。
#   实测：2 failed, 14 passed
#     test_raw_mod_128_normalization_is_rejected
#     test_slot_team_mismatch_is_rejected
#   注意：raw%128 那条也是靠 slot_team_agree 拒绝的（128%128=0 → slot 0 与 team 1
#   不自洽），所以删掉该约束会同时打红两条——这是正确的连锁，不是误报。

# 实验 B1（只删主键）——**行不通：得到 16 个 error，不是 failure**。
#   只删 `PRIMARY KEY (match_id, player_slot),`（尾逗号处理见原文说明）保留其余。
#   item_timings 有 FK `(match_id, player_slot) REFERENCES match_players(...)`，
#   主键一没，FK 就失去唯一约束依托，DDL 以
#     InvalidForeignKey: 没有唯一约束与关联表 "match_players" 给定的键值匹配
#   失败 → 迁移报错 → **16 errors**（实测确认；原文担心的语法错误路径同理，
#   都会落到"整批 error"，同样验证不到那条测试）。
#   这本身就是"pk 在守护 (match_id, player_slot) 唯一性"的实证：没有它，
#   连这条 FK 都建不起来。**故无法用"只删主键"来验证本测试。**

# 实验 B2（验证 (match_id, player_slot) 唯一性）——把唯一性真正抽走：
#   删掉 `PRIMARY KEY (match_id, player_slot),`
#   并删掉 item_timings 的 `FOREIGN KEY (match_id, player_slot) REFERENCES ...` 两行
#   （该 FK 依赖此唯一性；无任何测试插入 item_timings，故移除它不放松被测断言）
#   实测：1 failed, 15 passed —— 唯一失败者正是
#     test_duplicate_player_slot_is_rejected
#   这条即**真正的回归保护**：唯一性一消失，它立刻变红。

# 实验 C（验证 dsn 不陈旧）——不需要改 DDL 语义：
#   往 001_schema.sql 末尾追加 `CREATE TABLE staleness_sentinel (id INT PRIMARY KEY);`
#   直接重跑 pytest（**不手动 drop 库**），然后查 postgres_test 里该表存在 → 证明新
#   DDL 已被应用；再 git checkout 还原并重跑，该表消失。
#   实测：0 → 1 →（还原后）0 ✓

# 全部实验结束后：git checkout -- db/migrations/001_schema.sql && pytest tests/db -q
#   实测：16 passed ✓
```

**附加要求（实测教训，别再踩）**：`test_duplicate_player_slot_is_rejected` 里的
第二个 `account_id` 必须**先存在于 `players`**。否则该语句会被 FK（23503）拒绝，
`expect_violation` 照样通过，测试就变成恒绿的空断言——第一版正是这么写的，
实测"删掉主键仍然绿"。因此该测试显式传 `sqlstate="23505"`，把"拒绝来自
`(match_id, player_slot)` 唯一性"钉死。

#### 每条负向测试必须钉死"拒绝来自哪条约束"

`expect_violation` 不传参数时接受**任何** `psycopg.Error`，这是个很宽的网：一条被
FK 拒绝的语句同样能让 `with` 块通过，于是测试名声称在守护 X、实际什么都没守护。
本项目已实测踩到（一条声称守护 `(match_id, player_slot)` 主键的测试，实际可以被
FK 拒绝而恒绿）。

**结论：11 条负向测试全部同时钉住 `sqlstate` 与 `constraint`**（下表为实测值，
由逐条探测 `exc.sqlstate` 与 `exc.diag.constraint_name` 得到，不是照抄标准）。

**为什么连 `constraint` 也钉——实测证明必须钉。** 下列两个变形都**保持 DDL 合法**
（唯一性仍在，故 `item_timings` 的 FK 仍满足），且 SQLSTATE 仍是 23505：只看
SQLSTATE 时它们**全部 16 passed**，即规格明确论证过的那个主键其实无人守护。

| 变形 | SQLSTATE | 只钉 sqlstate | 同时钉 constraint |
|---|---|---|---|
| 主键换成 `UNIQUE(match_id, player_slot)` | 23505 | 照绿 ❌ | **1 failed** ✅ |
| 给主键改名（`CONSTRAINT mp_pk PRIMARY KEY ...`） | 23505 | 照绿 ❌ | **1 failed** ✅ |

实测失败信息：`期望约束 ('match_players_pkey',)，实际为
'match_players_match_id_player_slot_key'`（变形 1）与 `实际为 'mp_pk'`（变形 2）。

> 注：另一个看似更隐蔽的变形"把主键换成三列唯一
> `UNIQUE(match_id, player_slot, account_id)`"其实**到不了断言**——它使
> `item_timings` 的 FK 失去 `(match_id, player_slot)` 唯一性依托，DDL 直接
> `InvalidForeignKey`，得到 **16 errors**（与实验 B1 同因）。故它不构成对
> SQLSTATE-only 版本的绕过，列在这里只为避免后人重复踩。

**代价（有意接受）**：约束名由 PG 自动生成，**改名会让这些测试变红**。这是刻意
选择的耦合方向——"目标约束真的被触发"只有靠约束名才能验证；而一个会让测试变红
的改名，恰恰值得人来确认一次。

| 测试 | sqlstate | constraint |
|---|---|---|
| `test_duplicate_player_slot_is_rejected` | `23505` | `match_players_pkey` |
| `test_raw_mod_128_normalization_is_rejected` | `23514` | `slot_team_agree` |
| `test_slot_team_mismatch_is_rejected` | `23514` | `slot_team_agree` |
| `test_metric_weights_rejects_undefined_metric` | `23514` | `metric_weights_metric_check` |
| `test_data_source_rejects_undefined_value` | `23514` | `matches_data_source_check` |
| `test_draft_state_rejects_undefined_value` | `23514` | `matches_draft_state_check` |
| `test_draft_actions_rejects_unknown_hero` | `23503` | `draft_actions_hero_id_fkey` |
| `test_draft_actions_rejects_ord_out_of_range` | `23514` | `draft_actions_ord_check` |
| `test_rosters_allows_null_joined_at`（后半段） | `23505` | `rosters_team_id_account_id_idx` |
| `test_hero_token_index_rejects_duplicate_dense_index` | `23505` | `hero_token_index_pkey` |
| `test_hero_token_index_rejects_double_index_for_same_hero` | `23505` | `hero_token_index_snapshot_version_hero_id_key` |

两个易错点：
- **未命名约束的自动命名**：`CHECK (metric IN (...))` 这类内联约束 PG 会命名为
  `<表>_<列>_check`；`CREATE UNIQUE INDEX` 报的是**索引名**而非约束名
  （故 `rosters` 那条是 `..._idx`，`constraint` 参数因此也接受索引名）。
- **同一 SQLSTATE 下的歧义**：`hero_token_index` 两条测试都是 `23505`，靠
  `constraint` 区分主键与 `UNIQUE(snapshot_version, hero_id)`。

破坏-恢复验证（各 1 failed / 15 passed，失败者即目标测试）：
- 删 `(match_id, player_slot)` 唯一性（连带删 `item_timings` 的 FK，见实验 B2）→
  `test_duplicate_player_slot_is_rejected` 红
- 把主键换成 `UNIQUE(match_id, player_slot)` → 该测试红（只钉 SQLSTATE 时为 16 passed）
- 把 `'laning'` 加进 `metric_weights` 的 CHECK 白名单 → 该测试红
- 只把 `metric_weights_metric_check` 改名（SQLSTATE 仍 `23514`）→ 该测试红

#### `dsn` 的两道安全护栏（DROP 是不可逆的）

`dsn` 会**无条件** `DROP DATABASE ... WITH (FORCE)` 后重建。`TEST_DATABASE_URL`
恰恰是人手设置、最容易打错的旋钮：打错到真库上**不会报错，只会静默销毁它**。
故加两道护栏，实测行为如下：

1. **库名必须以 `_test` 结尾**，否则 `pytest.exit`（配置错误应整体停下并给出可
   操作提示，而非让每个测试各报一个莫名其妙的断言失败）。
   实测：`TEST_DATABASE_URL=.../devdb`（含 `precious` 表）→ 会话中止、exit 1、
   `precious` 数据完好。反过来，`_test` 结尾的库**会**被 DROP 重建，这是设计意图。
2. **查询串必须先切掉再拼后缀**：否则 `.../dota?sslmode=disable` 会推出
   `dota?sslmode=disable_test`，PG 报 `invalid sslmode value: "disable_test"`。
   实测：`.../mydb?application_name=x` → 推导出 `mydb_test?application_name=x`
   （库名正确、查询串保留）；带查询串的非 `_test` 库被正确识别并拒绝。

**请实际执行一次这个反向验证再继续。**

- [ ] **Step 6: 运行确认通过**

Run: `make db-reset && pytest tests/db/test_constraints.py -q`
Expected: **16 passed**

- [ ] **Step 7: Commit**

```bash
git add db/ tests/
git commit -m "feat(db): §5.1 DDL 作为迁移 001 + 16 条约束测试（含 raw%128 与匿名选手回归保护）"
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

def test_resolve_sources_returns_sorted_deduped():
    assert resolve_sources(["scrim", "pub_match", "pro_match", "pub_match"],
                           allow_scrim=True) == ["pro_match", "pub_match", "scrim"]
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

Run: `make db-reset && python -c "import os,psycopg; d=os.environ.get('DATABASE_URL','postgresql://dota:dota@localhost:5432/dota'); print([r[0] for r in psycopg.connect(d).execute(\"SELECT viewname FROM pg_views WHERE viewname LIKE 'opponent_profile%'\")])"`
Expected: `['opponent_profile_matches', 'opponent_profile_matches_with_pub']`（顺序可能不同）

> 不要用 `psql "$DATABASE_URL"`：计划里没有任何步骤导出该变量（它只存在于 `.env.example`，而没有任何步骤 source 它），`psql` 会拿到空 conninfo 并报出误导性的 `database "<用户名>" does not exist`。上面的写法自带默认值，且不要求宿主机装 `psql`。

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/db -q`
Expected: **22 passed**（16 约束 + 6 隔离）

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

def test_resolve_mirrors_when_first_pick_team_is_one():
    """规格 §8①：两种情形精确互为镜像；队首为 1 时对手侧取 1-fpt=0。"""
    assert resolve(12, 1) == (True, 0)
    assert resolve(6, 1) == (False, 0)

def test_resolve_rejects_bad_first_pick_team():
    with pytest.raises(ValueError):
        resolve(0, 2)
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
    """由 ord=0 的 team 推出先手方（规格 §8①）。

    输入为项目内形状 {"ord": int, "team": int}。OpenDota 原始 payload 与
    Kaggle CSV 的字段名是 `order`（且可能为 1-based 字符串），必须在入库边界
    先归一化为 `ord = int(order) - 1`（计划 Task 12 Step 2），本函数不做类型转换。
    """
    for a in actions:
        if a["ord"] == 0:
            return int(a["team"])
    raise ValueError("actions 中缺少 ord=0，无法推出先手方")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/shared -q`
Expected: **11 passed**

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
- Test: `tests/contracts/test_openapi_fresh.py`

- [ ] **Step 1: 写 `tests/contracts/__init__.py` 与 `tests/contracts/conftest.py`**

`tests/contracts/__init__.py` 为空文件（使 `from tests.contracts... import ...` 可用）。

`tests/contracts/conftest.py`：

```python
"""提供 validator_for() —— 让子 schema 的 $ref 能正确解析。

**两种常见写法都不通，不要用**：
  ① validate(instance, subschema) —— #/components/... 的 $ref 在子 schema
     内无根可依，抛 referencing 的 PointerToNowhere，而它**不是**
     jsonschema.ValidationError，常规 except 与 pytest.raises 都抓不到。
  ② Draft202012Validator({"$ref": "#/..."}, registry=Registry().with_resource("", doc))
     —— referencing 的 resolver_with_root 会用 resource.id() or "" 重新注册，
     把包装器盖在文档之上，仍然 PointerToNowhere。且 Resource.from_contents(openapi_doc)
     会直接抛 CannotDetermineSpecification（OpenAPI 文档没有 $schema 字段）。

**可用写法**：把整个文档交给校验器，再用 evolve() 换 schema。
"""
from __future__ import annotations
import pathlib
import pytest, yaml
from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).parents[2] / "contracts"

@pytest.fixture(scope="session")
def contract_doc() -> dict:
    return yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))

@pytest.fixture(scope="session")
def validator_for(contract_doc):
    root = Draft202012Validator(contract_doc)
    def _make(name: str) -> Draft202012Validator:
        return root.evolve(schema=contract_doc["components"]["schemas"][name])
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
    Factor:   # §6.0：Value 的加项分解，与 metric_weights.metric 是不同枚举
      type: string
      enum: [patch_strength, counter_matchup, player_comfort, first_pick]
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

# 说明：本 chunk 只冻结**响应体**（components.schemas），paths 刻意留空。
# 请求体 schema 与路径定义属 Plan 2+ 的接口实现范围——冻结它们需要先确定
# 服务端框架与鉴权方式，现在冻结会产生返工。M0 的验收条款只要求
# components/schemas 通过校验，故本 chunk 的产物已满足。

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

def test_degraded_rejects_non_null_value(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Degraded").validate({"value": 0, "reason": "insufficient_samples"})

def test_error_codes_are_closed(validator_for):
    with pytest.raises(jsonschema.ValidationError):
        validator_for("ErrorEnvelope").validate({"error": {"code": "boom", "message": "x"}})

def test_all_seven_their_opening_values_are_defined(common):
    assert set(common["TheirOpening"]["enum"]) == {
        "teamfight","push","pickoff","splitpush","protect","initiate","unknown"}

def test_reason_enum_closure_via_ref(validator_for):
    """四个 unavailable_reason 都必须被接受（逐个守护枚举成员），且经
    Degraded.reason 的 $ref 拒绝非法值——把该 $ref 换成 {type: string} 会让本测试变红。"""
    v = validator_for("Degraded")
    for reason in ["needs_replay", "insufficient_samples",
                   "source_not_allowed", "stat_unavailable"]:
        payload = {"value": None, "reason": reason}
        if reason == "needs_replay":
            payload["needs"] = "Phase B"
        v.validate(payload)
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"value": None, "reason": "bogus"})
```

- [ ] **Step 4b: 写 `tests/contracts/test_openapi_fresh.py`（生成物新鲜度）**

```python
"""契约生成物新鲜度：committed openapi.yaml 必须等于 schemas/*.yaml 的合并结果。

比较**解析后的字典**，不比较文本——文本比较会绑定 PyYAML 的输出格式，
依赖升级即误报。也**不要**用 subprocess 调 main()：那会先覆写生成物再比较，
测试将恒绿，正好失去意义。
"""
from contracts.tools.build_openapi import build

def test_openapi_is_freshly_built_from_schemas(contract_doc):
    assert contract_doc == build(), (
        "contracts/openapi.yaml 与 contracts/schemas/*.yaml 不一致——"
        "运行 python -m contracts.tools.build_openapi 并提交生成物")
```

- [ ] **Step 5: 生成并运行**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: `wrote .../openapi.yaml with 11 schemas`；**6 passed**

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

- [ ] **Step 1: 写失败测试 `tests/contracts/test_invariants.py`**

（先写测试：`invariants.py` 尚不存在，Step 2 运行时应报
`ModuleNotFoundError: No module named 'contracts.tools.invariants'`。）

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
                                    "penalized_score":0.52,
                                    "key_picks":[{"hero_id":105,"fallback":[67]}]}]}]}
    assert check_advise(body) == []

def test_playbook_flags_opponent_by_ord_in_own_branch():
    body = {"matchup":{"first_pick_team":0,"side_map":{"us":0,"them":1}},
            "branches":[{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                         "plans":[{"label":"A1",
                                   "key_picks":[{"by_ord":12,"fallback":[1]}]}]}],
            "op_hero_decision":[]}
    assert check_playbook(body) != []   # ord 12 属于后手方

def test_playbook_requires_key_pick_fallback():
    """规格 §6.3：fallback 在 key_picks[] 内，不是 plan 级字段。"""
    body = {"matchup":{"first_pick_team":0,"side_map":{"us":0,"them":1}},
            "branches":[{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                         "plans":[{"label":"A1","key_picks":[{"by_ord":13}]}]}],
            "op_hero_decision":[]}
    assert check_playbook(body) != []
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contracts.tools.invariants'`

- [ ] **Step 3: 写 `contracts/tools/invariants.py`**

```python
"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。"""
from __future__ import annotations
from shared.draft_template import TEMPLATE

TOL = 1e-3

# check_resolve 校验 §6.2/§6.6 的 next_ord/team/is_pick 推导。
# **本 chunk 的 fixtures 是纯响应体，不含请求上下文**，故无法在 fixture 层调用它；
# 它由 Plan 2+ 的接口集成测试使用（那里能同时拿到请求与响应）。
# 推导逻辑本身已在 Task 4 的 shared/draft_template.py 中受测。
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
    mu = body["matchup"]
    us_team = mu["side_map"]["us"]
    want_first = "us" if us_team == mu["first_pick_team"] else "them"
    # 系列赛剧本允许分支覆盖两种先手权（规格 §6.3）：
    # 此时分支必须带 applies_to_game 指向 series.games[] 中某一局。
    games = {g["game_no"]: g.get("first_pick_team")
             for g in (body.get("series") or {}).get("games", [])}
    for br in body["branches"]:
        fp = br["condition"]["first_pick"]
        game_no = br.get("applies_to_game")
        if games and game_no is not None:
            g_fpt = games.get(game_no)
            if g_fpt is None:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 不在 series.games 中")
                br_fpt = mu["first_pick_team"]
            else:
                exp = "us" if us_team == g_fpt else "them"
                if fp != exp:
                    errs.append(f"分支 {br['branch_id']} 在第 {game_no} 局的 first_pick 应为 {exp}")
                br_fpt = g_fpt
        else:
            if fp != want_first:
                errs.append(f"分支 {br['branch_id']} 的 first_pick 应为 {want_first}"
                            f"（单局剧本；若要覆盖另一先手权，需带 series 与 applies_to_game）")
            br_fpt = mu["first_pick_team"]
        for pl in br["plans"]:
            kps = pl.get("key_picks") or []
            if not kps:
                errs.append(f"plan {pl['label']} 的 key_picks 为空")
            for kp in kps:
                # fallback 位于 key_picks[] 内（规格 §6.3 示例），不是 plan 级字段
                if not kp.get("fallback"):
                    errs.append(f"plan {pl['label']} 的 key_pick(by_ord {kp.get('by_ord')}) 缺少 fallback")
                _, owner_team = resolve(kp["by_ord"], br_fpt)
                if owner_team != us_team:
                    errs.append(f"plan {pl['label']}: by_ord {kp['by_ord']} 属于对手，"
                                f"与分支 first_pick={fp} 不符")
    for oh in body.get("op_hero_decision", []):
        lv = oh["if_we_leave"]
        if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
            errs.append(f"hero {oh['hero_id']}: our_wr + their_wr != 1.0")
    return errs

def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    """支持两种 mode：realtime 返回 options[]，offline 返回 branches[].plans[]。"""
    errs = []
    if "options" in body:
        groups = [("options", body["options"])]
    elif "branches" in body:
        # 规格 §6.6 只要求 options[] 有序；offline 的 plans[] 按**分支内**排序，
        # 不跨分支比较（不同分支的 expected_wr 不可比）。
        groups = [(br["branch_id"], br["plans"]) for br in body["branches"]]
    else:
        return ["advise 响应既无 options 也无 branches"]
    for gname, items in groups:
        scores = [o["penalized_score"] for o in items]
        if scores != sorted(scores, reverse=True):
            errs.append(f"{gname}: 未按 penalized_score 降序")
        for o in items:
            key = o.get("hero_id", o.get("label"))
            exp = o["expected_wr"] - lam * max(0.0, o["robustness_delta"] - 0.10)
            if abs(exp - o["penalized_score"]) > TOL:
                errs.append(f"{gname}/{key}: penalized_score {o['penalized_score']} != {exp:.4f}")
            if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
                errs.append(f"{gname}/{key}: robustness_delta > 0.10 但缺 risk_note")
            # realtime 的 fallback 在 option 级；offline 的在 key_picks[] 内
            if "fallback" in o and not o["fallback"]:
                errs.append(f"{gname}/{key}: fallback 为空")
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

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: **11 passed**

- [ ] **Step 5: 写五个 schema 文件**

按规格 §6.1–§6.6 的响应示例逐字段落成 JSON Schema。硬性要求：

1. **组件名必须恰好是** `Value` / `Policy` / `Playbook` / `Profile` / `Advise`——`validate_fixtures.py` 用 `resource.capitalize()` 查找。
2. **所有降级字段必须 `$ref` 到 `Degraded`**，不得内联同形对象。否则规格 §15 的「降级契约测试」对它们不生效，`playbook__op_insufficient.json` 的反向保护（`needs` 必须缺席）也失去约束。
3. 规格标 `// optional` 的字段不进 `required`。
4. **每个资源 schema 必须有非空的 `required` 与 `properties`**——空 schema `{}` 会让所有 fixture 通过，使 M0 验收第 2 条形同虚设。

**同时写 `tests/contracts/test_schema_shape.py`**，把上述要求变成机器可检的断言：

```python
import pathlib
import yaml, pytest

ROOT = pathlib.Path(__file__).parents[2] / "contracts"
RESOURCES = ["Value", "Policy", "Playbook", "Profile", "Advise"]

def test_all_five_resources_are_defined(common):
    missing = [r for r in RESOURCES if r not in common]
    assert missing == [], f"契约缺少资源 schema: {missing}"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_schema_is_not_empty(common, name):
    schema = common[name]
    assert schema.get("required"), f"{name} 没有 required 字段——空 schema 会让任何 fixture 通过"
    assert schema.get("properties"), f"{name} 没有 properties"

def test_every_degraded_field_refs_the_Degraded_component(contract_doc):
    """规格 §15 的降级契约依赖此约束：降级字段必须 $ref，不得内联同形对象。"""
    schemas = contract_doc["components"]["schemas"]
    offenders = []
    def walk(node, path):
        if isinstance(node, dict):
            # 形如 {"value": ..., "reason": ...} 的内联降级对象
            if "reason" in (node.get("properties") or {}) and "value" in (node.get("properties") or {}):
                if "$ref" not in node:
                    offenders.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    for name in RESOURCES:
        walk(schemas[name], name)
    assert offenders == [], f"以下位置内联了降级对象而非 $ref Degraded: {offenders}"
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: **23 passed**（6 公共 + 9 不变式 + 8 schema 形状）

- [ ] **Step 7: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): 五资源 schema + 可执行不变式（降级字段一律 ref Degraded）"
```

---

### Task 7: 17 个边界 fixtures 与校验工具

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
from jsonschema import Draft202012Validator
import yaml
from . import invariants as inv

ROOT = pathlib.Path(__file__).parents[1]
FIX = ROOT / "fixtures"
MIN_FIXTURES = 17   # 目录为空时不得静默通过

ROUTES = {
    "value":    inv.check_value,
    "policy":   inv.check_policy,
    "playbook": inv.check_playbook,
    "profile":  inv.check_profile,
    "advise":   inv.check_advise,
}

def main() -> int:
    doc = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    root = Draft202012Validator(doc)

    def validator(name: str) -> Draft202012Validator:
        return root.evolve(schema=doc["components"]["schemas"][name])

    failures, files = [], sorted(FIX.glob("*.json"))
    if len(files) < MIN_FIXTURES:
        print(f"FAIL 只找到 {len(files)} 个 fixture，至少应有 {MIN_FIXTURES} 个")
        return 1
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
| `profile__position_unknown.json` | `coverage.pro_match.n_position_unknown > 0`（§7.3 要求返回该计数）；五个位置维度降级，**`hero_archetype` 仍返回六项且和为 1.0** |
| `advise__realtime.json` | `options[]` 形状 |
| `advise__offline.json` | `mode:"offline"` → `branches[].plans[]`（非 `options[]`） |
| `advise__robustness_penalized.json` | 含 `robustness_delta > 0.10` 的项，`penalized_score < expected_wr`、带 `risk_note`、排序被降 |
| `error__insufficient_data.json` | `error.code="insufficient_data"` |
| `error__source_not_allowed.json` | `error.code="source_not_allowed"` |

**本 chunk 不实现规格 §6.5「Phase A 必须不存在」清单的机器校验**。原因：那张表约束的是**运行时响应**里不出现回放相关字段，而 fixtures 是人工构造的，扫描它们只能证明构造者没有写进去，证明不了实现不写。该检查归入 Plan 3（画像引擎）的接口集成测试——那里能对真实响应做全字段扫描。**这是一处显式延迟，不是遗漏。**

**`contracts/fixtures/README.md` 必须写明 HTTP 状态码映射**——fixture 文件体只承载响应体，JSON 无法表达状态码：

| fixture 前缀 | HTTP 状态 |
|---|---|
| `value__*` / `policy__*` / `playbook__*` / `profile__*` / `advise__*` | 200 |
| `error__insufficient_data` | **200**（业务不足，非传输错误） |
| `error__source_not_allowed` | 403 |

机器校验状态码需上移到集成测试（计划 2+）。

- [ ] **Step 5: 运行校验**

Run: `make contract && pytest tests/contracts/test_fixtures.py -q`
Expected: `17 fixtures, 0 failures`；**1 passed**

- [ ] **Step 6: Commit**

```bash
git add contracts/
git commit -m "feat(contracts): 17 个边界 fixtures + 校验工具 + 状态码映射说明"
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
                  "UnavailableReason", "ErrorCode", "Side", "Factor"]

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
Expected: `wrote .../web/src/types/contract.ts`；**26 passed**

- [ ] **Step 4: 校验 OpenAPI 文档本身（M0 验收第 1 条）**

Run: `make lint-spec`
Expected: `contracts/openapi.yaml: OK`（openapi-spec-validator 0.9.x 会打印该行并 exit 0；
旧版本静默通过——以 exit code 为准）

- [ ] **Step 5: 全量测试**

Run: `make db-reset && make test`
Expected: **55 passed**（29 + 23 + 1 + 2）

- [ ] **Step 6: Commit（M0 完成）**

```bash
git add contracts/ web/ tests/
git commit -m "feat(contracts): TS 类型生成 + 完整性测试 + OpenAPI 校验 —— M0 完成"
```

**M0 验收对照**：

| 规格 §14 M0 条款 | 落地位置 |
|---|---|
| `contracts/openapi.yaml` 提交且通过 schema 校验 | Task 8 Step 4 `make lint-spec` |
| 17 个 fixtures 各自通过契约校验 | Task 7 Step 5 `make contract` |
| 三条线的目录边界建立 | Task 1 Step 7（`analysis/`、`models/`、`web/` 均已创建） |
## Chunk 3: 数据地基（M1）

本 chunk 把「拉取到的东西」真正**写进数据库**。Chunk 1 建好了表，但整个计划里唯一往 `heroes`/`items`/`patches` 写入的地方是 Chunk 1 的合成 `seeded` fixture（1 个英雄）——因此本 chunk 的核心是**入库任务**，没有它 M1 的验收断言不可能通过，Kaggle 加载器也会被 `draft_actions.hero_id` 的外键挡住。

**依赖**：Chunk 1 的 `db/migrations/001_schema.sql` + `tests/conftest.py` + `shared/draft_template.py`。

**离线策略**：Task 9/10 会打 OpenDota 与 Valve。为避免测试依赖网络与时间点事实漂移，**首次拉取后必须把原始响应落到 `tests/fixtures/network/`**，之后测试默认读缓存；设置 `REFRESH_NETWORK=1` 才重新拉取。仓库里提交这些缓存（它们很小：heroes 约 300 KB、items 约 500 KB、patch 列表约 20 KB）。

### Task 9: 常量层——英雄、道具与原型映射

**Files:**
- Create: `constants/__init__.py`, `constants/_http.py`, `constants/dotaconstants.py`, `constants/archetypes.py`
- Create: `tests/fixtures/network/.gitkeep`
- Create: `tests/constants/__init__.py`
- Test: `tests/constants/test_dotaconstants.py`, `tests/constants/test_archetypes.py`

- [ ] **Step 1: 写 `constants/_http.py`（带缓存的 HTTP 客户端）**

```python
"""统一的 HTTP 拉取：带自定义 User-Agent + 本地缓存。

实测：OpenDota 对 `Python-urllib/3.12` 返回 403，但对
python-httpx / python-requests / 无 UA 均返回 200。
仍然锚定一个明确的 UA —— 这是对公共 API 的基本礼貌，也让流量可追溯。
"""
from __future__ import annotations
import json, os, pathlib
import httpx

UA = "Dota2DraftAnalysis/0.1 (https://github.com/yourname/mcndotaga)"
CACHE = pathlib.Path(__file__).parents[1] / "tests" / "fixtures" / "network"

def fetch_json(url: str, cache_name: str) -> dict | list:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / cache_name
    if path.exists() and not os.environ.get("REFRESH_NETWORK"):
        return json.loads(path.read_text(encoding="utf-8"))
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
```

- [ ] **Step 2: 写失败测试**

`tests/constants/test_dotaconstants.py`：

```python
from constants.dotaconstants import fetch_heroes, fetch_items, derive_token_index

def test_hero_count_is_127():
    assert len(fetch_heroes()) == 127

def test_item_count_is_501():
    assert len(fetch_items()) == 501

def test_exactly_nine_hero_ids_exceed_126():
    ids = sorted(h["id"] for h in fetch_heroes())
    assert [i for i in ids if i > 126] == [128,129,131,135,136,137,138,145,155]

def test_token_index_is_dense_and_zero_based():
    m = derive_token_index(fetch_heroes())
    assert sorted(m.values()) == list(range(127))

def test_hero_135_maps_to_121():
    """规格 §16.2 用真实比赛验证过的具体映射。"""
    assert derive_token_index(fetch_heroes())[135] == 121

def test_ten_items_have_null_dname():
    assert sum(1 for i in fetch_items() if not i.get("dname")) == 10

def test_roles_vocabulary_is_the_measured_eight():
    """规格 §7 维度 6：词表只有 8 项，没有 Jungler。"""
    vocab = {r for h in fetch_heroes() for r in (h.get("roles") or [])}
    assert vocab == {"Carry","Disabler","Durable","Escape",
                     "Initiator","Nuker","Pusher","Support"}
```

`tests/constants/test_archetypes.py`：

```python
from constants.archetypes import archetypes_for
from constants.dotaconstants import fetch_heroes

SIX = {"initiate","protect","push","teamfight","pickoff","splitpush"}

def test_archetype_mapping_is_total():
    """规格 §16.3：127 个英雄零遗漏（Σ=0 会导致归一化除零）。"""
    heroes = fetch_heroes()
    missing = [h["localized_name"] for h in heroes if not archetypes_for(h["roles"])]
    assert missing == [], f"零命中英雄: {missing}"

def test_eight_historical_heroes_hit_teamfight():
    """上一版合取式规则让这 8 个零命中；其中 105 在契约示例里、135 在参考比赛里。"""
    by_id = {h["id"]: h for h in fetch_heroes()}
    for hid in [11,22,35,72,76,105,135,138]:
        assert "teamfight" in archetypes_for(by_id[hid]["roles"]), f"hero {hid} 未命中 teamfight"

def test_only_produces_known_archetypes():
    for h in fetch_heroes():
        assert set(archetypes_for(h["roles"])) <= SIX
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/constants -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'constants'`

> `constants/` 目录在本 Step 之前并不存在（Task 1 的 `mkdir` 不含它），
> 所以 Python 报的是 `constants` 而非 `constants.dotaconstants`。

- [ ] **Step 4: 实现**

`constants/dotaconstants.py`：

```python
from __future__ import annotations
from ._http import fetch_json

OPENDOTA = "https://api.opendota.com/api"

def fetch_heroes() -> list[dict]:
    raw = fetch_json(f"{OPENDOTA}/constants/heroes", "opendota_heroes.json")
    return list(raw.values())          # OpenDota 以 hero_id 为键返回

def fetch_items() -> list[dict]:
    raw = fetch_json(f"{OPENDOTA}/constants/items", "opendota_items.json")
    return list(raw.values())

def derive_token_index(heroes: list[dict]) -> dict[int, int]:
    """规格 §5.1：dense_index = 按 hero_id 升序排序后的 0-based 下标。

    hero_id 稀疏（1..155，含 9 个 > 126），不能直接当 token 用。
    """
    return {h["id"]: i for i, h in enumerate(sorted(heroes, key=lambda h: h["id"]))}
```

`constants/archetypes.py`：

```python
"""规格 §7 维度 6 的 roles → 原型 映射。

roles 词表实测只有 8 项（Carry/Disabler/Durable/Escape/Initiator/
Nuker/Pusher/Support）——没有 Jungler，也没有任何一项等于六类原型名。
teamfight 必须是**析取式**：上一版的合取式会让 8 个英雄零命中。
"""
from __future__ import annotations

RULES: list[tuple[str, callable]] = [
    ("initiate",   lambda r: "Initiator" in r),
    ("push",       lambda r: "Pusher" in r),
    ("pickoff",    lambda r: "Escape" in r and "Nuker" in r),
    ("splitpush",  lambda r: "Carry" in r and "Escape" in r and "Pusher" not in r),
    ("protect",    lambda r: "Support" in r and ("Nuker" in r or "Durable" in r)),
    ("teamfight",  lambda r: "Durable" in r or "Disabler" in r
                             or ("Carry" in r and "Nuker" in r)),
]

def archetypes_for(roles: list[str]) -> list[str]:
    r = set(roles or [])
    return [name for name, pred in RULES if pred(r)]
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/constants -q`
Expected: **10 passed**（7 + 3）

- [ ] **Step 6: Commit**

```bash
git add constants/ tests/constants/ tests/fixtures/network/
git commit -m "feat(constants): 英雄/道具常量 + token 索引 + 原型映射（实测零遗漏）"
```

---

### Task 10: 版本表——Valve 权威清单 + 子版本还原

规格 §16.3 记录了一个**用错规则会导致最大 47 天偏差**的陷阱。本任务把它变成受测代码，并覆盖规格 §15 要求的**三类用例**。

**Files:**
- Create: `constants/patches.py`
- Test: `tests/constants/test_patches.py`

- [ ] **Step 1: 写失败测试（三类用例缺一不可）**

```python
import pytest
from constants.patches import (fetch_valve_patches, fetch_patchdates,
                               restore_subpatch_dates, subpatch_for_timestamp)

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
    """用例①：边界两侧必须落到不同子版本（规格 §15 版本归属测试 class ①）。"""
    assert subpatch_for_timestamp(1789301470) == "7.41e"   # 2026-09-13，参考比赛
    assert subpatch_for_timestamp(1789498134) == "7.41f"   # 7.41f 发布时刻

def test_boundary_is_exclusive_on_the_left():
    """恰好在 7.41f 发布前一秒仍属 7.41e。"""
    assert subpatch_for_timestamp(1789498133) == "7.41e"

def test_known_exceptions_are_declared():
    """规格 §3.2：7.22 与 7.25 是已知例外，必须显式声明而非静默错标。"""
    from constants.patches import KNOWN_EXCEPTIONS
    assert 7.22 in KNOWN_EXCEPTIONS and 7.25 in KNOWN_EXCEPTIONS
    assert "unsorted" in KNOWN_EXCEPTIONS[7.22]
    assert "no add" in KNOWN_EXCEPTIONS[7.25]

def test_letter_mapping_agrees_with_valve_on_sampled_series():
    """交叉校验：抽样若干序列，映射出的字母版本必须与 Valve 的日期吻合（±2 天）。"""
    from constants.patches import cross_check_against_valve
    report = cross_check_against_valve()
    assert report["n_compared"] >= 80
    assert report["max_deviation_days"] <= 2, report
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants/test_patches.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'constants.patches'`

- [ ] **Step 3: 实现 `constants/patches.py`**

必须实现并导出**四个名字 + 一个常量**（测试直接 import 它们）：

| 名字 | 职责 |
|---|---|
| `fetch_valve_patches()` | 拉 `https://www.dota2.com/datafeed/patchnoteslist?language=english`，返回 `patches` 列表 |
| `fetch_patchdates()` | 拉 `D2-LRG-Metadata/patchdates.json`（无认证） |
| `restore_subpatch_dates(entry)` | **纯函数**，把一条 patchdates 记录展开为 `{版本名: 时间戳}`。规则见下 |
| `subpatch_for_timestamp(ts)` | 按 Valve 时间戳（优先）回落 patchdates，返回版本名如 `"7.41e"` |
| `KNOWN_EXCEPTIONS` | `{7.22: "...unsorted...", 7.25: "...no add..."}` |
| `cross_check_against_valve()` | 返回 `{"n_compared": int, "max_deviation_days": int, "outliers": [...]}`；**由此函数独占 Valve × patchdates 的交叉校验职责** |

**字母映射规则（必须固化为代码，用错规则会让约 20 个序列整体错位一个字母）**：

```
main       -> <code>
add        -> <code> + 'a'      (若存在)
dates[0]   -> <code> + 'b'      ← 从 'b' 起，不是 'a'
dates[1]   -> <code> + 'c'
...        依次递增
```

**两个已知例外的具体处理规则**（原计划只说"单独处理"而没给规则）：

| 版本 | 实测现象 | 处理规则 |
|---|---|---|
| **7.22** | `dates[]` 未排序，导致 `dates[2]` 被标为 7.22d 但比 Valve 早 34 天 | 交叉校验时**按时间戳排序后再分配字母**；排序后仍不符的槽位记入 `outliers` 并**采用 Valve 的字母** |
| **7.25** | patchdates **没有 `add` 字段**，而 Valve 有 7.25a；其 `dates[0]` 实际对应 Valve 的 7.25b | 该序列的 `dates[]` **整体后移一位**（`dates[i]` → 字母 `'c'+i`），即从 `add` 缺失但 Valve 存在 `<code>a` 时特判。**注意其偏差只有 +1 天，±2 天的阈值抓不到它**，所以必须靠显式声明而非靠偏差检测 |

**Valve 优先**：`subpatch_for_timestamp` 一律先用 Valve 的时间戳；patchdates 只用于补 Valve 未覆盖的序列（6.86–7.07 段）。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/constants/test_patches.py -q`
Expected: **8 passed**

- [ ] **Step 5: Commit**

```bash
git add constants/patches.py tests/constants/test_patches.py
git commit -m "feat(constants): 版本表（Valve 权威 + 子版本还原，含两个已知例外的显式规则）"
```

---

### Task 11: 常量入库（M1 的前置，原计划缺失）

**这是整个计划此前最大的缺口**：没有任何任务把常量写进 `heroes`/`items`/`patches`/`hero_token_index`/`constants_snapshot`/`app_config_kv`。没有它，M1 的四条验收断言不可能通过，Kaggle 加载器也会被 `draft_actions.hero_id` 的外键挡住。

**Files:**
- Create: `constants/load.py`
- Test: `tests/constants/test_load.py`

- [ ] **Step 1: 写失败测试**

```python
def test_load_constants_populates_all_tables(db):
    from constants.load import load_constants
    load_constants(db)

    assert db.execute("SELECT count(*) FROM constants_snapshot").fetchone()[0] == 1
    snap = db.execute("SELECT snapshot_version, n_heroes, n_items FROM constants_snapshot").fetchone()
    assert snap[1] == 127 and snap[2] == 501

    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 501
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM patches").fetchone()[0] == 118
    assert db.execute("SELECT count(*) FROM patches WHERE version_name ~ '[a-z]$'").fetchone()[0] == 84

def test_token_index_satisfies_the_derivation_rule(db):
    """规格 §5.1：dense_index == row_number() OVER (ORDER BY hero_id) - 1。"""
    from constants.load import load_constants
    load_constants(db)
    bad = db.execute("""
        SELECT count(*) FROM (
          SELECT dense_index, row_number() OVER (ORDER BY hero_id) - 1 AS expected
          FROM hero_token_index) t
        WHERE dense_index <> expected""").fetchone()[0]
    assert bad == 0

def test_hero_135_dense_index_is_121(db):
    from constants.load import load_constants
    load_constants(db)
    got = db.execute("SELECT dense_index FROM hero_token_index WHERE hero_id = 135").fetchone()[0]
    assert got == 121

def test_app_config_kv_has_the_four_required_keys(db):
    """规格 §5.1：M1 必须种入这四个键，否则 §6.6/§7/§9.1 的相关功能不可用。"""
    from constants.load import load_constants
    load_constants(db)
    keys = {r[0] for r in db.execute("SELECT key FROM app_config_kv")}
    assert {"archetype_role_map", "robustness_lambda",
            "op_decision_min_delta", "min_sample_n"} <= keys

def test_archetype_role_map_matches_the_python_rules(db):
    """规格 §7：映射存于 app_config_kv，可调而不改代码——故必须与代码一致。"""
    import json
    from constants.load import load_constants
    from constants.archetypes import RULES
    load_constants(db)
    raw = db.execute("SELECT value FROM app_config_kv WHERE key='archetype_role_map'").fetchone()[0]
    stored = json.loads(raw)
    assert set(stored) == {name for name, _ in RULES}

def test_load_is_idempotent(db):
    from constants.load import load_constants
    load_constants(db); load_constants(db)
    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants/test_load.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'constants.load'`

- [ ] **Step 3: 实现 `constants/load.py`**

```python
"""把常量写进数据库。幂等：全部使用 ON CONFLICT DO UPDATE/NOTHING。

必须在 Kaggle 引导数据入库之前跑——draft_actions.hero_id 是
heroes(hero_id) 的外键，matches.patch_id 是 patches(patch_id) 的外键。
"""
from __future__ import annotations
import json
import psycopg
from .dotaconstants import fetch_heroes, fetch_items, derive_token_index
from .archetypes import RULES
from .patches import fetch_valve_patches, declare_lettered_versions

SNAPSHOT_VERSION = 1

def load_constants(conn: psycopg.Connection) -> None:
    heroes, items = fetch_heroes(), fetch_items()
    token_index = derive_token_index(heroes)

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
                        [(i["id"], i["key"] if "key" in i else i["name"],
                          i.get("dname"), i.get("cost")) for i in items])
        cur.executemany("""INSERT INTO hero_token_index(snapshot_version, hero_id, dense_index)
                           VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                        [(SNAPSHOT_VERSION, hid, di) for hid, di in token_index.items()])

    # 版本表：Valve 为准；patchdates 仅补 Valve 未覆盖的序列
    for p in declare_lettered_versions():
        conn.execute("""INSERT INTO patches(version_name, base_version, released_at, opendota_patch)
                        VALUES (%s,%s,to_timestamp(%s),%s)
                        ON CONFLICT (version_name) DO UPDATE
                        SET released_at = EXCLUDED.released_at""",
                     (p["version_name"], p["base_version"], p["released_at"], p.get("opendota_patch")))

    conn.execute("""INSERT INTO app_config_kv(key, value, note) VALUES
        ('archetype_role_map',    %s, '规格 §7 维度 6 的 roles→原型 映射'),
        ('robustness_lambda',     '1.0'::jsonb, '规格 §6.6 penalized_score 的 λ'),
        ('op_decision_min_delta', '0.02'::jsonb, '规格 §9.1 三选一判定的最小差异阈值'),
        ('min_sample_n',          '30'::jsonb, '规格 §7.3/§9.1 的样本量门槛')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (json.dumps({name: "python:constants.archetypes.RULES" for name, _ in RULES}),))
    conn.commit()
```

> `items` 的短名取自 OpenDota 返回的字典键（`i["key"]`），不是 `i["name"]`（后者是 `item_blink` 这类内部名）。实现时按实际 payload 调整。

- [ ] **Step 4: 运行确认通过**

Run: `make db-reset && pytest tests/constants -q`
Expected: **24 passed**（`tests/constants` 下三个文件：Task 9 = 10、Task 10 = 8、Task 11 = 6）

- [ ] **Step 5: Commit**

```bash
git add constants/load.py tests/constants/test_load.py
git commit -m "feat(constants): 常量入库（127 英雄/501 道具/118 版本/4 个配置键）+ 幂等"
```

---

### Task 12: Kaggle 引导数据集（选择性下载 506 MB）

**Files:**
- Create: `ingest/__init__.py`, `ingest/kaggle_subset.py`, `ingest/load_bootstrap.py`
- Create: `tests/ingest/__init__.py`, `tests/ingest/conftest.py`
- Test: `tests/ingest/test_bootstrap.py`

- [ ] **Step 1: 写 `tests/ingest/conftest.py`（定义本 chunk 需要的两个 fixture）**

```python
"""本 chunk 独有的 fixture。Chunk 1 的 tests/conftest.py 只有 dsn/db/seeded。"""
from __future__ import annotations
import csv, os, pathlib
import psycopg, pytest

REPO = pathlib.Path(__file__).parents[2]
CACHE = REPO / "tests" / "fixtures" / "kaggle"

@pytest.fixture(scope="session")
def db_after_bootstrap(dsn):
    """跑完常量入库 + Kaggle 引导入库的**独立数据库连接**。

    会话级：引导入库很慢，不能每个测试重跑一次。
    """
    from constants.load import load_constants
    from ingest.load_bootstrap import load_bootstrap
    with psycopg.connect(dsn) as conn:
        load_constants(conn)
        load_bootstrap(conn, cache_dir=CACHE)
    return psycopg.connect(dsn)

@pytest.fixture(scope="session")
def sample_csv() -> pathlib.Path:
    """某个目录下的 picks_bans.csv，用于验证列名与 order 起始值。

    若缓存不存在则跳过——凭证缺失时不应让整个测试套件变红。
    """
    path = CACHE / "2016" / "picks_bans.csv"
    if not path.exists():
        pytest.skip(f"缺少 {path}；先运行 python -m ingest.kaggle_subset 并配置 Kaggle 凭证")
    return path
```

- [ ] **Step 2: 写列名与 order 起始值的验证测试（规格 §17 第 7 项的唯一前置）**

```python
import csv

def test_picks_bans_columns_and_order_origin(sample_csv):
    """规格 §17-7：必须确认 order 从 0 起，否则模板映射整体错位一位。

    用 utf-8-sig 读：Kaggle 的 CSV 常带 BOM，否则首列名会变成 '\\ufeffmatch_id'。
    """
    with open(sample_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows, "样本为空"
    assert {"match_id", "order", "is_pick", "team", "hero_id"} <= set(rows[0]), \
        f"列名不符，实际为 {sorted(rows[0])}"
    orders = [int(r["order"]) for r in rows]
    assert min(orders) == 0, f"order 从 {min(orders)} 起，模板映射会整体错位一位"
```

> **若该断言失败**：说明数据集是 1-based。此时**不要**在这里减 1 了事——
> 必须回到规格 §6.0 确认模板语义，并在 `ingest/load_bootstrap.py` 的
> **入库边界**处统一 `ord = int(row["order"]) - 1`，同时把**本测试的断言
> 改为 `min(orders) == 1`** 并加注释说明。仅仅改代码不改测试，测试会一直红。

- [ ] **Step 3: 实现 `ingest/kaggle_subset.py`（按文件白名单下载）**

```python
"""只下需要的三个 CSV，共 506.2 MB。**绝不整包下载**。

数据集总计 48.52 GB（1546 个文件）。其中 players.csv 是 **19 个分片文件**
合计 41.04 GB（最大单片 2025/players.csv = 6.92 GB）——不存在"单文件 41 GB"，
但无论如何都与 Phase A 无关。
"""
```

- 凭证：需 `KAGGLE_USERNAME` / `KAGGLE_KEY`（在 kaggle.com → Account → Create New API Token 获取）。
- 依赖：把 `python-dotenv>=1.0` 与 `kaggle>=1.6` 加入 `pyproject.toml` 的 `[project.optional-dependencies].ingest`，并在本 Step 的说明中写清安装命令 `python -m pip install -e ".[dev,ingest]"`。
- 从 `.env` 读凭证：本 Step 必须先 `cp .env.example .env`（若 `.env` 不存在）并提示用户填入，再 `load_dotenv()`。
- **白名单**（相对数据集根）：`*/picks_bans.csv`（198.8 MB，19 个目录）、`*/main_metadata.csv`（66.7 MB，19 个）、`*/draft_timings.csv`（240.7 MB，**仅 2016–2025 共 10 个目录**）。
- 落到 `tests/fixtures/kaggle/<folder>/<file>`（该目录已由 `.gitignore` 排除，**不入库**）。

- [ ] **Step 4: 实现 `ingest/load_bootstrap.py`**

入库顺序与要点（**`load_constants` 必须先跑**，否则外键失败）：

1. `leagues` ← `main_metadata.csv` 的 `leagueid` / `league_name`（去重）
2. `matches` ← `main_metadata.csv`：`match_id, data_source='pro_match', patch_id(查表), started_at=to_timestamp(start_time), duration_s, league_id, series_id, series_type, radiant_team_id, dire_team_id, radiant_win, lobby_type, draft_state='pending'`
3. `draft_actions` ← `picks_bans.csv`：按 `match_id` **先删后插**（规格 §5.2 的幂等要求），`ord` 取 CSV 的 `order`（若为 1-based 则减 1，见 Step 2 的说明）
4. `first_pick_team` ← 每场 `ord=0` 的 `team`（用 `shared.draft_template.first_pick_team_from_actions`）
5. `n_draft_actions` / `draft_state` / `anomaly` ← 按规格 §5.3 判定：手数 ≠ 24 或类型偏离模板 → `anomaly=true` 且写一行 `draft_anomalies`
6. `patch_id` ← `constants.patches.subpatch_for_timestamp(start_time)` 查 `patches`
7. 全部写入用 `ON CONFLICT`，并用单一事务；**每 N 场 commit 一次**以便中断可续

- [ ] **Step 5: 写引导入库的验收测试**

```python
def test_bootstrap_loaded_a_substantial_number_of_matches(db_after_bootstrap):
    n = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    assert n > 10000, f"只入库了 {n} 场"

def test_draft_actions_and_leagues_are_queryable(db_after_bootstrap):
    """规格 §14 M1：matches/draft_actions/leagues 可查。"""
    assert db_after_bootstrap.execute("SELECT count(*) FROM draft_actions").fetchone()[0] > 200000
    assert db_after_bootstrap.execute("SELECT count(*) FROM leagues").fetchone()[0] > 50

def test_anomaly_rate_under_2_percent(db_after_bootstrap):
    """规格 §15：异常率 < 2%，且偏离场次被记入 draft_anomalies。"""
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    anom = db_after_bootstrap.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0]
    assert anom / total < 0.02, f"异常率 {anom/total:.2%}"
    assert db_after_bootstrap.execute("SELECT count(*) FROM draft_anomalies").fetchone()[0] == anom

def test_first_pick_team_matches_ord_zero(db_after_bootstrap):
    """规格 §8①：先手方由 ord=0 的 team 推出。"""
    bad = db_after_bootstrap.execute("""
        SELECT count(*) FROM matches m
        JOIN draft_actions d ON d.match_id = m.match_id AND d.ord = 0
        WHERE m.first_pick_team IS DISTINCT FROM d.team""").fetchone()[0]
    assert bad == 0

def test_every_match_has_a_patch(db_after_bootstrap):
    """patch_id 必须能解析——否则版本维度全废。"""
    nulls = db_after_bootstrap.execute("SELECT count(*) FROM matches WHERE patch_id IS NULL").fetchone()[0]
    total = db_after_bootstrap.execute("SELECT count(*) FROM matches").fetchone()[0]
    assert nulls / total < 0.05, f"{nulls}/{total} 场无法解析版本"
```

- [ ] **Step 6: 运行**

Run: `python -m ingest.kaggle_subset && pytest tests/ingest -q`
Expected: **6 passed**（1 列名验证 + 5 入库验收）

- [ ] **Step 7: Commit**

```bash
git add ingest/ tests/ingest/ pyproject.toml
git commit -m "feat(ingest): Kaggle 子集下载（506MB）+ 引导入库 + 异常率与版本归属校验"
```

---

### Task 13: M1 验收

**Files:**
- Test: `tests/test_m1_acceptance.py`

- [ ] **Step 1: 写验收脚本（逐条对应规格 §14 的 M1）**

```python
def test_m1_acceptance(db_after_bootstrap):
    db = db_after_bootstrap

    # 条款 1：常量表英雄 = 127、道具 = 501
    assert db.execute("SELECT count(*) FROM heroes").fetchone()[0] == 127
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 501

    # 条款 2：版本表含 84 个字母子版本（该数字只能来自 Valve）
    n_lettered = db.execute(
        "SELECT count(*) FROM patches WHERE version_name ~ '[a-z]$'").fetchone()[0]
    assert n_lettered == 84, f"字母子版本数 {n_lettered} != 84（规格 §16.3）"

    # 条款 3：引导数据集入库，且异常场次占比 < 2%
    total = db.execute("SELECT count(*) FROM matches").fetchone()[0]
    anom  = db.execute("SELECT count(*) FROM matches WHERE anomaly").fetchone()[0]
    assert total > 10000
    assert anom / total < 0.02

    # 条款 4：matches / draft_actions / leagues 可查
    assert db.execute("SELECT count(*) FROM draft_actions").fetchone()[0] > 200000
    assert db.execute("SELECT count(*) FROM leagues").fetchone()[0] > 50
    assert db.execute("SELECT count(*) FROM matches LIMIT 1").fetchone()[0] == 1

    # 补充：token 索引完备且满足派生规则
    assert db.execute("SELECT count(*) FROM hero_token_index").fetchone()[0] == 127
    bad = db.execute("""SELECT count(*) FROM (
        SELECT dense_index, row_number() OVER (ORDER BY hero_id) - 1 AS expected
        FROM hero_token_index) t WHERE dense_index <> expected""").fetchone()[0]
    assert bad == 0

    # 补充：四个配置键已种入（规格 §5.1）
    keys = {r[0] for r in db.execute("SELECT key FROM app_config_kv")}
    assert {"archetype_role_map","robustness_lambda",
            "op_decision_min_delta","min_sample_n"} <= keys
```

- [ ] **Step 2: 运行**

Run: `pytest tests/test_m1_acceptance.py -q`
Expected: **1 passed**

- [ ] **Step 3: Commit**

```bash
git add tests/test_m1_acceptance.py
git commit -m "test: M1 验收（127/501/84/异常率/三表可查/token 索引/配置键）"
```

**M1 验收对照**：

| 规格 §14 M1 条款 | 落地断言 |
|---|---|
| 常量表英雄 = 127、道具 = 501 | Task 13 `test_m1_acceptance` + Task 11 `test_load_constants_populates_all_tables` |
| 版本表含 84 个字母子版本 | Task 13 + Task 10 `test_valve_list_has_118_versions_and_84_lettered` |
| 引导数据集 506 MB 子集入库 | Task 12 `test_bootstrap_loaded_a_substantial_number_of_matches` |
| `anomaly=true` 占比 < 2% | Task 13 + Task 12 `test_anomaly_rate_under_2_percent` |
| `matches`/`draft_actions`/`leagues` 可查 | Task 13 + Task 12 `test_draft_actions_and_leagues_are_queryable` |

---

## 完成后的状态

**已解锁**：三条 worktree 可并行启动
- 线 A（画像引擎）→ `analysis/`：`db/` + `contracts/` 就绪 → 开始计划 3
- 线 C（序列模型）→ `models/`：`contracts/` + `shared/draft_template.py` 就绪。
  **不需要数据库**——引导数据集与模板足以训练与评估；接入数据库是计划 5 后期的事
- 前端 → `web/`：`contracts/fixtures/` 就绪，**不需要数据库** → 开始计划 4

**未做**（属后续计划）：常驻采集器（M2）、回放导入（M2b）、画像引擎（M3–M5）、可视化（M6）、序列模型（M7–M9）、部署（M10）。

**本计划显式延迟的三项**（不是遗漏）：
1. `contracts/openapi.yaml` 的 `paths` 与请求体 schema —— 归 Plan 2+，需先定服务端框架
2. 规格 §6.5「Phase A 必须不存在」清单的机器校验 —— 归 Plan 3，需对真实响应做全字段扫描
3. `check_resolve` 的接口级调用 —— 归 Plan 2+，fixtures 是纯响应体、不含请求上下文

**仍未验证、但已设测试保护的一项**：Kaggle CSV 的列名与 `order` 起始值（Task 12 Step 2 会直接失败并给出处置说明）。Kaggle 凭证缺失时相关测试会 `skip` 而非变红。
