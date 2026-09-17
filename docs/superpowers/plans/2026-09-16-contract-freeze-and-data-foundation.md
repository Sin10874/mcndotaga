# 契约冻结与数据地基 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 `contracts/openapi.yaml` 与 17 个边界 fixtures（解锁三条并行 worktree），并建成可查询的数据地基（常量层、含字母子版本的版本表、引导数据集入库）。

**Architecture:** 单一 Postgres 实例承载全部关系数据；契约以 OpenAPI 3.1 为唯一权威源，fixtures 从它生成并由测试校验；TS 侧**只生成枚举联合与 `Resource` 联合**（`web/src/types/contract.ts`），53 个 schema 的**对象接口尚未生成**，属 Plan 4 开工第一件事（范围与做法见 Task 8 的「范围说明」）。常量层拆为「规范实体（`heroes`/`items`）+ 版本化 token 索引（`hero_token_index`）」，token 索引按快照冻结以防常量刷新时静默重映射。数据来源三类：Valve 官方补丁 feed（权威版本清单）、dotaconstants（英雄/道具）、Kaggle CC0 数据集（历史 BP 引导数据，仅下载 506 MB 子集）。

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

**已实现任务（Task 1–8）的权威来源是提交，不是本文的代码块。** 下面 Task 1–8 里嵌入的代码块
只是**首版草稿**：复核发现的修复**不一定**回贴到本文，所以逐字重放这些块会悄悄复活已修好的缺陷
（已有实例：Task 6 的 `check_profile` 缺 §7.3 同侪下限、Task 7 的 fixture 谓词偏宽，二者都由
`5e02f74` 修好，本文对应块**未**回贴）。以 `git log -p -- <path>` 与工作树里的已提交文件为准；
本文的代码块只用于理解意图与实现顺序。

**交付后即可并行的三条 worktree：**
- 线 A（画像引擎）→ `analysis/`：依赖 `db/` + `contracts/`（即 Chunk 1 + 2 + 3）
- 线 C（序列模型）→ `models/`：依赖 `contracts/` + `shared/draft_template.py`。
  **不需要数据库**——引导数据集与模板足以训练与评估
- 前端 → `web/`：依赖 `contracts/fixtures/`（即 Chunk 1 + 2），**不需要数据库、不需要 Chunk 3**

**三条 worktree 一律从 M0 冻结提交 `64c89a9` 开分支（`git worktree add -b <分支> <路径> 64c89a9`），
不要从 `main` 开。** `main` 停在 `c486c0d`（Task 2 之后），**不含** M0 的任何产出
（`contracts/openapi.yaml`、`contracts/fixtures/`、`contracts/tools/gen_ts_types.py`、
`web/src/types/contract.ts`、`shared/`），从 `main` 开出来的 worktree 会两手空空，
而且缺失是静默的——目录在、文件不在。要么从冻结提交开，要么先把 `main` 快进到它。

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
- Test: `tests/contracts/test_schema_shape.py`

- [ ] **Step 1: 写失败测试 `tests/contracts/test_invariants.py`**

（先写测试：`invariants.py` 尚不存在，Step 2 运行时应报
`ModuleNotFoundError: No module named 'contracts.tools.invariants'`。）

```python
"""契约不变式（规格 §6）的回归：阳性示例 + 每条守护的阴性用例。

阳性示例一律取自规格 §6 的代码块（去掉 `//` 注释后 `json.loads`，与
`/tmp` 的抽取脚本同源），并在测试内先用 `validator_for(<资源>)` 过 schema ——
规格示例同时在 schema 与不变式两层成立，是本轮的第一条验收线。

