# 交接说明

**更新于**：2026-09-17
**当前分支**：`plan-1-contract-and-data-foundation`（在 `.worktrees/plan-1-contract-and-data-foundation`）
**已完成**：Task 1–12 / 共 13 —— **M0（契约冻结）已达成，M1（数据地基）的常量层与引导数据集已入库**
**下一步**：Task 13（M1 验收）；**并先读文末「⚠ 必须由人裁决的一项」**——24 手模板只匹配 2026 年数据，全库仅 10.12%
**M0 冻结提交**：`64c89a9`（代码与计划都在这个提交上）。本文件自身的最后更新在它**之后**——开 worktree 一律用 `64c89a9`（见开头三条线的命令）
**当前 HEAD**：`02650ae`（Task 12 完成）。全量测试：**199 passed**（含真实 Kaggle 数据入库，约 128 秒）；不含 ingest 的快速子集 **165 passed / 0.55 秒**

---

## 一句话恢复

继续做计划里的任务（M1 数据地基的收尾）：

> 按 `docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md` 从 Task 13 继续。在 `.worktrees/plan-1-contract-and-data-foundation` 里干活（`.venv` 已装好，`tests/fixtures/kaggle/` 已有 498 MB 真实缓存）。用 subagent-driven-development：每个任务派实施者子代理，然后规格评审 + 质量评审。
>
> **先读文末「⚠ 必须由人裁决的一项」**：Task 4 冻结的 24 手模板是"2026 年那一种手序"，不是全库唯一手序——这会影响 Task 13 的验收口径与三条并行线的下游实现。

开三条并行 worktree（线 A / 线 C / 前端）：

> 从 M0 冻结提交开分支，**不要从 `main` 开**——`main` 停在 `c486c0d`，距离 M0 冻结提交 `64c89a9` 有 **25 个提交**（`git rev-list --count main..64c89a9`），**不含任何 M0 产出**：`contracts/openapi.yaml`、`contracts/fixtures/`、`contracts/tools/gen_ts_types.py`、`web/src/types/contract.ts`、`shared/` 全都不在 `main` 上。默认方式（从 `main`）建的 worktree 拿到的是一个空壳：目录在、文件不在，而且**不会报错**。

## 目录情况

| 位置 | 状态 |
|---|---|
| `.worktrees/plan-1-contract-and-data-foundation`（同名分支） | **在这里继续**。`.venv/` 已装好、依赖齐全；HEAD 含 Task 1–8 全部产出 |
| `/Users/xinzechao/MCNDOTAGA`（`main`） | **只有 Task 1–2**（`c486c0d`）。**没有 M0 的任何东西**，也没有 `.venv`。别从这里开 worktree、别在这里跑 pytest |
| `/tmp/taskN.md` | 上一会话抽取的任务文本，**新会话不存在，需重新抽取** |

**先做一件事（可选但推荐）**：把 `main` 快进到 M0 冻结提交，之后从 `main` 开 worktree 就安全了：

```bash
cd /Users/xinzechao/MCNDOTAGA
git checkout main && git merge --ff-only plan-1-contract-and-data-foundation
```

**没快进就按基点开**（三条线各自的分支名按计划里的边界取）：

```bash
cd /Users/xinzechao/MCNDOTAGA
git worktree add -b plan-2-analysis .worktrees/plan-2-analysis 64c89a9
git worktree add -b plan-5-models   .worktrees/plan-5-models   64c89a9
git worktree add -b plan-4-frontend .worktrees/plan-4-frontend 64c89a9
```

（`64c89a9` = M0 冻结提交；用 `git log --oneline -1 plan-1-contract-and-data-foundation` 随时复核。
新 worktree 里**没有 `.venv`**——要么从本 worktree 复制，要么在新 worktree 里重新
`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`。）

抽取任务文本的方式（新会话用，以 Task 9 为例）：

