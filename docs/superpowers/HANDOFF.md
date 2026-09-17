# 交接说明

**更新于**：2026-09-17
**当前分支**：`plan-1-contract-and-data-foundation`（在 `.worktrees/plan-1-contract-and-data-foundation`）
**已完成**：Task 1–2 / 共 13
**下一步**：Task 3

---

## 一句话恢复

打开新会话，说：

> 按 `docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md` 从 Task 3 继续。在 `.worktrees/plan-1-contract-and-data-foundation` 里干活（`.venv` 已装好，`main` 也已同步到同一提交）。用 subagent-driven-development：每个任务派实施者子代理，然后规格评审 + 质量评审。

## 目录情况

| 位置 | 状态 |
|---|---|
| `/Users/xinzechao/MCNDOTAGA`（`main`） | 已含 Task 1–2 的全部产出（快进合并） |
| `.worktrees/plan-1-contract-and-data-foundation`（同名分支） | **在这里继续**。`.venv/` 已装好、依赖齐全 |
| `/tmp/taskN.md` | 上一会话抽取的任务文本，**新会话不存在，需重新抽取** |

**在主目录（`main`）里没有 `.venv`**，直接跑 pytest 会找不到 psycopg。所以要么在 worktree 里干活，要么先在主目录建 venv。

抽取任务文本的方式（新会话用）：

```bash
python3 -c "
import pathlib
s = pathlib.Path('docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md').read_text(encoding='utf-8')
a = s.index('### Task 3:'); b = s.index('### Task 4:')
pathlib.Path('/tmp/task3.md').write_text(s[a:b], encoding='utf-8')
print(f'Task 3 = {len(s[a:b].splitlines())} 行')
"
```

---

## 两份权威文档

| 文档 | 作用 |
|---|---|
| `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md` | **设计规格**（17 节）。产品与技术决策的唯一权威。经 4 轮独立评审 + 在真实 PostgreSQL 16 上执行验证（§16 记录了实证结果） |
| `docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md` | **实施计划**（3 chunk / 13 任务 / 2036 行）。每个任务带完整代码、精确命令、预期输出，按"实施者零上下文也能照做"的标准写 |

**规格优先于计划**。计划是"怎么做"，规格是"做什么"。冲突时以规格为准，并回头修计划。

---

## 环境约束（踩过的坑，别再踩）

### 1. 本机没有 Docker

`command -v docker` 失败。因此计划里的 `make up` / `make db-reset` / Task 2 Step 6 的原文命令**都跑不了**。

**但本机有 PostgreSQL 16.14**（Homebrew）：`/opt/homebrew/bin/{postgres,psql,initdb,pg_ctl}`。

用临时集群替代：

```bash
initdb -D /tmp/pgx -U dota --auth=trust
pg_ctl -D /tmp/pgx -o "-p 55432 -k /tmp" -l /tmp/pgx.log start
export TEST_DATABASE_URL="postgresql://dota@localhost:55432/dota_test"
export DATABASE_URL="postgresql://dota@localhost:55432/dota"
```

用完 `pg_ctl -D /tmp/pgx stop`。

**Makefile 里的 `up`/`down`/`db-reset` 保持 Docker 原样不要改**——它们是给用户机器（有 Docker）用的。本机执行时跳过并在报告里说明偏离。

### 2. Python 是 3.14.3，不是计划写的 3.12

`requires-python = ">=3.12"` 满足，psycopg-binary 有 cp314 arm64 wheel，无需编译。**不要改 `requires-python`**。

### 3. 虚拟环境不在 PATH 上

用 `.venv/bin/python` / `.venv/bin/pytest`，或先 `source .venv/bin/activate`。

### 4. `TEST_DATABASE_URL` 有护栏（别绕过它）

`tests/conftest.py` 的 `dsn` fixture 每 session 会 **DROP + CREATE** 测试库，以保证 schema 永远对应当前迁移（否则改了 `001_schema.sql` 测试会静默跑在旧 schema 上——这个坑真实发生过）。

护栏规则：**库名必须以 `_test` 结尾**，否则 `pytest.exit` 拒绝运行。这是命名约定级的护栏，**不是**"不会删数据"的保证——`prod_test` 一样会被抹掉。别把 `TEST_DATABASE_URL` 指向任何你不想丢的东西。

---

## 已完成的两个任务

### Task 1 · 仓库骨架（commit `62d95fd` + `9972bd9`）

产出：`compose.yaml`、`.gitattributes`、`.env.example`、`pyproject.toml`、`Makefile` + 目录树。

**控制者额外修的一处**：`[tool.setuptools.packages.find]` 的 include 列表漏了 `constants*` 与 `ingest*`（该列表写于计划评审阶段，当时 Task 9/12 的 Files 清单还没拆出来）。缺了它们 editable install 不会映射这两个包。

### Task 2 · DDL 迁移 + 约束测试（commit `39d65b3` → `7dd703c` → `14cd7d5` → `4da16fb`）

产出：`db/migrations/001_schema.sql`（22 表 + 5 索引，与规格 §5.1 逐字节一致）、`db/migrate.py`、`tests/conftest.py`、`tests/db/test_constraints.py`（**16 passed**）。

**这个任务跑了四轮，每轮挖得更深**——值得记住，因为它说明了评审密度的价值：