阴性用例**全部是 schema 合法的**：先 `validate` 通过，再断言 `check_*` 拒绝。
这样失败必然来自不变式而非形态，避免"测试其实被 schema 拦住"的假阳性。
"""
from __future__ import annotations
import copy
import json

import jsonschema
import pytest

from contracts.tools.invariants import (check_value, check_policy, check_advise,
                                        check_playbook, check_profile, check_resolve)


def _spec(text: str) -> dict:
    """规格示例：注释已剥离，其余逐字保留。"""
    return json.loads(text)


SPEC_VALUE = {"radiant_win_prob": 0.530, "contributions": [
    {"factor": "patch_strength", "delta": 0.011}, {"factor": "counter_matchup", "delta": -0.014},
    {"factor": "player_comfort", "delta": 0.024}, {"factor": "first_pick", "delta": 0.009}]}

# 规格 §6.2「→ 200」示例（真实比赛 8996973546 的前 12 手）
SPEC_POLICY = _spec("""
{
  "next_ord": 12,
  "team": 1,
  "is_pick": true,
  "candidates": [
    {"hero_id": 112, "prob": 0.180,
     "reasons": ["该队在此阶段的历史首选", "克制对方已选核心"],
     "evidence_match_ids": [8996973546, 8988636430]}
  ],
  "top_n": 10,
  "other_prob": 0.820,
  "model": "sequence-v1",
  "baseline": {"frequency_top1": 0.041, "model_top1": null},
  "sources_used": ["pro_match"]
}
""")

# 规格 §6.3「→ 200」示例（逐字，仅去注释）
SPEC_PLAYBOOK = _spec("""
{
  "matchup": {
    "us":   {"team_id": 10251056, "name": "Dawn Bulls", "tag": "DB"},
    "them": {"team_id": 10232231, "name": "Klim Sani4", "tag": "KS"},
    "patch": "7.41e",
    "first_pick_team": 0,
    "side_map": {"us": 0, "them": 1}
  },
  "sources_used": ["pro_match"],
  "coverage": {
    "pro_match": {"n_matches": 42, "n_stat_available": 38},
    "pub_match": {"n_matches": 0,  "n_stat_available": 0}
  },
  "data_quality": {
    "n_pending_draft": 1,
    "n_unavailable_draft": 0,
    "n_anomalous_draft": 2,
    "oldest_pending_hours": 31,
    "note": "1 场 BP 数据尚未就绪（OpenDota 约 2.6 天滞后），未计入统计"
  },
  "bans": {
    "must_ban": [{"hero_id": 55, "why": "对手签名英雄，我方无人擅长应对",
                  "their_wr": 0.71, "our_wr_against": 0.29, "n": 24}],
    "consider": [{"hero_id": 77, "why": "...",
                  "if_we_leave_it_open": {
                    "hero_id": 77,
                    "if_we_pick":  {"wr": 0.47, "n": 33},
                    "if_we_ban":   {"wr": 0.49, "n": 58},
                    "if_we_leave": {"our_wr": 0.55, "their_wr": 0.45, "n": 40,
                                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
                    "recommendation": "leave_and_counter"}}],
    "bait_candidates": [{"hero_id": 90, "why": "双方都不擅长，浪费对手 ban 位"}]
  },
  "op_hero_decision": [{
    "hero_id": 83,
    "if_we_pick":  {"wr": 0.44, "n": 34},
    "if_we_ban":   {"wr": 0.50, "n": 62},
    "if_we_leave": {"our_wr": 0.58, "their_wr": 0.42, "n": 41,
                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
    "recommendation": "leave_and_counter"
  }],
  "branches": [
    {
      "branch_id": "A",
      "applies_to_game": 1,
      "condition": {"first_pick": "us", "their_opening": "teamfight"},
      "plans": [{
        "label": "A1",
        "goal": "拖到中后期，靠分推拉扯",
        "key_picks": [{"priority": 1, "hero_id": 105, "by_ord": 13,
                       "why": "...", "fallback": [67, 19]}],
        "expected_wr": 0.52, "n": 31,
        "robustness_delta": 0.04,
        "penalized_score": 0.52
      }]
    },
    {
      "branch_id": "B",
      "applies_to_game": 2,
      "condition": {"first_pick": "them", "their_opening": "push"},
      "plans": [{
        "label": "B1",
        "goal": "对手先手时抢下反制核心",
        "key_picks": [{"priority": 1, "hero_id": 19, "by_ord": 12,
                       "why": "...", "fallback": [67]}],
        "expected_wr": 0.51, "n": 31,
        "robustness_delta": 0.05,
        "penalized_score": 0.51
      }]
    }
  ],
  "series": {
    "series_id": 1141522,
    "games": [
      {"game_no": 1, "first_pick_team": 0},
      {"game_no": 2, "first_pick_team": 1,
       "note": "第 1 局的负者获得第 2 局先手权；该值在第 1 局结束后才能确定，未确定时为 null——此时不得产出指向该局的 them 分支"}
    ],
    "game1_plan": "...",
    "adjustment_rules": [{"if": "对手第 1 局暴露推进体系", "then": "..."}]
  },
  "positions": [
    {"side": "them", "role": 4,
     "notes": [{"kind": "ward", "text": "...", "evidence": [],
                "value": null, "reason": "needs_replay", "needs": "Phase B"}]}
  ]
}
""")

# 规格 §6.4「→ 200」示例（逐字，仅去注释）
SPEC_PROFILE = _spec("""
{
  "team_id": 10232231,
  "patch": "7.41e",
  "as_of": "2026-09-16",
  "sources_used": ["pro_match", "pub_match"],
  "coverage": {"pro_match": {"n_matches": 42, "n_stat_available": 38,
                             "n_position_unknown": 5},
               "pub_match": {"n_matches": 310, "n_stat_available": 298,
                             "n_position_unknown": 12}},
  "players": [{
    "account_id": 123456,
    "name": "示例选手",
    "role": 4,
    "hero_pool": {
      "window_games": 48,
      "signature":   [{"hero_id": 55, "games": 12, "wr": 0.75, "pct": 25}],
      "comfortable": [{"hero_id": 77, "games": 8,  "wr": 0.50, "pct": 17}],
      "effective_count": 14,
      "presence_pick_rate": 0.81
    },
    "dimensions": {
      "hero_pool":     {"percentile": 88, "n": 120},
      "laning":        {"percentile": 71, "n": 120},
      "combat":        {"percentile": 64, "n": 120},
      "map_vision":    {"percentile": 47, "n": 120},
      "tempo":         {"percentile": 55, "n": 120},
      "hero_archetype":{"initiate": 0.24, "protect": 0.18, "push": 0.17,
                        "teamfight": 0.19, "pickoff": 0.13, "splitpush": 0.09}
    }
  }],
  "team_bp_tendency": {
    "first_phase_ban_freq": [{"hero_id": 55, "freq": 0.42, "n": 31}],
    "first_pick_freq":      [{"hero_id": 123, "freq": 0.28, "n": 25}],
    "ban_by_phase":         [{"ord": 9, "hero_id": 53, "freq": 0.31, "n": 29}]
  }
}
""")

# 规格 §6.4 的 ProfileRef 示例
SPEC_PROFILE_REF = _spec("""
{"team_id": 10232231, "name": "Klim Sani4", "tag": "KS", "logo_url": null}
""")

# 规格 §6.6「→ 200 (mode="realtime")」示例（逐字，仅去注释）
SPEC_ADVISE = _spec("""
{
  "next_ord": 13,
  "team": 0,
  "is_pick": true,
  "options": [
    {"hero_id": 105, "expected_wr": 0.552, "robustness_delta": 0.04,
     "penalized_score": 0.552,
     "why": "对手按预测分布应对时最优；换招后仍不劣于 0.51",
     "fallback": [67, 19],
     "counterparty_plan": "对手若抢 105，我方案转为 ..."},
    {"hero_id": 67,  "expected_wr": 0.548, "robustness_delta": 0.03,
     "penalized_score": 0.548,
     "why": "...", "fallback": [19, 105], "counterparty_plan": "..."},
    {"hero_id": 19,  "expected_wr": 0.556, "robustness_delta": 0.22,
     "penalized_score": 0.436,
     "risk_note": "原始胜率最高，但对手一旦不按预测出牌，本方案会明显劣化",
     "why": "...", "fallback": [67, 105], "counterparty_plan": "..."}
  ],
  "assumptions": {"opponent_model": "sequence-v1", "value_model": "value-v1"},
  "sources_used": ["pro_match"]
}
""")


def _op_hero_decision(pick, ban, leave, counter_wr=None, rec="pick", n=40):
    """按 §9.1 的三量构造一个 op_hero_decision[0]（n 一律充足，只考验复算）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    counters = [] if counter_wr is None else [{"hero_id": 36, "wr": counter_wr, "n": 31}]
    body["op_hero_decision"] = [{
        "hero_id": 83,
        "if_we_pick": {"wr": pick, "n": n},
        "if_we_ban": {"wr": ban, "n": n},
        "if_we_leave": {"our_wr": leave, "their_wr": round(1.0 - leave, 4), "n": n,
                        "our_counter_options": counters},
        "recommendation": rec,
    }]
    return body


# ── 规格示例：schema + 不变式两层都要通过 ────────────────────────────────

def test_spec_value_example_passes(validator_for):
    body = copy.deepcopy(SPEC_VALUE)
    body |= {"confidence": "high", "n_samples": 412, "sources_used": ["pro_match"]}
    validator_for("Value").validate(body)
    assert check_value(body) == []

def test_spec_policy_example_passes(validator_for):
    validator_for("Policy").validate(SPEC_POLICY)
    assert check_policy(SPEC_POLICY) == []

def test_spec_playbook_example_passes(validator_for):
    validator_for("Playbook").validate(SPEC_PLAYBOOK)
    assert check_playbook(SPEC_PLAYBOOK) == []

def test_spec_profile_example_passes(validator_for):
    validator_for("Profile").validate(SPEC_PROFILE)
    assert check_profile(SPEC_PROFILE) == []

def test_spec_profile_ref_example_passes(validator_for):
    validator_for("ProfileRef").validate(SPEC_PROFILE_REF)

def test_spec_advise_example_passes(validator_for):
    validator_for("Advise").validate(SPEC_ADVISE)
    assert check_advise(SPEC_ADVISE) == []


# ── §6.1 Value ────────────────────────────────────────────────────────────

def test_value_detects_the_original_round1_defect():
    """规格一轮抓到的原始错误：求和 0.054 而 radiant_win_prob-0.5 = 0.030。"""
    bad = {**SPEC_VALUE, "contributions": [
        {"factor": "patch_strength", "delta": 0.021}, {"factor": "counter_matchup", "delta": -0.014},
        {"factor": "player_comfort", "delta": 0.038}, {"factor": "first_pick", "delta": 0.009}]}
    assert check_value(bad) != []

def test_value_detects_unrequested_source():
    body = {**SPEC_VALUE, "sources_used": ["pro_match", "pub_match"]}
    assert check_value(body, requested_sources=["pro_match"]) != []

def test_value_rejects_non_finite_delta(validator_for):
    """I5：delta 无上下界，NaN 能过 schema；`abs(nan-x) > TOL` 恒 False，
    不加非有限扫描则求和校验静默通过。"""
    body = {**SPEC_VALUE, "contributions": [
        {"factor": "patch_strength", "delta": float("nan")}, {"factor": "counter_matchup", "delta": -0.014},
        {"factor": "player_comfort", "delta": 0.024}, {"factor": "first_pick", "delta": 0.009}]}
    validator_for("Value").validate(dict(body, confidence="high", n_samples=412, sources_used=[]))
    errs = check_value(body)
    assert any("非有限数值" in e and "contributions[0].delta" in e for e in errs), errs


# ── §6.2 Policy ───────────────────────────────────────────────────────────

def test_policy_rejects_non_finite_prob(validator_for):
    """I5：NaN 会让 `abs(sum - 1.0) > TOL` 静默为 False。
    注意 NaN 连 schema 的数值上下界都逃得掉（`minimum`/`maximum` 用比较实现，
    `nan < min` 与 `nan > max` 都是 False）—— 故只有 checker 这一层能拦。"""
    body = {**SPEC_POLICY,
            "candidates": [{"hero_id": 112, "prob": float("nan"), "reasons": ["x"],
                            "evidence_match_ids": []}]}
    validator_for("Policy").validate(body)          # NaN 过 schema（I5 的前提）
    errs = check_policy(body)
    assert any("非有限数值" in e and "candidates[0].prob" in e for e in errs), errs

def test_policy_names_the_reasonless_candidate(validator_for):
    """§6.2 reasons 非空：schema 有 minItems，checker 作为第二层并点出 hero_id。"""
    body = {**SPEC_POLICY, "candidates": [{"hero_id": 112, "prob": 0.180, "reasons": [],
                                           "evidence_match_ids": []}]}
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Policy").validate(body)
    errs = check_policy(body)
    assert any("hero 112" in e and "reasons" in e for e in errs), errs


# ── §6.4 Profile ──────────────────────────────────────────────────────────

def test_profile_rejects_five_category_distribution(validator_for):
    """规格四轮抓到的原始缺陷：只有 5 类且凑巧和为 1.0。
    schema 也会拦（HeroArchetype 要求六个键齐备），checker 的第二层要指出缺了哪个键。"""
    body = {"players": [{
        "account_id": 1,
        "name": "甲",
        "role": 4,                       # role 非 null ⇒ 不触发 §7.3 的降级联动
        "hero_pool": {"window_games": 20, "signature": [], "comfortable": [],
                      "effective_count": 6, "presence_pick_rate": 0.5},
        "dimensions": {
            "hero_pool": {"percentile": 50, "n": 100}, "laning": {"percentile": 50, "n": 100},
            "combat": {"percentile": 50, "n": 100}, "map_vision": {"percentile": 50, "n": 100},
            "tempo": {"percentile": 50, "n": 100},
            "hero_archetype": {"teamfight": 0.32, "push": 0.18, "pickoff": 0.21,
                               "splitpush": 0.11, "protect": 0.18}}}]}
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("键集不完整" in e and "initiate" in e for e in errs), errs

def test_profile_rejects_non_finite_archetype_weight(validator_for):
    """I5：NaN 权重同样让 `abs(sum - 1.0) > TOL` 静默为 False，
    且 NaN 能过 schema 的 `minimum`/`maximum`（比较对 NaN 恒假）——checker 是唯一防线。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["dimensions"]["hero_archetype"]["initiate"] = float("nan")
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("非有限数值" in e and "initiate" in e for e in errs), errs

def test_profile_rejects_effective_count_over_window_third(validator_for):
    """F5/§6.4：effective_count <= window_games / 3（示例 14 <= 48/3 = 16）。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["hero_pool"]["effective_count"] = 20   # 20 > 48/3
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    assert any("effective_count=20" in e and "48/3" in e for e in errs), errs

def test_profile_role_null_requires_degraded_position_dimensions(validator_for):
    """F5/§6.4+§7.3：role 推断不出时，五个依赖位置的维度必须降级为 insufficient_samples。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["role"] = None
    validator_for("Profile").validate(body)
    errs = check_profile(body)
    for dim in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        assert any(dim in e and "insufficient_samples" in e for e in errs), (dim, errs)

def test_profile_role_null_accepts_all_five_degraded_dimensions(validator_for):
    """阴性用例的对照：五个维度都降级时 role=null 合法（hero_archetype 仍返回）。"""
    body = copy.deepcopy(SPEC_PROFILE)
    body["players"][0]["role"] = None
    degraded = {"value": None, "reason": "insufficient_samples"}
    for dim in ("hero_pool", "laning", "combat", "map_vision", "tempo"):
        body["players"][0]["dimensions"][dim] = copy.deepcopy(degraded)
    validator_for("Profile").validate(body)
    assert check_profile(body) == []


# ── §6.3 Playbook ─────────────────────────────────────────────────────────

def test_playbook_flags_opponent_by_ord_in_own_branch(validator_for):
    """规格 §6.3：`by_ord` 的归属必须与所属分支自洽（此处 ord 12 属于后手方）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]                                # 单局剧本
    body["branches"] = [body["branches"][0]]           # 只留分支 A（first_pick=us）
    body["branches"][0]["plans"][0]["key_picks"][0]["by_ord"] = 12
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("by_ord 12 属于对手" in e for e in errs), errs

def test_playbook_requires_key_pick_fallback(validator_for):
    """规格 §6.3：fallback 在 key_picks[] 内且非空 —— schema 与 check 两层都拦。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]
    body["branches"] = [body["branches"][0]]
    kp = body["branches"][0]["plans"][0]["key_picks"][0]
    del kp["fallback"]                                # 整字段缺失：schema 已拦
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Playbook").validate(body)
    assert any("缺少 fallback" in e for e in check_playbook(body))
    kp["fallback"] = []                               # 空数组：schema 也拦（minItems:1）
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Playbook").validate(body)
    assert any("缺少 fallback" in e for e in check_playbook(body))

def test_playbook_nested_consider_enforces_leave_wr_pair(validator_for):
    """F1：`consider[].if_we_leave_it_open` 与 `op_hero_decision[]` 同形（OpHeroOption），
    our_wr + their_wr == 1.0 必须同样生效（此前只查后者）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["bans"]["consider"][0]["if_we_leave_it_open"]["if_we_leave"]["their_wr"] = 0.50
    validator_for("Playbook").validate(body)      # schema 不看这条等式
    errs = check_playbook(body)
    assert any("bans.consider[0].if_we_leave_it_open" in e and "their_wr" in e for e in errs), errs

def test_playbook_series_branch_requires_applies_to_game(validator_for):
    """F2/§6.3：系列赛剧本（响应含 series）**每个**分支都必须带 applies_to_game；
    且失败信息不得再误称"单局剧本"。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["branches"][1]["applies_to_game"]
    validator_for("Playbook").validate(body)      # applies_to_game 是可选字段
    errs = check_playbook(body)
    assert any("分支 B 缺少 applies_to_game" in e for e in errs), errs
    assert not any("单局剧本" in e for e in errs), errs

def test_playbook_single_game_branch_keeps_single_game_message(validator_for):
    """对照：响应不含 series 时，先手权不一致仍报"单局剧本"（该措辞只在此时正确）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    del body["series"]
    body["branches"] = [body["branches"][0]]        # 只留分支 A（"us" 与 expected 一致）
    validator_for("Playbook").validate(body)
    assert check_playbook(body) == []
    body["branches"][0]["condition"]["first_pick"] = "them"   # 单局剧本里不允许
    errs = check_playbook(body)
    assert any("单局剧本" in e for e in errs), errs

def test_playbook_rejects_applies_to_game_pointing_at_null_first_pick():
    """§6.3：该局 first_pick_team 为 null 时无法推导 expected，不得作为 applies_to_game 的目标。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["series"]["games"][1]["first_pick_team"] = None
    errs = check_playbook(body)
    assert any("分支 B" in e and "null" in e for e in errs), errs

def test_playbook_n_below_threshold_must_degrade(validator_for):
    """F3/§9.1：逐量 n >= 30。n=4 的量必须降级，且 recommendation 必须是 insufficient_data。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["op_hero_decision"][0]["if_we_pick"] = {"wr": 0.44, "n": 4}
    validator_for("Playbook").validate(body)      # n 无下界，schema 放行
    errs = check_playbook(body)
    assert any("if_we_pick 的 n=4 < 30 却未降级" in e for e in errs), errs
    assert any("应为 insufficient_data" in e for e in errs), errs

def test_playbook_degraded_quantity_forces_insufficient_data(validator_for):
    """F3/§6.3：任一量降级 ⇒ recommendation 必须是 insufficient_data。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["op_hero_decision"][0]["if_we_ban"] = {"value": None, "reason": "insufficient_samples"}
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("if_we_ban(已降级)" in e and "insufficient_data" in e for e in errs), errs

def test_playbook_rejects_recommendation_contradicting_9_1(validator_for):
    """F3：三量都充分时必须按 §9.1 复算 —— 下面这组应为 pick, 却写 leave_and_counter。"""
    body = _op_hero_decision(0.60, 0.50, 0.45, counter_wr=0.61, rec="leave_and_counter")
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any("与 §9.1 复算的 pick 不符" in e for e in errs), errs

@pytest.mark.parametrize("pick,ban,leave,counter,expected,why", [
    (0.60, 0.50, 0.45, None, "pick", "wr_pick 最大且领先 0.10"),
    (0.45, 0.60, 0.50, None, "ban", "wr_ban 最大"),
    (0.44, 0.50, 0.58, 0.61, "leave_and_counter", "规格 §6.3 示例验算"),
    (0.45, 0.48, 0.58, 0.55, "ban", "leave 最大但 wr_counter 未超过 wr_leave → 退化 ban"),
    (0.45, 0.48, 0.58, None, "ban", "leave 最大但无反制选项可取 wr_counter → 退化 ban"),
    (0.55, 0.54, 0.50, None, "insufficient_data", "最大-次大 = 0.01 < 0.02 噪声窗"),
    (0.50, 0.50, 0.45, None, "insufficient_data", "并列最大（差 0 < 0.02）"),
])
def test_spec_9_1_recompute_matches_recommendation(validator_for, pick, ban, leave, counter,
                                                   expected, why):
    """§9.1 复算的七种出口都必须被接受（否则守护会退化成"一律报错"）。"""
    body = _op_hero_decision(pick, ban, leave, counter_wr=counter, rec=expected)
    validator_for("Playbook").validate(body)
    assert check_playbook(body) == [], (why, check_playbook(body))

@pytest.mark.parametrize("wrong", ["pick", "ban", "leave_and_counter"])
def test_spec_9_1_noise_window_rejects_any_confident_recommendation(validator_for, wrong):
    """§9.1：0.01 的噪声窗内只能给 insufficient_data（F3 的第三个变体）。"""
    body = _op_hero_decision(0.55, 0.54, 0.50, counter_wr=None, rec=wrong)
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any(f"recommendation={wrong} 与 §9.1 复算的 insufficient_data 不符" in e
               for e in errs), errs

def test_playbook_reports_all_offenders_with_location(validator_for):
    """报错必须点名位置（两处 OpHeroOption 同形，只有位置能区分）。"""
    body = copy.deepcopy(SPEC_PLAYBOOK)
    body["bans"]["consider"][0]["if_we_leave_it_open"]["recommendation"] = "pick"
    body["op_hero_decision"][0]["if_we_leave"]["their_wr"] = 0.50
    validator_for("Playbook").validate(body)
    errs = check_playbook(body)
    assert any(e.startswith("bans.consider[0].if_we_leave_it_open hero 77") for e in errs), errs
    assert any(e.startswith("op_hero_decision[0] hero 83") for e in errs), errs


# ── §6.6 Advise ───────────────────────────────────────────────────────────

def test_advise_handles_offline_branches_shape():
    """offline 模式返回 branches[].plans[]，不含 options —— 不得 KeyError。"""
    body = {"branches": [{"branch_id": "A", "condition": {"first_pick": "us", "their_opening": "teamfight"},
                          "plans": [{"label": "A1", "expected_wr": 0.52, "robustness_delta": 0.04,
                                     "penalized_score": 0.52,
                                     "key_picks": [{"hero_id": 105, "fallback": [67]}]}]}]}
    assert check_advise(body) == []

def test_advise_rejects_empty_counterparty_plan(validator_for):
    """F6/§6.6：每项必须有非空 counterparty_plan —— schema（minLength）与 check 两层都拦。"""
    body = copy.deepcopy(SPEC_ADVISE)
    body["options"][0]["counterparty_plan"] = ""
    with pytest.raises(jsonschema.ValidationError):
        validator_for("Advise").validate(body)
    errs = check_advise(body)
    assert any("counterparty_plan 为空" in e for e in errs), errs

def test_advise_rejects_non_finite_score(validator_for):
    """I5：NaN 的 expected_wr 会让降权等式静默通过；NaN 也能过 schema 的数值界，
    故 checker 的非有限扫描是这条不变式的唯一防线。"""
    body = copy.deepcopy(SPEC_ADVISE)
    body["options"][0]["expected_wr"] = float("nan")
    validator_for("Advise").validate(body)
    errs = check_advise(body)
    assert any("非有限数值" in e and "options[0].expected_wr" in e for e in errs), errs

def test_advise_lam_follows_app_config_robustness_lambda():
    """§6.6：λ 取自 app_config.robustness_lambda（默认 1.0），可配置。"""
    body = copy.deepcopy(SPEC_ADVISE)
    assert check_advise(body) == []
    body["options"][2]["penalized_score"] = 0.556 - 2.0 * (0.22 - 0.10)   # λ=2 下的正确值
    assert check_advise(body, lam=2.0) == []
    assert check_advise(body) != []


# ── §6.0 resolve()：check_resolve 的单测（此前零执行覆盖） ────────────────

def test_check_resolve_accepts_spec_6_2_example():
    """§6.2 示例：first_pick_team=0，第 12 手由后手方 pick → team=1。"""
    assert check_resolve(next_ord=12, team=1, is_pick=True, first_pick_team=0) == []

def test_check_resolve_rejects_wrong_team_and_type():
    errs = check_resolve(next_ord=12, team=0, is_pick=False, first_pick_team=0)
    assert any("归属应为 team 1" in e for e in errs), errs
    assert any("应为 pick" in e for e in errs), errs

def test_check_resolve_rejects_non_finite_argument():
    """I5 同族：NaN 会让 resolve() 抛 TypeError，必须在入口拦下。"""
    errs = check_resolve(next_ord=float("nan"), team=1, is_pick=True, first_pick_team=0)
    assert any("非有限数值" in e for e in errs), errs
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'contracts.tools.invariants'`

- [ ] **Step 3: 写 `contracts/tools/invariants.py`**

```python
"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。

**前置条件**：传入的 `body` 必须**已通过 schema 校验**（`contracts/openapi.yaml`
里对应的资源组件）。本模块只负责 JSON Schema 表达不了的那一半：

- 求和/等式——§6.1 的 delta 求和、§6.2 的概率求和、§6.3 的 `our_wr + their_wr`、
  §6.6 的降权等式（JSON Schema 没有算术）。
- 跨字段条件——§6.3 的先手权/`by_ord` 自洽、§9.1 的逐量门槛与 `recommendation`
  复算、§6.4 的 `role` 与降级联动（需要先算再判）。
- 跨条目一致性——有序性、非空列表、键集完整性、`applies_to_game` 的指向。
- 值的来源——`sources_used` ⊆ 请求的 `sources`。
- 非有限数值——`NaN`/`Infinity` 会让一切算术静默通过（见 `_nonfinite_errors`）。

形态类约束（required/enum/range/降级形态/对象封闭性/oneOf 判别）由 schema 负责。
本模块里少数与 schema 重复的形态判断是**防御性的**：把 live 响应直接喂进来时
也能得到一条可读错误，而不是 KeyError——不构成第二套定义。
"""
from __future__ import annotations
import math
from shared.draft_template import resolve

# 容差：规格 §6.1「容差 ±0.001」、§6.2「容差 ±0.001」、§6.3「±0.001」、
# §6.6「（±0.001）」——四处一致，故只此一个常量，不得各写各的。
TOL = 1e-3


def _nonfinite_errors(node, path: str = "$") -> list[str]:
    """递归找出 NaN/±Infinity（I5）。

    JSON 标准不允许裸 `NaN`/`Infinity`，但 `json.loads` 默认接受它们，而
    `abs(nan - x) > TOL` 恒为 False —— 所有求和/等式校验都会被**静默绕过**。
    故每个 `check_*` 都在最前面扫一遍；发现非有限数值即提前返回：
    此时一切算术都无意义（`resolve(by_ord=nan)` 还会直接抛 TypeError）。
    """
    if isinstance(node, float) and not math.isfinite(node):
        return [f"{path}: 非有限数值 {node}（NaN/Infinity 会让求和校验静默通过）"]
    errs: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            errs += _nonfinite_errors(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            errs += _nonfinite_errors(v, f"{path}[{i}]")
    return errs


# check_resolve 校验 §6.2/§6.6 的 next_ord/team/is_pick 推导。
# **本 chunk 的 fixtures 是纯响应体，不含请求上下文**，故无法在 fixture 层调用它；
# 它由 Plan 2+ 的接口集成测试使用（那里能同时拿到请求与响应）。
# 推导逻辑本身已在 Task 4 的 shared/draft_template.py 中受测。
def check_resolve(next_ord: int, team: int, is_pick: bool, first_pick_team: int) -> list[str]:
    errs = _nonfinite_errors([next_ord, team, is_pick, first_pick_team], "resolve 参数")
    if errs:
        return errs
    exp_pick, exp_team = resolve(next_ord, first_pick_team)
    if bool(exp_pick) != bool(is_pick):
        errs.append(f"ord {next_ord} 类型应为 {'pick' if exp_pick else 'ban'}")
    if exp_team != team:
        errs.append(f"ord {next_ord} 归属应为 team {exp_team}，实际 {team}")
    return errs


def check_value(body: dict, requested_sources: list[str] | None = None) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
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
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    s = sum(c["prob"] for c in body["candidates"]) + body["other_prob"]
    if abs(s - 1.0) > TOL:
        errs.append(f"sum(candidates.prob)+other_prob = {s:.4f} != 1.0")
    if len(body["candidates"]) > body["top_n"]:
        errs.append(f"len(candidates)={len(body['candidates'])} > top_n={body['top_n']}")
    for c in body["candidates"]:
        # schema 有 minItems:1，这里是防御性重复（live 响应可直接喂入）
        if not c.get("reasons"):
            errs.append(f"candidate hero {c.get('hero_id')}: reasons 为空（§6.2：不得返回无依据的候选）")
    return errs


def check_playbook(body: dict) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    mu = body["matchup"]
    us_team = mu["side_map"]["us"]
    want_first = "us" if us_team == mu["first_pick_team"] else "them"
    # 先手权自洽（§6.3）：单局剧本所有分支必须等于 expected；
    # 系列赛剧本（响应含 series）允许两种先手权，但**每个**分支都必须带
    # applies_to_game 指向 series.games[] 中的某一局。
    series = body.get("series")
    games = {g["game_no"]: g.get("first_pick_team")
             for g in (series or {}).get("games") or []}
    for br in body["branches"]:
        fp = br["condition"]["first_pick"]
        game_no = br.get("applies_to_game")
        if series is not None:
            if game_no is None:
                errs.append(f"分支 {br['branch_id']} 缺少 applies_to_game："
                            f"系列赛剧本的每个分支都必须指向 series.games[] 中的某一局（§6.3）")
                br_fpt = mu["first_pick_team"]
            elif game_no not in games:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 不在 series.games 中")
                br_fpt = mu["first_pick_team"]
            elif games[game_no] is None:
                errs.append(f"分支 {br['branch_id']} 的 applies_to_game={game_no} 指向的"
                            f"first_pick_team 为 null，无法推导 expected，不得作为目标（§6.3）")
                br_fpt = mu["first_pick_team"]
            else:
                g_fpt = games[game_no]
                exp = "us" if us_team == g_fpt else "them"
                if fp != exp:
                    errs.append(f"分支 {br['branch_id']} 在第 {game_no} 局的 first_pick 应为 {exp}")
                br_fpt = g_fpt
        else:
            if fp != want_first:
                errs.append(f"分支 {br['branch_id']} 的 first_pick 应为 {want_first}"
                            f"（单局剧本：响应不含 series；若要覆盖另一先手权，"
                            f"需带 series 与 applies_to_game）")
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
    # consider[].if_we_leave_it_open 与 op_hero_decision[] 同形（§6.3），共用一份检查
    for i, bc in enumerate((body.get("bans") or {}).get("consider") or []):
        _check_op_hero_option(bc["if_we_leave_it_open"],
                              f"bans.consider[{i}].if_we_leave_it_open", errs)
    for i, oh in enumerate(body.get("op_hero_decision") or []):
        _check_op_hero_option(oh, f"op_hero_decision[{i}]", errs)
    return errs


# ── §9.1 的逐量门槛与 recommendation 复算 ────────────────────────────────
_N_MIN = 30        # §9.1：三个胜率量各自的 n >= 30（§6.3：「逐量判定，不是逐条目判定」）
_OP_DELTA = 0.02   # §9.1：最大与次大之差 < 0.02 → 噪声内（app_config.op_decision_min_delta 默认值）


def _wr_n(q: dict) -> tuple[float | None, int | None]:
    """取一个胜率量的 (胜率, n)。

    `if_we_pick`/`if_we_ban` 是 `WinRateSample`（`wr`），`if_we_leave` 是
    `LeaveEvaluation`（`our_wr`）——**两者形状不同**（§6.3），故分开取。
    降级形态（§6.0 第一层，只有 value/reason/needs）没有胜率 → (None, None)。
    """
    if "wr" in q:
        return q["wr"], q.get("n")
    if "our_wr" in q:
        return q["our_wr"], q.get("n")
    return None, None


def _max_counter_wr(leave: dict) -> float | None:
    """`wr_counter` = 我方反制选项的最高胜率（§9.1）。全部降级时无值 → None。"""
    wrs = [c["wr"] for c in leave.get("our_counter_options") or [] if "wr" in c]
    return max(wrs) if wrs else None


def _check_op_hero_option(oh: dict, where: str, errs: list[str]) -> None:
    """OpHeroOption 的两处使用共用（§6.3：`consider[].if_we_leave_it_open` 与
    `op_hero_decision[]` **同形**，不得各写一套）——一处修好，两处生效。"""
    tag = f"{where} hero {oh['hero_id']}"
    lv = oh["if_we_leave"]
    if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
        errs.append(f"{tag}: our_wr + their_wr != 1.0（同一批比赛的两个视角，§6.3）")
    errs.extend(_recommendation_errors(oh, tag))


def _recommendation_errors(oh: dict, tag: str) -> list[str]:
    """§9.1 的数值判定规则，逐字对应规格伪代码（规格 §9.1 原文）：

        if 任一项 n < 30:                      -> insufficient_data
        elif max(wr_pick, wr_ban, wr_leave) - second_max < 0.02: -> insufficient_data
        elif wr_pick 为最大:                    -> pick
        elif wr_ban  为最大:                    -> ban
        else:                                   -> leave_and_counter
                                                （并要求 wr_counter > wr_leave，否则退化为 ban）

    `wr_pick`/`wr_ban`/`wr_leave` 是三个量各自的胜率（`if_we_leave` 取 `our_wr`；
    `their_wr` 是"对手拿的胜率"，只用于 `our_wr + their_wr == 1.0`，不参与比较）。
    规格未写明的一处（本实现取保守解并在此声明）：三个量**全部或部分降级**时
    无法取 max，按第一条判为 `insufficient_data`；`if_we_leave` 为最大但
    `our_counter_options[]` 全部降级时取不到 `wr_counter`，按"否则退化为 ban"处理。
    """
    errs: list[str] = []
    wr: dict[str, float] = {}
    short: list[str] = []
    for qname in ("if_we_pick", "if_we_ban", "if_we_leave"):
        w, n = _wr_n(oh[qname])
        if w is None:            # 已是降级形态：n 不可得 ⇒ §9.1 视作 n < 30
            short.append(f"{qname}(已降级)")
        elif n is None or n < _N_MIN:
            errs.append(f"{tag}: {qname} 的 n={n} < {_N_MIN} 却未降级"
                        f"（§9.1 的门槛逐量判定，不是逐条目判定）")
            short.append(f"{qname}(n={n})")
        else:
            wr[qname] = w
    rec = oh["recommendation"]
    if short:
        if rec != "insufficient_data":
            errs.append(f"{tag}: {'、'.join(short)} 样本不足，"
                        f"recommendation={rec} 应为 insufficient_data（§9.1）")
        return errs
    ranked = sorted(wr.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top = ranked[0]
    second_name, second = ranked[1]
    if top - second < _OP_DELTA:
        expected = "insufficient_data"
        why = (f"最大 {top_name}={top:.4f} 与次大 {second_name}={second:.4f} 之差 "
               f"{top - second:.4f} < {_OP_DELTA}（噪声内）")
    elif top_name == "if_we_pick":
        expected, why = "pick", f"最大 {top_name}={top:.4f}"
    elif top_name == "if_we_ban":
        expected, why = "ban", f"最大 {top_name}={top:.4f}"
    else:
        counter = _max_counter_wr(oh["if_we_leave"])
        if counter is not None and counter > top:
            expected = "leave_and_counter"
            why = f"最大 if_we_leave={top:.4f} 且 wr_counter={counter:.4f} > wr_leave"
        else:
            expected = "ban"
            why = (f"最大 if_we_leave={top:.4f} 但 wr_counter={counter} 未超过 wr_leave"
                   f"（§9.1：要求 wr_counter > wr_leave，否则退化为 ban）")
    if rec != expected:
        errs.append(f"{tag}: recommendation={rec} 与 §9.1 复算的 {expected} 不符（{why}）")
    return errs


def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    """支持两种 mode：realtime 返回 options[]，offline 返回 branches[].plans[]。

    `lam` 即 §6.6 的 λ，取自 `app_config.robustness_lambda`（默认 1.0）：
    排序依据是 `expected_wr − λ × max(0, robustness_delta − 0.10)`，不是 `expected_wr`。
    """
    errs = _nonfinite_errors(body)
    if errs:
        return errs
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
            # 以下几项 schema 已能表达（robustness_delta>0.10 ⇒ risk_note、
            # fallback/counterparty_plan 非空）——这里是**防御性重复**：
            # 未过 schema 的 live 响应直接喂进来时仍得到可读错误，而不是静默通过。
            if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
                errs.append(f"{gname}/{key}: robustness_delta > 0.10 但缺 risk_note")
            # realtime 的 fallback 在 option 级；offline 的在 key_picks[] 内
            if "fallback" in o and not o["fallback"]:
                errs.append(f"{gname}/{key}: fallback 为空")
            # §6.6：options[] 每项必须有非空 counterparty_plan（与 fallback 同级的硬要求）
            if "counterparty_plan" in o and not o["counterparty_plan"]:
                errs.append(f"{gname}/{key}: counterparty_plan 为空")
    return errs


_ARCHETYPES = {"initiate", "protect", "push", "teamfight", "pickoff", "splitpush"}

# §7.3：这五项是**依赖位置**的维度；role 推断不出时必须整体降级为 insufficient_samples。
# hero_archetype 不依赖位置，仍必须返回。
_POSITION_DIMENSIONS = ("hero_pool", "laning", "combat", "map_vision", "tempo")


def check_profile(body: dict) -> list[str]:
    errs = _nonfinite_errors(body)
    if errs:
        return errs
    for p in body["players"]:
        dims = p["dimensions"]
        ha = dims["hero_archetype"]
        if set(ha) != _ARCHETYPES:
            missing = sorted(_ARCHETYPES - set(ha))
            extra = sorted(set(ha) - _ARCHETYPES)
            errs.append(f"player {p['account_id']}: hero_archetype 键集不完整"
                        f"（缺 {missing}，多 {extra}）")
        elif abs(sum(ha.values()) - 1.0) > TOL:
            errs.append(f"player {p['account_id']}: hero_archetype 和 != 1.0")
        # §6.4：effective_count 的上界依据 —— effective_count <= window_games / 3。
        # 用整数乘法避免浮点边界（3 × effective_count <= window_games 与规格等价）。
        hp = p["hero_pool"]
        if 3 * hp["effective_count"] > hp["window_games"]:
            errs.append(f"player {p['account_id']}: effective_count={hp['effective_count']} > "
                        f"window_games/3 = {hp['window_games']}/3（§6.4）")
        # §6.4 + §7.3：role 推断不出（null 或缺省）时，五个依赖位置的维度
        # 必须都是 §6.0 的降级形态且 reason == insufficient_samples
        if p.get("role") is None:
            for dname in _POSITION_DIMENSIONS:
                d = dims.get(dname)
                if not (isinstance(d, dict) and d.get("value", 0) is None
                        and d.get("reason") == "insufficient_samples"):
                    errs.append(f"player {p['account_id']}: role 为 null 时 {dname} 必须降级为 "
                                f"insufficient_samples（§6.4/§7.3），实际 {d!r}")
    return errs


# ─────────────────────────────────────────────────────────────────────────
# 尚未落成可执行守护的规则清单（**有意留白，不是遗忘**）——免得下一轮重新发现：
#
# 需要请求上下文（fixtures 是纯响应体，拿不到请求；归 Plan 2+ 的接口集成测试）：
#   1. `sources_used` ⊆ 请求的 `sources`：check_value(..., requested_sources=...) 已就绪，
#      Policy/Playbook/Profile/Advise 的对应参数待接口层接入。
#   2. `check_resolve`：§6.2/§6.6 的 next_ord/team/is_pick 由 §6.0 resolve() 推出，
#      需要请求的 draft/first_pick_team —— 本模块已实现并有单测，fixture 层无法调用。
#   3. §6.2：`candidates[].hero_id` 不得与请求 draft 重复。
#   4. §6.6：mode=offline 时请求的 `branches` 参数与响应 `branches[].condition` 的双射
#      （「每个请求值都必须出现」）。
#   5. §6.6：`options[].hero_id` 必须已被 resolve() 判定为可行动作（不与 draft 重复）。
#
# 与规格示例直接冲突、需先做规格决策（F4）：
#   6. §6.3「所有（2 先手 × 7 体系）组合必须被覆盖或有显式 unknown 分支」——
#      §6.3 自身的示例只有 2 个分支，与该条冲突；示例同时是 §15 模板推导测试的输入，
#      故不实现，等规格决策。
#
# 已知留白 / 待规格澄清（不加固，避免把猜测冻结进契约）：
#   7. §9.1 原文说五个量（含 wr_counter）都要 n >= 30；本轮按控制器决定只对
#      OpHeroOption 内的三个量强制，故 `our_counter_options[].n < 30` **不要求降级**，
#      `wr_counter` 直接取可用项的最大值（见 _max_counter_wr）。
#   8. §6.3：`applies_to_game` 出现在不带 series 的响应里（且 first_pick 恰好等于
#      单局 expected）不会被拒 —— 无害，但语义未定义。
#   9. §6.3：`bans.consider[].hero_id` 与 `if_we_leave_it_open.hero_id` 未校验一致。
#  10. §6.3：`data_quality.oldest_pending_hours` 为 null **当且仅当**
#      `n_pending_draft == 0` —— 已在规格显式规定（本轮只做文档化），未落成守护。
#  11. 明确跳过的两项：`Assumptions` 组件改名（M6，纯外观）、`pct` 的取整模式
#      （M8，Plan 3 territory）。
# ─────────────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: **45 passed**

- [ ] **Step 5: 写五个 schema 文件**

按规格 §6.1–§6.6 的响应示例逐字段落成 JSON Schema。硬性要求：

1. **组件名必须恰好是** `Value` / `Policy` / `Playbook` / `Profile` / `Advise`——`validate_fixtures.py` 用 `resource.capitalize()` 查找。
2. **所有降级字段必须 `$ref` 到 `Degraded`**，不得内联同形对象。否则规格 §15 的「降级契约测试」对它们不生效，`playbook__op_insufficient.json` 的反向保护（`needs` 必须缺席）也失去约束。
3. 规格标 `// optional` 的字段不进 `required`，并在 properties 里标 `x-optional: true`（约定写在 `contracts/schemas/common.yaml` 头部；`test_schema_shape.py` 逐组件锚定「`required` == 未标 x-optional 的属性集」，删 required 或漏标注都会变红）。
4. **每个资源 schema 必须有非空的 `required` 与 `properties`**——空 schema `{}` 会让所有 fixture 通过，使 M0 验收第 2 条形同虚设。
5. **降级位必须封闭**：`oneOf: [X, {$ref: Degraded}]` 一律加 `unevaluatedProperties: false`（只有校验成功的分支贡献已求值属性，故降级形态不得夹带 `percentile`/`wr`/`n` 等绝对值）；`PositionNote` 用 `$ref Degraded + unevaluatedProperties: false + 自己的 properties`，不重列 `value`/`reason`。**不得**给 `Degraded` 加 `additionalProperties: false`（会把 `$ref` 复用它再添字段的位置误杀）。

**同时写 `tests/contracts/test_schema_shape.py`**，把上述要求变成机器可检的断言：

```python
"""契约形态的机器可检断言：空 schema / 降级字段必须 $ref / required 锚定 / 降级位封闭。

这些是**结构性**约束——它们不校验某个 fixture，而是防止契约本身退化
（例如把 required 删空、把降级对象内联、让降级位夹带绝对值）。
"""
import jsonschema
import pytest

RESOURCES = ["Value", "Policy", "Playbook", "Profile", "Advise"]


def test_all_five_resources_are_defined(common):
    missing = [r for r in RESOURCES if r not in common]
    assert missing == [], f"契约缺少资源 schema: {missing}"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_schema_is_not_empty(common, name):
    schema = common[name]
    assert schema.get("required"), f"{name} 没有 required 字段——空 schema 会让任何 fixture 通过"
    assert schema.get("properties"), f"{name} 没有 properties"

@pytest.mark.parametrize("name", RESOURCES)
def test_resource_rejects_empty_body(validator_for, name):
    """F8 反向：五个资源都不得接受 `{}`（证明 schema 不是空壳、required 真在生效）。"""
    with pytest.raises(jsonschema.ValidationError):
        validator_for(name).validate({})

def test_every_degraded_field_refs_the_Degraded_component(contract_doc):
    """规格 §15 的降级契约依赖此约束：降级字段必须 $ref，不得内联同形对象。

    **遍历 components.schemas 的每一个组件**（跳过 `Degraded` 自身——它是合法定义）。
    只从五个资源节点出发的旧写法走不进被 `$ref` 的组件内部：在
    `LeaveEvaluation` 之类的组件里内联一个 `{value, reason}` 可以完全逃逸（F7）。
    """
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
    for name, schema in schemas.items():
        if name == "Degraded":
            continue
        walk(schema, name)
    assert offenders == [], f"以下位置内联了降级对象而非 $ref Degraded: {offenders}"

def test_required_matches_declared_optionality(common):
    """F8：`required` 必须恰好等于「未标 `x-optional` 的属性集」（约定见 common.yaml 头部）。

    删掉 `Playbook.required` 里的一项、或让某个属性悄悄变成必填，都会在此变红。
    注：本断言按**组件**锚定（约定所在层级）；内联子对象的 required 不在本断言范围内。
    """
    offenders = []
    for name, schema in common.items():
        props = schema.get("properties")
        if not isinstance(props, dict) or not props:
            continue
        expected = {p for p, sub in props.items()
                    if not (isinstance(sub, dict) and sub.get("x-optional"))}
        actual = set(schema.get("required") or [])
        if actual != expected:
            offenders.append(f"{name}: required={sorted(actual)} 应为 {sorted(expected)}")
    assert offenders == [], ("required 与 x-optional 标注不一致（新增可选字段请标 x-optional）: "
                             + "；".join(offenders))

@pytest.mark.parametrize("name, body", [
    ("ProfileRef", {"team_id": 1, "name": "A", "tag": "A"}),                       # logo_url
    ("SeriesGame", {"game_no": 1, "first_pick_team": None}),                       # note
    ("PolicyBaseline", {"frequency_top1": None}),                                  # model_top1
    ("CoverageEntry", {"n_matches": 0, "n_stat_available": 0}),                    # n_position_unknown
    ("AdviseOption", {"hero_id": 1, "expected_wr": 0.5, "robustness_delta": 0.04,
                      "penalized_score": 0.5, "why": "x", "fallback": [2],
                      "counterparty_plan": "y"}),                                  # risk_note
])
def test_x_optional_properties_may_be_omitted(validator_for, name, body):
    """`x-optional` 不只是标注：省略这些属性必须仍然合法（否则标注是假的）。"""
    validator_for(name).validate(body)

# ── I4：四个 oneOf 降级位必须封闭 ─────────────────────────────────────────
# 规格 §6.4/§6.5：降级形态只含 value/reason(/needs)。若 oneOf 不封闭，
# `{"value": null, "reason": ..., "percentile": 72}` 这类"降级但仍带绝对值"
# 的响应会通过——Phase A 边界（§6.5）与 §7.3 的门槛同时失效。

@pytest.mark.parametrize("name, body", [
    ("PercentileDimension", {"value": None, "reason": "insufficient_samples", "percentile": 72}),
    ("PercentileDimension", {"value": None, "reason": "needs_replay", "needs": "Phase B",
                             "percentile": 72, "n": 120}),
    ("WinRateSample", {"value": None, "reason": "stat_unavailable", "wr": 0.44, "n": 4}),
    ("LeaveEvaluation", {"value": None, "reason": "insufficient_samples", "our_wr": 0.55}),
    ("CounterOption", {"value": None, "reason": "insufficient_samples",
                       "hero_id": 36, "wr": 0.61, "n": 31}),
    ("PositionNote", {"kind": "ward", "text": "x", "evidence": [], "value": None,
                      "reason": "needs_replay", "needs": "Phase B", "percentile": 72}),
])
def test_degraded_branches_are_closed(validator_for, name, body):
    with pytest.raises(jsonschema.ValidationError):
        validator_for(name).validate(body)

@pytest.mark.parametrize("name, body", [
    ("PercentileDimension", {"value": None, "reason": "insufficient_samples"}),
    ("PercentileDimension", {"percentile": 72, "n": 120}),
    ("WinRateSample", {"value": None, "reason": "stat_unavailable"}),
    ("LeaveEvaluation", {"value": None, "reason": "insufficient_samples"}),
    ("CounterOption", {"value": None, "reason": "insufficient_samples"}),
    ("PositionNote", {"kind": "ward", "text": "x", "evidence": [], "value": None,
                      "reason": "needs_replay", "needs": "Phase B"}),
])
def test_degraded_branches_accept_the_legal_forms(validator_for, name, body):
    """封闭不等于误杀：合法的降级形态与合法有值形态都必须继续通过。"""
    validator_for(name).validate(body)

def test_position_note_keeps_needs_conditionality(validator_for):
    """PositionNote 改成 `$ref Degraded` + `unevaluatedProperties:false` 后，
    §6.0 的 needs 条件规则（仅 needs_replay 必填、其余必须省略）必须原样生效。"""
    v = validator_for("PositionNote")
    base = {"kind": "ward", "text": "x", "evidence": []}
    v.validate({**base, "value": None, "reason": "needs_replay", "needs": "Phase B"})
    v.validate({**base, "value": None, "reason": "insufficient_samples"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "value": None, "reason": "needs_replay"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "value": None, "reason": "insufficient_samples", "needs": "Phase B"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**base, "hero_id": 1, "value": None, "reason": "insufficient_samples"})
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: **82 passed**（6 公共 + 45 不变式 + 31 schema 形状）

- [ ] **Step 7: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): 五资源 schema + 可执行不变式（降级字段一律 ref Degraded）"
```

---

### Task 7: 17 个边界 fixtures 与校验工具

**Files:**
- Create: `contracts/fixtures/*.json`（17 个）、`contracts/fixtures/README.md`
- Create: `contracts/tools/validate_fixtures.py`
- Test: `tests/contracts/test_fixtures.py`

- [ ] **Step 1: 先写测试（保证有红态）**

（先写测试：`validate_fixtures.py` 尚不存在，Step 2 运行时应报
`ModuleNotFoundError: No module named 'contracts.tools.validate_fixtures'`。
本文件除计划原有的 `main() == 0` 外，还有控制器追加的**边界覆盖**测试——只跑
`main()` 只能证明 fixture 合法，证明不了它真的覆盖了规格 §11 承诺的边界。）

```python
"""fixture 校验入口与**边界覆盖**守护（计划 Task 7）。

本文件的两条测试守两件不同的事：

1. `test_all_fixtures_conform`（计划 Task 7 Step 1 原文）：每个 fixture 都
   **合法**——过其资源 schema，且过 `contracts/tools/invariants.py` 的该资源不变式。
2. `test_every_fixture_covers_its_declared_boundary`（控制器追加）：每个 fixture
   都**有覆盖**——规格 §11 / 计划 Task 7 Step 4 表声明的那条边界真的落在它身上。

第 2 条不可省：只有第 1 条时，fixture 可以被"漂白"成合法但平庸的响应（例如把
`value__low_confidence.json` 改成 `confidence:"high"` + `n_samples:412`——schema
与不变式全绿），前端于是对着**从未触发边界**的 mock 开发，而 mock 的全部价值
恰在边界上。`MIN_FIXTURES` 只保证"数量够"，保证不了"覆盖到"。

谓词约定：
- 每条谓词只读该 fixture 自身的响应体——fixtures 是**纯响应体**，没有请求上下文。
  依赖请求的边界（`sources_used ⊆ sources`、`next_ord == resolve(...)`、
  `options[].hero_id` 不与 draft 重复）由 Plan 2+ 的接口集成测试覆盖，
  见 `contracts/tools/invariants.py` 末尾的留白清单。
- 谓词在 schema/不变式之外断言"边界的**存在性**"：哪些字段真的出现、哪些值真的
  为 null、哪种形状真的被选中。算术（求和/等式/复算）不在这里重复——那是
  `main()` 的职责，重复一份只会制造第二个定义。
- 新增 fixture 必须同时在 `CASES` 里加一行：下面的 `set(paths) == set(CASES)`
  双向比对会让"新文件未登记"与"表里有文件却不存在"都立刻变红。
"""
from __future__ import annotations
import json
import pathlib

from contracts.tools.validate_fixtures import main

FIX = pathlib.Path(__file__).parents[2] / "contracts" / "fixtures"


def test_all_fixtures_conform():
    assert main() == 0


# ── 每条边界的谓词（一条 = 计划 Task 7 Step 4 表的一行）────────────────────

# 规格 §6.0「降级契约第一层」的原样形态：value 必为 null、reason 必填，
# 非 needs_replay 时**必须省略** needs（不得传 null）。用整字典相等而不是
# 逐键判断，是为了让"多夹带一个绝对值/needs"也同样失败。
_DEGRADED_INSUFFICIENT = {"value": None, "reason": "insufficient_samples"}

# 规格 §7 维度 6 + §6.4：六类原型原型恒为这六项（含 initiate）。
_ARCHETYPES = {"initiate", "protect", "push", "teamfight", "pickoff", "splitpush"}

# 规格 §7.3 / §6.4：这五项依赖位置（同侪集合按位置切分）；role 推断不出时必须
# 整体降级，而 hero_archetype 不依赖位置、仍必须返回。
_POSITION_DIMENSIONS = ("hero_pool", "laning", "combat", "map_vision", "tempo")


def _value_spec_example(b: dict) -> bool:
    """规格 §6.1「→ 200」示例（真实比赛 8996973546 的加项分解）。

    识别标志：四个 delta 逐字等于规格示例（0.011 / -0.014 / 0.024 / 0.009），
    `n_samples=412` 落在 high 桶。求和不变式（0.030 == 0.530 - 0.5）由
    `check_value` 守，这里只认身份。
    """
    return (b["radiant_win_prob"] == 0.530 and b["confidence"] == "high"
            and b["n_samples"] == 412
            and {c["factor"]: c["delta"] for c in b["contributions"]}
            == {"patch_strength": 0.011, "counter_matchup": -0.014,
                "player_comfort": 0.024, "first_pick": 0.009})


def _value_low_confidence(b: dict) -> bool:
    """规格 §11 表（value__low_confidence 行）/ §6.0 枚举表：n_samples < 30 → low。

    低样本是本项目的常态（对手在本版本往往只有个位数场次），前端必须能渲染
    `confidence:"low"` 而不是只见过 high。
    """
    return b["confidence"] == "low" and b["n_samples"] < 30


def _policy_spec_example(b: dict) -> bool:
    """规格 §6.2「→ 200」示例（真实比赛 8996973546 的前 12 手 BP）。

    识别标志：`draft` 长度 12 ⇒ `next_ord = 12`（§6.0：next_ord = max(ord)+1），
    由 §6.0 模板推出 team=1 / is_pick=true；候选 hero 112 与两条真实比赛 id。
    """
    cand = b["candidates"][0]
    return (b["next_ord"] == 12 and b["team"] == 1 and b["is_pick"] is True
            and cand["hero_id"] == 112
            and cand["evidence_match_ids"] == [8996973546, 8988636430])


def _policy_no_model(b: dict) -> bool:
    """规格 §11 表（policy__no_model 行）/ §6.2：模型未就绪态。

    `baseline.model_top1` 为 null 且**字段存在**（必填但可空，不得省略字段），
    `model` 同样为 null。M6 的前端必须能渲染"无模型"而不是显示 0 或崩溃。
    """
    return ("model_top1" in b["baseline"] and b["baseline"]["model_top1"] is None
            and b["model"] is None)


def _playbook_spec_example(b: dict) -> bool:
    """规格 §6.3「→ 200」示例（真实比赛 8996973546，series 1141522）。

    识别标志：真实队伍 id、`MustBan` 的 hero 55 且 n=24、以及示例修正后的自洽
    事实——**两个分支都带 `applies_to_game`**，第 2 局 `first_pick_team = 1`
    （规格 §6.3「示例验算」段）。
    """
    games = {g["game_no"]: g["first_pick_team"] for g in b["series"]["games"]}
    return (b["matchup"]["us"]["team_id"] == 10251056
            and b["matchup"]["them"]["team_id"] == 10232231
            and b["series"]["series_id"] == 1141522
            and games == {1: 0, 2: 1}
            and [br.get("applies_to_game") for br in b["branches"]] == [1, 2]
            and b["bans"]["must_ban"][0]["hero_id"] == 55
            and b["bans"]["must_ban"][0]["n"] == 24
            and b["op_hero_decision"][0]["recommendation"] == "leave_and_counter")


def _playbook_draft_incomplete(b: dict) -> bool:
    """规格 §11 表（playbook__draft_incomplete 行）/ §12 R1：数据不完整的载体。

    `n_pending_draft > 0` 且 `n_unavailable_draft > 0`——UI 据此标注"统计不完整"。
    同时钉住 §6.3 的当且仅当：pending > 0 ⇒ `oldest_pending_hours` 必须给值
    （不得用 0 冒充"有值"）。
    """
    dq = b["data_quality"]
    return (dq["n_pending_draft"] > 0 and dq["n_unavailable_draft"] > 0
            and dq["oldest_pending_hours"] is not None)


def _playbook_anomalous(b: dict) -> bool:
    """规格 §11 表（playbook__anomalous 行）/ §5.3：异常手序的场次计数必须暴露。

    `n_anomalous_draft > 0`（不得静默剔除异常场次）。本 fixture 落在
    `n_pending_draft == 0` 一侧，故同时钉住 §6.3「oldest_pending_hours 为 null
    当且仅当 n_pending_draft == 0」的另一半。
    """
    dq = b["data_quality"]
    return (dq["n_anomalous_draft"] > 0 and dq["n_pending_draft"] == 0
            and dq["oldest_pending_hours"] is None)


def _playbook_positions_phase_b(b: dict) -> bool:
    """规格 §11 表（playbook__positions_phase_b 行）/ §6.0 第一层 + §6.5。

    两半合起来才是"`needs` 存在**当且仅当** reason == needs_replay"：
    - ward（眼位坐标，Phase A 必须不存在）降级为 `value: null` +
      `reason: "needs_replay"` + `needs: "Phase B"`；
    - 另有一条非 needs_replay 的降级 note **不含** `needs`
      （§6.0：其余 reason 下必须省略该字段，不得传 null）。
    """
    notes = [n for p in b["positions"] for n in p["notes"]]
    ward = [n for n in notes if n["kind"] == "ward"]
    others = [n for n in notes if n["reason"] != "needs_replay"]
    return (bool(ward)
            and all(n["value"] is None and n["reason"] == "needs_replay"
                    and n["needs"] == "Phase B" for n in ward)
            and bool(others)
            and all("needs" not in n for n in others))


def _playbook_op_insufficient(b: dict) -> bool:
    """规格 §11 表（playbook__op_insufficient 行）/ §9.1 + §6.0。

    某一胜率量样本不足（本 fixture 是 `if_we_ban`）⇒ 该量降级为
    `insufficient_samples` 且**无** `needs`，结论必须是 `insufficient_data`
    而非猜测（§9.1 第一条；§6.3：逐量判定，不是逐条目判定）。
    """
    oh = b["op_hero_decision"][0]
    ban = oh["if_we_ban"]
    return (oh["recommendation"] == "insufficient_data"
            and ban.get("value", 0) is None and "wr" not in ban
            and ban["reason"] == "insufficient_samples" and "needs" not in ban)


def _profile_spec_example(b: dict) -> bool:
    """规格 §6.4「→ 200」示例（`window_games = 48`）。

    识别标志：真实队伍 id、`window_games == 48`、signature 里 hero 55 的
    `12/48 = 25%`（§6.4 字段表的示例验算）、`effective_count = 14 <= 48/3`。
    """
    pool = b["players"][0]["hero_pool"]
    sig = pool["signature"][0]
    return (b["team_id"] == 10232231 and pool["window_games"] == 48
            and pool["effective_count"] == 14
            and (sig["hero_id"], sig["games"], sig["wr"], sig["pct"]) == (55, 12, 0.75, 25))


def _profile_map_vision_counts_only(b: dict) -> bool:
    """规格 §11 表（profile__map_vision_counts_only 行）/ §6.5。

    位置未知（`coverage.*.n_position_unknown > 0`）**不得**降级数量口径的
    `map_vision`——它返回真实 percentile；§6.5 明文提醒"不要误降级它"。
    本 fixture 里降级的是需要位置同侪集合的其他维度。
    """
    mv = b["players"][0]["dimensions"]["map_vision"]
    return (b["coverage"]["pro_match"]["n_position_unknown"] > 0
            and "percentile" in mv and "n" in mv and "reason" not in mv)


def _profile_position_unknown(b: dict) -> bool:
    """规格 §11 表（profile__position_unknown 行）/ §6.4 + §7.3。

    role 推断不出（null）⇒ 五个**依赖位置**的维度全部降级为
    `insufficient_samples`（且不带 needs），而**不依赖位置**的 `hero_archetype`
    仍返回六项、和为 1.0（±0.001）。这条区分专门防实现者把六维一起降级。
    """
    player = b["players"][0]
    dims = player["dimensions"]
    archetype = dims["hero_archetype"]
    return (b["coverage"]["pro_match"]["n_position_unknown"] > 0
            and player.get("role") is None
            and all(dims[d] == _DEGRADED_INSUFFICIENT for d in _POSITION_DIMENSIONS)
            and set(archetype) == _ARCHETYPES
            and abs(sum(archetype.values()) - 1.0) <= 0.001)


def _advise_realtime(b: dict) -> bool:
    """规格 §11 表（advise__realtime 行）/ §6.6：realtime 返回 `options[]` 形状。

    `mode` 是**请求**字段：`Advise` schema 是封闭的（additionalProperties:false）
    且没有 `mode` 属性，响应靠形状判别（schema 的 oneOf）——故谓词不能读
    `b["mode"]`，只能断言形状本身。
    """
    return ("options" in b and "branches" not in b and len(b["options"]) >= 1
            and {"next_ord", "team", "is_pick", "assumptions"} <= set(b)
            and all(o["fallback"] and o["counterparty_plan"] for o in b["options"]))


def _advise_offline(b: dict) -> bool:
    """规格 §11 表（advise__offline 行）/ §6.6 mode 表：offline 返回
    `branches[].plans[]`，不返回 `options[]`；每支必须带 `branch_id` 与
    `condition`（否则多分支结果无法归属）。同上：响应里没有 `mode` 字段。
    """
    return ("branches" in b and "options" not in b and len(b["branches"]) >= 1
            and all(br.get("branch_id") and br.get("condition") and br.get("plans")
                    for br in b["branches"]))


def _advise_robustness_penalized(b: dict) -> bool:
    """规格 §11 表（advise__robustness_penalized 行）/ §6.6 稳健性降权。

    三件事缺一不可：
    - `robustness_delta > 0.10` 的项带 `risk_note` 且 `penalized_score < expected_wr`
      （降权真的发生了）；
    - 该项的 `expected_wr` 是全体最高——否则"排序被降"没有证据；
    - 它不在首位——原始胜率最高却被排到后面，正是这条边界的可见形态
      （排序依据是 penalized_score，不是 expected_wr）。
    """
    options = b["options"]
    risky = [i for i, o in enumerate(options) if o["robustness_delta"] > 0.10]
    if not risky:
        return False
    i = risky[0]
    o = options[i]
    return (bool(o.get("risk_note")) and o["penalized_score"] < o["expected_wr"]
            and o["expected_wr"] == max(x["expected_wr"] for x in options) and i > 0)


def _error_insufficient_data(b: dict) -> bool:
    """规格 §11 表（error__insufficient_data 行）/ §6.0 第二层。

    整体样本不足是**业务结果**（HTTP 200，见 fixtures/README.md），
    `detail` 必须能说明"差多少"（n_samples < required），否则前端只能显示一句
    无信息量的错误。
    """
    err = b["error"]
    detail = err.get("detail") or {}
    return (err["code"] == "insufficient_data"
            and detail.get("n_samples") is not None
            and detail.get("required") is not None
            and detail["n_samples"] < detail["required"])


def _error_source_not_allowed(b: dict) -> bool:
    """规格 §11 表（error__source_not_allowed 行）/ §4.1 + §6.0 错误码表。

    请求了未授权的来源（对手画像路径请求 `scrim`）→ 403 且 `code` 为
    `source_not_allowed`；detail 点名被拒的来源，便于前端提示。
    """
    err = b["error"]
    return (err["code"] == "source_not_allowed"
            and (err.get("detail") or {}).get("source") == "scrim")


# 表 = 计划 Task 7 Step 4 的 17 行，一行一个文件。key 必须与文件名逐字相同。
CASES = {
    "advise__offline.json": _advise_offline,
    "advise__realtime.json": _advise_realtime,
    "advise__robustness_penalized.json": _advise_robustness_penalized,
    "error__insufficient_data.json": _error_insufficient_data,
    "error__source_not_allowed.json": _error_source_not_allowed,
    "playbook__anomalous.json": _playbook_anomalous,
    "playbook__draft_incomplete.json": _playbook_draft_incomplete,
    "playbook__op_insufficient.json": _playbook_op_insufficient,
    "playbook__positions_phase_b.json": _playbook_positions_phase_b,
    "playbook__spec_example.json": _playbook_spec_example,
    "policy__no_model.json": _policy_no_model,
    "policy__spec_example.json": _policy_spec_example,
    "profile__map_vision_counts_only.json": _profile_map_vision_counts_only,
    "profile__position_unknown.json": _profile_position_unknown,
    "profile__spec_example.json": _profile_spec_example,
    "value__low_confidence.json": _value_low_confidence,
    "value__spec_example.json": _value_spec_example,
}


def test_every_fixture_covers_its_declared_boundary():
    """每个 fixture 必须真的覆盖它声明的那条边界（防止 fixture 被漂白）。

    实现为**表驱动 + 逐行独立判定**（而不是 17 个 parametrize 用例）是刻意的：
    计划 Task 7/8 的计数（Step 5「2 passed」、Task 8「87 / 120 passed」）以本文件
    恰好两条测试为前提；逐行判定还能一次性报出**所有**丢覆盖的 fixture，
    而不是修一个跑一次。
    """
    paths = sorted(p.name for p in FIX.glob("*.json"))
    # 双向比对：新 fixture 未登记、表里的文件被删/改名，都必须失败。
    assert set(paths) == set(CASES), (
        f"CASES 表与 {FIX}/*.json 不一致：\n"
        f"  新增未登记: {sorted(set(paths) - set(CASES))}\n"
        f"  表中有但文件缺失: {sorted(set(CASES) - set(paths))}")

    failures = []
    for name in paths:
        body = json.loads((FIX / name).read_text(encoding="utf-8"))
        pred = CASES[name]
        try:
            ok = bool(pred(body))
        except Exception as exc:                    # 形状漂移也算丢覆盖，给出可读原因
            ok, note = False, f"（谓词求值失败：{type(exc).__name__}: {exc}）"
        else:
            note = ""
        if not ok:
            boundary = (pred.__doc__ or "").strip().splitlines()[0]
            failures.append(f"{name}: 未覆盖声明的边界「{boundary}」{note}")
    assert not failures, "fixture 丢失边界覆盖：\n" + "\n".join(failures)
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

- [ ] **Step 4: 写 17 个 fixture 与 `README.md`**

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
| `profile__map_vision_counts_only.json` | `map_vision` 返回真实 `percentile`（非降级）且与 `coverage.n_position_unknown > 0` 并存（ward 坐标降级归 `playbook__positions_phase_b.json`；Profile 无 `notes[]`，见 §6.4/§6.5） |
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
（实测环境 venv 不在 PATH 上：`make contract` 会报 `make: python: No such file or
directory`，故本轮改用 `.venv/bin/python -m contracts.tools.validate_fixtures`；
make 配方本身不动。）
Expected: `17 fixtures, 0 failures`；**2 passed**（Step 1 的合法 + 边界覆盖两条）

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

> ⚠️ **本块已被 `64c89a9` 取代——下面 47 行是首版草稿，不是权威实现。**
> **权威实现 = 仓库里已提交的 `contracts/tools/gen_ts_types.py`（201 行）。**
> 照抄下面的草稿会把 `64c89a9` 修掉的洞原样复活（Plan 4 扩展发射器时尤其危险——
> 要改的是已提交文件，不是本块）。硬化相对草稿新增：
> ① 枚举成员一律走 `json.dumps` 转义——草稿的 `f'"{v}"'` 对含 `"` / 反斜杠 / 换行的值
> 会产出非法 TS（`TS1002`）或**静默变形**（`back\slash` 编译成 `backslash`）；
> ② `UnrenderableEnum` 异常——带 `enum` 却渲染不出来的 schema **报错**，草稿则 `return None`
> 让枚举从生成物里静默消失而退出码仍是 0；
> ③ 冻结 `RESOURCE_MEMBERS` 基线，草稿把资源名写死在输出字符串里、无人看守；
> ④ **发射校验**：查的是「生成物里真的发射了这个类型」，不是草稿的「契约里有这个名字」；
> ⑤ `--check` 只读模式（配合 Step 2 的新鲜度测试，见该步的警示）；
> ⑥ `schema.description` 发射为 JSDoc（`Side`/`Team` 各多一行），并转义 `*/`；
> ⑦ `render()` 改为纯函数，全部校验在**任何写盘之前**完成。

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

**范围说明（Task 8 复核追加）——本任务只生成枚举，不生成对象接口：**

生成的**只有**顶层枚举联合（`Confidence`/`ErrorCode`/`Factor`/`NoteKind`/`Recommendation`/
`Side`/`Team`/`TheirOpening`/`UnavailableReason`）与 `Resource` 联合，共 10 个导出，别无其它。
契约里 53 个 schema 的**对象图不生成**：4 个 `oneOf` + `unevaluatedProperties` 联合
（`WinRateSample`/`LeaveEvaluation`/`CounterOption`/`PercentileDimension`）、`allOf` 条件
（`AdviseOption`/`Degraded`/`Plan`/`Profile`/`SideMap`/`Value`——`Advise` **没有** `allOf`，
它的互斥是 `oneOf`，见下表；其中 `Profile` 的 `allOf` 嵌在 `coverage` 内、`Value` 的另有一处
嵌在 `contributions` 内，其余在 schema 顶层）、`type: [X, "null"]` 数组
（如 `SeriesGame.first_pick_team`）、`$ref` 链、`required` / `x-optional` 全部没有对应 TS 类型。
**前端 worktree 不要以为有可用的对象类型**（例如 `Value`、`Policy`、`Playbook` 这些名字在
`contract.ts` 里只出现在 `Resource` 联合里，不是接口）。

**Plan 4 开工第一件事就是补齐它们**，做法是**扩展本仓库这个发射器**（`contracts/tools/
gen_ts_types.py`），而不是另起一个生成器：

| 契约构造 | 生成规则 |
|---|---|
| `required` 里的属性 | 必填字段（`name: T`） |
| 其余属性 | 可选（`name?: T`） |
| `allOf`，且各项是 `$ref` / `properties` | 展平成单个接口。本契约**只有一处**：`Profile.coverage` = `Coverage` 基类 + 补充属性 |
| `allOf`，且各项是 `if` / `then` / `not` / `contains` | **只用于校验，不生成类型**。`Plan`/`AdviseOption` 是 `if`/`then`，`Degraded` 是 `if`/`then`（其 `then` 里含 `not`），`SideMap` 是两条裸 `not`，`Value` 顶层是 `if`/`then`、`Value.contributions` 是 `contains`。**这些 `allOf` 里没有可展平的属性**——按上一行去展平会得到空接口 |
| `oneOf`，且分支带 `properties` / `$ref` | `A \| B \| C` 联合。本契约 4 处（`WinRateSample`/`LeaveEvaluation`/`CounterOption`/`PercentileDimension`），每处都是「内联对象 \| `Degraded`」 |
| `oneOf`，且分支只有 `required` / `not` | **只用于校验，不生成类型**——这是**跨字段条件必填**，不是值联合。本契约只有 `Advise`（分支 1 = `required: [next_ord, team, is_pick, options, assumptions]` + `not: {required: [branches]}`；分支 2 = `required: [branches]` + `not: {required: [options]}`），两个分支都**不是**形状。**绝不能渲染成 `A \| B`**：`Advise` 的 TS 对象类型只能来自它自己的 `properties`（`sources_used` 必填，其余六个 mode 专属字段可选），两个分支保持只校验 |
| 裸 `not` | **只用于校验，不生成类型**（`SideMap` 的 `allOf` 用两条 `not` 表达「`us`/`them` 不得同为 0、也不得同为 1」；`Degraded` 第二个 `then` 里的 `not` 同） |
| `$ref` | 引用目标 schema 的 TS 类型名 |
| `type: [X, "null"]` | `X \| null` |
| `additionalProperties: false` | 不生成索引签名（本契约多数对象是 `true`/缺省） |
| `if` / `then` | **只用于校验，不生成类型**（JSON Schema 的条件约束没有直接 TS 对应物） |

生成后同样要有冻结基线 + `--check` 新鲜度测试，理由与枚举一致：契约与生成物
可能被同一次改动一起重建，两边「一致」是自洽的退化。

**两条可复现性备注（Plan 4 需要处理）：**

1. **`web/tsconfig.json` 目前不存在**。本任务的 `tsc` 手工校验因此显式传了
   `--noEmit --strict --target es2020 --typeRoots /tmp/ts-empty-types`——`--typeRoots`
   是绕开父目录 `/Users/xinzechao/node_modules/@types/node`（它依赖未安装的
   `undici-types`，会报 5 条无关的 TS2792）。Plan 4 建前端工程时要落一份真的
   `tsconfig.json`（含 `strict`），把这套参数固化进去。
2. **Plan 4 需要给 `contract.ts` 配 `.prettierignore`（或调 `printWidth`）**。
   Prettier 默认 `printWidth: 80`，而生成物里 17 行（`wc -l`）中有 6 行超宽（`ErrorCode`、
   `NoteKind`、`TheirOpening`、`UnavailableReason` 等长联合）。按默认值格式化会
   **重写生成物**，让 `--check` 新鲜度测试因纯排版差异变红（假失败）。二选一：
   把 `web/src/types/contract.ts` 加进 `.prettierignore`（推荐，生成物本就不该手改），
   或在 Prettier 配置里给 TS 放宽 `printWidth`。

- [ ] **Step 2: 写新鲜度与完整性测试**

> ⚠️ **本块已被 `64c89a9` 取代——下面 104 行是首版草稿，不是权威实现。**
> **权威实现 = 仓库里已提交的 `tests/contracts/test_ts_types_fresh.py`（142 行）。**
> 尤其注意草稿里的 `test_generated_ts_is_up_to_date` 是**写盘后再比对**的形式
> （`subprocess.run([...])` 不带 `--check` → 先覆盖生成物，再断言文本没变）：
> 它把受版本控制的 `web/src/types/contract.ts` 当草稿纸，本地手改会被测试**静默抹掉**，
> 失败信息里的 diff 也随之消失。**已提交的测试明文禁止这种写法**，改用生成器的只读
> `--check`（见 Step 1 的警示 ⑤）。硬化相对草稿还新增：`RESOURCE_MEMBERS` 冻结基线，
> 以及 `assert_resource_matches` 的三方比对（冻结基线 ↔ 生成物 ↔ 契约的五个资源组件）
> ——`Resource` 此前是唯一无人看守的导出，换成 `'Value' | 'Policy' | 'Bogus'` 再重生成
> 曾能 87 条全绿。测试条数仍是 3 条（`Resource` 断言加在既有的逐成员测试里）。

```python
"""TS 生成物的新鲜度与完整性。

路径一律由 ``__file__`` 推出仓库根，**不依赖 cwd**：从 ``tests/`` 里跑
pytest 与从仓库根跑必须同结果（仓库内其余测试同样 cwd 无关）。
"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).parents[2]
TS = ROOT / "web" / "src" / "types" / "contract.ts"
GENERATOR = "contracts.tools.gen_ts_types"

# 生成器要求存在、前端需要引用的枚举（contracts/tools/gen_ts_types.REQUIRED_ENUMS
# 的超集检查见 test_ts_enum_members_match_the_contract：契约里的**每个**枚举都比对）
REQUIRED_ENUMS = ["Confidence", "Recommendation", "TheirOpening", "NoteKind",
                  "UnavailableReason", "ErrorCode", "Side"]

# 冻结的枚举成员基线——**前端可见枚举面的唯一权威期望**。
#
# 为什么不从 openapi.yaml 现算：契约与生成物可能被同一次改动一起重建，
# 那样两边的「一致」是自洽的退化，漏值不会被发现（实测：删掉
# TheirOpening.unknown 后重建两边，只比对二者的测试全绿）。基线把期望钉在
# 评审过的字面量上，删成员必须连带改这里，于是**必然**出现在 diff 里。
# 改名/增删成员的步骤：改契约 → 重建 → 改这里 → 重生成 → 一起提交。
ENUM_MEMBERS: dict[str, list[str]] = {
    "Confidence": ["low", "medium", "high"],
    "ErrorCode": ["insufficient_data", "invalid_request", "source_not_allowed",
                  "not_found", "upstream_unavailable"],
    "Factor": ["patch_strength", "counter_matchup", "player_comfort", "first_pick"],
    "NoteKind": ["ward", "timing", "lane", "smoke", "combat", "resource", "communication"],
    "Recommendation": ["pick", "ban", "leave_and_counter", "insufficient_data"],
    "Side": ["us", "them"],
    "Team": ["0", "1"],
    "TheirOpening": ["teamfight", "push", "pickoff", "splitpush", "protect",
                     "initiate", "unknown"],
    "UnavailableReason": ["needs_replay", "insufficient_samples", "source_not_allowed",
                          "stat_unavailable"],
}

# 「export type X = A | B;」一行一条，无嵌套、无换行——小显式解析器足够。
_ENUM_DECL = re.compile(r"^export type (\w+) = (.+);$", re.M)
_QUOTED = re.compile(r"""^(?:"[^"]*"|'[^']*')$""")   # 字符串枚举成员（生成器用 "…"，Resource 用 '…'）
_NUMERIC = re.compile(r"^\d+$")                       # 整数枚举成员（Team）


def ts_enums() -> dict[str, list[str]]:
    """{枚举名: [成员裸值]}——解析 ``export type X = A | B;``，按生成顺序保留。"""
    out: dict[str, list[str]] = {}
    for name, body in _ENUM_DECL.findall(TS.read_text(encoding="utf-8")):
        members = [m.strip() for m in body.split("|")]
        assert all(_QUOTED.match(m) or _NUMERIC.match(m) for m in members), (
            f"{name} 含非法成员（既不是字符串字面量也不是整数）: {members}")
        out[name] = [m[1:-1] if _QUOTED.match(m) else m for m in members]  # 去引号
    return out


def _contract_members(values: list) -> list[str]:
    """契约枚举值 → 裸值形式（与 ``ts_enums()`` 的输出同形）。"""
    return [str(v) for v in values]


def test_generated_ts_is_up_to_date():
    before = TS.read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", GENERATOR], check=True, cwd=ROOT)
    assert before == TS.read_text(encoding="utf-8"), "contract.ts 已过期，运行 make contract-ts 并提交"


def test_ts_contains_all_required_enums():
    """生成器漏掉 §6.0 的枚举时失败。"""
    ts = TS.read_text(encoding="utf-8")
    for name in REQUIRED_ENUMS:
        assert f"export type {name} =" in ts, f"{name} 未生成"


def test_ts_enum_members_match_the_contract(contract_doc):
    """逐成员比对契约、生成物与冻结基线——漏值/多值/取错 schema 都必须变红。

    ``test_ts_contains_all_required_enums`` 只断言 ``export type X =`` 存在，
    对「成员被静默丢掉」或「成员来自另一个 schema」无能为力；本测试补上。
    比对范围是**契约里全部带 enum 的 schema**（REQUIRED_ENUMS 只是其中前端
    硬依赖的子集），外加生成器固定输出的 ``Resource``。
    """
    enums = ts_enums()
    assert set(enums) == set(ENUM_MEMBERS) | {"Resource"}, (
        f"contract.ts 的导出枚举与冻结基线不一致：\n"
        f"  仅 TS 有: {sorted(set(enums) - set(ENUM_MEMBERS) - {'Resource'})}\n"
        f"  仅基线有: {sorted(set(ENUM_MEMBERS) - set(enums))}")
    schemas = contract_doc["components"]["schemas"]
    for name in REQUIRED_ENUMS:                     # 前端依赖的枚举，缺席即失败
        assert name in schemas, f"契约缺少必需枚举 {name}"
    from_contract = {n: _contract_members(s["enum"])
                     for n, s in schemas.items() if s.get("enum")}
    assert set(from_contract) == set(ENUM_MEMBERS), (
        f"契约的枚举面与冻结基线不一致：\n"
        f"  仅契约有: {sorted(set(from_contract) - set(ENUM_MEMBERS))}\n"
        f"  仅基线有: {sorted(set(ENUM_MEMBERS) - set(from_contract))}")
    for name in sorted(ENUM_MEMBERS):
        want = ENUM_MEMBERS[name]
        assert from_contract[name] == want, (
            f"{name} 契约成员与冻结基线不符（顺序也算）：\n"
            f"  基线: {want}\n  契约: {from_contract[name]}")
        assert enums[name] == want, (
            f"{name} 生成成员与冻结基线不符（顺序也算）——生成器漏值/多值/取错 "
            f"schema，或契约改了而 contract.ts 未重新生成：\n"
            f"  基线: {want}\n  生成: {enums[name]}")
```

- [ ] **Step 3: 生成并运行**

Run: `make contract-ts && pytest tests/contracts -q`
Expected: `wrote .../web/src/types/contract.ts`；**87 passed**（82 既有 + 2 fixtures + 3 TS）

**实测（Task 8 落地时）**：87 passed ✓（`.venv/bin/pytest tests/contracts -q` → `87 passed in 0.18s`）。
生成器**确定性**：连跑两次 `contracts.tools.gen_ts_types`，`git status` 保持干净、哈希不变。

> **实测（Task 8 复核修复后，`64c89a9`）**：87 passed ✓（条数不变——Resource 断言
> 加在既有的逐成员测试里，没有新增测试函数）。生成物 15 行 → **17 行**（行数一律按
> `wc -l web/src/types/contract.ts` 计：15 @ 硬化前 `cd5b5f0` → 17 @ 硬化后 `64c89a9`；
> `Side`/`Team` 各多一行 JSDoc），sha256 =
> `56e840e7f0d15369b3b1166fcc9b3556edcebc666988e2445b82dc1b41d01fca`。
> 新鲜度检查已改为只读 `--check`（`test_generated_ts_is_up_to_date` 不再写盘）：
> 干净树 exit 0，手改后 exit 1 且**不覆盖**手改。

**TS 真实编译器验证（手工步骤，**不**做成测试——没有 tsc 的机器会误红）**：

```bash
/Users/xinzechao/node_modules/.bin/tsc --noEmit --strict --target es2020 \
    --typeRoots /tmp/ts-empty-types web/src/types/contract.ts
# → 无输出，exit 0（tsc 5.9.3 / node v24.13.0）
```

`--typeRoots` 指向空目录是必要的：repo 的父目录 `/Users/xinzechao/node_modules/@types/node`
会被自动纳入，而它依赖未安装的 `undici-types`，不加该参数会报 5 条
`TS2792 Cannot find module 'undici-types'`——与本生成物无关的噪声。反向对照：
把 `"HIGH"` 赋给 `Confidence` 会得到
`error TS2820: Type '"HIGH"' is not assignable to type 'Confidence'. Did you mean '"high"'?`，
证明这份类型真的能约束前端。

**环境偏差（本机无 Docker、venv 不在 PATH）**：本机 `make contract` / `make contract-ts` /
`make lint-spec` 都以 `make: python: No such file or directory` 失败，`make db-reset` 需要
Docker 也不可用。故上面三步分别改用 `.venv/bin/python -m contracts.tools.gen_ts_types`、
`.venv/bin/python -m contracts.tools.validate_fixtures`、
`.venv/bin/python -m openapi_spec_validator contracts/openapi.yaml`；
全量测试用临时 PostgreSQL 16 集群 + 环境变量
`TEST_DATABASE_URL=postgresql://dota@localhost:55432/dota_test`、
`DATABASE_URL=postgresql://dota@localhost:55432/dota` 跑 `.venv/bin/pytest -q`。
**Makefile 未改**——它在 venv/PATH 配好的机器上仍然正确，只是本机 PATH 里没有 `python`。

- [ ] **Step 4: 校验 OpenAPI 文档本身（M0 验收第 1 条）**

Run: `make lint-spec`
Expected: `contracts/openapi.yaml: OK`（openapi-spec-validator 0.9.x 会打印该行并 exit 0；
旧版本静默通过——以 exit code 为准）

- [ ] **Step 5: 全量测试**

Run: `make db-reset && make test`
Expected: **120 passed**（33 + 82 + 2 + 3：db/shared + 既有契约 + Task 7 fixtures 两条
+ Task 8 TS **三**条。实测分解：db/shared 33 = db 22（16 约束 + 6 隔离）+ shared 11；
既有契约 82 = 公共 **5** + openapi 新鲜度 1 + 不变式 45 + schema 形状 31。相对上一版的 117：
Task 7 复核修复给 `test_invariants.py` 补了 2 条 §7.3 用例（43 → 45），既有契约由 80 变 82；
此前「Task 7 由 1 条变 2 条」的 +1 仍计入。Task 8 的 TS 由 2 条变 3 条（新增逐成员比对，
见 Step 2 说明），故为 120）

> **分解算式更正（Task 8 复核）**：上面这行此前写「公共 **6**」，是**重复计数**。
> `test_common_schema.py` 实际只收 **5** 条（`.venv/bin/pytest tests/contracts -q --collect-only`
> 逐文件核对：common_schema 5、openapi_fresh 1、invariants 45、schema_shape 31 → 82；
> 加 fixtures 2、ts_types 3 → 87；加 db/shared 33 → 120）。总数 120 一直是对的，
> 错的只是加数；5 + 1 + 45 + 31 = 82 才与实际收集一致。

**实测（Task 8 落地时）**：120 passed ✓（临时 PostgreSQL 16 集群 + `TEST_DATABASE_URL`/
`DATABASE_URL` 指向 55432 端口，`.venv/bin/pytest -q` → `120 passed in 0.36s`；
`--collect-only` 逐文件核对：invariants 45、schema_shape 31、constraints 16、
draft_template 11、isolation 6、common_schema 5、ts_types 3、fixtures 2、openapi_fresh 1）

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
- Test: `tests/constants/test_dotaconstants.py`, `tests/constants/test_archetypes.py`, `tests/constants/test_http.py`

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
_TRUTHY = {"1", "true", "yes"}

def _refresh() -> bool:
    """严格的刷新开关：只有 1/true/yes（忽略大小写与空白）才算开启。

    **不能**用 os.environ.get(...) 的真值判断：那样 REFRESH_NETWORK=0 与
    =false 都会被当成「开启刷新」——把「关」写成 0 是最自然的写法，后果却是
    每次跑测试都打网络，且覆盖掉已提交的缓存字节。""/0/false/no 一律是关闭。
    """
    return os.environ.get("REFRESH_NETWORK", "").strip().lower() in _TRUTHY

def _cache_dir() -> pathlib.Path:
    """缓存根目录。可用 MCNDOTAGA_CACHE_DIR 覆盖（指向只读介质时也能命中缓存）。"""
    override = os.environ.get("MCNDOTAGA_CACHE_DIR")
    return pathlib.Path(override) if override else CACHE

def fetch_json(url: str, cache_name: str) -> dict | list:
    """拉取并解析 JSON；命中缓存则不碰网络。

    `follow_redirects=True` 是**必须**的：上游若把 canonical URL 302 到别处（raw → CDN 镜像这一类），
    跟随才拿得到 JSON；不跟随时 `raise_for_status()` 会抛一个只说「302 Found」的 HTTPStatusError，
    把"这个 URL 只是转发"伪装成一次真实的上游故障。
    """
    path = _cache_dir() / cache_name
    if path.exists() and not _refresh():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"缓存文件已损坏，无法解析：{path}\n"
                f"  恢复方式二选一：REFRESH_NETWORK=1 重新拉取，或删除该文件。\n"
                f"  解析错误：{exc}") from exc
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()          # 必须先于写盘：非 JSON 响应绝不落进缓存
    path.parent.mkdir(parents=True, exist_ok=True)   # 只在写路径上建目录
    path.write_bytes(resp.content)   # 落**原始字节**，缓存与上游可逐字节比对
    return data
```

- [ ] **Step 2: 写失败测试**

`tests/constants/test_dotaconstants.py`：

```python
import pytest
from constants.dotaconstants import fetch_heroes, fetch_items, derive_token_index

def _expect_count(what: str, got: int, want: int) -> None:
    """冻结计数是**流程事件**，不是测试事实。

    出现新英雄/新道具时正确动作是提升 constants_snapshot.snapshot_version 并重训
    （规格 §5.1：dense_index 稳定性靠冻结快照，不靠顺序假设），而不是改这条断言
    或手改 tests/fixtures/network/ 下的缓存。
    """
    if got != want:
        pytest.exit(
            f"{what} 计数变了：期望 {want}，实测 {got}。\n"
            f"常量快照是版本化的：请提升 constants_snapshot.snapshot_version 并重训模型；\n"
            f"若这是上游例行变化，用 REFRESH_NETWORK=1 重抓缓存后再核对 M1 的验收计数。\n"
            f"**不要**为了让测试变绿而改这条断言或手改缓存字节。",
            returncode=1,
        )

def test_hero_count_is_127():
    heroes = fetch_heroes()
    _expect_count("英雄", len(heroes), 127)
    # 对照组：英雄 payload 自带 name，不需要从字典键补（道具才需要）
    assert {h["id"]: h["name"] for h in heroes}[1] == "npc_dota_hero_antimage"

def test_item_count_is_501():
    items = fetch_items()
    _expect_count("道具", len(items), 501)
    # 短名（blink 等）只在字典键上——payload 里没有 key/name 字段，
    # 丢了它 Task 11 入库 items.name（TEXT NOT NULL UNIQUE）只能瞎猜。
    missing_key = [i.get("id") for i in items if not i.get("key")]
    assert missing_key == [], f"缺短名的 item_id: {missing_key}"
    assert next(i["key"] for i in items if i["id"] == 1) == "blink"   # Blink Dagger

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

def test_token_index_is_order_independent():
    """§5.1 的规则是「按 hero_id 升序排序后取下标」，不是「相信上游返回顺序」。

    live payload 恰好按 id 升序到达，只有打乱输入才能区分这两者。
    """
    assert derive_token_index([{"id": 155}, {"id": 1}, {"id": 128}]) == {1: 0, 128: 1, 155: 2}
    assert derive_token_index([]) == {}
```

`tests/constants/test_archetypes.py`：

```python
import collections
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

def test_population_matches_the_spec_table():
    """规格 §7 的命中分布。逐规则计数：删任何一条规则、改任何一个条件都会改数。"""
    counts = collections.Counter(a for h in fetch_heroes() for a in archetypes_for(h["roles"]))
    assert dict(counts) == {"teamfight":117, "initiate":55, "protect":44,
                            "pickoff":34, "push":29, "splitpush":23}, f"分布漂移: {dict(counts)}"
    assert sum(counts.values()) == 302   # Σ=302 是 §7:1047 六项之和

def test_archetypes_for_tolerates_missing_or_unknown_roles():
    """`heroes.roles` 是可空列：None 不得抛异常，未知角色不得命中任何原型。"""
    assert archetypes_for(None) == []
    assert archetypes_for([]) == []
    assert archetypes_for(["Unknown"]) == []
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
    # 短名（blink 等）只是字典键，payload 里没有 key/name 字段
    return [{**v, "key": k} for k, v in raw.items()]

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
from collections.abc import Callable

RULES: list[tuple[str, Callable[[set[str]], bool]]] = [
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
Expected: **16 passed**（Task 9 = 10 `dotaconstants`+`archetypes` 基线 + 1 顺序无关性
+ 1 分布 + 1 None 容错 + 3 `test_http.py`；`key`/`name` 两条断言折进既有的两条计数测试，
不新增用例）

- [ ] **Step 6: Commit**

```bash
git add constants/ tests/constants/ tests/fixtures/network/
git commit -m "feat(constants): 英雄/道具常量 + token 索引 + 原型映射（实测零遗漏）"
```

---

### Task 10: 版本表——Valve 权威清单 + 子版本还原

规格 §16.3 记录了一个**用错规则会导致最大 47 天偏差**的陷阱。本任务把它变成受测代码，并覆盖规格 §15 要求的**三类用例**。

> **2026-09-17 实测更正（三处裁决）**：计划原文的三条规则与实测数据冲突，控制器对两个上游逐一复核后裁定如下。
> 本节的代码块已经是**裁决后的实现**，`constants/patches.py` 与 `tests/constants/test_patches.py` 与本节逐字节一致。
>
> **R1 「Valve 优先」是硬规则 → 7.41f 的边界取 Valve 的时间戳。** 规格 §3.2 入库规则：以 Valve 的时间戳为准，
> patchdates 只用于补 Valve 未覆盖的序列。实测两个来源的 7.41f：**Valve = `1789455600`（2026-09-15 07:00 UTC）**、
> **patchdates = `1789498134`（同日 18:48，晚 `42534 s ≈ 11.8 h`）**；7.41e 同样有两个值（Valve `1785394800` /
> patchdates `1785456079`）。因此计划原文 `test_boundary_is_exclusive_on_the_left` 的断言
> `subpatch_for_timestamp(1789498133) == "7.41e"` **与 §3.2 矛盾**：按 Valve 的时间戳，`1789455600` 起已是 7.41f。
> 边界测试重写为 `1789455599 -> "7.41e"`、`1789455600 -> "7.41f"`，并保留 `subpatch_for_timestamp(1789301470) == "7.41e"`
> （参考比赛，两种规则下都成立）。**受影响的窗口是 `[1789455600, 1789498134)` 这 11.8 h**：落在其中的比赛若改用
> patchdates 的时间戳会被错标成 7.41e。
>
> **R2 7.25 不得移位（原计划的 `dates[i] -> 'c'+i` 会造出不存在的 7.25d）。** 实测 patchdates key `"44"`
> （code 7.25）：`main=1584403200`、**没有 `add`**、`dates=[1585107278, 1586230920]`；Valve：7.25=1584428400、
> 7.25a=1584514800、7.25b=1585033200、7.25c=1586156400。`dates[0]` 距 Valve 的 7.25b **+0.86 天**（同日不同小时）、
> 距 7.25a +6.86 天、距基础版本 7.25 +7.86 天；`dates[1]` 距 7.25c +0.86 天。即**默认的位置映射已经给出正确的 b/c，7.25a 由 Valve 提供**；
> 移位只会凭空造出 Valve 并不存在的 7.25d。故**不移位**，只保留显式声明（偏差 +1 天落在 ±2 天阈值内，靠偏差检测抓不到）。
>
> **R3 7.22 不得排序（原计划「按时间戳排序后再分配字母」与规格自己的校准冲突）。** 实测 patchdates key `"41"`
> （code 7.22）`dates=[1559009567, 1560133416, **1558915200**, 1563170519, 1564362927, 1567790965, 1569806921]`
> —— 第 3 项比第 2 项早 14 天，是数据事实。两种规则的实测直方图（与 Valve 比**日历天**偏差，只统计 83 个可核验字母槽）：
>
> | 规则 | 完全一致 | 相差 ±1–2 天 | 例外（\|偏差\| > 2 天） |
> |---|---|---|---|
> | **位置（给定顺序）** | **34** | **48** | **1**：7.22d −34 天 |
> | 先排序再分配字母 | 35 | 46 | **2**：7.22c −12 天、7.22d −20 天 |
>
> 规格 §3.2 的校准逐字是「83 个可核验字母槽中 **34 个日期完全一致，48 个相差 ±1–2 天，仅 1 个异常（7.22d 差 34 天）**」
> 逐槽实测（秒级偏差）：位置映射 7.22b +0.80、c +0.81、**d −34.29**、e +0.96、f +0.76、g +0.44、h +0.77 天；
> 排序后 7.22b −0.29、**c −12.20、d −20.19**、e/f/g/h 不变 —— 排序把两个本来正确的槽位（c 与 d 的位置）
> 一起弄错。
>
> —— **只有位置映射能复现**；排序会同时弄错 7.22c 与 7.22d。故**不排序**，7.22 的例外由 `cross_check_against_valve()`
> 报出，并以 Valve 的日期为准。
>
> **R4 `cross_check_against_valve()` 的双口径。** `outliers` 收录**全部** \|偏差\| > 2 天的槽位（含已声明的 7.22d
> —— 例外必须可见）；`max_deviation_days` 则**排除已声明例外序列**后取最大，故计划原文的 `<= 2` 断言含义是
> 「没有**意外**漂移」。实测：`n_compared=83`、`max_deviation_days=2`（非例外最大值来自 7.31c，恰好 2 天）、
> `outliers=[7.22d −34 天]`。非例外槽位出现新漂移时 `max_deviation_days` 会立刻破 2（有专门的变异守护测试）。
>
> **R5 `declare_lettered_versions()` 必须实现且受测（Task 11 的 `constants/load.py` 逐字 import 它）。** 它的返回值
> 就是 `patches` 表的行集：**Valve 的 118 行 / 其中 84 个字母子版本** —— 规格 §3.2「`patches` 表的行集以此为准」、
> §15③「行集来自 Valve（118 / 84）而非 patchdates（141 槽）」、M1 验收（Task 13 断言字母版本数 == 84）三处都要求如此。
> patchdates 补出的 6.70–7.07 段（27 个基础版本 + **56 个字母槽** = 83 行）由 `patchdates_only_versions()` 暴露、只用于归属，
> **不写进 `patches`**（否则 118/84 两个验收数字都会破）；`declare_lettered_versions(include_patchdates_only=True)`
> 给出并集（201 行 / 140 个字母槽）。**上游 patchdates 的 141 槽属性不变**（规格 §3.2 与 README 记的都是上游，
> 见下一段 R6）；收缩只发生在我们暴露的行集上。
>
> **第二轮评审（2026-09-17，R6–R9）**：
>
> **R6 时间线不倒挂（本轮最重要的修复）**：patchdates key `"25"`（code 7.06，main=1494856800）的
> `dates[4]=1471651200` 是**错档** —— 那其实是 6.88c 的公告时刻（patchdates 的 6.88c=1471564800、
> 6.88d=1472774400），位置映射把它当成 7.06f，使 `[1471651200, 1472774400)` 这 12 天整段被错标成 7.06f。
> **既有守卫全都抓不到它**：`cross_check_against_valve` 有意只比 Valve 覆盖的序列（patchdates 独有的
> 6.70–7.07 段被跳过），测试侧的直方图口径相同。故新增一条**通用不变式**（`_drop_backdated_slots`）：
> **槽位不得早于「前一个序列」的 `main`**（前一个 = main 小于本条目、且 main 最大的那条 patchdates 记录）。
> 实测 61 条记录里**只有这一个**槽位违反；丢弃后 `patchdates_only_versions()` 84 → **83** 行（57 → **56** 字母槽）、
> 并集 202 → **201** 行（141 → **140** 字母槽）。上游字节和 141 槽属性都不变，声明见 `KNOWN_EXCEPTIONS[7.06]`。
>
> **R7 2018 归属边界**：Valve 清单从 7.08 = 1517472000（2018-02-01）起，patchdates 独有的 6.70–7.07 段
> **全部**排在其之前，故 `subpatch_for_timestamp(ts)` 返回的名字**只有**对 `ts >= 1517472000` 才保证出现在
> `patches` 行集里（`1517471999 -> "7.07d"`，而 `patches` 里没有 7.07d）。Task 12 的 2016–2017 Kaggle 数据
> 全在左侧，它的 `patch_id` 规则已按这条边界改写（不再用 `< 0.05` 的臆测预算）。
>
> **R8 `opendota_patch` 必须现在导出**：patchdates 的键**就是** OpenDota 的粗粒度 patch id，而 Valve 的
> 34 个基础版本在 patchdates 里**全部**有对应条目（缺失 0）—— 故 `declare_lettered_versions()` 的每行都带上
> `opendota_patch`（int；字母行继承其基础版本的 id）。否则 Task 11 写入的 118 行会全是 NULL，
> 而它的 `ON CONFLICT (version_name) DO UPDATE SET released_at = ...` 让该列**永远无法回填**。
>
> **R9 冻结计数走 `_expect_count`**：118 / 84 / 141 与校准的 83 / 34 / 48 / 1（含排序反证的 35 / 46 / 2）
> 一律改用可操作的退出信息（与 `tests/constants/test_dotaconstants.py` 同一口径），让 README 的
> 「计数测试会给出快照版本升级的指引」对本文件同样成立。副作用：上游漂移到这些数字时会
> `pytest.exit` **中止整个会话**（而不是只红一条测试）—— 与 `test_dotaconstants.py` 的行为一致。
>
> **测试计数**：计划原文的 8 条 → 第一轮裁决后 **17 条**（R1 重写 2 + 新增 1、R2 +1、R3 +3、R4 +1、R5 +2、
> 下界守护 +1）→ 第二轮评审后 **21 条**（R6 +2、R7 +1、R8 +1）。

**Files:**
- Create: `constants/patches.py`
- Test: `tests/constants/test_patches.py`
- Commit（网络缓存，本任务一并提交，离线可测）：`tests/fixtures/network/dota2_patchnoteslist.json`、
  `tests/fixtures/network/d2lrg_patchdates.json`，并在 `tests/fixtures/network/README.md` 记录 URL / 大小 / sha256

- [ ] **Step 1: 写失败测试（三类用例缺一不可）**

```python
"""`constants/patches.py`：Valve 权威清单 + 子版本还原（规格 §3.2、§15「版本归属测试」）。

规格 §15 要求的三类用例，缺一不可：
① 边界 —— 7.41e / 7.41f 两侧落到不同子版本；
② 字母映射 —— `add` 存在（7.41）与 `add` 缺失（7.32）两个方向都断言 `dates[0] -> 'b'`；
③ 交叉校验 —— `patches` 的行集来自 Valve（118 版本 / 84 字母版本），不是 patchdates（141 槽）。

三处控制器裁决（2026-09-17 对两个上游实测后裁定，见计划 Task 10 的更正注记）固化在本文件：
R1 **Valve 时间戳优先**：patchdates 的 7.41f 比 Valve 晚 11.8 h，边界取 Valve 的 1789455600；
R2 **7.25 不移位**：位置映射给出的 b/c 已经正确，移位会造出 Valve 并不存在的 7.25d；
R3 **7.22 不排序**：位置映射复现规格的 34 精确 / 48 ±1–2 天 / 1 例外；排序会变成 35 / 46 / 2。

第二轮评审（同 2026-09-17，见计划 Task 10 的 R6–R9）新增四条守护：
R6 **时间线不倒挂**：槽位不得早于前一个序列的 `main` —— 7.06 的错档槽位（6.88c 的日期被记在
   7.06 名下）被丢弃，`[1471651200, 1472774400)` 因此归 6.88c/6.88d；
R7 **2018 归属边界**：Valve 清单从 7.08 = 1517472000 起，更早的时间戳返回的名字**不在** `patches` 行集里；
R8 **`opendota_patch` 可导出**：`declare_lettered_versions()` 的每行都带 patchdates 的键（int，缺失 0）；
R9 **计数指引**：本文件的冻结计数（118/84/141/83/34/48/1）一律走 `_expect_count`，
   让 tests/fixtures/network/README.md 的「计数测试会给出快照版本升级的指引」对本文件同样成立。
"""
import datetime

import pytest

import constants.patches as patches
from constants.patches import (fetch_valve_patches, fetch_patchdates,
                               restore_subpatch_dates, subpatch_for_timestamp)

UTC = datetime.timezone.utc


def _expect_count(what: str, got: int, want: int) -> None:
    """冻结计数是**流程事件**，不是测试事实（与 tests/constants/test_dotaconstants.py 同一口径）。

    出现新版本/上游改数据时，正确动作是提升 constants_snapshot.snapshot_version 并重训
    （规格 §5.1：dense_index 稳定性靠冻结快照，不靠顺序假设），而不是改这条断言
    或手改 tests/fixtures/network/ 下的缓存。
    """
    if got != want:
        pytest.exit(
            f"{what} 计数变了：期望 {want}，实测 {got}。\n"
            f"常量快照是版本化的：请提升 constants_snapshot.snapshot_version 并重训模型；\n"
            f"若这是上游例行变化，用 REFRESH_NETWORK=1 重抓缓存后再核对 M1 的验收计数。\n"
            f"**不要**为了让测试变绿而改这条断言或手改缓存字节。",
            returncode=1,
        )


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
    valve_bases = {patches._base_version(name) for name in valve}
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
    _expect_count("Valve 版本", len(ps), 118)
    _expect_count("Valve 字母版本",
                  sum(1 for p in ps if any(c.isalpha() for c in p["patch_number"])), 84)


def test_patchdates_has_141_letter_slots():
    pd = fetch_patchdates()
    _expect_count("patchdates 字母槽",
                  sum(len(v.get("dates") or []) + (1 if v.get("add") else 0)
                      for v in pd.values()), 141)


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
    """规格 §3.2：7.22 与 7.25 是已知例外，必须显式声明而非静默错标；R6 再加上 7.06。"""
    from constants.patches import KNOWN_EXCEPTIONS
    assert 7.06 in KNOWN_EXCEPTIONS and 7.22 in KNOWN_EXCEPTIONS and 7.25 in KNOWN_EXCEPTIONS
    assert "错档" in KNOWN_EXCEPTIONS[7.06]          # 早于前一个序列的 main -> 整条丢弃
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
    _expect_count("可核验字母槽", hist["n"], 83)
    _expect_count("完全一致的槽位", hist["exact"], 34)
    _expect_count("相差 ±1–2 天的槽位", hist["within_1_2"], 48)
    _expect_count("例外槽位", len(hist["outliers"]), 1)
    assert hist["outliers"] == [("7.22d", -34)]


def test_sorting_7_22_contradicts_the_spec_calibration():
    """R3 反证：排序后再分配字母得到 35 / 46 / 2（7.22c 与 7.22d 同时错位），与规格对不上。"""
    hist = _deviation_histogram(sorted_dates=True)
    _expect_count("排序后完全一致的槽位", hist["exact"], 35)
    _expect_count("排序后相差 ±1–2 天的槽位", hist["within_1_2"], 46)
    _expect_count("排序后例外槽位", len(hist["outliers"]), 2)
    assert hist["outliers"] == [("7.22c", -12), ("7.22d", -20)]


def test_letter_mapping_agrees_with_valve_on_sampled_series():
    """用例③：交叉校验 —— 没有**意外**漂移（> ±2 天）；已声明例外单独可见（R4）。"""
    report = patches.cross_check_against_valve()
    assert report["n_compared"] == 83, report
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
    键必须**恰好**是 Task 11 迭代的四个（`version_name` / `base_version` / `released_at` /
    `opendota_patch`）—— 少了 `opendota_patch` 时 Task 11 会把 118 行全写成 NULL（R8）。
    """
    rows = patches.declare_lettered_versions()
    assert rows, "declare_lettered_versions() 不能为空"
    assert len(rows) == 118
    assert {frozenset(r) for r in rows} == {
        frozenset({"version_name", "base_version", "released_at", "opendota_patch"})}
    assert len({r["version_name"] for r in rows}) == 118
    assert sum(1 for r in rows if r["version_name"][-1].isalpha()) == 84
    assert all(isinstance(r["released_at"], int) and r["released_at"] > 0 for r in rows)
    assert all(r["version_name"].startswith(r["base_version"]) for r in rows)
    assert all(isinstance(r["opendota_patch"], int) and r["opendota_patch"] > 0 for r in rows)
    valve = {p["patch_number"]: p["patch_timestamp"] for p in fetch_valve_patches()}
    assert {r["version_name"]: r["released_at"] for r in rows} == valve   # 时间戳来自 Valve


def test_opendota_patch_comes_from_the_patchdates_keys():
    """R8：`opendota_patch` 必须等于该行基础版本在 patchdates 里的**键**（int），且一行都不缺。

    patchdates 的键就是 OpenDota 的粗粒度 patch id（61 个 = 34 个 Valve 覆盖的序列 + 27 个独有序列）；
    字母行继承其基础版本的 id。三个抽查名字逐一对键，并断言 **0 行 None** —— 否则 Task 11 的
    `ON CONFLICT (version_name) DO UPDATE SET released_at` 会让这一列永远无法回填。
    """
    key_by_code = {entry["code"]: int(key) for key, entry in fetch_patchdates().items()}
    rows = patches.declare_lettered_versions()
    assert sum(1 for r in rows if r["opendota_patch"] is None) == 0, "有行缺 opendota_patch"
    by_name = {r["version_name"]: r for r in rows}
    for name in ("7.08", "7.22", "7.41f"):
        assert by_name[name]["opendota_patch"] == key_by_code[by_name[name]["base_version"]], name
    assert by_name["7.08"]["opendota_patch"] == 27      # patchdates 键 "27"（7.08）
    assert by_name["7.22"]["opendota_patch"] == 41      # patchdates 键 "41"（7.22）
    assert by_name["7.41f"]["opendota_patch"] == 60     # patchdates 键 "60"（7.41f 继承 7.41）


def test_7_06_slot_is_dropped_and_the_window_maps_to_6_88c():
    """R6：7.06 的第 5 个槽位是错档 —— 丢弃后 [1471651200, 1472774400) 归 6.88c。

    实测 patchdates key "25"（code 7.06）：main=1494856800（2017-05-15）、
    dates=[1495324800, 1496016000, 1497139200, 1498953600, **1471651200**] —— 第 5 项是
    2016-08-20，比 7.06 自己的 main 早 268 天。它其实是 6.88c 的公告时刻（patchdates 的
    6.88c=1471564800 / 2016-08-19，6.88d=1472774400 / 2016-09-02），位置映射把它变成 7.06f，
    使这 12 天窗口整段被错标。不变式丢弃它之后，窗口两侧分别归 6.88c / 6.88d。
    """
    entry = next(v for v in fetch_patchdates().values() if v["code"] == "7.06")
    assert entry["dates"][-1] == 1471651200            # 上游确实错档（不是我们改了数据）
    assert restore_subpatch_dates(entry)["7.06f"] == 1471651200   # 位置映射确实产出 7.06f
    assert subpatch_for_timestamp(1471651200) == "6.88c"          # 窗口左端（7.06f 消失）
    assert subpatch_for_timestamp(1471651199) == "6.88c"          # 左端前一秒仍属 6.88c
    assert subpatch_for_timestamp(1472774400) == "6.88d"          # 窗口右端（6.88d 发布）
    assert "7.06f" not in {r["version_name"] for r in patches.patchdates_only_versions()}


def test_no_timeline_slot_precedes_its_predecessor_series_main():
    """R6 的通用守护：整条时间线里没有任何槽位早于「前一个序列的 main」。

    「前一个序列」= main 小于本条目、且 main 最大的那条 patchdates 记录（这里**独立重算**，
    不调用生产实现的过滤）。实测当前快照只丢 1 条：7.06f（前一个序列 7.05 的 main=1491750000）。
    """
    entries = sorted(fetch_patchdates().values(), key=lambda e: int(e["main"]))
    previous_main = {after["code"]: int(before["main"]) for before, after in zip(entries, entries[1:])}
    valve_bases = {patches._base_version(n) for n in patches._valve_versions()}

    # (1) 生产时间线里，patchdates 独有的序列不许出现倒挂槽位
    seen: dict[str, dict[str, int]] = {}
    for ts, name in patches._version_timeline():
        seen.setdefault(patches._base_version(name), {})[name] = ts
    checked = 0
    for code, slots in seen.items():
        if code in valve_bases:
            continue
        for name, ts in slots.items():
            checked += 1
            if code not in previous_main:       # 最早的一条序列（6.70）没有「前一个」，无约束
                continue
            assert ts >= previous_main[code], (
                f"{name}（{_day(ts)}）早于前一个序列的 main（{_day(previous_main[code])}）：时间线倒挂")
    assert checked == 83, f"应检查 83 个 patchdates 独有槽位（27 基础 + 56 字母），实测 {checked}"

    # (2) 被丢弃的槽位必须**恰好**是那一条 —— 且 raw 映射里它仍在（是过滤在起作用，不是数据变了）
    dropped = patches._dropped_backdated_slots()
    assert [(d["base_version"], d["version_name"], d["released_at"]) for d in dropped] == [
        ("7.06", "7.06f", 1471651200)]
    assert dropped[0]["previous_main"] == 1491750000      # 7.05 的 main


def test_patchdates_only_slots_are_available_but_stay_out_of_the_row_set():
    """R5 的另一半：patchdates 补出的槽位（Valve 未覆盖的 6.70–7.07 段）必须可达且受测。

    它们**不能进 `patches` 表** —— 否则 M1 的「118 行 / 84 个字母子版本」两个验收数字都会破；
    但它们必须参与归属（否则 6.70–7.07 的比赛无法定位子版本），故以
    `include_patchdates_only=True` 的并集形式暴露：27 个基础版本 + 56 个字母槽 = 83 行
    （上游 patchdates 有 57 个字母槽，7.06f 那个错档槽位被 R6 的不变式丢弃，见上一个测试）。
    """
    extra = patches.patchdates_only_versions()
    assert len(extra) == 83
    assert sum(1 for r in extra if r["version_name"][-1].isalpha()) == 56
    bases = {r["base_version"] for r in extra}
    assert "6.86" in bases and "7.07" in bases      # Valve 的清单从 7.08 起（规格 §16.3）
    assert "7.08" not in bases
    assert {r["version_name"] for r in extra} >= {"6.86", "6.86b", "7.07d"}
    assert "7.06f" not in {r["version_name"] for r in extra}   # R6：错档槽位不进任何暴露的行集

    union = patches.declare_lettered_versions(include_patchdates_only=True)
    assert len(union) == 118 + 83
    assert sum(1 for r in union if r["version_name"][-1].isalpha()) == 84 + 56
    assert {frozenset(r) for r in union} == {
        frozenset({"version_name", "base_version", "released_at", "opendota_patch"})}
    # 6.70–7.07 段也要能归属：Valve 无记录的年代由 patchdates 兜底
    assert subpatch_for_timestamp(1450224000) == "6.86"
    assert subpatch_for_timestamp(1509462000) == "7.07"


def test_attribution_boundary_is_the_valve_list_start():
    """R7：Valve 清单从 7.08 = 1517472000（2018-02-01）起 —— 两侧的**入库保证**不同。

    左：1517471999 属 7.07d，而 7.07d **不在** `patches` 行集里（patchdates 独有）；
    右：1517472000 属 7.08，且 7.08 **在** `patches` 行集里。
    Task 12 的 Kaggle 数据（2016–2017）全在左侧，故它的 `patch_id` 必然有 NULL：规则是
    「仅 `start_time < 1517472000` 允许 NULL，之后必须 0 个 NULL」，并要求报出 pre-2018 占比。
    """
    row_set = {r["version_name"] for r in patches.declare_lettered_versions()}
    assert subpatch_for_timestamp(1517471999) == "7.07d"
    assert "7.07d" not in row_set
    assert subpatch_for_timestamp(1517472000) == "7.08"
    assert "7.08" in row_set


def test_subpatch_for_timestamp_refuses_timestamps_before_the_earliest_version():
    """早于最早已知版本（6.70，2010-12）的时间戳必须响亮报错，而不是静默落到某个版本。"""
    with pytest.raises(ValueError, match=r"6\.70"):
        subpatch_for_timestamp(0)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants/test_patches.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'constants.patches'`

- [ ] **Step 3: 实现 `constants/patches.py`**

必须实现并导出**六个名字 + 一个常量**（测试直接 import 它们）：

| 名字 | 职责 |
|---|---|
| `fetch_valve_patches()` | 拉 `https://www.dota2.com/datafeed/patchnoteslist?language=english`，返回 `data["patches"]` 的**防御性拷贝**（118 条；`lru_cache` 只包私有的 `_fetch_valve_patches_cached()`——写在被缓存的函数里等于把拷贝本身也缓存起来，调用方一次 `append`/`sort` 照样污染整个进程）。规格 §3.2：该系列 API **出错也返回 HTTP 200**，故实现里检查 `success` 字段 |
| `fetch_patchdates()` | 拉 `D2-LRG-Metadata/patchdates.json`（无认证）。**计划原文没钉 URL，实现钉 canonical raw URL**：`https://raw.githubusercontent.com/leamare/D2-LRG-Metadata/master/patchdates.json`（7686 B / 61 键）；raw 抛 `httpx.HTTPError` 时回退镜像 `https://cdn.jsdelivr.net/gh/leamare/D2-LRG-Metadata@master/patchdates.json`（2026-09-17 实测 raw TLS 握手超时，镜像 0.9 s / 同样 7686 B）。返回的是缓存里的**同一个 dict**，调用方不得原地修改 |
| `restore_subpatch_dates(entry)` | **纯函数**，把一条 patchdates 记录展开为 `{版本名: 时间戳}`。规则见下 |
| `subpatch_for_timestamp(ts)` | 按 Valve 时间戳（优先）回落 patchdates，返回版本名如 `"7.41e"`；左闭右开；早于 6.70 抛 `ValueError`。**返回的名字只有对 `ts >= 1517472000`（Valve 清单起点 7.08）才保证出现在 `patches` 行集里**（R7） |
| `declare_lettered_versions()` | **本任务必须导出**：返回 `patches` 表的行集（Valve 的 118 行 / 84 个字母子版本），键恰好是 Task 11 迭代的四个 `version_name`/`base_version`/`released_at`/`opendota_patch`（R8）。Task 11 的 `constants/load.py` 只 import `declare_lettered_versions`（第二轮评审删掉了从未被使用的 `fetch_valve_patches`——留着它会让读者以为版本表还从 Valve 直取，实际入库路径走的是 `declare_lettered_versions()`）——漏掉它时 Task 9/10 全绿、Task 11 直接 `ImportError` |
| `patchdates_only_versions()` | patchdates 补出的 6.70–7.07 段（27 个基础版本 + 56 个字母槽 = 83 行；已按 R6 丢掉 7.06f 那个错档槽位），只用于归属、**不进 `patches`**（规格 §3.2 / §15③；否则 M1 的 118 / 84 都会破） |
| `KNOWN_EXCEPTIONS` | `{7.06: "...错档...", 7.22: "...unsorted...", 7.25: "...no add..."}`，文本必须**逐条**说清为什么丢弃 / 为什么不排序 / 为什么不移位——三个例外的性质不同（7.22 的偏差本来就超阈值），不能用一句「都在 ±2 天阈值**之内**」概括 |
| `cross_check_against_valve()` | 返回 `{"n_compared": int, "max_deviation_days": int, "outliers": [...]}`；**由此函数独占 Valve × patchdates 的交叉校验职责**；口径见 R4（`max_deviation_days` 排除已声明例外序列，故只有**非例外**序列的漂移会抬高它） |

**字母映射规则（必须固化为代码，用错规则会让约 20 个序列整体错位一个字母）**：

```
main       -> <code>
add        -> <code> + 'a'      (若存在)
dates[0]   -> <code> + 'b'      ← 从 'b' 起，不是 'a'
dates[1]   -> <code> + 'c'
...        依次递增
```

映射出来的槽位还要过**不倒挂不变式**：槽位不得早于「前一个序列的 `main`」（前一个 = main 小于本条目、
且 main 最大的那条 patchdates 记录）。实测只丢 7.06 的一个错档槽位（见 R6 与 `KNOWN_EXCEPTIONS[7.06]`）。

**两个已知例外的处理规则（2026-09-17 按实测更正，原表述已废弃）**：

| 版本 | 实测现象 | 处理规则 |
|---|---|---|
| **7.22** | `dates[]` 未排序：`dates[2]` 比 `dates[1]` 早 14 天，位置映射把它标为 7.22d，比 Valve 早 **34 天** | **不排序**（排序会同时弄错 7.22c/7.22d，见 R3 的直方图）；该槽位记入 `outliers` 并**以 Valve 的日期为准**。原文「按时间戳排序后再分配字母」已废弃 |
| **7.25** | patchdates **没有 `add` 字段**，而 Valve 有 7.25a；其 `dates[0]` 实际对应 Valve 的 7.25b | **不移位**：位置映射 `dates[0] -> 'b'` 已经给出正确的 b/c，原文的 `dates[i] -> 'c'+i` 会造出 Valve 并不存在的 7.25d。偏差只有 +1 天，±2 天阈值抓不到它，所以必须靠显式声明而非靠偏差检测 |

**Valve 优先**：`subpatch_for_timestamp` 一律先用 Valve 的时间戳（同一 `base_version` 只取 Valve 的条目）；
patchdates 只用于补 Valve 未覆盖的序列（6.70–7.07 段）。

```python
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


def _opendota_patch_by_base() -> dict[str, int]:
    """`{基础版本名: OpenDota 粗粒度 patch id}` —— **patchdates 的键就是这个 id**（字符串）。

    实测（2026-09-17 快照）：patchdates 有 61 个键 = 34 个 Valve 覆盖的序列 + 27 个 patchdates
    独有的序列；Valve 的 34 个基础版本**全部**在这里有对应条目（缺失 0），故 `declare_lettered_versions()`
    的每一行都能导出 `opendota_patch`，不是 NULL。
    """
    return {entry["code"]: int(key) for key, entry in fetch_patchdates().items()}


def patchdates_only_versions() -> list[dict]:
    """Valve 未覆盖的序列（6.70–7.07 段）：patchdates 独有的基础版本 + 字母槽（27 + 56 = 83 行）。

    **不写入 `patches` 表**：规格 §3.2「`patches` 表的行集以 Valve 为准（118 版本 / 84 字母版本）」、
    §15③、以及 M1 验收（Task 13 断言字母版本数 == 84）都要求行集来自 Valve，
    而 patchdates 原始有 141 个字母槽。它的用途是让 `subpatch_for_timestamp` 能归属 Valve 无记录的年代。

    相对上游的原始映射有**两处收缩**，都在这里体现：字母槽只算 Valve 未覆盖的序列（141 → 57），
    再被时间线不倒挂不变式丢掉 7.06 的错档槽位 7.06f（57 → 56），行数 84 → 83。
    每行四个键（含 `opendota_patch`，与 `declare_lettered_versions()` 的行形状一致）；
    上游 `patchdates.json` 的 141 槽属性本身不变（规格 §3.2 / README 记的是上游）。

    **归属边界**：这些行**全部**早于 Valve 清单起点（7.08 = 1517472000，2018-02-01），
    故它们不属于 `patches` 行集；`subpatch_for_timestamp` 对早于该时刻的时间戳返回的名字
    **不在** `patches` 里。
    """
    valve_bases = {_base_version(name) for name in _valve_versions()}
    kept_by_code, _ = _kept_and_dropped_slots()
    opendota = _opendota_patch_by_base()
    rows = []
    for code, slots in kept_by_code.items():
        if code in valve_bases:
            continue
        for name, ts in slots.items():
            rows.append({"version_name": name, "base_version": code,
                         "released_at": ts, "opendota_patch": opendota[code]})
    rows.sort(key=lambda r: (r["released_at"], r["version_name"]))
    return rows


def declare_lettered_versions(*, include_patchdates_only: bool = False) -> list[dict]:
    """`patches` 表的行集：Valve 的 118 个版本（含 84 个字母子版本），按时间戳升序。

    每行**恰好**四个键，对应 Task 11（`constants/load.py`）迭代的字段：
    `version_name` / `base_version` / `released_at`（int，Unix 秒，来自 Valve）/
    `opendota_patch`（int，OpenDota 粗粒度 patch id —— **patchdates 的键就是它**；字母行继承其
    基础版本的 id，实测 34 个 Valve 基础版本在 patchdates 里全有对应条目，故没有一行是 None）。

    `include_patchdates_only=True` 时并上 `patchdates_only_versions()`（6.70–7.07 段，83 行）——
    那是**归属用**的并集（201 行 / 140 个字母槽），**不要**写进 `patches` 表（见该函数说明）。
    """
    opendota = _opendota_patch_by_base()
    rows = [{"version_name": name, "base_version": _base_version(name), "released_at": ts,
             "opendota_patch": opendota.get(_base_version(name))}
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/constants/test_patches.py -q`
Expected: **21 passed**（原文 8 条 → 第一轮裁决 17 条 → 第二轮评审 21 条：R6 +2、R7 +1、R8 +1）

- [ ] **Step 5: 提交（4 个 commit，缓存与计划同步各自独立）**

```bash
git add constants/patches.py
git commit -m "feat(constants): 版本表（Valve 权威 + 子版本还原；7.25 不移位 / 7.22 不排序 / cross-check 语义）"

git add tests/constants/test_patches.py tests/constants/test_http.py constants/_http.py
git commit -m "test(constants): declare_lettered_versions + 边界/例外/口径守护 + test_http 缓存目录隔离"

git add tests/fixtures/network/dota2_patchnoteslist.json tests/fixtures/network/d2lrg_patchdates.json \
        tests/fixtures/network/README.md
git commit -m "feat(constants): 提交 Valve/patchdates 网络缓存（离线可测）+ README 溯源"

git add docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md
git commit -m "docs(plan): Task 10 同步实现与实测校准（含三处裁决依据）"
```

### Task 10 实测记录（2026-09-17，提交后复核；含同日第二轮评审 R6–R9）

- **两个上游**：Valve `patchnoteslist` → HTTP 200 / 9129 B / `{"patches": […], "success": true}` / **118 条、84 个字母版本**、最后一条 `7.41f`；
  patchdates → **7686 B / 61 键**（键是 OpenDota 粗粒度 patch id 的字符串）。两文件已随本任务提交进
  `tests/fixtures/network/`（`dota2_patchnoteslist.json` sha256 `9ec57bde…`、`d2lrg_patchdates.json` sha256 `5c54af61…`）。
- **R3 校准表**（见上文 R3 表格）：位置映射 **34 / 48 / 1**（唯一例外 7.22d −34 天）与规格 §3.2 逐字吻合；排序 **35 / 46 / 2**（7.22c −12、7.22d −20）与规格冲突。
- **`cross_check_against_valve()`**：`{"n_compared": 83, "max_deviation_days": 2, "outliers": [{"version_name": "7.22d", "deviation_days": -34, "patchdates": 1558915200, "valve": 1561878000, "declared_exception": true}]}`。
- **`declare_lettered_versions()`**：118 行 / 84 个字母子版本 / 键恰好 4 个（R8 加入 `opendota_patch`）/ `released_at` 全为正 int / 与 Valve 的 `patch_number → patch_timestamp` 逐条相等；
  `opendota_patch` **0 行缺失**（Valve 的 34 个基础版本在 patchdates 的 61 个键里全有对应条目；抽查 `7.08→27`、`7.22→41`、`7.41f→60`）。
  `patchdates_only_versions()`：**83 行**（27 基础 + 56 字母）；并集 `include_patchdates_only=True`：**201 行 / 140 个字母槽** —— R6 丢掉 7.06f 后由 84 / 202 / 141 收缩而来。
- **R6 实测**：按「槽位不得早于前一个序列的 main」扫全部 61 条 patchdates 记录，**违反者恰好 1 条** ——
  `7.06f = 1471651200`（2016-08-20；前一个序列 7.05 的 main = 1491750000）。丢弃后
  `1471651200 → "6.88c"`、`1472774400 → "6.88d"`（原先 `[1471651200, 1472774400)` 这 12 天整段被错标成 7.06f）。
- **R7 实测**：`1517471999 → "7.07d"`（该名字**不在** `patches` 行集里）、`1517472000 → "7.08"`（**在**行集里）。
- **变异守护（7 条，全部 apply → run → restore → sha256 复原）**：7.25 移位 → `test_7_25_is_not_shifted` 失败；
  7.22 排序 → 7.22 顺序测试失败，校准正证改走 `_expect_count` 后以 `pytest.exit` 中止会话（R9 的行为变化）；边界改用 patchdates 时间戳 → 两条边界测试失败；
  `max_deviation_days` 计入例外 → 计划原文的 `<= 2` 断言以 `max_deviation_days=34` 失败；删除 `declare_lettered_versions` → 两条 R5 测试失败；
  **把 7.06f 放回 `dates[]`（R6 反证）→ `test_7_06_slot_is_dropped_and_the_window_maps_to_6_88c` +
  `test_no_timeline_slot_precedes_its_predecessor_series_main` 失败**；
  **从行里删掉 `opendota_patch`（R8 反证）→ `test_declare_lettered_versions_matches_the_patches_row_set` +
  `test_opendota_patch_comes_from_the_patchdates_keys` 失败**。
- **离线**：`pytest tests/constants -q` 命中已提交缓存，**0.09 s / 37 passed**；`REFRESH_NETWORK=1` 复跑后 `git status` 干净（上游字节未变）。
- **测试计数口径**：`tests/constants` 实测 = `test_archetypes.py` 5 + `test_dotaconstants.py` 8 + `test_http.py` 3 + `test_patches.py` **21** = **37**。
  Task 11 的 Step 4 期望值（原文 24）已同步为 **45**（Task 9 实测 16 + Task 10 实测 21 + Task 11 实测 8 —— 首轮 6 + 第二轮 2）。
### Task 11: 常量入库（M1 的前置，原计划缺失）

**这是整个计划此前最大的缺口**：没有任何任务把常量写进 `heroes`/`items`/`patches`/`hero_token_index`/`constants_snapshot`/`app_config_kv`。没有它，M1 的四条验收断言不可能通过，Kaggle 加载器也会被 `draft_actions.hero_id` 的外键挡住。

**Files:**
- Create: `constants/load.py`
- Test: `tests/constants/test_load.py`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/constants/test_load.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'constants.load'`

- [ ] **Step 3: 实现 `constants/load.py`**

```python
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
```

> `items` 的短名取自 OpenDota 返回的字典键（`i["key"]`），不是 `i["name"]`（后者是 `item_blink` 这类内部名）。实现时按实际 payload 调整。
>
> `patches` 的 `ON CONFLICT` 必须更新**所有可能被后续修复改动的列**（`released_at` / `base_version` /
> `opendota_patch`）：只更新 `released_at` 时，第一版写进去的 NULL 永远无法回填（本轮 R8 之前
> `declare_lettered_versions()` 正是这种情况）。因此这里直接取 `p["opendota_patch"]`（Task 10 保证该键存在，
> 见 R8），不用 `p.get(...)` —— 键缺失要在 Task 10 的接口测试里炸，而不是静默写 NULL。
>
> `load_constants(conn, *, commit: bool = True)` 的 `commit` 参数是**测试事务隔离的硬要求**（2026-09-17
> 控制器裁定，见下方实测记录 (a)）：默认 `True` 是生产语义（一次性加载器必须让常量立刻对其它会话可见），
> `commit=False` 让测试能把加载放进 `db` fixture 的 rollback 事务里，避免真实常量被提交进共享的 `dota_test`。

- [ ] **Step 4: 运行确认通过**

Run: `make db-reset && pytest tests/constants -q`
Expected: **45 passed**（实测口径：Task 9 = 16（`test_archetypes.py` 5 + `test_dotaconstants.py` 8 + `test_http.py` 3）、
Task 10 = 21、Task 11 = 8（首轮 6 + 第二轮新增「拒绝静默重映射」「归属闭合」2 条，见实测记录 (c)(e)）；
原文写的 24（Task 9 = 10、Task 10 = 8）是计划阶段的估计，已按实测校准）
注：本机无 Docker，`make db-reset` 跑不了；session 级 `dsn` fixture 每次会话都 DROP + CREATE `dota_test`
并重放迁移，等价于"从零建库"，故直接跑 pytest 即可（2026-09-17 实测口径见下）。

- [ ] **Step 5: Commit**

```bash
git add constants/load.py tests/constants/test_load.py
git commit -m "feat(constants): 常量入库（127 英雄/501 道具/118 版本/4 个配置键）+ 幂等 + 测试事务隔离"
```

### Task 11 实测记录（2026-09-17，提交后复核）

- **Step 2 红**：`pytest tests/constants/test_load.py -q` → `6 failed`，六条全部是
  `ModuleNotFoundError: No module named 'constants.load'`（与 Step 2 预期逐字一致）。
- **计数（第二轮修复后的最终口径）**：`tests/constants/test_load.py` **8 passed**；`tests/constants` **45 passed**；
  全量 `tests` **165 passed**（163 + 2）。**顺序无关性**：把 `tests/db` 强制放到最前
  （`pytest tests/db tests/constants tests/contracts tests/shared -q`）同样 **165 passed** ——
  两种收集顺序都绿，跨套件污染已消除。（首轮口径为 6 / 43 / 163；第二轮新增的两条测试见 (c)(e)。）
- **入库实测（直接查库，不是 Python 层推断）**：`constants_snapshot = (1, 127, 501, 'dotaconstants')`；
  `heroes` 127；`items` 501；`hero_token_index` 快照 1 共 127 行、hero 135 → dense_index **121**；
  `patches` **118 行 / 84 个字母版本 / `opendota_patch` NULL 0 行**，抽查 `7.41f → 60`、`7.22 → 41`、
  `7.08 → 27`，最早/最晚 = 7.08 → 7.41f。`app_config_kv` 四个键：`archetype_role_map`（6 个原型名 →
  `"python:constants.archetypes.RULES"`）、`robustness_lambda` = `1.0`、`op_decision_min_delta` = `0.02`、
  `min_sample_n` = `30`。
- **(a) `commit=False`：测试事务隔离（计划原文缺，控制器要求）**。`load_constants` 末尾的 `conn.commit()`
  对生产是对的，但它与 `tests/conftest.py` 的 `db` fixture 直接冲突 —— 该 fixture 给每个测试一个事务、
  结束时 `rollback()`。不加参数时，测试里的加载会把真实常量**提交**进共享的 `dota_test`；pytest 默认
  收集顺序是 `tests/constants/` 先于 `tests/db/`，后面的 `seeded` fixture 随即在
  `constants_snapshot(snapshot_version=1)` 与 `heroes(80)` 上主键冲突。**实测反证**：把测试里的
  `commit=False` 全改回默认（即计划原文的调用方式）后跑全量 → **`145 passed, 18 errors`**，18 个 error
  全在 `tests/db/`，首个报错是 `psycopg.errors.UniqueViolation: 重复键违反唯一约束
  "constants_snapshot_pkey" / DETAIL: 键值"(snapshot_version)=(1)" 已经存在`（含
  `test_hero_token_index_rejects_duplicate_dense_index`）。这是**跨套件污染，不是约束测试有问题** ——
  故 `tests/db/test_constraints.py` 与 `tests/conftest.py` 一行未动。实现因此改为
  `def load_constants(conn, *, commit: bool = True)`，只在 `commit` 为真时 `conn.commit()`；
  `tests/constants/test_load.py` 每处都写 `load_constants(db, commit=False)`，加载随 fixture 的
  `rollback()` 一起消失。**隔离实测**：上述反证实验后新连接看到 `heroes = 127`（说明该检查确实敏感），
  随后跑一次绿色的 `pytest tests/constants -q`，再用新连接查 → `heroes = 0` / `patches = 0` /
  `app_config_kv = 0`：测试库确实干净。
- **(b) `opendota_patch` 的库级断言（计划原文没有，控制器要求）**。只断言
  `declare_lettered_versions()` 的键存在（Task 10 的 R8 接口测试）**不够**：入库路径丢键、
  `ON CONFLICT` 只更新 `released_at`、`VALUES` 少一列，都能让 118/84 全绿而该列静默为 NULL，
  且该列可空、没有任何约束拦得住。故 `test_load_constants_populates_all_tables` 增加
  `SELECT count(*) FROM patches WHERE opendota_patch IS NULL == 0` 与 `7.41f → 60` / `7.22 → 41` /
  `7.08 → 27` 三处抽查；`test_load_is_idempotent` 增加 `patches` **整行**比对（二次加载后仍 118 行，
  且 `version_name/base_version/released_at/opendota_patch` 逐行不变）。**实测反证**：把入库参数改成
  `None` 后，原有的 118 行 / 84 字母两条断言**照样通过**，新增的 NULL 断言失败 —— 正是计划缺的那道守护。
  （两条新增断言写进计划原有的两个测试里，不新增测试函数，故**首轮**计数仍是 6 / 43 / 163。）
- **变异守护（4 条，全部 apply → run → restore → sha256 复原 `58df5525e1ee…` → 复跑 6 passed）**：
  (a) 去掉 `heroes` 的 `ON CONFLICT` → `test_load_is_idempotent` 以 `UniqueViolation hero_id=(1)` 失败；
  (b) 写 NULL 进 `opendota_patch` → `test_load_constants_populates_all_tables` 在新增的 NULL 断言上失败；
  (c) 只加载 126 个英雄 → 同一测试（`126 == 127`）与 `test_load_is_idempotent` 失败；
  (d) 跳过 `hero_token_index` 插入 → `test_hero_135_dense_index_is_121` 失败。
- **计划原文的一处驱动器口径修正**：`test_archetype_role_map_names_agree_with_the_rules`（第二轮改名；原名 `..._matches_the_python_rules` 名不副实，它只比对六个原型名）原文写
  `stored = json.loads(raw)`，但 `app_config_kv.value` 是 **JSONB**，psycopg 3 默认已把它解码成 Python
  对象，再 `loads` 会抛 `TypeError: the JSON object must be str, bytes or bytearray, not dict`。
  改为 `json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw`，断言不变。

#### Task 11 第二轮评审修复（2026-09-17；承接上面的 (a)(b) 顺延编号；计数 8 / 45 / 165）

- **(c) 冻结快照守护：拒绝静默重映射（本轮唯一的实质性缺陷，计划原文缺）**。`hero_token_index`
  的写入是 `ON CONFLICT DO NOTHING` + 写死的 `SNAPSHOT_VERSION = 1`：上游一旦增删英雄
  （尤其是用 hero_id 1..155 里的空位补人 —— 实测空位 = 24 / 115–118 / 122 / 124 / 125），
  其后所有英雄的 `dense_index` 会**整体位移**，`heroes` 与 `constants_snapshot.n_heroes` 跟着变，
  而 token 映射仍是旧的 —— `load_constants` 却返回成功。这正是规格 §5.1:214–220 明令禁止的
  「静默重映射」：模型 token 与英雄的对应关系悄悄错了，没有任何报错。
  **为什么 §5.1 要求 3 的 N vs N+1 跨快照检查替代不了它**（故此事不能推给 Plan 2）：那条检查
  只在**快照 2 被创建时**才有机会运行，口径是「对快照 N 与 N+1 断言所有**已存在**的 hero_id
  其 dense_index 不变」——快照 N 里本来不存在的新英雄在它面前是**空集，检查空过**；而本轮要拦的
  恰恰是"新英雄插进空位"这一刻。
  实现：`derive_token_index(heroes)` 之后、**任何 INSERT 之前**读一次
  `SELECT hero_id, dense_index FROM hero_token_index WHERE snapshot_version = %s`，非空且与
  本次派生结果不相等即 `RuntimeError`，要求显式提升 `SNAPSHOT_VERSION`（重派生 dense_index =
  重训模型）。`SNAPSHOT_VERSION` 仍是模块常量，不从库里反推。
  **变异反证**：把守卫条件改成 `if False:` → `test_load_refuses_to_silently_remap_tokens` 以
  `Failed: DID NOT RAISE RuntimeError` 失败（`1 failed, 7 passed`）；还原后（sha256 复原
  `961b5590…`）→ 8 passed。测试里合成的英雄 id 用 **24**：评审原文写的是 id 9，但 9 在实测快照里
  **存在**（不是空位），用它就退化成"重复 id"而非"插入空位"，`heroes` 行数不变，
  `SELECT count(*) FROM heroes == 127` 这条断言反而抓不到守卫被摘掉（实测确认）。
- **(d) 三个数值调参改为按值断言（计划原文只查键存在）**。`'0.02'::jsonb` → `'0.2'::jsonb`
  这类改动能让键集断言全绿，而 §9.1 的最小差异阈值悄悄变成 10 倍。现断言
  `robustness_lambda == 1.0`、`op_decision_min_delta == 0.02`（键名是 `..._delta`）、
  `min_sample_n == 30` —— JSONB 经 psycopg 3 解码后已是 Python 数字，直接比较；键集断言保留。
- **(e) 归属闭合测试（Task 12 的隐含契约）**。新增
  `test_timeline_after_the_first_loaded_patch_resolves_to_loaded_versions`：
  `{n for ts, n in _version_timeline() if ts >= min(patches.released_at)} - {patches.version_name}`
  必须为空，并钉死分界 = 1517472000（7.08 = 2018-02-01）与可达名字数 = 118（防空集空过）。
  这就是 Task 12「2018 分界之后 `patch_id` 0 个 NULL」所依赖的闭合性：归属函数只保证返回名字，
  名字能否对上 `patches` 行必须另外断言（loader 少写一行、或时间线多出一个 patchdates 独有的槽位，
  都会在这里炸）。
- **(f) `archetype_role_map` 是「代码出处指针」，不是映射表（口径修正）**。值一直是
  `{六个原型名: "python:constants.archetypes.RULES"}`，而 `note` 原文写「规格 §7 维度 6 的
  roles→原型 映射」，读起来像一张**可执行的** roles→原型 表。规格 §7:1053 的「该映射存于
  `app_config_kv`，可调而不改代码」**没有实现**：`RULES` 的规则带**取反**（`"Pusher" not in r`）
  与**嵌套 OR**（`teamfight`），扁平 KV 表表达不了。故 note 改为如实说明"这里记录的是算 §7 维度 6
  的代码位置"，并把 §7 的"可调而不改代码"记入**显式延迟清单第 5 项**（见文末）。
  测试 `test_archetype_role_map_matches_the_python_rules` 更名为它真正检查的东西：
  `test_archetype_role_map_names_agree_with_the_rules`（只断言六个原型名 DB 与 `RULES` 一致）。
- **变异守护（第二轮 2 条，apply → run → restore → sha256 复原 `961b5590…` → 复跑 8 passed）**：
  (i) 摘掉冻结快照守卫（`if False:`）→ `test_load_refuses_to_silently_remap_tokens` 失败（见 (c)）；
  (ii) upsert 去掉 `note = EXCLUDED.note` → `test_load_is_idempotent` 新增的"先把 note 改脏再加载"
  断言失败（`'stale' != '规格 §7 …'`）。**同一轮还有一条"没人抓"的变异**：把 items 的 `i["key"]`
  改回 `i["key"] if "key" in i else i["name"]` → `pytest tests/constants tests/db -q` **67 passed，
  全绿**：`dotaconstants.fetch_items()` 已经给每条 payload 注入 `"key"`，那个 fallback
  **永远不会执行**。故这条只能靠删死分支收口 —— 若将来 `fetch_items` 不再注入 `key`，正确行为是
  `KeyError` 炸掉，而不是把 `item_blink` 这类内部名写进 `items.name`（TEXT NOT NULL UNIQUE，
  写错了没有任何约束会报错）。
- **三处小缺陷（第二轮）**：(1) items 的死 fallback 删除（见上）；(2) 删掉 `constants/load.py` 里
  从未被使用的 `from .patches import fetch_valve_patches`（只留 `declare_lettered_versions`），
  计划 Task 10 接口表里「Task 11 逐字 import `fetch_valve_patches`」的说法随之改为现实；
  (3) `app_config_kv` 的 upsert 补上 `note = EXCLUDED.note`（否则修正过的 note 永远传播不出去）。
- **自包含性（第二轮）**：`test_token_index_satisfies_the_derivation_rule` 增加
  `SELECT count(*) FROM hero_token_index == 127` —— 原文空表时 `bad == 0` 会**空过**。

---

### Task 12: Kaggle 引导数据集（选择性下载 506 MB）

**Files:**
- Create: `ingest/__init__.py`, `ingest/kaggle_subset.py`, `ingest/load_bootstrap.py`
- Create: `tests/ingest/__init__.py`, `tests/ingest/conftest.py`
- Test: `tests/ingest/test_bootstrap.py`
- Edit: `pyproject.toml`（新增 `[project.optional-dependencies].ingest`）
- Edit: `.gitignore`（排除 `tests/fixtures/kaggle/`）——**计划原文说"该目录已由 `.gitignore` 排除"，
  实测没有**（`git check-ignore` 退出码 1），本轮补上；否则 506.7 MB 的 CSV 会被 `git add .` 收进去。

- [x] **Step 0: 先真跑一次下载并逐列核对（本轮新增，它推翻了计划的三处假设）**

计划的 Step 2/4 是照着"`main_metadata.csv` 里应该有 `start_time`/`league_name`、`picks_bans.csv`
的 `order` 是整数"写的。实测（2026-09-18）三处都不成立，见下面的实测记录。**先核对再写代码**
这一步不能省：照计划原文写出来的 loader 在真实数据上第一行就炸（`start_time` 列不存在）。

- [x] **Step 1: `tests/ingest/conftest.py`（含会话级独占数据库与合成数据构造器）**

会话级 fixture **不能**借用共享的 `dota_test`（计划原文的做法）。两条路都不通：
`commit=True` 会把常量与 21 万场比赛提交进共享库，复现 Task 11 的 18 个
`UniqueViolation: constants_snapshot_pkey`；改成 `commit=False` 则未提交事务会一直持有
`heroes`/`patches` 的行锁到 session 结束，同 session 的 `load_constants(commit=False)`
**锁等待挂死**（顺序执行也会挂）。故引导入库写进自己拥有的 `<db>_test_bootstrap`。

<!-- FILE: tests/ingest/__init__.py -->
```python
```
<!-- （空文件：与 `tests/constants/`、`tests/db/` 等既有测试包一致，仅为让 pytest 以包路径导入） -->

<!-- FILE: tests/ingest/conftest.py -->
```python
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
```

- [x] **Step 2: `tests/ingest/test_bootstrap.py`**

数据侧 9 条（缓存缺失时 `skip`，消息写明缺什么、怎么补）+ 合成侧 25 条（照抄真实 CSV 形状）。
规格 §17-7 的 `order` 起点断言按实测改成 `int(float(...))`：真实值是 `'0.0'` 这样的浮点串，
计划原文的 `int(r["order"])` 会直接 `ValueError`（那是类型信号，不是 1-based 信号）。

<!-- FILE: tests/ingest/test_bootstrap.py -->
````python
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
````

- [x] **Step 3: `ingest/kaggle_subset.py`（按文件白名单下载）**

只下需要的文件，共 506.7 MB。**绝不整包下载**（数据集 48.52 GB / 1546 个文件，其中
players.csv 是 19 个分片合计 41.04 GB）。白名单 = 规格 §10.2 的三个 CSV
（`*/picks_bans.csv` 198.8 MB / 19 个目录、`*/main_metadata.csv` 66.7 MB / 19 个、
`*/draft_timings.csv` 240.7 MB / 仅 10 个目录）**外加** `Constants/Constants.Leagues.csv`
（429 KB）—— 后者是实测补上的：`main_metadata.csv` 根本没有 `league_name` 列，而
`leagues.name` 是 `NOT NULL`、M1 又要求 `leagues > 50` 行，联赛名只在这个文件里。

凭证：`KAGGLE_USERNAME`/`KAGGLE_KEY`（可写进仓库根的 `.env`）或 `~/.kaggle/kaggle.json`
（kaggle 2.x 还认 `KAGGLE_API_TOKEN` / `~/.kaggle/access_token`）。
依赖：`python -m pip install -e ".[dev,ingest]"`（`kaggle` / `python-dotenv` 只在真要下载时才 import）。

<!-- FILE: ingest/__init__.py -->
```python
"""Kaggle 引导数据集的下载与入库（规格 §5.2/§5.3/§3.2/§10.2、计划 Task 12）。

两个模块刻意分开，因为它们的失败模式完全不同：
- `ingest.kaggle_subset`：需要网络与凭证（`KAGGLE_USERNAME`/`KAGGLE_KEY` 或
  `~/.kaggle/kaggle.json`），失败时必须给出**可操作**的提示，绝不整包下载 48.52 GB。
- `ingest.load_bootstrap`：需要数据库与常量层（先 `constants.load.load_constants`），
  幂等、可中断续跑，且必须按规格 §3.2 处理 2018 的版本归属边界。

`kaggle` / `python-dotenv` 只在**真正要下载时**才 import（见 `kaggle_subset`），
故未安装 `[ingest]` extra 的环境里测试套件照常收集与运行。
"""
```

<!-- FILE: ingest/kaggle_subset.py -->
```python
"""只下需要的三个 CSV，共 506.2 MB。**绝不整包下载**。

数据集总计 48.52 GB（1546 个文件）。其中 players.csv 是 **19 个分片文件**
合计 41.04 GB（最大单片 2025/players.csv = 6.92 GB）——不存在"单文件 41 GB"，
但无论如何都与 Phase A 无关。

白名单（相对数据集根，规格 §10.2）：

| 文件 | 大小 | 目录数 |
|---|---|---|
| `*/picks_bans.csv` | 198.8 MB | 19 |
| `*/main_metadata.csv` | 66.7 MB | 19 |
| `*/draft_timings.csv` | 240.7 MB | **10**（仅 2016–2025；已知缺口，以 picks_bans 为权威） |

落到 `tests/fixtures/kaggle/<folder>/<file>`（该目录已由 `.gitignore` 排除，**不入库**）。
下载走 `kaggle` 包的**单文件**接口 `dataset_download_file`（对应
`/api/v1/datasets/download/{owner}/{dataset}/{fileName}`），不是整包 `dataset_download_files`。

**对已安装客户端（kaggle 2.2.4）源码的三条实测结论**（本机 `pip install -e ".[dev,ingest]"`
成功，故这部分不是猜的）：

1. `dataset_download_file(dataset, file_name, path=None, force=False, quiet=True)` 存在，
   签名与本模块的调用一致；
2. `dataset_list_files(dataset, page_token=None, page_size=20)` **默认 20 条/页**，而本数据集
   有 1546 个文件 —— 必须显式给 `page_size` 并翻页，否则白名单文件大概率一个都看不到。
   见 `list_all_files`；
3. 落盘名取的是**下载 URL 的最后一段**（源码
   `outfile = os.path.join(effective_path, url.split("?")[0].split("/")[-1])`），不是
   `file_name`。URL 形状无法在无凭证环境下观察，故 `download_one` 在目标路径不存在时做一次
   **有界**补救（只认"本次调用新出现的唯一文件"），否则报错。**仍未验证**的是这一段的真实
   落盘名（需要凭证才能看到 URL）。

凭证（二选一，规格 §10.2）：
- 环境变量 `KAGGLE_USERNAME` / `KAGGLE_KEY`（也可写进仓库根的 `.env`，见 `.env.example`）；
- `~/.kaggle/kaggle.json`（kaggle.com → Account → Create New API Token）。

安装：`python -m pip install -e ".[dev,ingest]"`（`kaggle` / `python-dotenv` 在 `[ingest]` extra 里，
故**只有真要下载时才 import** —— 未安装时测试套件照常收集、数据侧测试 skip 而非 ImportError）。

失败行为：无凭证 → `KaggleCredentialsMissing`（含补齐方式与下载命令）；缺依赖 →
`KaggleDependencyMissing`（含安装命令）。CLI 把两者都变成**非零退出 + 一行提示，不打印 traceback**。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import Iterable

REPO = pathlib.Path(__file__).parents[1]
DEFAULT_CACHE_DIR = REPO / "tests" / "fixtures" / "kaggle"

DATASET = "bwandowando/dota-2-pro-league-matches-2023"

#: 相对数据集根的文件白名单（按 **basename** 匹配，见 `select_files`）。
WHITELIST = ("main_metadata.csv", "picks_bans.csv", "draft_timings.csv")

#: 规格 §10.2 的清单**之外**、但实测结构上必需的文件（精确相对路径）。
#:
#: 实测（2026-09-18，本机跑通真实下载后逐列检查）：`main_metadata.csv` **没有**
#: `league_name` 列（2016/2018/2025 三个样本年度的列集都没有，列名是
#: `leagueid`/`league_name` 里的后者根本不存在），而 `leagues.name` 是 `NOT NULL`、
#: M1 又要求 `leagues` 可查（> 50 行）。联赛名只在这一个文件里：
#: `Constants/Constants.Leagues.csv`（429 KB，列 = `leagueid,leaguename,tier`）。
#: 不加它 → `leagues` 一行都写不进去（或必须编造名字），Task 13 的 M1 断言直接不可能通过。
#: 代价：506.2 MB → 506.6 MB。
EXTRA_FILES = ("Constants/Constants.Leagues.csv",)

#: 规格 §10.2 的目录数。只用于**报警**（上游会更新数据集），不作断言。
EXPECTED_MIN_FOLDERS = {"main_metadata.csv": 19, "picks_bans.csv": 19, "draft_timings.csv": 10}

INSTALL_COMMAND = 'python -m pip install -e ".[dev,ingest]"'
DOWNLOAD_COMMAND = "python -m ingest.kaggle_subset"

CREDENTIALS_HELP = (
    "缺少 Kaggle 凭证，无法下载引导数据集。补齐方式（二选一）：\n"
    "  1) 环境变量：export KAGGLE_USERNAME=... KAGGLE_KEY=...\n"
    "  2) ~/.kaggle/kaggle.json：kaggle.com → Account → Create New API Token，"
    "下载后放到该路径（chmod 600）\n"
    "  也可以写进仓库根的 .env：cp .env.example .env 后填 KAGGLE_USERNAME/KAGGLE_KEY"
    "（.env 已在 .gitignore 中，不会入库）\n"
    "  （kaggle 2.x 另支持 token 流：export KAGGLE_API_TOKEN=... 或 ~/.kaggle/access_token）\n"
    f"凭证就位后运行：{DOWNLOAD_COMMAND}"
)


class KaggleCredentialsMissing(RuntimeError):
    """没有凭证 —— 提示里必须写清怎么补（规格 §10.2 要求凭证只存本地）。"""


class KaggleDependencyMissing(RuntimeError):
    """没有装 `[ingest]` extra。"""


# ------------------------------------------------------------------------ 凭证

def kaggle_json_path() -> pathlib.Path:
    """`~/.kaggle/kaggle.json`，可用 `KAGGLE_CONFIG_DIR` 覆盖（kaggle 官方客户端同样认它）。"""
    return _config_dir() / "kaggle.json"


def access_token_path() -> pathlib.Path:
    """kaggle 2.x 的 `~/.kaggle/access_token`（OAuth 流落盘的位置）。"""
    return _config_dir() / "access_token"


def _config_dir() -> pathlib.Path:
    override = os.environ.get("KAGGLE_CONFIG_DIR")
    return pathlib.Path(override) if override else pathlib.Path.home() / ".kaggle"


def credentials_present() -> bool:
    """只做环境/文件检查，**不 import kaggle**（缺依赖时要能给出"缺凭证"而不是 ImportError）。

    两条口径都认（实测 kaggle 2.2.4 的 `authenticate()` 按
    1) access token → 2) legacy key → 3) OAuth 的顺序尝试）：

    - **legacy**（计划 Step 3 的写法）：`KAGGLE_USERNAME` + `KAGGLE_KEY`，或 `~/.kaggle/kaggle.json`；
    - **2.x token 流**：`KAGGLE_API_TOKEN`，或 `~/.kaggle/access_token`。
      只认 legacy 会让"已经用 `kaggle auth login` 登过"的机器被误报成缺凭证。
    """
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    if os.environ.get("KAGGLE_API_TOKEN"):
        return True
    return kaggle_json_path().is_file() or access_token_path().is_file()


def require_credentials() -> None:
    if not credentials_present():
        raise KaggleCredentialsMissing(CREDENTIALS_HELP)


def load_env_file(path: pathlib.Path | None = None, *, log=print) -> bool:
    """读 `.env`（若存在）。返回是否真的加载了。

    `python-dotenv` 缺失**不是**错误：凭证也可以来自环境变量或 `~/.kaggle/kaggle.json`，
    故这里只警告并继续。默认路径是仓库根的 `.env`（不是 cwd）—— 计划 Task 12 Step 3 要求
    `cp .env.example .env` 后从 `.env` 读，而 `.env` 就该在仓库根。
    """
    path = pathlib.Path(path) if path is not None else REPO / ".env"
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        log(f"警告：{path} 存在但 python-dotenv 未安装（{INSTALL_COMMAND}），本次忽略该文件")
        return False
    load_dotenv(path)
    return True


# ------------------------------------------------------------------------ 白名单

def select_files(names: Iterable[str]) -> list[str]:
    """按 basename（外加 `EXTRA_FILES` 的精确路径）过滤数据集清单，保持原顺序、去重。

    白名单是"选择性下载 506 MB"的**唯一实现处**：一旦这里放宽，`players.csv` 的 41 GB
    就会被拉下来。故它单独成函数并被测试直接覆盖。
    """
    selected: list[str] = []
    for name in names:
        keep = name.rsplit("/", 1)[-1] in WHITELIST or name in EXTRA_FILES
        if keep and name not in selected:
            selected.append(name)
    return selected


def manifest_entries(response) -> list[tuple[str, int | None]]:
    """把 `dataset_list_files` 的单页响应适配成 `[(文件名, 字节数)]`。

    kaggle 客户端跨版本返回过对象 / 字典 / 字符串三种形状，字段名有 `name` / `ref`、
    `total_bytes` / `size` 多种；这里全部接住 —— 认不出形状只会让白名单为空，而那是**报错**，
    不是静默少下。
    """
    entries = getattr(response, "files", response)
    out: list[tuple[str, int | None]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            name, size = entry, None
        elif isinstance(entry, dict):
            name = entry.get("name") or entry.get("ref")
            size = entry.get("total_bytes") or entry.get("totalBytes") or entry.get("size")
        else:
            name = getattr(entry, "name", None) or getattr(entry, "ref", None)
            size = getattr(entry, "total_bytes", None) or getattr(entry, "size", None)
        if name:
            out.append((str(name), int(size) if size else None))
    return out


def manifest_names(response) -> list[str]:
    """单页响应里的文件名（`manifest_entries` 的薄封装）。"""
    return [name for name, _size in manifest_entries(response)]


#: 单页条数。**必须显式给大值**：kaggle 2.2.4 的 `dataset_list_files(page_size=20)` 默认
#: 只返回 20 条，而本数据集有 1546 个文件 —— 用默认值只会看到第一页，白名单里的文件大概率
#: 一个都不在，然后以"清单里没有白名单文件"报错（或更糟：只下到前 20 个里的那几个）。
LIST_PAGE_SIZE = 1000
#: 翻页上限：纯粹防"上游永远返回同一个 next_page_token"把 CLI 挂死。
MAX_LIST_PAGES = 200


def list_all_files(client, dataset: str, *, page_size: int = LIST_PAGE_SIZE,
                   max_pages: int = MAX_LIST_PAGES, log=print) -> dict[str, int | None]:
    """翻完所有页的文件清单 → `{文件名: 字节数}`（`dataset_list_files` 默认只有 20 条/页）。"""
    files: dict[str, int | None] = {}
    token: str | None = None
    for page in range(1, max_pages + 1):
        response = client.dataset_list_files(dataset, page_token=token, page_size=page_size)
        error = getattr(response, "error_message", None)
        if error:
            raise RuntimeError(f"{dataset} 的清单接口返回错误：{error}")
        files.update(dict(manifest_entries(response)))
        token = getattr(response, "next_page_token", None) or None
        if not token:
            if page > 1:
                log(f"清单共 {page} 页 / {len(files)} 个文件")
            return files
    raise RuntimeError(
        f"{dataset} 的清单翻页超过 {max_pages} 页仍未结束（已收 {len(files)} 个文件）："
        f"上游分页行为异常，拒绝用一个可能不完整的清单去决定下哪些文件。")


#: 缓存盘点时认识的 basename（年度目录里的三个 CSV + `EXTRA_FILES` 的 basename）。
KNOWN_BASENAMES = (*WHITELIST, *(name.rsplit("/", 1)[-1] for name in EXTRA_FILES))


def summarize(cache_dir) -> dict:
    """盘点缓存：每个已知文件有几个、分布在哪些目录、共多少字节。"""
    cache_dir = pathlib.Path(cache_dir)
    files = {name: 0 for name in KNOWN_BASENAMES}
    folders: dict[str, list[str]] = {}
    total_bytes = 0
    for path in sorted(cache_dir.glob("*/*.csv")) if cache_dir.is_dir() else []:
        if path.name not in files:
            continue
        files[path.name] += 1
        folders.setdefault(path.parent.name, []).append(path.name)
        total_bytes += path.stat().st_size
    return {"files": files, "folders": {k: sorted(v) for k, v in sorted(folders.items())},
            "n_folders": len(folders), "total_bytes": total_bytes}


# ------------------------------------------------------------------------ 下载

def _build_api():
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ModuleNotFoundError as exc:
        raise KaggleDependencyMissing(
            f"缺少 kaggle 依赖（{exc}）。安装：{INSTALL_COMMAND}") from exc
    api = KaggleApi()
    api.authenticate()                 # 自己会读 KAGGLE_* / ~/.kaggle/kaggle.json
    return api


def download_one(client, dataset: str, name: str, cache_dir: pathlib.Path, *,
                 expected_bytes: int | None = None, log=print) -> tuple[pathlib.Path, bool]:
    """下一个文件并保证它落在 `cache_dir/<name>`；返回 `(路径, 是否真的下载了)`。

    **可续跑**：本地文件大小与清单一致时直接跳过。506 MB 的下载中断后重跑不该从头再来，
    而 `force=True` 每次都会重下全部 —— 这也是"每 N 场 commit 一次以便中断可续"的同一条理由。

    **为什么不能只信 `path` 参数**：kaggle 2.2.4 的 `dataset_download_file` 把落盘名写成
    下载 URL 的最后一段（源码：`outfile = os.path.join(effective_path,
    url.split("?")[0].split("/")[-1])`），而不是 `file_name`。实测本数据集下两者同名
    （落盘名 == basename），但 URL 形状随版本/接口变化，所以这里做一次**有界**补救：
    只认"这次调用新出现的、且全目录唯一的新文件"，唯一才改名；否则**报错并列出目录内容**，
    绝不猜。
    """
    dest = cache_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if expected_bytes and dest.is_file() and dest.stat().st_size == expected_bytes:
        log(f"  跳过 {name}（本地 {expected_bytes} 字节与清单一致）")
        return dest, False
    before = {p.name for p in dest.parent.iterdir()}
    client.dataset_download_file(dataset, name, path=str(dest.parent), force=True, quiet=True)
    if dest.is_file():
        return dest, True
    new_files = sorted(p for p in dest.parent.iterdir() if p.name not in before and p.is_file())
    if len(new_files) == 1:
        log(f"  {name}: kaggle 客户端落盘为 {new_files[0].name}，按白名单路径改名为 {dest.name}")
        os.replace(new_files[0], dest)
        return dest, True
    raise RuntimeError(
        f"下载 {name} 后既没有 {dest}，也没有「唯一的新文件」可认"
        f"（新文件 = {[p.name for p in new_files]}，目录现有 = "
        f"{sorted(p.name for p in dest.parent.iterdir())}）：拒绝猜测哪个是目标文件，"
        f"请人工确认 kaggle 客户端的落盘命名规则。")


def download(cache_dir=DEFAULT_CACHE_DIR, *, dataset: str = DATASET, api=None, log=print) -> dict:
    """按白名单下载到 `cache_dir/<folder>/<file>`，返回 `summarize()` 的盘点。

    `api` 可注入（测试用假客户端验证"只请求白名单文件"），注入时仍然先查凭证 ——
    凭证检查是这条路径的前置条件，不该因为测试注入而消失。
    """
    load_env_file(log=log)
    require_credentials()

    cache_dir = pathlib.Path(cache_dir)
    client = api if api is not None else _build_api()
    manifest = list_all_files(client, dataset, log=log)
    wanted = select_files(manifest)
    if not wanted:
        raise RuntimeError(
            f"{dataset} 的文件清单里没有任何白名单文件（清单 {len(manifest)} 项，"
            f"白名单 {WHITELIST} + {EXTRA_FILES}）：清单前几项 = {list(manifest)[:5]}。"
            f"检查数据集 slug、kaggle 客户端接口形状或分页。")

    expected_bytes = sum(manifest[n] for n in wanted if manifest[n])
    log(f"白名单命中 {len(wanted)} 个文件 / 清单合计 {expected_bytes / 1e6:.1f} MB")
    downloaded = 0
    for name in wanted:
        _path, fetched = download_one(client, dataset, name, cache_dir,
                                      expected_bytes=manifest.get(name), log=log)
        downloaded += fetched

    summary = summarize(cache_dir)
    summary["requested_files"] = len(wanted)
    summary["downloaded_files"] = downloaded
    log(f"下载完成：{summary['files']}（缓存共 {summary['total_bytes'] / 1e6:.1f} MB，"
        f"{summary['n_folders']} 个目录；本次实际下载 {downloaded} 个文件）")
    short = {name: (count, EXPECTED_MIN_FOLDERS[name])
             for name, count in summary["files"].items()
             if name in EXPECTED_MIN_FOLDERS and count < EXPECTED_MIN_FOLDERS[name]}
    if short:
        log(f"警告：以下文件的目录数少于规格 §10.2 的预期（{short}）——"
            f"draft_timings 本来就只有 10/19 个目录，其余缺口需要人看一眼")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingest.kaggle_subset",
        description="按白名单下载 Kaggle 引导数据集的 506.2 MB 子集（绝不整包下载）")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE_DIR,
                        help=f"落盘目录（默认 {DEFAULT_CACHE_DIR}）")
    parser.add_argument("--dataset", default=DATASET, help=f"数据集 slug（默认 {DATASET}）")
    args = parser.parse_args(argv)

    try:
        download(args.cache_dir, dataset=args.dataset)
    except (KaggleCredentialsMissing, KaggleDependencyMissing) as exc:
        print(f"\n[ingest.kaggle_subset] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                      # 上游/网络/磁盘：给一行提示，不吐 traceback
        print(f"\n[ingest.kaggle_subset] 下载失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: `ingest/load_bootstrap.py`**

入库顺序与要点（**`load_constants` 必须先跑**，否则外键失败）：

1. `leagues` ← `Constants/Constants.Leagues.csv` 的 `leagueid/leaguename/tier`（`main_metadata.csv`
   没有联赛名；缺名字时不编造，FK 写 NULL 并计数）
2. `teams` ← `main_metadata.csv` 的队 id/队名（实测没有队名列 → 目前 0 行，FK 写 NULL 并报数量）
3. `matches` ← `main_metadata.csv`：`started_at=to_timestamp(start_date_time)`（**按 UTC**，
   列名实测不是 `start_time`）、`duration_s`、`league_id`、`series_id`、`series_type`、
   `radiant_team_id`、`dire_team_id`、`radiant_win`、`lobby_type`、`draft_state`
4. `draft_actions` ← `picks_bans.csv`：按 `match_id` **先删后插**（规格 §5.2），
   `ord = int(float(order)) - 起点`（实测四个年度都是 0 起）
5. `first_pick_team` ← 每场 `ord=0` 的 `team`（`shared.draft_template.first_pick_team_from_actions`）
6. `n_draft_actions` / `draft_state` / `anomaly` ← 按规格 §5.3 判定，但**顺序族**必须按实测表
   （见 `DRAFT_ORDERS`）：手数 ∉ 已登记族、或类型偏离最接近的族、或缺 ord=0、或有越界/重复手
   → `anomaly=true` 并写一行 `draft_anomalies`
7. `patch_id` ← `constants.patches.subpatch_for_timestamp(start_time)` 查 `patches`
   （**只有 `start_time >= 1517472000` 才保证查得到**；更早的场次留 NULL 并**报出占比**）
8. 全部写入用 `ON CONFLICT`；`commit=True` 时每 N 场提交一次以便中断可续

<!-- FILE: ingest/load_bootstrap.py -->
```python
"""Kaggle 引导数据集 → `leagues` / `teams` / `matches` / `draft_actions` / `draft_anomalies`。