```bash
python3 -c "
import pathlib
s = pathlib.Path('docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md').read_text(encoding='utf-8')
a = s.index('### Task 9:'); b = s.index('### Task 10:')
pathlib.Path('/tmp/task9.md').write_text(s[a:b], encoding='utf-8')
print(f'Task 9 = {len(s[a:b].splitlines())} 行')
"
```

---

## M0 验收证据（复核修复后重跑，全部可复现）

| 项 | 结果 | 命令 |
|---|---|---|
| 契约测试 | **87 passed** | `.venv/bin/pytest tests/contracts -q` |
| 全量测试 | **120 passed** | 见下面 DB 环境变量 + `.venv/bin/pytest -q` |
| fixtures | **17 个，0 失败** | `.venv/bin/python -m contracts.tools.validate_fixtures` |
| OpenAPI 文档 | `contracts/openapi.yaml: OK`（exit 0） | `.venv/bin/python -m openapi_spec_validator contracts/openapi.yaml` |
| TS 严格编译 | exit 0（无输出） | `/Users/xinzechao/node_modules/.bin/tsc --noEmit --strict --target es2020 --typeRoots /tmp/ts-empty-types web/src/types/contract.ts` |
| 生成物新鲜度 | `--check` exit 0（**只读**，不写盘） | `.venv/bin/python -m contracts.tools.gen_ts_types --check` |
| 生成物 sha256 | `56e840e7f0d15369b3b1166fcc9b3556edcebc666988e2445b82dc1b41d01fca`（17 行，`wc -l`） | `shasum -a 256 web/src/types/contract.ts` |

契约测试 87 的分解：common_schema 5 + openapi_fresh 1 + invariants 45 + schema_shape 31
+ fixtures 2 + ts_types 3。全量 120 = 契约 87 + db 22（16 约束 + 6 隔离）+ shared 11。

**生成物变了要一起改的三处**（改契约 → 重生成 → 改冻结基线 → 一起提交）：`contracts/openapi.yaml`、
`tests/contracts/test_ts_types_fresh.py`（`ENUM_MEMBERS` / `RESOURCE_MEMBERS`）、
`web/src/types/contract.ts`。

---

## 两份权威文档

| 文档 | 作用 |
|---|---|
| `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md` | **设计规格**（17 节）。产品与技术决策的唯一权威。经 4 轮独立评审 + 在真实 PostgreSQL 16 上执行验证（§16 记录了实证结果） |
| `docs/superpowers/plans/2026-09-16-contract-freeze-and-data-foundation.md` | **实施计划**（3 chunk / 13 任务）。每个任务带完整代码、精确命令、预期输出，按"实施者零上下文也能照做"的标准写 |

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

用 `.venv/bin/python` / `.venv/bin/pytest`，或先 `source .venv/bin/activate`。顺带：`make contract` / `make contract-ts` / `make lint-spec` 直接跑会以 `make: python: No such file or directory` 失败（Makefile 里写的是 `python`），本机改用 `.venv/bin/python -m ...`。

### 4. `TEST_DATABASE_URL` 有护栏（别绕过它）

`tests/conftest.py` 的 `dsn` fixture 每 session 会 **DROP + CREATE** 测试库，以保证 schema 永远对应当前迁移（否则改了 `001_schema.sql` 测试会静默跑在旧 schema 上——这个坑真实发生过）。

护栏规则：**库名必须以 `_test` 结尾**，否则 `pytest.exit` 拒绝运行。这是命名约定级的护栏，**不是**"不会删数据"的保证——`prod_test` 一样会被抹掉。别把 `TEST_DATABASE_URL` 指向任何你不想丢的东西。

---

## 已完成的八个任务

### Task 1 · 仓库骨架（commit `62d95fd` + `9972bd9`）

产出：`compose.yaml`、`.gitattributes`、`.env.example`、`pyproject.toml`、`Makefile` + 目录树。

**控制者额外修的一处**：`[tool.setuptools.packages.find]` 的 include 列表漏了 `constants*` 与 `ingest*`（该列表写于计划评审阶段，当时 Task 9/12 的 Files 清单还没拆出来）。缺了它们 editable install 不会映射这两个包。