| 轮次 | 发现 | 谁能发现 |
|---|---|---|
| 0 | 15 条测试全绿 | 看起来完成了 |
| 1 | 主键测试名不副实 | 读代码 |
| 2 | `expect_violation` 吞掉任意 `psycopg.Error`，所以按建议拆开后**仍是恒绿空断言** | 只有破坏验证能发现 |
| 3 | `dsn` 会无条件摧毁 `TEST_DATABASE_URL` 指向的库；主键测试没钉住主键本身 | 只有把变量指向真库、以及逐条改约束定义才能发现 |

**核心教训**：`with expect_violation(db):` 读起来完全正确，`DROP DATABASE ... CREATE` 读起来也完全正常。**"测试是绿的"和"测试真的在测东西"是两件事**，只有主动破坏被测对象、确认它变红，才能区分。后续任务的负向测试请沿用这个模式。

**已建立的模式（后续任务请沿用）**：

- 负向测试必须钉 **SQLSTATE**（`expect_violation(db, sqlstate="23514")`）
- 有歧义时还要钉 **约束名**（`constraint="slot_team_agree"`）——光钉 SQLSTATE 时，把约束改名或换成同 SQLSTATE 的另一个约束都能骗过它
- 注意 PG 对 `CREATE UNIQUE INDEX` 报的是**索引名**而非约束名
- 改完任何约束后，**实际破坏一次**确认目标测试变红

---

## 剩余 11 个任务

| Task | 内容 | 依赖 |
|---|---|---|
| 3 | 对手侧隔离视图 + `db/sources.py` + 负向测试 | Task 2 |
| 4 | `shared/draft_template.py`（24 手模板唯一定义处） | 无 |
| 5 | 契约公共组件（枚举 / 降级形态 / 错误信封） | Task 1 |
| 6 | 五资源 schema + 可执行不变式 | Task 5 |
| 7 | 17 个边界 fixtures + 校验工具 | Task 6 |
| 8 | TS 类型生成 + OpenAPI 校验（**M0 完成**） | Task 7 |
| 9 | 常量层（英雄/道具/原型映射） | Task 1 |
| 10 | 版本表（Valve 权威 + 子版本还原） | Task 9 |
| 11 | 常量入库（六张表） | Task 10 |
| 12 | Kaggle 引导数据集（506 MB 子集） | Task 11 |
| 13 | M1 验收 | Task 12 |

**Task 3 的两个提示**（来自 Task 2 实施者）：
- `dsn` 现在每 session 重建，改了 migration 直接重跑就生效，不需要 `make db-reset`
- Task 3 的负向测试如果也用 `expect_violation`，**从一开始就钉 SQLSTATE**

**Task 4 特别注意**：`shared/draft_template.py` 里的 24 手模板是**规格 §6.0 / §8① 的唯一定义处**，分析引擎、序列模型、契约校验器全部 import 它。规格 §16 已实测确认它与真实比赛 1,014 场逐手一致。两份副本必然漂移——不要复制它。

**Task 12 的前置**：需要 Kaggle 凭证（`KAGGLE_USERNAME` / `KAGGLE_KEY`）。凭证缺失时相关测试应 `skip` 而非变红。另需先 `python -m pip install -e ".[dev,ingest]"`（`ingest` extra 含 `python-dotenv` 与 `kaggle`）。

---

## 工作流（subagent-driven-development）

每个任务：

1. **派实施者子代理**（`subagent`）——给它任务的**完整文本**（从计划里抽取到 `/tmp/taskN.md` 再让它读，等价于粘贴）、场景上下文、环境约束、预期结果。明确告诉它：**做完要自查，发现问题自己修**。
2. **派规格符合性评审**——独立验证实现是否匹配规格，不多不少。**要求它执行验证而非只读代码**（本项目最有价值的发现全部来自"真的跑一遍"）。
3. **派代码质量评审**——判断是否经得起后续 11 个任务的使用。
4. 评审发现问题 → **同一个实施者子代理**修（`send_message`），修完重新评审。
5. 任务完成才进下一个。

**三个 worktree 的边界**（规格 §11）：线 A → `db/` + `analysis/`，线 C → `models/`，前端 → `web/`。`shared/` 不属于任何一条线。契约冻结（Task 8）之后三条线才能真正并行——**M0 之前不要开并行 worktree**。

---

## 计划文档被实施者改过，这是有意的

Task 2 的实施者按控制者要求把计划里的代码块同步成了实现的样子，并改对了三处**实测证明写错的**预期数字（例如"只删主键会得到 syntax error"实际是 16 errors）。

**这意味着计划文档在 Task 2 那一段已经不是"原始要求"的记录了**——它是当前实现的镜像。评估后续任务时若需要区分"原本要求什么"和"实际做成了什么"，要看 commit 历史，不要只看计划。

同时请注意：**实施者可能把实现写进计划文档来自圆其说**。Task 2 的三次文档改动都经评审确认"改对了"，但这个模式需要保持警惕。

---

## 完成后的状态

Task 8 完成后（M0 达成），三条 worktree 即可并行：线 A 做画像与剧本引擎（计划 3）、线 C 做序列模型（计划 5）、前端做可视化（计划 4）。

计划 2/3/4/5/6 尚未编写。