对应规格 §5.2（幂等）、§5.3（异常不阻断但必须记录）、§3.2/§15（版本归属）、
§10.2（`picks_bans.csv` 是 BP 序列的权威来源，`draft_timings.csv` 仅作补充 —— 本模块不读它）。

**这个模块是对着真实 CSV 写的，不是对着计划里的列名猜的。** 2026-09-18 在本机跑通真实下载后
逐列核对，四处与计划的假设不符（全部在计划 Task 12 的实测记录里留证）：

1. `main_metadata.csv` **没有 `start_time`**，只有 `start_date_time`
   （`'2016-01-02 15:12:19'`，朴素字符串，按 **UTC** 解释 —— OpenDota 的 `start_time`
   本就是 UTC epoch，导出时格式化成了这个形状）。
2. `main_metadata.csv` **没有 `league_name`**（也没有队名）：联赛名只在
   `Constants/Constants.Leagues.csv`（`leagueid,leaguename,tier`）里，而 `leagues.name`
   是 `NOT NULL`。故白名单必须补上这个文件（见 `ingest.kaggle_subset.EXTRA_FILES`）。
3. `picks_bans.csv` 的 `order`/`team`/`hero_id` 在 2016/2018 里是**浮点字符串**
   （`'0.0'`/`'78.0'`），2025 里是整数串；直接 `int()` 会 ValueError。故统一
   `int(float(...))`。