### Task 2 · DDL 迁移 + 约束测试（commit `39d65b3` → `7dd703c` → `14cd7d5` → `4da16fb`）

产出：`db/migrations/001_schema.sql`（22 表 + 6 索引，与规格 §5.1 逐字节一致）、`db/migrate.py`、`tests/conftest.py`、`tests/db/test_constraints.py`（**16 passed**）。

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

### Task 3–7 · 隔离视图 / 共享模板 / 契约公共组件 / 五资源 schema / 17 fixtures

产出：`db/sources.py` + `tests/db/test_isolation.py`（6）、`shared/draft_template.py` +
`tests/shared/test_draft_template.py`（11）、`contracts/schemas/common.yaml`、
`contracts/schemas/{value,policy,playbook,profile,advise}.yaml`、`contracts/tools/invariants.py`、
`contracts/fixtures/`（17 个）、`contracts/tools/validate_fixtures.py`。

跨任务复用的两条经验：

- **`shared/draft_template.py` 里的 24 手模板是规格 §6.0 / §8① 的唯一定义处**，分析引擎、序列模型、契约校验器全部 import 它。规格 §16 已实测确认它与真实比赛 1,014 场逐手一致。**两份副本必然漂移——不要复制它。**
- 契约的字段级语义（可空、可选）写在 schema 里而不是散文里：`required` 是唯一权威，
  `x-optional` 表示"key 可缺席但值不可为 null"。

### Task 8 · TS 类型生成 + OpenAPI 校验（M0 收尾）

产出：`contracts/tools/gen_ts_types.py`、`web/src/types/contract.ts`（生成物，17 行，`wc -l`）、
`tests/contracts/test_ts_types_fresh.py`（3 条）。

复核修复（生成器硬化在 `64c89a9`，计划/HANDOFF 的更正随之；本文件上一版交接时还没有）：

- 枚举值走 `json.dumps`——含 `"` / 反斜杠 / 换行的值此前产出非法 TS（`TS1002`），
  或**静默变形**（`back\slash` 编译成 `backslash`）
- 生成器对"带 `enum` 但渲染不出来"的 schema **报错**，且必需枚举的 guard 校验的是
  **生成物真的发射了**它，不是"契约里有这个名字"；失败发生在任何写盘之前
- 新增冻结 `RESOURCE_MEMBERS` 基线：`Resource` 联合此前是唯一无人看守的导出
- 新增 `--check` 只读模式，新鲜度测试改用它——旧写法会把受版本控制的
  `contract.ts` 当草稿纸，本地手改被测试静默抹掉
- `schema.description` 发射为 JSDoc：`Team = 0 | 1` 现在带
  `/** 0=Radiant, 1=Dire；仅数值字段 */`，`Side` 带 `/** 仅 Playbook 语义字段 */`

**范围（重要）**：TS 侧只生成**枚举联合 + `Resource` 联合**，53 个 schema 的
**对象接口没有生成**。前端 worktree 不要以为 `Value` / `Policy` / `Playbook` 有对应接口——
这些名字目前只出现在 `Resource` 联合里。补齐它们是 Plan 4 开工第一件事，
做法与规则见计划 Task 8 的「范围说明」。

---

## 剩余 5 个任务（M1）

| Task | 内容 | 依赖 |
|---|---|---|
| 9 | 常量层（英雄/道具/原型映射） | Task 1 |
| 10 | 版本表（Valve 权威 + 子版本还原） | Task 9 |
| 11 | 常量入库（六张表） | Task 10 |
| 12 | Kaggle 引导数据集（506 MB 子集） | Task 11 |
| 13 | M1 验收 | Task 12 |

