# 契约冻结与数据地基 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结 `contracts/openapi.yaml` 与 13 个边界 fixtures（解锁三条并行 worktree），并建成可查询的数据地基（常量层、含字母子版本的版本表、引导数据集入库）。

**Architecture:** 单一 Postgres 实例承载全部关系数据；契约以 OpenAPI 3.1 为唯一权威源，fixtures 与 TS 类型均从它生成并由 CI 校验。常量层拆为「规范实体（`heroes`/`items`）+ 版本化 token 索引（`hero_token_index`）」，token 索引按快照冻结以防常量刷新时静默重映射。数据来源分三类：Valve 官方补丁 feed（权威版本清单）、dotaconstants（英雄/道具）、Kaggle CC0 数据集（历史 BP 引导数据，仅下载 506 MB 子集）。

**Tech Stack:** Python 3.12 · PostgreSQL 16 · Docker Compose · pytest · jsonschema · httpx · openapi-spec-validator

**Spec:** `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md`

---

## 前置说明：本计划的边界

**本计划只做 M0 + M1。** 采集器（M2）、画像引擎（M3–M5）、可视化（M6）、序列模型（M7–M9）各由独立计划覆盖。

**本计划交付后即可并行的三条 worktree：**
- 线 A（画像引擎）：依赖 `db/` + `contracts/`
- 线 C（序列模型）：依赖 `contracts/` + 引导数据集
- 前端：依赖 `contracts/fixtures/`，**不需要数据库**

---

## Chunk 1: 契约冻结（M0）

契约是本项目唯一「定了就不能随便改」的产物。本 chunk 先冻结它，因为后续三个 worktree 全部依赖它。

### Task 1: 仓库骨架与 Compose

**Files:**
- Create: `compose.yaml`
- Create: `.env.example`
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `db/.gitkeep`, `contracts/.gitkeep`

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
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-dota}"]
      interval: 5s
      retries: 10
volumes:
  pgdata:
```

**关键约束（规格 §13）**：数据卷用**命名卷 `pgdata`**，不用宿主机 bind mount——Windows 上 bind mount 大量小文件极慢，且 SQLite/网络文件系统会锁坏。Postgres 用命名卷即可绕开。

- [ ] **Step 2: 写 `.env.example` 与 `pyproject.toml`**

`.env.example`（真实 `.env` 已被 `.gitignore` 排除）：
```
POSTGRES_USER=dota
POSTGRES_PASSWORD=dota
POSTGRES_DB=dota
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://dota:dota@localhost:5432/dota
KAGGLE_USERNAME=
KAGGLE_KEY=
LIQUIPEDIA_USER_AGENT=Dota2DraftAnalysis/0.1 (https://github.com/yourname/mcndotaga; you@example.com)
```

`pyproject.toml`：
```toml
[project]
name = "mcndotaga"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "psycopg[binary]>=3.2",
  "httpx>=0.27",
  "sqlalchemy>=2.0",
  "jsonschema>=4.23",
  "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-postgresql>=6.1", "openapi-spec-validator>=0.7",
       "datamodel-code-generator>=0.26"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: 写 `Makefile`（统一入口，跨平台）**

```makefile
.PHONY: up down db-reset migrate test contract

up:      ; docker compose up -d db
down:    ; docker compose down
db-reset: ; docker compose down -v && docker compose up -d db && sleep 3 && $(MAKE) migrate
migrate: ; python -m db.migrate
test:    ; pytest -q
contract: ; python -m contracts.tools.validate_fixtures
```

- [ ] **Step 4: 验证 compose 起得来**

Run: `make up && docker compose ps`
Expected: `db` 服务状态为 `running (healthy)`，端口 5432 可连。

- [ ] **Step 5: Commit**

```bash
git add compose.yaml .env.example pyproject.toml Makefile
git commit -m "chore: 仓库骨架与 Docker Compose（Postgres 用命名卷，跨平台）"
```

---

### Task 2: §5.1 的 DDL 作为迁移 001

规格 §16.1 已证明这段 DDL 在 PostgreSQL 16 上可执行。本任务把它变成可重复执行的迁移，并把它验证过的 15 条约束探针变成自动化测试。

**Files:**
- Create: `db/migrations/001_schema.sql`
- Create: `db/migrate.py`
- Create: `db/__init__.py`
- Test: `tests/db/test_constraints.py`

- [ ] **Step 1: 把规格 §5.1 的 SQL 块逐字复制为 `db/migrations/001_schema.sql`**

从规格文件 `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md` 的 ` ```sql ` 块中提取，**一字不改**（包括注释）。可用脚本提取以确保无转录错误：

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
"""Apply SQL migrations in lexical order. Idempotent per-file via schema_migrations."""
from __future__ import annotations
import os, pathlib, sys
import psycopg

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"

def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "postgresql://dota:dota@localhost:5432/dota")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (path.name,))
        conn.commit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 写失败的约束测试**

`tests/db/test_constraints.py` —— 覆盖规格 §16.1 表中已实测的每一条：

```python
import pytest

def test_full_ten_player_match_inserts(db):
    """Radiant slot 0-4 + Dire slot 5-9 必须全部入库。"""
    db.execute("INSERT INTO constants_snapshot VALUES (1,127,501)")
    db.execute("INSERT INTO heroes(hero_id,name,localized_name) VALUES (80,'npc_dota_hero_lone_druid','Lone Druid')")
    db.execute("INSERT INTO hero_token_index VALUES (1,80,73)")
    db.execute("INSERT INTO leagues(league_id,name) VALUES (1,'L')")
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,first_pick_team,draft_state)
                  VALUES (1,'pro_match',now(),0,'complete')""")
    rows = [(1,s,None,0 if s<5 else 1,80) for s in range(10)]
    with db.cursor() as cur:
        cur.executemany("INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id) VALUES (%s,%s,%s,%s,%s)", rows)
    assert db.execute("SELECT count(*) FROM match_players WHERE match_id=1").fetchone()[0] == 10

def test_raw_mod_128_normalization_is_rejected(db, seeded_match):
    """规格 §16.2：raw%128 会把 Dire 的 128..132 塌缩成 0..4，必须被 slot_team_agree 拒绝。"""
    with pytest.raises(Exception, match="slot_team_agree"):
        db.execute("INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id) VALUES (1,0,NULL,1,80)")

def test_metric_weights_rejects_undefined_metric(db, seeded_match):
    with pytest.raises(Exception, match="metric"):
        db.execute("INSERT INTO metric_weights VALUES ('laning','pro_match',1.0,NULL)")

def test_metric_weights_accepts_all_six_spec_keys(db, seeded_match):
    for m in ["patch_strength","hero_pool","system_pref","bp_tendency","tempo","map_vision"]:
        db.execute("INSERT INTO metric_weights VALUES (%s,'pro_match',1.0,NULL)", (m,))

def test_draft_actions_rejects_unknown_hero(db, seeded_match):
    with pytest.raises(Exception):
        db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,999)")

def test_draft_actions_rejects_ord_out_of_range(db, seeded_match):
    with pytest.raises(Exception, match="ord"):
        db.execute("INSERT INTO draft_actions VALUES (1,24,false,0,80)")

def test_rosters_allows_null_joined_at(db, seeded_match):
    db.execute("INSERT INTO players(account_id,name) VALUES (111,'p')")
    db.execute("INSERT INTO teams(team_id,name) VALUES (10,'A')")
    db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,NULL,'liquipedia')")
    with pytest.raises(Exception):
        db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,'2020-01-01','liquipedia')")

def test_hero_token_index_is_versioned(db, seeded_match):
    with pytest.raises(Exception):
        db.execute("INSERT INTO hero_token_index VALUES (1,80,121)")   # dense_index 重复
    db.execute("INSERT INTO constants_snapshot VALUES (2,128,501)")
    db.execute("INSERT INTO hero_token_index VALUES (2,80,73)")        # 新快照可复用索引
```

`tests/conftest.py` 提供 `db`（连到测试库，每个测试回滚）与 `seeded_match` fixtures。

- [ ] **Step 4: 运行测试确认失败**

Run: `make up && make db-reset && pytest tests/db/test_constraints.py -q`
Expected: 全部 FAIL（`db/migrate.py` 尚未跑，或 `conftest.py` 未写）

- [ ] **Step 5: 写 `tests/conftest.py`**

```python
import os, pathlib, pytest, psycopg

@pytest.fixture(scope="session")
def dsn():
    return os.environ.get("TEST_DATABASE_URL",
                          "postgresql://dota:dota@localhost:5432/dota_test")

@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn, autocommit=False) as conn:
        yield conn
        conn.rollback()

@pytest.fixture
def seeded_match(db):
    db.execute("INSERT INTO constants_snapshot VALUES (1,127,501)")
    db.execute("INSERT INTO heroes(hero_id,name,localized_name) VALUES (80,'npc_dota_hero_lone_druid','Lone Druid')")
    db.execute("INSERT INTO hero_token_index VALUES (1,80,73)")
    db.execute("INSERT INTO leagues(league_id,name) VALUES (1,'L')")
    db.execute("INSERT INTO matches(match_id,data_source,started_at,first_pick_team,draft_state) VALUES (1,'pro_match',now(),0,'complete')")
    return 1
```

- [ ] **Step 6: 运行测试确认通过**

Run: `make db-reset && pytest tests/db/test_constraints.py -q`
Expected: **8 passed**

- [ ] **Step 7: Commit**

```bash
git add db/ tests/
git commit -m "feat(db): §5.1 DDL 作为迁移 001 + 8 条约束测试（含 raw%128 回归保护）"
```

---

### Task 3: 对手侧隔离视图与负向测试

规格 §4.1 要求「`scrim` 数据永不进入对手画像」，并规定强制机制是只读视图 + `resolve_sources()` 白名单 + 一条**可观测量**的负向测试。本任务实现它。

**Files:**
- Create: `db/views/opponent_profile.sql`
- Create: `db/sources.py`
- Test: `tests/db/test_isolation.py`

- [ ] **Step 1: 写失败的隔离测试**

```python
import pytest
from db.sources import resolve_sources, SourceNotAllowed

def test_opponent_view_excludes_scrim_and_pub(db, seeded_match):
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                  VALUES (2,'scrim',now(),'complete'), (3,'pub_match',now(),'complete')""")
    rows = db.execute("SELECT match_id FROM opponent_profile_matches ORDER BY match_id").fetchall()
    assert [r[0] for r in rows] == [1], "对手画像视图只能看到 pro_match"