4. **规格 §6.0 的 24 手模板只是诸多合法 CM 顺序中的一种**：Valve 改过 ban 顺序，
   2025 年的比赛里 95.4% 用的是与 §6.0 不同的顺序（见 `DRAFT_ORDERS`）。
   按 §6.0 一种模板判异常会把 95%+ 的正常比赛判成异常 —— 异常率断言必然假红。

**前置：常量层必须先入库。** `draft_actions.hero_id` 是 `heroes(hero_id)` 的外键、
`matches.patch_id` 是 `patches(patch_id)` 的外键，且归属要读 `patches`。没有它，第一条
INSERT 会以 FK 违规炸在深处；本模块在动手之前显式检查并给出可操作报错（`_require_constants`）。

**`order` → `ord` 的入库边界（规格 §17-7）**：CSV 的 `order` 起点**不假设**为 0。
`detect_ord_origin()` 按整个文件的 `min(order)` 判定（0 → 原样，1 → 统一减 1），两者都不是
就报错。实测 2016/2018/2025 全部是 0 起（且 2016/2018 还带一列 OpenDota 自己的 `ord`，
与 `order` 逐行相同）。

**异常判定（规格 §5.3）**：`anomaly=true` 的语义是「这份 draft 不符合**任何一种已知的合法
CM 顺序**」，而不是「不符合 §6.0 那一种」。理由见第 4 条：Valve 改过顺序，而 §6.0 的模板
如今只覆盖 2025 年 3.4% 的比赛。每个场次命中的顺序族记在 `detail["order_family"]`，
并在入库报告里按族统计 —— **下游（序列模型）必须自己按族过滤**，因为
`shared.draft_template.resolve()` 只对 `spec_6_0_24` 族正确。