**Task 12 的前置**：需要 Kaggle 凭证（`KAGGLE_USERNAME` / `KAGGLE_KEY`）。凭证缺失时相关测试应 `skip` 而非变红。另需先 `python -m pip install -e ".[dev,ingest]"`（`ingest` extra 含 `python-dotenv` 与 `kaggle`）。

---

## 工作流（subagent-driven-development）

每个任务：

1. **派实施者子代理**（`subagent`）——给它任务的**完整文本**（从计划里抽取到 `/tmp/taskN.md` 再让它读，等价于粘贴）、场景上下文、环境约束、预期结果。明确告诉它：**做完要自查，发现问题自己修**。
2. **派规格符合性评审**——独立验证实现是否匹配规格，不多不少。**要求它执行验证而非只读代码**（本项目最有价值的发现全部来自"真的跑一遍"）。
3. **派代码质量评审**——判断是否经得起后续任务的使用。
4. 评审发现问题 → **同一个实施者子代理**修（`send_message`），修完重新评审。
5. 任务完成才进下一个。

**三个 worktree 的边界**（规格 §11）：线 A → `db/` + `analysis/`，线 C → `models/`，前端 → `web/`。`shared/` 不属于任何一条线。契约冻结（Task 8）已完成——**三条线现在可以真正并行**，但三条都必须从 M0 冻结提交开（见开头）。

---

## 计划文档被实施者改过，这是有意的

Task 2 的实施者按控制者要求把计划里的代码块同步成了实现的样子，并改对了三处**实测证明写错的**预期数字（例如"只删主键会得到 syntax error"实际是 16 errors）。Task 8 的复核又在计划里改了三类：分解算式里的"公共 6"更正为 5（重复计数）、worktree 基点声明、TS 生成范围说明（含两条可复现性备注），并同步了实测哈希。

**这意味着计划文档在已实现任务那几段已经不是"原始要求"的记录了**——它是当前实现的镜像。评估后续任务时若需要区分"原本要求什么"和"实际做成了什么"，要看 commit 历史，不要只看计划。

同时请注意：**实施者可能把实现写进计划文档来自圆其说**。Task 2 的三次文档改动都经评审确认"改对了"，但这个模式需要保持警惕。

---

## 完成后的状态

M0（Task 1–8）已达成，契约冻结：`contracts/openapi.yaml` 通过 schema 校验、17 个 fixtures 全部通过契约校验、三条线的目录边界已建立。

三条 worktree 从此可并行（基点提交 `64c89a9`；本说明文档的最后更新在它之后，只补充说明、不改产出）：线 A 做画像与剧本引擎（计划 3）、线 C 做序列模型（计划 5）、前端做可视化（计划 4）。M5 之前不建议动 `contracts/openapi.yaml`；确需变更时按上面的"三处一起改"流程走，并回头同步冻结基线。

计划 2/3/4/5/6 尚未编写。

---

## ⚠ 必须由人裁决的一项：24 手模板只匹配 2026 年的手序

**这是本轮最重要的发现，不是 bug，是规格假设被真实数据推翻。**

Task 12 用真实 Kaggle 全库（**211,051 场**）实测：

| 事实 | 数值 |
|---|---|
| `shared/draft_template.TEMPLATE`（规格 §6.0/§8①，Task 4 的"唯一定义处"）对 2026 年数据的匹配率 | **100%** |
| 同一模板对全库（2016–2026）的匹配率 | **10.12%** |
| 全库真实存在的**合法手序族** | **10 种**（已记录在 `ingest/load_bootstrap.DRAFT_ORDERS`） |
| 按计划字面规则（"手数≠24 即异常"）算出的异常率 | **58.33%** —— 直接违反 M1 的 `< 2%` |
| 实际采用的异常口径（"不匹配任何实测合法手序族"） | **0.97%** ✅ |

**为什么必须由人裁决**：`resolve()` 的 `(is_pick, team)` 推导是契约的一部分（Task 6 的 `check_playbook` 用它校验 `by_ord` 归属、Task 8 的 TS 契约、线 C 的位置 token 都由它派生）。若历史比赛的手序族不同，则：