def test_resolve_sources_rejects_scrim_for_opponent_paths():
    with pytest.raises(SourceNotAllowed):
        resolve_sources(["pro_match","scrim"], allow_scrim=False)

def test_resolve_sources_accepts_scrim_when_explicitly_allowed():
    assert resolve_sources(["pro_match","scrim"], allow_scrim=True) == ["pro_match","scrim"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/db/test_isolation.py -q`
Expected: FAIL — `No module named 'db.sources'`

- [ ] **Step 3: 写 `db/views/opponent_profile.sql`**

```sql
-- 规格 §4.1：对手侧数据路径的唯一合法来源。
-- 刻意不含 scrim；pub_match 由调用方通过 resolve_sources() 显式开启。
CREATE OR REPLACE VIEW opponent_profile_matches AS
SELECT * FROM matches WHERE data_source = 'pro_match';

-- 带 pub_match 的放宽版，供显式开启时使用；仍不含 scrim。
CREATE OR REPLACE VIEW opponent_profile_matches_with_pub AS
SELECT * FROM matches WHERE data_source IN ('pro_match','pub_match');
```

在 `db/migrate.py` 中于迁移后执行 `views/*.sql`（`CREATE OR REPLACE` 故可重复跑）。

- [ ] **Step 4: 写 `db/sources.py`**

```python
"""规格 §4.1 的隔离规则实现。

对手侧模块（playbook / value 的对手侧 / profile / policy / advise）
调用时必须 allow_scrim=False（默认）。
"""
from __future__ import annotations

ALLOWED = {"pro_match", "pub_match", "scrim"}
OPPONENT_SAFE = {"pro_match", "pub_match"}

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

- [ ] **Step 5: 运行确认通过**

Run: `make db-reset && pytest tests/db/test_isolation.py -q`
Expected: **3 passed**

- [ ] **Step 6: Commit**

```bash
git add db/views db/sources.py tests/db/test_isolation.py
git commit -m "feat(db): 对手侧隔离视图 + resolve_sources 白名单 + 负向测试"
```

---

### Task 4: 契约公共组件（枚举 / 降级形态 / 错误信封）

规格 §6.0 定义了全部枚举、统一降级形态与错误信封。本任务把它落成机器可读的 schema。

**Files:**
- Create: `contracts/schemas/common.yaml`
- Create: `contracts/tools/build_openapi.py`
- Test: `tests/contracts/test_common_schema.py`

- [ ] **Step 1: 写 `contracts/schemas/common.yaml`**

```yaml
openapi: 3.1.0
info: {title: MCNDOTAGA Contract, version: "1.0.0"}
components:
  schemas:
    Team:        {type: integer, enum: [0, 1], description: "0=Radiant, 1=Dire；仅用于数值字段"}
    Side:        {type: string, enum: [us, them], description: "仅用于 Playbook 语义字段"}
    Confidence:  {type: string, enum: [low, medium, high]}
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
      # 规格 §6.0：needs 仅当 reason == needs_replay 时存在，其余必须省略
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

- [ ] **Step 2: 写失败测试**

```python
import pytest, jsonschema
from contracts.tools.build_openapi import load_common

def test_degraded_requires_needs_only_for_needs_replay(common):
    D = common["Degraded"]
    jsonschema.validate({"value": None, "reason": "needs_replay", "needs": "Phase B"}, D)
    jsonschema.validate({"value": None, "reason": "insufficient_samples"}, D)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"value": None, "reason": "insufficient_samples", "needs": "Phase B"}, D)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"value": None, "reason": "needs_replay"}, D)

def test_error_codes_are_closed(common):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"error": {"code": "boom", "message": "x"}},
                            common["ErrorEnvelope"])
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/contracts/test_common_schema.py -q`
Expected: FAIL — `No module named 'contracts.tools.build_openapi'`

- [ ] **Step 4: 写 `contracts/tools/build_openapi.py`**

阅读 `contracts/schemas/*.yaml`，合并为一个 `contracts/openapi.yaml`，并暴露 `load_common()` 供测试用。合并时各文件的 `components.schemas` 取并集，重名冲突直接抛错（防止两个文件定义同名不同义的枚举）。

- [ ] **Step 5: 运行确认通过**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts/test_common_schema.py -q`
Expected: `contracts/openapi.yaml` 生成；**2 passed**

- [ ] **Step 6: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): §6.0 公共组件（枚举/降级形态/错误信封）+ schema 测试"
```

---

### Task 5: 五个接口的响应 schema

规格 §6.1–§6.6 定义了五个资源。本任务把它们落成 schema，并**逐条把不变式写成可执行断言**。

**Files:**
- Create: `contracts/schemas/value.yaml`, `policy.yaml`, `playbook.yaml`, `profile.yaml`, `advise.yaml`
- Create: `contracts/tools/invariants.py`
- Test: `tests/contracts/test_invariants.py`

- [ ] **Step 1: 写 `contracts/tools/invariants.py`（先写断言，后写 schema）**

```python
"""规格 §6 的每条不变式，作为可执行函数。fixtures 与真实响应都跑这些。"""
from __future__ import annotations

TOL = 1e-3

def check_value(body: dict) -> list[str]:
    errs = []
    total = sum(c["delta"] for c in body["contributions"])
    if abs(total - (body["radiant_win_prob"] - 0.5)) > TOL:
        errs.append(f"contributions 求和 {total:.4f} != radiant_win_prob-0.5 {body['radiant_win_prob']-0.5:.4f}")
    if not set(body.get("sources_used", [])) <= set(body.get("_requested_sources", body.get("sources_used", []))):
        errs.append("sources_used ⊄ sources")
    return errs

def check_policy(body: dict) -> list[str]:
    errs = []
    s = sum(c["prob"] for c in body["candidates"]) + body["other_prob"]
    if abs(s - 1.0) > TOL:
        errs.append(f"sum(candidates.prob)+other_prob = {s:.4f} != 1.0")
    if len(body["candidates"]) > body["top_n"]:
        errs.append("len(candidates) > top_n")
    if any(not c.get("reasons") for c in body["candidates"]):
        errs.append("存在 reasons 为空的候选")
    return errs

# 规格 §6.0 的 24 手模板。**唯一定义处** —— check_resolve 与 check_playbook 共用。
# (is_pick, 归属方)  归属方 'F' = 先手方, 'O' = 后手方
_TEMPLATE = [
    (0,'F'),(0,'F'),(0,'O'),(0,'O'),(0,'F'),(0,'O'),(0,'O'),   # ord 0-6   ban
    (1,'F'),(1,'O'),                                            # ord 7-8   pick
    (0,'F'),(0,'F'),(0,'O'),                                    # ord 9-11  ban
    (1,'O'),(1,'F'),(1,'F'),(1,'O'),(1,'O'),(1,'F'),            # ord 12-17 pick
    (0,'F'),(0,'O'),(0,'F'),(0,'O'),                            # ord 18-21 ban
    (1,'F'),(1,'O'),                                            # ord 22-23 pick
]

def check_resolve(next_ord: int, team: int, is_pick: bool, first_pick_team: int) -> list[str]:
    exp_pick, who = _TEMPLATE[next_ord]
    exp_team = first_pick_team if who == 'F' else 1 - first_pick_team
    errs = []
    if bool(exp_pick) != bool(is_pick): errs.append(f"ord {next_ord} 类型应为 {bool(exp_pick)}")
    if exp_team != team: errs.append(f"ord {next_ord} 归属应为 team {exp_team}")
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
            # by_ord 必须是绝对手数，且其归属方与所属分支自洽（规格 §6.3）
            for kp in pl["key_picks"]:
                _, who = _TEMPLATE[kp["by_ord"]]
                owner_is_us = (who == "F") if us_first else (who == "O")
                if not owner_is_us:
                    errs.append(
                        f"plan {pl['label']}: by_ord {kp['by_ord']} 属于对手，"
                        f"但分支 first_pick={want_first}（我方先手）")
    for oh in body.get("op_hero_decision", []):
        lv = oh["if_we_leave"]
        if "our_wr" in lv and "their_wr" in lv and abs(lv["our_wr"] + lv["their_wr"] - 1.0) > TOL:
            errs.append(f"hero {oh['hero_id']}: our_wr + their_wr != 1.0")
    return errs
```

> **实现提示**：把 §6.0 的 24 项模板提为**模块级常量 `_TEMPLATE`**，由 `check_resolve` 与 `check_playbook` 共用。两份模板副本必然漂移，而模板是本项目最不能出错的东西。

def check_advise(body: dict, lam: float = 1.0) -> list[str]:
    errs = []
    scores = [o["penalized_score"] for o in body["options"]]
    if scores != sorted(scores, reverse=True):
        errs.append("options 未按 penalized_score 降序")
    for o in body["options"]:
        exp = o["expected_wr"] - lam * max(0.0, o["robustness_delta"] - 0.10)
        if abs(exp - o["penalized_score"]) > TOL:
            errs.append(f"hero {o['hero_id']}: penalized_score {o['penalized_score']} != {exp:.4f}")
        if o["robustness_delta"] > 0.10 and not o.get("risk_note"):
            errs.append(f"hero {o['hero_id']}: robustness_delta > 0.10 但缺 risk_note")
        if not o.get("fallback"): errs.append(f"hero {o['hero_id']}: fallback 为空")
    return errs

def check_profile(body: dict) -> list[str]:
    errs = []
    for p in body["players"]:
        ha = p["dimensions"]["hero_archetype"]
        six = {"initiate","protect","push","teamfight","pickoff","splitpush"}
        if set(ha) != six: errs.append(f"player {p['account_id']}: hero_archetype 键集不完整")
        elif abs(sum(ha.values()) - 1.0) > TOL: errs.append(f"player {p['account_id']}: hero_archetype 和 != 1.0")
    return errs
```

- [ ] **Step 2: 写失败测试**

```python
import pytest
from contracts.tools.invariants import check_value, check_policy, check_advise, check_profile

SPEC_VALUE = {"radiant_win_prob": 0.530, "contributions": [
    {"factor":"patch_strength","delta":0.011},{"factor":"counter_matchup","delta":-0.014},
    {"factor":"player_comfort","delta":0.024},{"factor":"first_pick","delta":0.009}],
    "sources_used": ["pro_match"]}

def test_spec_value_example_passes():
    assert check_value(SPEC_VALUE) == []

def test_spec_value_example_detects_drift():
    bad = {**SPEC_VALUE, "radiant_win_prob": 0.53, "contributions": [
        {"factor":"patch_strength","delta":0.021},{"factor":"counter_matchup","delta":-0.014},
        {"factor":"player_comfort","delta":0.038},{"factor":"first_pick","delta":0.009}]}
    assert check_value(bad) != []   # 这是规格一轮抓到过的原始错误

def test_spec_policy_example_passes():
    assert check_policy({"candidates":[{"hero_id":112,"prob":0.180,"reasons":["x"]}],
                         "top_n":10,"other_prob":0.820}) == []

def test_spec_advise_example_passes():
    opts = [{"hero_id":105,"expected_wr":0.552,"robustness_delta":0.04,"penalized_score":0.552,"fallback":[67],"why":"x"},
            {"hero_id":67,"expected_wr":0.548,"robustness_delta":0.03,"penalized_score":0.548,"fallback":[19],"why":"x"},
            {"hero_id":19,"expected_wr":0.556,"robustness_delta":0.22,"penalized_score":0.436,"fallback":[67],"risk_note":"r","why":"x"}]
    assert check_advise({"options": opts}) == []

def test_advise_detects_unpenalized_score():
    opts = [{"hero_id":19,"expected_wr":0.556,"robustness_delta":0.22,"penalized_score":0.556,"fallback":[67],"why":"x"}]
    assert check_advise({"options": opts}) != []
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/contracts/test_invariants.py -q`
Expected: FAIL — `No module named 'contracts.tools.invariants'`

- [ ] **Step 4: 写五个 schema 文件**

按规格 §6.1–§6.6 的响应示例逐字段落成 JSON Schema。**必填性以规格为准**：规格标 `// optional` 的字段不进 `required`。

**注意规格里给出的示例值必须全部通过 Step 1 的不变式**——这是本任务的自检标准。

- [ ] **Step 5: 运行确认通过**

Run: `python -m contracts.tools.build_openapi && pytest tests/contracts -q`
Expected: **全部 passed**

- [ ] **Step 6: Commit**

```bash
git add contracts/ tests/contracts/
git commit -m "feat(contracts): 五个资源的 schema + 不变式可执行断言（含规格示例自检）"
```

---

### Task 6: 13 个边界 fixtures 与校验工具

规格 §11 列出的 13 个 fixture 是前端 worktree 的启动条件。它们必须**逐个通过契约校验与不变式**。

**Files:**
- Create: `contracts/fixtures/*.json`（13 个）
- Create: `contracts/tools/validate_fixtures.py`
- Test: `tests/contracts/test_fixtures.py`

- [ ] **Step 1: 写 `contracts/tools/validate_fixtures.py`**

```python
"""校验每个 fixture：① 通过其资源的 schema；② 通过该资源的不变式。"""
from __future__ import annotations
import json, pathlib, sys
import jsonschema, yaml
from . import invariants as inv

FIX = pathlib.Path(__file__).parents[1] / "fixtures"
TOOLS = pathlib.Path(__file__).parent

ROUTES = {
    "value":    inv.check_value,
    "policy":   inv.check_policy,
    "playbook": inv.check_playbook,
    "profile":  inv.check_profile,
    "advise":   inv.check_advise,
}

def main() -> int:
    spec = yaml.safe_load((pathlib.Path(__file__).parents[1] / "openapi.yaml").read_text())
    schemas = spec["components"]["schemas"]
    failures = []
    for f in sorted(FIX.glob("*.json")):
        body = json.loads(f.read_text(encoding="utf-8"))
        resource = f.name.split("__")[0]
        if resource == "error":
            jsonschema.validate(body, schemas["ErrorEnvelope"]); continue
        try:
            jsonschema.validate(body, schemas[resource.capitalize()])
        except jsonschema.ValidationError as e:
            failures.append(f"{f.name}: schema — {e.message}"); continue
        for err in ROUTES[resource](body):
            failures.append(f"{f.name}: invariant — {err}")
    for msg in failures: print("FAIL", msg)
    print(f"{len(list(FIX.glob('*.json')))} fixtures, {len(failures)} failures")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 写 13 个 fixture**

按规格 §11 的表格逐个创建。**每个 fixture 必须能通过 Step 3 的校验**——若某个 fixture 触发不变式失败，说明契约或不变式有洞，**先修契约而不是放宽断言**。

| 文件 | 必须覆盖 |
|---|---|
| `value__low_confidence.json` | `confidence:"low"`、`n_samples < 30` |
| `value__spec_example.json` | 规格 §6.1 的示例（真实比赛数据） |
| `policy__spec_example.json` | 规格 §6.2 的示例（前 12 手真实 BP） |
| `policy__no_model.json` | `baseline.model_top1 = null` |
| `playbook__spec_example.json` | 规格 §6.3 的示例 |
| `playbook__draft_incomplete.json` | `data_quality.n_pending_draft > 0` 且 `n_unavailable_draft > 0` |
| `playbook__anomalous.json` | `data_quality.n_anomalous_draft > 0` |
| `playbook__positions_phase_b.json` | `notes[].value=null` + `reason="needs_replay"` + `needs="Phase B"` |
| `playbook__op_insufficient.json` | `recommendation="insufficient_data"`、`if_we_ban` 为降级形态且**无** `needs` |
| `profile__spec_example.json` | 规格 §6.4 的示例（含 `window_games=48`） |
| `profile__map_vision_counts_only.json` | `map_vision` 返回真实 `percentile`（非降级），同时 ward 坐标条目降级 |
| `profile__position_unknown.json` | 五个位置维度降级，**`hero_archetype` 仍返回六项且和为 1.0** |
| `advise__offline.json` | `mode:"offline"` → 返回 `branches[]`（非 `options[]`） |
| `advise__robustness_penalized.json` | 含 `robustness_delta > 0.10` 的 option，`penalized_score < expected_wr`、带 `risk_note`、排序被降 |
| `error__insufficient_data.json` | HTTP 200 + `error.code="insufficient_data"` |
| `error__source_not_allowed.json` | HTTP 403 + `error.code="source_not_allowed"` |

- [ ] **Step 3: 运行校验**

Run: `make contract`
Expected: `16 fixtures, 0 failures`

- [ ] **Step 4: 写 `tests/contracts/test_fixtures.py`**

```python
from contracts.tools.validate_fixtures import main

def test_all_fixtures_conform():
    assert main() == 0
```

- [ ] **Step 5: Commit**

```bash
git add contracts/
git commit -m "feat(contracts): 16 个边界 fixtures + 校验工具（每个都过 schema 与不变式）"
```

---

### Task 7: TS 类型生成（前端 worktree 的最后一块）

规格 §11 要求「枚举未在客户端硬编码」。本任务从契约生成 TS 类型，使前端与后端不可能漂移。

**Files:**
- Create: `contracts/tools/gen_ts_types.py`
- Create: `web/src/types/contract.ts`（生成物，提交入库）
- Test: `tests/contracts/test_ts_types_fresh.py`

- [ ] **Step 1: 写生成脚本**

用 `datamodel-code-generator` 的 TypeScript 后端，或直接手写一个简版生成器（枚举 + 接口）。要求：**生成物必须包含 §6.0 的全部枚举**，且文件头写 `// AUTO-GENERATED — do not edit. Run: make contract-ts`。

- [ ] **Step 2: 写新鲜度测试**

```python
import subprocess, pathlib

def test_generated_ts_is_up_to_date():
    """契约改了但忘记重新生成时，此测试失败。"""
    before = pathlib.Path("web/src/types/contract.ts").read_text(encoding="utf-8")
    subprocess.run(["python","-m","contracts.tools.gen_ts_types"], check=True)
    after = pathlib.Path("web/src/types/contract.ts").read_text(encoding="utf-8")
    assert before == after, "contract.ts 已过期，请运行 make contract-ts 并提交"
```

- [ ] **Step 3: 运行确认通过**

Run: `python -m contracts.tools.gen_ts_types && pytest tests/contracts/test_ts_types_fresh.py -q`
Expected: **1 passed**

- [ ] **Step 4: Commit（M0 完成）**

```bash
git add contracts/ web/ tests/
git commit -m "feat(contracts): TS 类型生成 + 新鲜度测试 —— M0 契约冻结完成"
```

**M0 验收**：`contracts/openapi.yaml` 提交且通过校验；16 个 fixtures 全部过 schema 与不变式；TS 类型已生成且新鲜度受测；三条 worktree 的目录边界（`db/`+`analysis/`、`models/`、`web/`）已建立。

---

## Chunk 2: 数据地基（M1）

### Task 8: 常量层——英雄与道具

**Files:**
- Create: `constants/dotaconstants.py`
- Test: `tests/constants/test_dotaconstants.py`

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

`constants/dotaconstants.py`：从 `https://api.opendota.com/api/constants/heroes` 与 `/constants/items` 拉取（**必须带自定义 User-Agent**，默认 UA 会被 403）。

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

### Task 9: 版本表——Valve 权威清单 + 子版本还原

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

### Task 10: Kaggle 引导数据集（选择性下载 506 MB 而非 48.5 GB）

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

### Task 11: M1 验收

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