**`patch_id` 归属（规格 §3.2/§15）**：走
`constants.patches.subpatch_for_timestamp(start_time)` 拿**版本名**，再按 `version_name`
查 `patches`，**不做 `released_at` 的区间二分**。两者看着等价，其实差一个静默钳位：
对 `patches.released_at` 做二分/取最近行时，早于 7.08 的比赛会被钳到**最早的版本**（7.08），
2016–2017 的场次集体错标成 2018 年的版本，且不会有任何报错。
`patches` 的行集来自 Valve（Task 10 的 R7），Valve 清单从 7.08 = 1517472000（2018-02-01）起，
故只有 `start_time >= 1517472000` 才保证有行可指；更早的场次写 NULL，并在结束时**报出占比**
（pre-2018 占比是测量结果，不是可以预设的预算）。CSV 自带的 `patch` 列（OpenDota 粗粒度 id）
被用作**独立交叉校验**：与本模块解析出的 `patches.opendota_patch` 逐场比对并报出吻合率。

**幂等（规格 §5.2）**：`leagues`/`teams`/`matches` 用主键 upsert；`draft_actions` 按
`match_id` **先删后插**（否则上一次运行残留的手会留下来，`n_draft_actions` 与实际手数分叉）；
`draft_anomalies` 每场最多一行，重新加载后不再异常的场次要**删掉旧行**（否则
`draft_anomalies` 的行数与 `matches.anomaly` 的计数会分叉，而 M1 正是这么校验的）。
"""
from __future__ import annotations

import csv
import datetime
import json
import pathlib
import re
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from constants.patches import subpatch_for_timestamp
from shared.draft_template import TEMPLATE as SPEC_TEMPLATE
from shared.draft_template import first_pick_team_from_actions

REPO = pathlib.Path(__file__).parents[1]
DEFAULT_CACHE_DIR = REPO / "tests" / "fixtures" / "kaggle"

METADATA_FILENAME = "main_metadata.csv"
ACTIONS_FILENAME = "picks_bans.csv"
#: 联赛名/分级的唯一来源（`leagueid,leaguename,tier`）。见模块 docstring 第 2 条。
LEAGUES_FILENAME = "Constants/Constants.Leagues.csv"

#: 规格 §3.2/§15：Valve 清单起点 7.08 = 2018-02-01（Unix 1517472000）。
#: **左闭**：恰好等于该时刻的比赛属于 7.08；早一秒的属于 patchdates 独有的旧版本，无行可指。
PATCH_ATTRIBUTION_MIN_START_TIME = 1517472000

ORD_MAX = 23
_F = "F"          # 先手方（first pick team）
_O = "O"          # 后手方

#: 实测的合法 CM 顺序族（2026-09-18 对 **205,005 场**真实数据的全量频次统计得出，
#: 并用 OpenDota 实时 API 的 `picks_bans` 抽样交叉验证）。
#:
#: 「归属」列是相对**先手方** F（由 ord=0 的 team 推出，规格 §8①）的：
#: 实测 ord=0 的队与第一手 pick 的队永远相同（§16.3 的不变式在真实数据上成立）。
#:
#: **为什么必须是一张表而不是一个模板**：Valve 在历次改版里换过 CM 的 ban 顺序，语料横跨
#: 2016–2026（20 / 22 / 24 三种手数、共十种顺序）。按 §6.0 那**一种**模板判异常，首次真实
#: 入库实测把 **58.33%** 的正常比赛判成了异常 —— 异常率断言必然假红，而"异常"这个字段也就
#: 失去了意义。
#:
#: 登记门槛：**在各自手数里支持度 >= 1%** 的顺序（尾部稀有 pattern 一律算异常）。
#: 十族合计覆盖 24 手的全部 149,522 场、22 手的 99.88%、20 手的 99.75%；未登记的
#: （各奇零手数 + 尾部）合计约 1.0%，落在 §15 的 2% 之内，且这个数是**测出来的**。
#: 每族后面标注：实测场次、占比、出现的年度目录。
DRAFT_ORDERS: dict[str, tuple[tuple[bool, str], ...]] = {
    # bbbbPPPPbbbbPPPPbbPP / FOFOFOOFOFOFOFOFOFFO —— 12340 场（84.85%），2016/2017
    "cm20_a": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _O), (False, _F), (False, _O), (False, _F),
        (True, _O), (True, _F), (True, _O), (True, _F), (False, _O), (False, _F),
        (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbPPPPbbPP / FOFOFOOFFOFOOFOFOFOF —— 2167 场（14.90%），仅 2016
    "cm20_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _O), (True, _F), (True, _O), (True, _F), (False, _O), (False, _F),
        (True, _O), (True, _F),
    ),
    # bbbbbbPPPPbbbbPPPPbbPP / FOFOFOFOOFFOFOOFOFOFFO —— 26126 场（67.04%），2018-2020
    "cm22_a": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _F), (True, _O), (True, _O), (True, _F), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbbbPPPPbbPPPPbbPP / FOFOFOFOFOOFFOOFOFOFFO —— 8035 场（20.62%），仅 2020
    "cm22_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _F), (True, _O), (True, _O), (True, _F),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbPPPPbbbbPPPPbbPP / FOFOFOFOOFOFOFOFOFOFFO —— 4767 场（12.23%），2017/2018
    "cm22_c": (
        (False, _F), (False, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (True, _F), (True, _O), (True, _O), (True, _F), (False, _O), (False, _F),
        (False, _O), (False, _F), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbbbbPPbbbPPPPPPbbbbPP / FOOFOOFFOFFOOFFOOFFOOFFO —— 68775 场（46.00%），2023-2025
    "cm24_a": (
        (False, _F), (False, _O), (False, _O), (False, _F), (False, _O), (False, _O),
        (False, _F), (True, _F), (True, _O), (False, _F), (False, _F), (False, _O),
        (True, _O), (True, _F), (True, _F), (True, _O), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _O), (False, _F), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOOFFOFOFOOFFOFOFOFO —— 46347 场（31.00%），2021-2023
    "cm24_b": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _F), (True, _O),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOFOFOFOFOOFOFFOFOFO —— 14045 场（9.39%），2020/2021
    "cm24_c": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _F), (True, _O), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # bbbbPPPPbbbbbbPPPPbbbbPP / FOFOFOOFFOFOFOOFOFFOFOFO —— 5220 场（3.49%），仅 2021
    "cm24_d": (
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
        (True, _O), (True, _F), (False, _F), (False, _O), (False, _F), (False, _O),
        (False, _F), (False, _O), (True, _O), (True, _F), (True, _O), (True, _F),
        (False, _F), (False, _O), (False, _F), (False, _O), (True, _F), (True, _O),
    ),
    # 规格 §6.0 / `shared/draft_template.TEMPLATE`（**直接 import，不复制**）。
    # 实测就是 2025 年下半年起 + 全部 2026 目录在用的那一族（15135 场 = 10.12%），也是
    # OpenDota 实时 API 上 match 8996973546 的顺序 —— 即**当下**的 CM 顺序。
    # 2023-2025 上半年的比赛用的是 cm24_a（46.00%），与它只差两段 ban。
    "spec_6_0_24": tuple(SPEC_TEMPLATE),
}

#: 逻辑列名 → CSV 里可接受的列名。只放**实测过**的别名；猜错时 `resolve_columns` 会报错
#: 并打印实际列名，而不是静默写 NULL（规格 §17-7）。
METADATA_COLUMNS: dict[str, tuple[str, ...]] = {
    "match_id": ("match_id",),
    # 实测只有 start_date_time；start_time 作为兼容别名保留（上游换 schema 时不至于立刻炸）
    "start_time": ("start_time", "start_date_time"),
    "duration_s": ("duration", "duration_s"),
    "league_id": ("leagueid", "league_id"),
    "league_name": ("league_name",),            # 实测缺失：联赛名走 Constants.Leagues.csv
    "series_id": ("series_id",),
    "series_type": ("series_type",),
    "radiant_team_id": ("radiant_team_id",),
    "radiant_team_name": ("radiant_team_name",),
    "dire_team_id": ("dire_team_id",),
    "dire_team_name": ("dire_team_name",),
    "radiant_win": ("radiant_win",),
    "lobby_type": ("lobby_type",),
    "opendota_patch": ("patch",),               # 仅供交叉校验，不参与 patch_id 归属
}
REQUIRED_METADATA = ("match_id", "start_time")

ACTIONS_COLUMNS: dict[str, tuple[str, ...]] = {
    "match_id": ("match_id",),
    "order": ("order", "ord"),                  # 实测两列都有且逐行相同
    "is_pick": ("is_pick",),
    "team": ("team",),
    "hero_id": ("hero_id",),
}

LEAGUES_COLUMNS: dict[str, tuple[str, ...]] = {
    "league_id": ("leagueid", "league_id"),
    "name": ("leaguename", "league_name", "name"),
    "tier": ("tier",),
}

_TRUE_WORDS = {"true", "t", "1", "yes", "y"}
_FALSE_WORDS = {"false", "f", "0", "no", "n"}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


# ------------------------------------------------------------------ 值解析

def parse_number(value) -> int:
    """`'78.0'` / `'78'` / `78.0` → `78`。

    实测 `picks_bans.csv` 的 `hero_id`/`team`/`order` 在 2016/2018 是**浮点字符串**
    （pandas 导出的痕迹），2025 是整数串；只认 `int()` 会在真实数据上直接 ValueError。
    只接受整值浮点：`12.5` 这种非整值一律报错，而不是悄悄截断。
    """
    if isinstance(value, bool):
        raise ValueError(f"布尔值不是数字：{value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"不是整值：{value!r}")
        return int(value)
    text = str(value).strip()
    if not _NUMBER_RE.fullmatch(text):
        raise ValueError(f"无法解析为数字：{value!r}")
    number = float(text)
    if not number.is_integer():
        raise ValueError(f"不是整值（拒绝截断）：{value!r}")
    return int(number)


def parse_bool(value) -> bool:
    """`True/1/'true'/'T'` 等都要认（CSV 里布尔是字符串，Python `repr` 会写成 `True`）。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError(f"无法解析为布尔值：{value!r}")