- `analysis/`（线 A）对非 2026 场次的 `by_ord` 归属会算错；
- 线 C 的位置 token 会整体错位；
- Task 13 的 M1 验收若按"模板匹配率"而不是"合法族匹配率"判定，会直接失败。

**三个选项**（都需要人拍板，因为都触及冻结契约或验收口径）：

1. **保持契约不变**，把"族感知"下沉到 ingest 与下游：入库按十个族记录，分析与序列模型**必须按族过滤**（当前 ingest 已这么做）。代价：契约文档必须写明"§6.0 模板是 2026 年族的定义，不是全库唯一定义"。
2. **修订规格 §6.0/§8①**：把模板定义为**族相关**，`resolve()` 增加族参数；连带改 Task 4 的 11 条测试、Task 6 的不变式、Task 8 的 TS 类型与 17 个 fixtures。代价最大，但语义最干净。
3. **限定数据范围**：只把 2026 年（或某一起始版本之后）的场次计入分析与验收，早期数据仅作冷启动。代价：丢 90% 数据，序列模型样本大幅缩水。

**未决期间的安全默认**：Task 12 的实现按选项 1 工作（族感知入库 + 异常率 0.97%），任何按 `resolve()` 做归属的下游代码都必须先确认场次所属族。

---

## Task 9–12 的落地状态（供 Task 13 与三条并行线使用）

- **Task 9** 常量层：127 英雄 / 501 道具 / 8 项 roles 词表 / 六类原型映射（分布 117/55/44/34/29/23 = §7 声明值）；网络缓存提交入库（含 `.gitattributes` 的 `-text` 例外以保字节忠实）。
- **Task 10** 版本表：Valve 权威 118 版本 / 84 字母子版本；`dates[]` 位置映射（**不排序**，排序会与 §3.2 的 34/48/1 校准冲突）；**7.25 不移位**（移位会造出不存在的 7.25d）；7.06 有一个错档日期，已由"槽位不得早于前一系列 `main`"的通用不变式丢弃；`opendota_patch` 由 patchdates 的键导出（34/34 覆盖）。
- **Task 11** 常量入库：六张表幂等写入；**快照冻结守护**（库中 `hero_token_index` 与本次派生不一致时拒绝写入并报错，防止静默重映射）；测试用 `commit=False` 留在 fixture 事务里，共享测试库保持 0 行。
- **Task 12** 引导数据集：**该数据集无需 Kaggle 凭证即可下载**（CC0 的 `dataset_download_file` 端点；`pip install -e ".[dev,ingest]"` 已装 kaggle 2.2.4）。缓存 498 MB 在 `tests/fixtures/kaggle/`（已 gitignore）。实测 M1 数字：`matches` **211,051**、`draft_actions` **4,772,342**、`leagues` **1,557**、`anomaly=true` **2,048 = 0.97%**、2018 前 **17,552（8.3%）且 `patch_id IS NULL`**、2018 后晚归属 NULL **0**。
- **已知空缺**：`teams` 为空（CSV 不含队名），`matches.*_team_id` 为 NULL，需 Plan 3 用 `Constants/Constants.Leagues.csv` 之外的来源补全。

## 评审债（下一会话请补）

本轮 Task 3–10 都走完了"实施者 → 规格评审 → 质量评审 → 修复 → 复审"。**未走完的是**：

1. **Task 11 的第二轮修复**（`0ff2a56`/`2c12762`/`e955ff1`：快照冻结守护等）**尚未独立复审**；
2. **Task 12 完全没有评审**（`38a880d`/`9ac55f8`/`02650ae`，含 853 行入库代码 + 808 行测试 + 计划 2,485 行改动）；
3. **Task 13 未开始**。

按本项目已证实的经验，这两处复审最可能发现的是"绿而不守"的断言与跨套件污染，请照 Task 6/9 的做法要求评审者**执行变异验证**（改坏被测对象，确认测试变红），而不是只读代码。