def parse_timestamp(value) -> int:
    """`start_time`（epoch 秒）或 `start_date_time`（`'YYYY-MM-DD HH:MM:SS'`）→ Unix 秒。

    朴素字符串按 **UTC** 解释：OpenDota 的 `start_time` 本就是 UTC epoch，Kaggle 的导出
    只是把它格式化成了 `start_date_time`。带显式偏移的 ISO 串按偏移换算，不做二次假设。
    这个假设由 `patch` 列的交叉校验间接验证（时区错会让补丁边界附近的场次系统性错配）。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if _NUMBER_RE.fullmatch(text):
        return int(float(text))
    try:
        parsed = datetime.datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError as exc:
        raise ValueError(f"无法解析为时间戳：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return int(parsed.timestamp())


# --------------------------------------------------------------------- CSV 读取

def read_csv(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    """读 CSV，返回 `(列名, 行)`。

    `utf-8-sig` 是**必需**的：Kaggle 的 CSV 常带 BOM，否则首列名会变成 `'\\ufeffmatch_id'`，
    于是"列名不符"会以最难查的形式出现（只有第一列对不上）。列名与取值都 strip —— 上游
    导出工具会在逗号后留空格。
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [n.strip() if isinstance(n, str) else n for n in (reader.fieldnames or [])]
        rows = [{k.strip() if isinstance(k, str) else k:
                 (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in reader]
    return fieldnames, rows


def resolve_columns(fieldnames: Sequence[str], spec: Mapping[str, tuple[str, ...]],
                    required: Iterable[str], path: pathlib.Path) -> dict[str, str | None]:
    """把逻辑列名解析为实际列名；必需列缺失则**指名报错**（含实际列名）。"""
    mapping = {logical: next((a for a in aliases if a in fieldnames), None)
               for logical, aliases in spec.items()}
    missing = [logical for logical in required if mapping[logical] is None]
    if missing:
        raise RuntimeError(
            f"{path} 缺少必需列 {missing}（可接受的列名："
            f"{ {m: spec[m] for m in missing} }）；实际列名 = {sorted(fieldnames)}。"
            f"规格 §17-7：Kaggle CSV 的列名与语义必须实测确认后再决定映射，猜错必须在这里炸，"
            f"而不是写一堆 NULL 或静默跳过。")
    return mapping


def read_header(path: pathlib.Path) -> list[str]:
    """只读表头（`preflight_columns` 用；不必为验列名把 66 MB 全读进来）。"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        row = next(csv.reader(f), [])
    return [n.strip() if isinstance(n, str) else n for n in row]


def preflight_columns(folders: Iterable[pathlib.Path], cache_dir: pathlib.Path) -> None:
    """**先**把所有输入文件的列名契约验完，再开始写。

    列名由上游决定（规格 §17-7 的未验证事项）。猜错时必须**在写第一行之前**炸：否则会跑完
    19 个目录里的前 18 个、写进几万场之后才因最后一个目录的列名报错 —— 虽然幂等重跑能收敛，
    但"看起来成功了一半"的运行本身就是误导。整个 preflight 只读表头，代价可忽略。
    """
    leagues_path = cache_dir / LEAGUES_FILENAME
    if leagues_path.is_file():
        resolve_columns(read_header(leagues_path), LEAGUES_COLUMNS,
                        ("league_id", "name"), leagues_path)
    for folder in folders:
        targets = [(folder / METADATA_FILENAME, METADATA_COLUMNS, REQUIRED_METADATA)]
        actions_path = folder / ACTIONS_FILENAME
        if actions_path.is_file():
            targets.append((actions_path, ACTIONS_COLUMNS, tuple(ACTIONS_COLUMNS)))
        for path, spec, required in targets:
            resolve_columns(read_header(path), spec, required, path)


def read_leagues(cache_dir: pathlib.Path) -> dict[int, tuple[str, str | None]]:
    """`Constants/Constants.Leagues.csv` → `{league_id: (name, tier)}`（缺失则返回空表）。"""
    path = cache_dir / LEAGUES_FILENAME
    if not path.is_file():
        return {}
    fieldnames, rows = read_csv(path)
    mapping = resolve_columns(fieldnames, LEAGUES_COLUMNS, ("league_id", "name"), path)
    leagues: dict[int, tuple[str, str | None]] = {}
    for row in rows:
        league_id = _cell(row, mapping, "league_id")
        name = _cell(row, mapping, "name")
        if league_id is None or name is None:
            continue
        leagues[parse_number(league_id)] = (name, _cell(row, mapping, "tier"))
    return leagues


def _cell(row: Mapping[str, str], mapping: Mapping[str, str | None], logical: str) -> str | None:
    column = mapping.get(logical)
    if column is None:
        return None
    value = row.get(column)
    return value if isinstance(value, str) and value.strip() != "" else None


def _int_cell(row, mapping, logical, *, path, match_id) -> int | None:
    value = _cell(row, mapping, logical)
    if value is None:
        return None
    try:
        return parse_number(value)
    except ValueError as exc:
        raise RuntimeError(
            f"{path} 的 match_id={match_id} 的 {logical}={value!r} 不是整数") from exc


def detect_ord_origin(orders: Iterable[int]) -> int:
    """判定 `picks_bans.csv` 的 `order` 起点：返回 0（原样）或 1（需减 1）。

    规格 §17-7：模板映射对起点极敏感，错一位就整体错位。判定依据是**整个文件**的
    `min(order)` —— 按单行判定会在 1-based 文件里被"恰好等于 1 的那一行"骗过。
    实测（2016/2018/2025）三个年度都是 0 起。
    """
    values = [parse_number(o) for o in orders]
    if not values:
        raise ValueError("空的 picks_bans：无法判定 order 起点（规格 §17-7）")
    low = min(values)
    if low == 0:
        return 0
    if low == 1:
        return 1
    raise ValueError(
        f"order 的最小值是 {low}（最大 {max(values)}）：既不是 0 起也不是 1 起，"
        f"规格 §17-7 的模板映射无从对齐，必须先确认数据集语义。")


def normalize_actions(rows: Sequence[Mapping[str, object]], *, ord_origin: int = 0
                      ) -> tuple[list[dict], dict[str, int]]:
    """把 CSV 手数行归一化为 `{"ord", "is_pick", "team", "hero_id"}`，并统计两类脏数据。

    - 越界手（归一化后不在 0..23）**丢弃并计数**：`draft_actions.ord` 有 CHECK，
      直接插会整场失败，而规格 §5.3 要求"不阻断入库、显式记录"。
    - 同一 `ord` 出现多次时**以最后一条为准**并计数（`(match_id, ord)` 是主键，
      重复直接插会以 UniqueViolation 炸掉整场）。取最后一条是约定，不是事实。
    """
    by_ord: dict[int, dict] = {}
    problems = {"ord_out_of_range": 0, "duplicate_ord": 0}
    for row in rows:
        ord_ = parse_number(row["order"]) - ord_origin
        if not 0 <= ord_ <= ORD_MAX:
            problems["ord_out_of_range"] += 1
            continue
        if ord_ in by_ord:
            problems["duplicate_ord"] += 1
        by_ord[ord_] = {"ord": ord_, "is_pick": parse_bool(row["is_pick"]),
                        "team": parse_number(row["team"]),
                        "hero_id": parse_number(row["hero_id"])}
    return [by_ord[k] for k in sorted(by_ord)], problems


# ------------------------------------------------------- 顺序族与异常判定（§5.3）

def resolve_in(family: str, ord_: int, first_pick_team: int) -> tuple[bool, int]:
    """按**指定顺序族**推导该手的 (is_pick, team)，语义与 `shared.draft_template.resolve` 相同。

    `shared.draft_template` 只定义 §6.0 那一种（`spec_6_0_24`），而 Valve 改过 ban 顺序，
    所以异常判定不能只问它。引擎/模型侧仍以 `shared.draft_template` 为准（那份是契约），
    本函数只服务入库期的结构判定。
    """
    is_pick, who = DRAFT_ORDERS[family][ord_]
    return is_pick, (first_pick_team if who == _F else 1 - first_pick_team)


def matching_order(actions: Sequence[Mapping[str, object]], first_pick_team: int | None) -> str | None:
    """返回该 draft 命中的顺序族名（没有命中返回 None）。"""
    if first_pick_team not in (0, 1):
        return None
    n_actions = len(actions)
    for family, template in DRAFT_ORDERS.items():
        if len(template) != n_actions:
            continue
        if all((a["is_pick"], a["team"]) == resolve_in(family, a["ord"], first_pick_team)
               for a in actions):
            return family
    return None


def _deviations(actions: Sequence[Mapping[str, object]], first_pick_team: int, family: str) -> list[dict]:
    out = []
    for action in actions:
        want_pick, want_team = resolve_in(family, action["ord"], first_pick_team)
        if action["is_pick"] != want_pick or action["team"] != want_team:
            out.append({"ord": action["ord"], "is_pick": action["is_pick"], "team": action["team"],
                        "expected_is_pick": want_pick, "expected_team": want_team})
    return out


def closest_order_family(actions: Sequence[Mapping[str, object]], first_pick_team: int | None) -> str | None:
    """手数相同、与实测差得最少的顺序族（只为把偏差说清楚，不改变异常判定）。"""
    if first_pick_team not in (0, 1):
        return None
    candidates = [f for f, t in DRAFT_ORDERS.items() if len(t) == len(actions)]
    if not candidates:
        return None
    return min(candidates, key=lambda f: len(_deviations(actions, first_pick_team, f)))


def detect_anomaly(actions: Sequence[Mapping[str, object]], first_pick_team: int | None, *,
                   ord_out_of_range: int = 0, duplicate_ord: int = 0) -> dict | None:
    """规格 §5.3：draft 结构异常 → 返回 `{"n_actions", "kinds", "detail"}`；否则 None。

    异常 = **不符合任何一种已知的合法 CM 顺序**（见 `DRAFT_ORDERS`），或手数不在 {22, 24}，
    或推不出先手方，或有被丢弃/重复的手。0 手**不是**异常：规格 §5.2 里 `picks_bans` 不存在
    就是 `pending`（状态机还在等数据）。

    `detail["order_family"]` 记录命中的族（异常行为 None），`detail["deviations"]` 记录与
    **最接近的族**的逐手偏差 —— 只报"异常"而不报"差在哪"等于没报。
    """
    if not actions and not ord_out_of_range and not duplicate_ord:
        return None

    n_actions = len(actions)
    kinds: list[str] = []
    detail: dict[str, object] = {"n_actions": n_actions}

    family = matching_order(actions, first_pick_team) if actions else None
    detail["order_family"] = family

    if actions and first_pick_team not in (0, 1):
        # 推不出先手方时**不做类型判定**：没有 F 就没有"应当是哪一队"，硬判会造出假偏差
        kinds.append("missing_ord_zero")
    elif actions and family is None:
        if n_actions > 24:
            kinds.append("long_draft")
        elif n_actions in (22, 24):
            kinds.append("type_deviation")
        else:                                   # < 22 或 23 手：少手
            kinds.append("short_draft")
        if n_actions in (22, 24):
            nearest = closest_order_family(actions, first_pick_team)
            deviations = _deviations(actions, first_pick_team, nearest) if nearest else []
            detail["closest_order_family"] = nearest
            detail["n_deviations"] = len(deviations)
            detail["deviations"] = deviations[:20]     # 只留前 20 条，避免 detail 无界增长

    if ord_out_of_range:
        kinds.append("ord_out_of_range")
        detail["ord_out_of_range"] = ord_out_of_range
    if duplicate_ord:
        kinds.append("duplicate_ord")
        detail["duplicate_ord"] = duplicate_ord

    if not kinds:
        return None
    return {"n_actions": n_actions, "kinds": kinds, "detail": detail}


# ------------------------------------------------------------------ 版本归属（§3.2）

def patch_ids_by_version(conn) -> dict[str, int]:
    """`{version_name: patch_id}`：一次读全表，避免每场一次 SELECT。"""
    return dict(conn.execute("SELECT version_name, patch_id FROM patches").fetchall())


def opendota_patch_by_id(conn) -> dict[int, int | None]:
    """`{patch_id: opendota_patch}`：`patch` 列交叉校验用。"""
    return dict(conn.execute("SELECT patch_id, opendota_patch FROM patches").fetchall())


def patch_id_for(conn, start_time: int, *, patch_ids: Mapping[str, int] | None = None) -> int | None:
    """给定 `start_time`（Unix 秒），返回 `matches.patch_id`（或 None）。

    规则（规格 §3.2/§15 + Task 10 的 R7）：

    - `start_time < 1517472000`（7.08 = 2018-02-01）→ **NULL**。该年代的版本名来自
      patchdates 独有的 6.70–7.07 段，`patches` 里没有这些行。此处**不能**退化成
      "取最早的版本"，那会把 2016–2017 的场次静默错标成 7.08。
    - 之后必须命中：名字来自 `subpatch_for_timestamp`（Valve 优先的时间线），若查不到行，
      说明常量层与归属时间线不一致（loader 少写一行 / 快照换了），**报错**而不是写 NULL。
    """
    if int(start_time) < PATCH_ATTRIBUTION_MIN_START_TIME:
        return None
    name = subpatch_for_timestamp(int(start_time))
    ids = patch_ids if patch_ids is not None else patch_ids_by_version(conn)
    if name not in ids:
        raise RuntimeError(
            f"start_time={start_time} 归属到版本 {name!r}，但 patches 表里没有这一行："
            f"归属边界（>= {PATCH_ATTRIBUTION_MIN_START_TIME}，即 2018-02-01 起）之后必须覆盖。"
            f"先确认 constants.load.load_constants 已跑过且快照与 Task 10 的时间线一致。")
    return ids[name]


# ------------------------------------------------------------------------ 入库

def _require_constants(conn) -> int:
    """常量层非空且归属边界一致才允许动手（在任何 INSERT 之前）。"""
    n_heroes = conn.execute("SELECT count(*) FROM heroes").fetchone()[0]
    n_patches, earliest = conn.execute(
        "SELECT count(*), min(extract(epoch FROM released_at)) FROM patches").fetchone()
    if not n_heroes or not n_patches:
        raise RuntimeError(
            "常量层为空：必须先跑 constants.load.load_constants(conn)"
            "（draft_actions.hero_id → heroes、matches.patch_id → patches 都是外键，"
            "版本归属也要读 patches）。")
    if int(earliest) != PATCH_ATTRIBUTION_MIN_START_TIME:
        raise RuntimeError(
            f"patches 最早的版本时间是 {int(earliest)}，与归属边界 "
            f"{PATCH_ATTRIBUTION_MIN_START_TIME}（7.08，2018-02-01）不一致："
            f"先确认常量快照（Task 10 的 R7），再调整本模块的边界常量。")
    return n_patches


def _metadata_row(row: Mapping[str, str], mapping: Mapping[str, str | None],
                  path: pathlib.Path) -> dict:
    match_id = _int_cell(row, mapping, "match_id", path=path, match_id="?")
    raw_start = _cell(row, mapping, "start_time")
    if match_id is None or raw_start is None:
        raise RuntimeError(f"{path} 里有一行的 match_id/start_time 为空：主键与时间不可为空")
    try:
        start_time = parse_timestamp(raw_start)
    except ValueError as exc:
        raise RuntimeError(
            f"{path} 的 match_id={match_id} 的 start_time={raw_start!r} 无法解析") from exc
    radiant_win = _cell(row, mapping, "radiant_win")
    return {
        "match_id": match_id,
        "start_time": start_time,
        "duration_s": _int_cell(row, mapping, "duration_s", path=path, match_id=match_id),
        "league_id": _int_cell(row, mapping, "league_id", path=path, match_id=match_id),
        "league_name": _cell(row, mapping, "league_name"),
        "series_id": _int_cell(row, mapping, "series_id", path=path, match_id=match_id),
        "series_type": _int_cell(row, mapping, "series_type", path=path, match_id=match_id),
        "radiant_team_id": _int_cell(row, mapping, "radiant_team_id", path=path, match_id=match_id),
        "radiant_team_name": _cell(row, mapping, "radiant_team_name"),
        "dire_team_id": _int_cell(row, mapping, "dire_team_id", path=path, match_id=match_id),
        "dire_team_name": _cell(row, mapping, "dire_team_name"),
        "radiant_win": parse_bool(radiant_win) if radiant_win is not None else None,
        "lobby_type": _int_cell(row, mapping, "lobby_type", path=path, match_id=match_id),
        "opendota_patch": _int_cell(row, mapping, "opendota_patch", path=path, match_id=match_id),
    }


def _action_row(row: Mapping[str, str], mapping: Mapping[str, str | None],
                path: pathlib.Path) -> dict:
    match_id = _int_cell(row, mapping, "match_id", path=path, match_id="?")
    for logical in ("order", "team", "hero_id"):
        if _cell(row, mapping, logical) is None:
            raise RuntimeError(f"{path} 的 match_id={match_id} 缺少 {logical}")
    return {
        "match_id": match_id,
        "order": _int_cell(row, mapping, "order", path=path, match_id=match_id),
        "is_pick": parse_bool(_cell(row, mapping, "is_pick")),
        "team": _int_cell(row, mapping, "team", path=path, match_id=match_id),
        "hero_id": _int_cell(row, mapping, "hero_id", path=path, match_id=match_id),
    }


def _league_rows(rows: Sequence[Mapping], names: Mapping[int, tuple[str, str | None]]
                 ) -> tuple[dict[int, tuple[str, str | None]], int]:
    """`{league_id: (name, tier)}` 与"有 id 但没名字"的计数（缺名字的联赛**不**编造名字）。

    名字优先取 `Constants/Constants.Leagues.csv`（实测 `main_metadata.csv` 根本没有这一列），
    回落到 metadata 的 `league_name`（若上游某天补上）。
    """
    leagues: dict[int, tuple[str, str | None]] = {}
    unnamed = 0
    for row in rows:
        league_id = row["league_id"]
        if league_id is None:
            continue
        if int(league_id) in names:
            leagues[int(league_id)] = names[int(league_id)]
            continue
        name = row["league_name"]
        if not name:
            unnamed += 1
            continue
        leagues[int(league_id)] = (name, None)
    return leagues, unnamed


def _team_rows(rows: Sequence[Mapping]) -> tuple[dict[int, str], int]:
    """实测 `main_metadata.csv` **没有队名列**，2016–2022 连队 id 都是空的（2023+ 才有 id）。

    Phase A 的引导子集不含 `*/teams.csv`（55.4 MB，且实测只覆盖约 66% 的 team id），
    故这里只在上游确实给了队名时才建 `teams` 行；否则把 FK 写 NULL 并**报出数量** ——
    编造队名比留空更糟。
    """
    teams: dict[int, str] = {}
    unnamed = 0
    for row in rows:
        for side in ("radiant", "dire"):
            team_id = row[f"{side}_team_id"]
            if team_id is None:
                continue
            name = row[f"{side}_team_name"]
            if not name:
                unnamed += 1
                continue
            teams[int(team_id)] = name
    return teams, unnamed


MATCH_UPSERT = """
INSERT INTO matches (match_id, data_source, patch_id, started_at, duration_s, league_id,
                     series_id, series_type, first_pick_team, radiant_team_id, dire_team_id,
                     radiant_win, lobby_type, draft_state, n_draft_actions, anomaly)
VALUES (%s, 'pro_match', %s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (match_id) DO UPDATE SET
    data_source = EXCLUDED.data_source, patch_id = EXCLUDED.patch_id,
    started_at = EXCLUDED.started_at, duration_s = EXCLUDED.duration_s,
    league_id = EXCLUDED.league_id, series_id = EXCLUDED.series_id,
    series_type = EXCLUDED.series_type, first_pick_team = EXCLUDED.first_pick_team,
    radiant_team_id = EXCLUDED.radiant_team_id, dire_team_id = EXCLUDED.dire_team_id,
    radiant_win = EXCLUDED.radiant_win, lobby_type = EXCLUDED.lobby_type,
    draft_state = EXCLUDED.draft_state, n_draft_actions = EXCLUDED.n_draft_actions,
    anomaly = EXCLUDED.anomaly
"""

ANOMALY_UPSERT = """
INSERT INTO draft_anomalies (match_id, n_actions, kinds, detail)
VALUES (%s, %s, %s, %s)
ON CONFLICT (match_id) DO UPDATE SET
    n_actions = EXCLUDED.n_actions, kinds = EXCLUDED.kinds,
    detail = EXCLUDED.detail, detected_at = now()
"""


def _write_match(conn, row: Mapping, actions: Sequence[Mapping], problems: Mapping[str, int],
                 teams: Mapping[int, str], leagues: Mapping[int, tuple[str, str | None]],
                 patch_ids: Mapping[str, int], patch_numbers: Mapping[int, int | None],
                 stats: dict) -> None:
    match_id = row["match_id"]
    first_pick_team = None
    if actions:
        try:
            first_pick_team = first_pick_team_from_actions(actions)   # 规格 §8①
        except ValueError:
            first_pick_team = None                # 缺 ord=0：记 missing_ord_zero，不中断整场
    anomaly = detect_anomaly(actions, first_pick_team,
                             ord_out_of_range=problems.get("ord_out_of_range", 0),
                             duplicate_ord=problems.get("duplicate_ord", 0))
    patch_id = patch_id_for(conn, row["start_time"], patch_ids=patch_ids)
    draft_state = "complete" if actions else "pending"

    conn.execute(MATCH_UPSERT, (
        match_id, patch_id, row["start_time"], row["duration_s"],
        row["league_id"] if row["league_id"] in leagues else None,
        row["series_id"], row["series_type"], first_pick_team,
        row["radiant_team_id"] if row["radiant_team_id"] in teams else None,
        row["dire_team_id"] if row["dire_team_id"] in teams else None,
        row["radiant_win"], row["lobby_type"], draft_state,
        len(actions) if actions else None, anomaly is not None,
    ))

    # 规格 §5.2：先按 match_id 删再整体插入，避免上一次运行残留旧手
    conn.execute("DELETE FROM draft_actions WHERE match_id = %s", (match_id,))
    if actions:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO draft_actions (match_id, ord, is_pick, team, hero_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(match_id, a["ord"], a["is_pick"], a["team"], a["hero_id"]) for a in actions])

    if anomaly is not None:
        conn.execute(ANOMALY_UPSERT, (match_id, anomaly["n_actions"], anomaly["kinds"],
                                      json.dumps(anomaly["detail"], ensure_ascii=False)))
        for kind in anomaly["kinds"]:
            stats["anomaly_kinds"][kind] += 1
    else:
        conn.execute("DELETE FROM draft_anomalies WHERE match_id = %s", (match_id,))

    stats["matches"] += 1
    stats["draft_actions"] += len(actions)
    stats["anomalies"] += anomaly is not None
    stats["pending"] += not actions
    if anomaly is None and actions:
        family = matching_order(actions, first_pick_team)
        stats["order_families"][family] += 1
    if row["start_time"] < PATCH_ATTRIBUTION_MIN_START_TIME:
        stats["pre_2018"] += 1
        stats["pre_2018_null_patch"] += patch_id is None
        if row["opendota_patch"] is not None:
            stats["patch_column_pre_2018"] += 1
    else:
        stats["post_2018_null_patch"] += patch_id is None
        if row["opendota_patch"] is None:
            stats["patch_column_missing"] += 1
        elif patch_id is not None:
            # 交叉校验：CSV 自带的 OpenDota 粗粒度 patch id 与本模块的归属是否一致
            if patch_numbers.get(patch_id) == row["opendota_patch"]:
                stats["patch_column_agree"] += 1
            else:
                stats["patch_column_mismatch"] += 1


def load_bootstrap(conn, *, cache_dir=DEFAULT_CACHE_DIR, commit: bool = True,
                   batch_matches: int = 2000, log=print) -> dict:
    """把 `<cache_dir>/<folder>/*.csv` 灌进数据库；返回本次运行的统计（含 pre-2018 占比）。

    `commit=False` 给测试事务隔离用（`tests/conftest.py::db` 在测试结束时 `rollback()`）；
    `commit=True` 时每 `batch_matches` 场提交一次 —— 引导入库很慢（506 MB / 19 个目录），
    中断后必须能续跑，而全部写入都是幂等的，故续跑安全。

    幂等：`leagues`/`teams`/`matches` 主键 upsert，`draft_actions` 先删后插，
    `draft_anomalies` 不再异常的场次删行（规格 §5.2）。
    """
    cache_dir = pathlib.Path(cache_dir)
    _require_constants(conn)

    folders = sorted(p for p in cache_dir.iterdir() if (p / METADATA_FILENAME).is_file()) \
        if cache_dir.is_dir() else []
    if not folders:
        raise FileNotFoundError(
            f"{cache_dir} 下没有任何 <folder>/{METADATA_FILENAME}（也没有 {ACTIONS_FILENAME}）："
            f"先跑 `python -m ingest.kaggle_subset`（需要 KAGGLE_USERNAME/KAGGLE_KEY 或 "
            f"~/.kaggle/kaggle.json），或把 CSV 放到该目录下。")

    stats: dict = {"folders": [p.name for p in folders], "matches": 0, "draft_actions": 0,
                   "leagues": 0, "teams": 0, "anomalies": 0, "pending": 0, "actions_dropped": 0,
                   "orphan_action_matches": 0, "unnamed_leagues": 0, "unresolved_team_refs": 0,
                   "total": 0, "pre_2018": 0, "pre_2018_null_patch": 0, "post_2018_null_patch": 0,
                   "pre_2018_share": 0.0, "patch_column_agree": 0, "patch_column_mismatch": 0,
                   "patch_column_missing": 0, "patch_column_pre_2018": 0,
                   "order_families": defaultdict(int), "anomaly_kinds": defaultdict(int)}
    preflight_columns(folders, cache_dir)        # 列名契约先整体验完，再写第一行
    patch_ids = patch_ids_by_version(conn)
    patch_numbers = opendota_patch_by_id(conn)
    league_names = read_leagues(cache_dir)
    since_commit = 0

    for folder in folders:
        meta_path = folder / METADATA_FILENAME
        fieldnames, raw_rows = read_csv(meta_path)
        mapping = resolve_columns(fieldnames, METADATA_COLUMNS, REQUIRED_METADATA, meta_path)
        rows = [_metadata_row(r, mapping, meta_path) for r in raw_rows]

        actions_by_match: dict[int, tuple[list[dict], dict[str, int]]] = {}
        actions_path = folder / ACTIONS_FILENAME
        if actions_path.is_file():
            a_fields, a_raw = read_csv(actions_path)
            a_mapping = resolve_columns(a_fields, ACTIONS_COLUMNS, tuple(ACTIONS_COLUMNS),
                                        actions_path)
            parsed = [_action_row(r, a_mapping, actions_path) for r in a_raw]
            ord_origin = detect_ord_origin(a["order"] for a in parsed)
            grouped: dict[int, list[dict]] = defaultdict(list)
            for action in parsed:
                grouped[action["match_id"]].append(action)
            for match_id, group in grouped.items():
                actions_by_match[match_id] = normalize_actions(group, ord_origin=ord_origin)

        leagues, unnamed_leagues = _league_rows(rows, league_names)
        teams, unresolved_teams = _team_rows(rows)
        stats["unnamed_leagues"] += unnamed_leagues
        stats["unresolved_team_refs"] += unresolved_teams

        if leagues:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO leagues (league_id, name, tier) VALUES (%s, %s, %s)
                                   ON CONFLICT (league_id) DO UPDATE
                                   SET name = EXCLUDED.name, tier = EXCLUDED.tier""",
                                [(lid, name, tier) for lid, (name, tier) in sorted(leagues.items())])
            stats["leagues"] += len(leagues)
        if teams:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO teams (team_id, name) VALUES (%s, %s)
                                   ON CONFLICT (team_id) DO UPDATE SET name = EXCLUDED.name""",
                                sorted(teams.items()))
            stats["teams"] += len(teams)

        known_matches = {row["match_id"] for row in rows}
        stats["orphan_action_matches"] += len(set(actions_by_match) - known_matches)
        for row in rows:
            actions, problems = actions_by_match.get(row["match_id"], ([], {}))
            stats["actions_dropped"] += problems.get("ord_out_of_range", 0)
            _write_match(conn, row, actions, problems, teams, leagues, patch_ids, patch_numbers,
                         stats)
            since_commit += 1
            if commit and since_commit >= batch_matches:
                conn.commit()
                since_commit = 0

        log(f"  {folder.name}: 累计 matches={stats['matches']} "
            f"draft_actions={stats['draft_actions']}")

    if commit:
        conn.commit()

    stats["total"] = stats["matches"]
    stats["pre_2018_share"] = stats["pre_2018"] / stats["total"] if stats["total"] else 0.0
    stats["order_families"] = dict(stats["order_families"])
    stats["anomaly_kinds"] = dict(stats["anomaly_kinds"])
    log(f"引导入库完成：folders={len(folders)} matches={stats['matches']} "
        f"draft_actions={stats['draft_actions']} leagues={stats['leagues']} teams={stats['teams']} "
        f"anomalies={stats['anomalies']} pending={stats['pending']} "
        f"丢弃的越界手={stats['actions_dropped']} 无 metadata 的 picks_bans 场次="
        f"{stats['orphan_action_matches']}")
    pre, total = stats["pre_2018"], stats["total"]
    log(f"pre-2018 场次 {pre}/{total}（{stats['pre_2018_share']:.1%}），"
        f"其中 patch_id IS NULL 的 {stats['pre_2018_null_patch']} 场；"
        f"start_time >= {PATCH_ATTRIBUTION_MIN_START_TIME} 的场次中 patch_id IS NULL 的 "
        f"{stats['post_2018_null_patch']} 场（规格 §3.2：必须为 0）")
    log(f"顺序族分布（仅统计 anomaly=false 的场次）：{stats['order_families']}")
    log(f"patch 列交叉校验（>= 2018-02-01）：吻合 {stats['patch_column_agree']} / "
        f"不吻合 {stats['patch_column_mismatch']} / CSV 缺该列值 {stats['patch_column_missing']}；"
        f"pre-2018 有该列值的 {stats['patch_column_pre_2018']} 场（patches 表无对应行，不参与比对）")
    return stats
```

- [x] **Step 5: `pyproject.toml` + `.gitignore`**

```toml
[project.optional-dependencies]
dev = ["pytest>=8.3", "openapi-spec-validator>=0.7"]
# 引导数据集（Task 12）用；只有真要下载时才需要。安装：python -m pip install -e ".[dev,ingest]"
ingest = ["python-dotenv>=1.0", "kaggle>=1.6"]
```

```gitignore
# Kaggle 引导数据集的 506.7 MB 子集：由 `python -m ingest.kaggle_subset` 落到此处，绝不入库
tests/fixtures/kaggle/
```

- [x] **Step 6: 运行**

```
python -m pip install -e ".[dev,ingest]"   # 本机成功（kaggle 2.2.4 + python-dotenv）
python -m ingest.kaggle_subset             # 实测：49 个文件 / 506.7 MB / 19 个年度目录
pytest tests/ingest -q                     # 实测：34 passed（含真实入库 211,051 场，约 128 s）
pytest -q                                  # 实测：199 passed
把 tests/fixtures/kaggle 挪走后：
  pytest tests/ingest -q -rs               # 实测：25 passed, 9 skipped（0.6 s，零失败）
  pytest -q                                # 实测：190 passed, 9 skipped
```

- [x] **Step 7: Commit**

```bash
git add ingest/ pyproject.toml .gitignore
git commit -m "feat(ingest): Kaggle 引导数据集入库（幂等 + 2018 归属规则 + 异常率）"
git add tests/ingest/
git commit -m "test(ingest): 无凭证/无数据时 skip + 合成数据全覆盖 + 引导库隔离"
```

#### Task 12 实测记录（2026-09-18；凭证缺失下完成的一切与仍未验证的部分）

**凭证状态（必须先说清）**：本机 `KAGGLE_USERNAME`/`KAGGLE_KEY` **未设置**、`~/.kaggle/kaggle.json`
**不存在**（`credentials_present()` 返回 False）。但**数据集仍然下到了**：这个数据集是 CC0 公共
数据集，Kaggle 的 `dataset_list_files` / `dataset_download_file` 两个接口对**无有效凭证的请求
也返回数据**（用 `KAGGLE_USERNAME=bogus KAGGLE_KEY=bogus` 实测：清单 8 页 / 1546 个文件，
下载得到 49 个白名单文件 / 506.7 MB，逐字节可用）。CLI 仍按规格 §10.2 先查凭证再下载，故
"无凭证"路径给出的是可操作提示（下面有实测），而**绕过 gate 后下载是通的**这一点如实记在这里 ——
计划/规格若认为"无凭证不能下载"，这条实测事实需要更新那一句。

**下载侧实测**：
- 清单：8 页 / **1546** 个文件（`dataset_list_files` 默认 `page_size=20`，实测服务端上限 200；
  不翻页只能看到第一页 —— `list_all_files` 因此显式给 `page_size` 并翻页）。
- 白名单命中：`main_metadata.csv` 19 个目录、`picks_bans.csv` 19 个、`draft_timings.csv` **10** 个、
  `Constants/Constants.Leagues.csv` 1 个 = **49 个文件 / 506.7 MB**（与规格 §10.2 的
  19/19/10 与 506.2 MB 吻合，多出的 0.43 MB 就是联赛名文件）。
- 整包规模核对：`players.csv` = 19 个分片 / 41.04 GB；数据集合计 48.52 GB（与规格 §10.2 逐字吻合）。
- 年度目录：2016–2025 + `202601`–`202609`（共 19 个）。
- 无凭证 CLI 实测：`python -m ingest.kaggle_subset` → 退出码 **2**、stderr 给出补齐方式与下载命令、
  **无 traceback**、缓存目录未被创建（由 `test_cli_without_credentials_fails_gracefully` 守护）。
- 依赖安装实测：`python -m pip install -e ".[dev,ingest]"` **成功**（kaggle 2.2.4）。

**计划三处假设被实测推翻（全部已改正并写进测试）**：

| 计划原文 | 实测（2016/2018/2025 三个年度的列集 + 全量入库） |
|---|---|
| `main_metadata.csv` 有 `start_time` | **没有**。只有 `start_date_time`（`'2016-01-02 15:12:19'`，朴素字符串；按 **UTC** 解释） |
| `main_metadata.csv` 有 `league_name` | **没有**（也没有队名）。联赛名只在 `Constants/Constants.Leagues.csv`（`leagueid,leaguename,tier`，10099 行，覆盖全部 metadata 的 leagueid） |
| `picks_bans.csv` 的 `order` 是整数、起点待确认 | 是**浮点字符串**（`'0.0'`/`'78.0'`）；`int()` 直接 `ValueError`。起点实测 **0 起**（2016/2017/2018/2025 四个年度抽样全部 min=0）。2016/2018 还带一列 OpenDota 自己的 `ord`，与 `order` 逐行相同 |

**最重要的数据事实：Valve 换过 CM 的 ban 顺序，规格 §6.0 的模板只覆盖其中一族。**
对 205,005 场逐场把 `(is_pick, team)` 归一化成相对先手方的串后统计，语料里有
**20 / 22 / 24 三种手数、十种支持度 >= 1% 的合法顺序**（详见 `DRAFT_ORDERS` 的逐族注释）：

| 族 | 手数 | 实测场次 | 占比 | 出现的年度 |
|---|---|---|---|---|
| `cm20_a` / `cm20_b` | 20 | 12340 / 2167 | 84.85% / 14.90% | 2016–2017 |
| `cm22_a` / `cm22_b` / `cm22_c` | 22 | 26126 / 8035 / 4767 | 67.04% / 20.62% / 12.23% | 2017–2020 |
| `cm24_a` | 24 | 68775 | 46.00% | 2023–2025 |
| `cm24_b` | 24 | 46347 | 31.00% | 2021–2023 |
| `cm24_c` / `cm24_d` | 24 | 14045 / 5220 | 9.39% / 3.49% | 2020–2021 |
| `spec_6_0_24`（规格 §6.0 = `shared/draft_template.TEMPLATE`） | 24 | 15135 | 10.12% | **2025 下半年 + 全部 2026 目录** |

`spec_6_0_24` 是 OpenDota 实时 API 上 match 8996973546 的顺序（本轮实拉核对过），即**当下**的 CM
顺序；2023–2025 上半年的比赛用的是 `cm24_a`，与它只差两段 ban。**首次实现只登记了四种顺序，
真实入库实测异常率 58.33%**（把九成正常比赛判成异常）；按 >= 1% 支持度登记十族后降到 **0.97%**。
下游影响（不在 Task 12 范围内，但必须记下来）：`shared/draft_template.resolve()` 只对
`spec_6_0_24` 正确，2020–2025 的历史比赛（约 19 万场）按它的 `ord→(type, team)` 映射是错的；
序列模型/Policy 必须按族过滤（族可由 `matches.n_draft_actions` + 该场 `draft_actions` 复算）。

**M1 相关实测数（真实数据，19 个目录全量入库）**：
- `matches` **211,051**；`draft_actions` **4,772,342**；`leagues` **1,557**（DB 直查的 distinct 行数；
  loader 日志里逐目录累加的 1,808 是**跨目录重复计数**，不是表里的行数 —— 以 DB 为准）；
  `teams` **0**（CSV 没有队名 → 不编造，FK 写 NULL；未解析的队引用 137,371 次，
  已计入 `stats["unresolved_team_refs"]`）。
- `anomaly=true` **2,048 / 211,051 = 0.97%**（规格 §15 要求 < 2%）；`draft_anomalies` 行数
  与 `matches.anomaly` 计数**逐场一致**；异常原因分布：各奇零手数（10–19 手共 1,022 场、
  21 手 262 场、23 手 640 场）+ 20 手尾部 36 场 + 22 手尾部 45 场 + 24 手 0 场。
- `draft_state='pending'` **6,046**（有 metadata、无 picks_bans；规格 §5.2 的状态机语义，
  不算异常）。
- **pre-2018 场次 17,552 / 211,051 = 8.3%**，其中 `patch_id IS NULL` 的 **17,552**（全部）；
  `started_at >= 1517472000` 的场次里 `patch_id IS NULL` 的 **0** 场。
- `patch` 列交叉校验（CSV 自带 OpenDota 粗粒度 id vs 本模块归属出的 `patches.opendota_patch`）：
  **吻合 191,149 / 不吻合 2,350**（可比对 193,499 场，吻合率 98.79%）。不吻合集中在补丁发布
  边界附近（Valve 的发布时间 vs patchdates 的公告时间本就相差 0–2 天，见 Task 10 的交叉校验），
  这里**只报数不设阈值**：它是一个独立证据，不是本模块的判据。
- `hero_id` 外键：全量 4,772,342 行 **0 个未知 hero_id**；`ord` 越界 0 行、重复 `(match_id, ord)` 0 行。

**测试计数（两套状态实测）**：

| 状态 | `pytest tests/ingest -q` | `pytest -q`（默认顺序） | `pytest tests/ingest tests/constants tests/contracts tests/db tests/shared -q` |
|---|---|---|---|
| 缓存存在（本机） | **34 passed**（128 s，含真实入库） | **199 passed** | **199 passed** |
| 缓存不存在（模拟无凭证机器） | **25 passed, 9 skipped**（0.6 s） | **190 passed, 9 skipped** | 同上 |

skip 的 9 条 = `sample_csv` 2 条 + `db_after_bootstrap` 7 条，跳过消息写明缓存路径、
缺哪个凭证、以及补齐后要跑的命令。**零失败**。

**变异守护（4 条，apply → run → restore → sha256 复原 `fab0262842d4` / `0cb7b7103810`）**：
(a) 摘掉 20 手族 → `2 failed, 1 error`（顺序族分布 + 异常判定测试）；
(b) 去掉 2018 归属边界（等价于允许把 pre-2018 钳到最早版本）→ `2 failed`（合成侧的
反钳位测试 + patch 列交叉校验）；
(c) 摘掉 `draft_actions` 的先删后插 → `1 failed`（幂等/残留手）；
(d) 会话 fixture 改用共享 `dota_test` → `1 failed`（数据库隔离守护）。

**仍未验证的部分（诚实清单）**：
1. **下载端点的长期行为**：本轮实测的是 2026-09-18 这一天、这一台机器、这个数据集版本。
   "无凭证也能下载"是实测事实，但**不是**契约；上游随时可能要求有效凭证。
2. **时区假设**：`start_date_time` 按 UTC 解释。间接证据是 patch 列交叉校验的 98.79% 吻合率
   （时区若错 8 小时，补丁边界附近的场次会系统性错配）；未做直接的时区核对。
3. **2,350 场 patch 列不吻合**已做距离诊断（2025 年度代表样本）：**251/251 = 100% 落在距最近
   补丁发布 <= 2 天之内** —— 与 Task 10 记录的两来源时间差（Valve 发布时间 vs patchdates 公告
   时间，0–2 天）完全一致，即不吻合来自**补丁边界的时间差**而非归属规则出错。仍未做的是逐场
   人工核对，也没给吻合率设断言阈值（只断言"可比对场次 > 1000 以免空过"）。
4. `draft_timings.csv`（240.7 MB）**下了但没入库** —— 规格 §10.2 说它只作补充（思考耗时），
   本任务的范围是 BP 序列，故未写 loader。
5. `teams` 仍为空：数据集里唯一带队名的是 `*/teams.csv`（55.4 MB，19 个文件），
   实测只覆盖约 66% 的 metadata team id，故**没有**纳入白名单（会把子集从 506.7 MB 推到 562 MB
   且仍有三分之一的队 id 解析不出）。补齐路径留给定计划 3（OpenDota `/teams` 或该文件）。
6. Task 13 的 M1 异常率断言需按本记录修正：`anom/total < 0.02` 在"全部年份"口径下**成立**
   （0.97%），但它的语义是"不符合任何一种实测合法顺序"；若要表达规格 §5.3 的
   "可入模的现代模板比赛"，应改成 `WHERE n_draft_actions = 24` 或按 `order_family` 过滤。

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

> **Task 12 实测后对本任务的两条修正（2026-09-18，必须照改，否则这条验收要么找不到 fixture、
> 要么用了错的口径）**：
>
> 1. **fixture 可见性**：`db_after_bootstrap` 定义在 `tests/ingest/conftest.py` 里（Task 12 的
>    Files 就是这么定的），而本任务的测试文件在 `tests/` 下 —— **跨目录的 conftest 不生效**，
>    直接跑会以 `fixture 'db_after_bootstrap' not found` 报错。修法：在
>    `tests/test_m1_acceptance.py` 顶部加一行
>    `pytest_plugins = ["tests.ingest.conftest"]`（本仓 `tests/` 与 `tests/ingest/` 都有
>    `__init__.py`，故模块路径就是这个）。
> 2. **异常率的口径**：`anom / total < 0.02` 在"全部年份"口径下**成立**（实测 0.97% =
>    2,048/211,051），但 `anomaly` 的语义是"不符合任何一种**实测的合法 CM 顺序**"，
>    不等于规格 §5.3 的"可入模的现代模板比赛"。若要表达后者，把第 3 条改成
>    `WHERE n_draft_actions = 24`（或按 `draft_actions` 复算的顺序族过滤）—— 详见
>    Task 12 实测记录里的顺序族表：20/22 手是 2016–2020 的历史顺序，24 手里也只有
>    `spec_6_0_24` 一族与 `shared/draft_template.TEMPLATE` 一致。
>
> 另：`test_m1_acceptance` 会用到 `db_after_bootstrap`，即**整个 211,051 场的真实入库**
> （约 2 分钟/次），这是会话级 fixture 的设计意图（每 session 只跑一次）。

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

**本计划显式延迟的五项**（不是遗漏）：
1. `contracts/openapi.yaml` 的 `paths` 与请求体 schema —— 归 Plan 2+，需先定服务端框架
2. 规格 §6.5「Phase A 必须不存在」清单的机器校验 —— 归 Plan 3，需对真实响应做全字段扫描
3. `check_resolve` 的接口级调用 —— 归 Plan 2+，fixtures 是纯响应体、不含请求上下文
4. 规格 §5.1 要求 3 的**跨快照不变性检查**（§5.1:220：「对快照 N 与 N+1 断言所有**已存在**的
   hero_id 其 dense_index 不变，变化必须报错并强制模型版本号同步提升」）——归 **Plan 2 / 沿
   `constants_snapshot` 迁移路径**：它需要两个快照并存，本计划只落一个快照（Task 11 的
   `snapshot_version = 1`）。Task 11 的 `test_token_index_satisfies_the_derivation_rule` 只校验
   快照**内部**一致性（`dense_index == row_number() OVER (ORDER BY hero_id) - 1`），
   按 §5.1:214–216 的论证，这一条**抓不到**"新英雄插进空位导致其后整体位移"。
   （第二轮已在 Task 11 补上**同一快照内**的 pre-write 守护：加载前把库中现有映射与本次派生结果
   逐 hero_id 比对，不一致即报错并要求显式提升 `SNAPSHOT_VERSION` —— 见 Task 11 实测记录 (c)。
   跨快照的那条仍需两个快照并存，故依然延迟。）

5. 规格 §7:1053 的「`archetype_role_map` 存于 `app_config_kv`，**可调而不改代码**」——归
   **Plan 3（画像引擎，`analysis/`）里计算 §7 维度 6 的那个任务**。当前 `app_config_kv` 里存的
   **不是映射表**，而是一个**指针**：`{六个原型名: "python:constants.archetypes.RULES"}`
   （"算 §7 维度 6 的代码在这里"，见 Task 11 实测记录 (f)）。规则本身带**取反**
   （`"Pusher" not in r`）与**嵌套 OR**（`teamfight`），扁平 KV 表表达不了；要真正做到
   "可调而不改代码"，需要一套**规则 DSL + 求值器** —— 例如把每条规则写成可 JSON 化的谓词树
   （关键字 `all` / `any` / `none`，如 `splitpush = {"all": ["Carry", "Escape"], "none": ["Pusher"]}`），
   由求值器解释，`RULES` 退化成它的默认值与测试基准。

**Kaggle 引导数据集（Task 12 收尾，2026-09-18）**：CSV 的列名与 `order` 起始值**已对真实数据
核对完毕**（结论与计划原文的假设不同三处：`start_date_time` 而非 `start_time`、没有 `league_name`、
`order` 是浮点字符串），并已在两个年度目录上实测 `min(order) == 0`。本机凭证缺失，但该数据集是
CC0 公共数据集、其清单/下载接口对无有效凭证的请求也返回数据，故 506.7 MB 子集**已实际下载并全量
入库**（211,051 场 / 4,772,342 手 / 异常率 0.97%），数据侧测试不再 skip。**仍未验证**的部分逐条列在
Task 12 实测记录里（下载端点的长期行为、`start_date_time` 的时区假设、2,350 场 patch 列不吻合的
逐场归因、`draft_timings.csv` 未入库、`teams` 仍为空）。缺缓存时相关测试仍会 `skip` 而非变红
（实测 190 passed / 9 skipped）。
