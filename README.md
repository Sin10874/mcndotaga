# MCNDOTAGA

DOTA2 职业比赛 BP 与战队画像分析系统。当前提供浏览器工作台、真实画像、职业采集，以及可交互测试的 Value、Policy、Advise、Playbook 实验链。

本机工作台：[打开对局实验室](http://127.0.0.1:8016/#analysis)。载入真实 BP 前缀或逐手选禁，运行双方对局分析。实验模型尚未完成独立质量验收。画像页继续支持以下操作。先选战队、精确版本和日期，再查看选手档案、双人对比、BP 倾向与数据来源。新版使用深色分析界面，提供英雄视觉、五维雷达、选手矩阵与双人叠加对比。页面连接真实数据库；首次在其他机器使用需按下文准备数据并启动服务。

## 当前状态

- 已实现：数据契约、PostgreSQL 地基、职业采集器、断点与限流、画像计算与 HTTP 查询，可追溯的历史位置推断与逐赛事层级映射，以及同源 GUI、公开目录和进程管理。
- 验证：本轮 493 项全仓 Python 测试通过，11 项前端行为测试通过。真实 HTTP 返回 8 个下一手候选、3 个当前手方案与7类剧本分支，已实际操作浏览器。
- 真实结果：4 支队伍、22 名选手的 110 个维度中，73 个可以计算百分位，37 个按冻结契约降级。公开查询插入训练赛前后保持一致。
- 工作台目录：90 天公开 CM 窗口内 319 支战队、3 个精确版本、1,501 场比赛；全部有选手详情。
- 数据快照：2026-09-22 本地库共 211,311 场比赛，1,562 场有选手详情。数据库和原始数据不随代码发布。
- 已接入：实验 Value、条件概率 Policy、当前手搜索 Advise、七体系 Playbook。Policy 开发评估 Top1 为 9.95%，阶段频率基准为 8.71%。Value 7.41e 提升很弱；7.41d 劣于基准，不进入决策搜索。
- 尚待补齐：新的独立质量验收集、神经序列模型、历史 CM 池快照、位置真值与准确率、更多赛事证据、天梯与回放、48 小时采集验收。

[对局实验室验收](docs/reviews/2026-09-22-analysis-lab.html) · [前端重做与能力核查](docs/reviews/2026-09-22-workbench-redesign.html) · [上一版工作台验收记录](docs/reviews/2026-09-22-workbench.html) · [画像来源报告](docs/reviews/2026-09-22-m3-provenance.html) · [旧画像快照](docs/reviews/2026-09-22-m3-profile.html) · [采集报告](docs/reviews/2026-09-22-m3-collector.html) · [频率基线报告](docs/reviews/2026-09-22-m3-frequency.html) · [交接说明](docs/superpowers/HANDOFF.md)

HTML 报告可下载后直接用浏览器打开，不需要启动服务。GitHub 页面默认显示 HTML 源码。

## 本地运行

需要 Python 3.12 或更高版本，以及 PostgreSQL 13 或更高版本。仓库提供 PostgreSQL 16 的 Docker Compose 配置。

```bash
git clone https://github.com/Sin10874/mcndotaga.git
cd mcndotaga
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,ingest]'
cp .env.example .env
docker compose up -d --wait db
export DATABASE_URL=postgresql://dota:dota@localhost:5432/dota
export TEST_DATABASE_URL=postgresql://dota:dota@localhost:5432/dota_test
python -m db.migrate
```

示例数据库账号仅用于本地开发。Python 命令读取环境变量，`.env` 不会由所有模块自动加载。

首次初始化常量与画像权重：

```bash
python - <<'PY'
import os
import psycopg
from constants.load import load_constants
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    load_constants(conn)
PY
python -m analysis.profile_bootstrap
python -m ingest.collector --once --max-pages 2 --max-details 20 --daily-limit 2400
python scripts/workbench.py start
```

打开 `http://127.0.0.1:8016/` 即可操作。前端使用原生 HTML、CSS 和 JavaScript，无需 Node 构建；由 Python 同源提供页面与接口。

```bash
python scripts/workbench.py status
python scripts/workbench.py stop
```

启动器只管理当前工作区的 GUI 进程，不启动采集或数据库。停止 GUI 不会停止 PostgreSQL。JSON 可在页面预览并复制保存。英雄图像来自 Valve 官方 CDN，无法载入时保留名称和分析数据。对局实验室提供完整的实验计算路径，样本或模型不足的模块返回明确降级。开发复用过留出集，因此不能把当前结果称为独立验收或成熟预测。

目录接口：`GET /v1/catalog`。画像接口：`GET /v1/profile?team_id=...&patch=...&as_of=YYYY-MM-DD&sources=pro_match`。新数据库需要先收集真实比赛，不能把空库或样本不足视为完整画像。

`GET /health` 仅检查进程存活。画像 API 默认只监听 `127.0.0.1`。

## 位置和赛事来源

位置使用整队分路及赛后经济的保守规则，仅为历史画像分组，不是提供方位置或真值，不能作为同场赛前 BP 特征。有歧义时保留未知。原始 `match_players.position` 不变，推断存于旁表；身份、分路、经济或解析状态变化后，旧标注自动失效。

赛事层级由 [明确赛事 ID 的证据配置](config/league_tiers.json) 提供，保留原始 `leagues.tier`，不统一转换 `premium/professional`。当前配置包含 5 个已核定赛事；默认会验证每个 ID 和名称，任意一项缺失都停止整次写入。新库需要先采集这些赛事，或通过函数参数提供针对该库审定的配置。

```bash
python -m analysis.profile_annotations --since 2026-06-25
python -m analysis.profile_annotations --since 2026-06-25 --apply
```

第一条只预览，第二条才写入旁表；需设置 `DATABASE_URL` 并先运行迁移。日期示例对应本次 90 天快照，后续根据分析窗口调整。重复相同输入不产生重复记录或刷新生成时间。新增采集数据后需显式刷新标注，API 不自动写库。

## 测试与复算

完整 M1 验收依赖约 506 MB 的公开历史子集。数据不在 Git 中，先下载再测试：

```bash
python -m ingest.kaggle_subset
TEST_DATABASE_URL=postgresql://dota:dota@localhost:5432/dota_test python -m pytest -q
```

测试必须显式设置 `TEST_DATABASE_URL`，缺失时直接中止，不使用默认连接，也不从 `DATABASE_URL` 推导。测试会删除并重建指定测试库，名称必须以 `_test` 结尾。不要指向开发库；并行运行使用不同测试库名。缺少历史数据时完整验收会失败，不会假装通过。

前端可视化行为测试不依赖数据库，仅需 Node.js：

```bash
node --test tests/web/*.test.cjs
```

原阶段频率脚本保留独立评估用途；在线 Policy 的模型与基准由以下新增训练脚本生成。需要包含历史比赛的数据库及 `psql`：

```bash
python scripts/evaluate_frequency_baseline.py --dsn "$DATABASE_URL"
```

原始数据、数据库卷、凭据、回放、运行日志均留在本地。仓库中的常量缓存是公开测试快照，审阅报告只包含公开比赛的筛选结果与聚合指标。

## 对局实验室与模型复算

当前本机 PostgreSQL 独占 `127.0.0.1:55439`，开发库为 `mcndotaga_dev`。上方 Docker 的 5432 示例用于新机器，当前工作区不要使用它。

模型与原始数据留在本机，GitHub 不携带数据库或模型计数表。新机器须先准备真实数据，再训练；缺少模型时接口返回 `insufficient_data`，不会使用内置样例假装成功。

```bash
export DATABASE_URL=postgresql://dota@127.0.0.1:55439/mcndotaga_dev
.venv/bin/python scripts/train_value_model.py --patch 7.41e --as-of 2026-09-22
.venv/bin/python scripts/train_policy_model.py --until 2026-09-22T13:58:56.732247+00:00
.venv/bin/python scripts/workbench.py start
```

训练只读数据库，写入 `data/models/`。参数固定，不搜索留出集。此次修复前已查看过同一尾段，报告因此保留开发复用标记。重复运行不能把它升级为独立验证；正式质量门需要新的未读样本。

新增接口：

- `GET /v1/analysis/catalog`：真实 BP 输入和唯一 CM 顺序模板。
- `POST /v1/analysis`：双方摘要及四个分析模块的实验报告。
- `POST /v1/value`、`POST /v1/policy/next`、`POST /v1/advise`：冻结资源响应。
- `GET /v1/playbook` 与 `POST /v1/playbook`：剧本。须明确 `us_side` 和 `first_pick_team`，0 为天辉，1 为夜魇。系列赛已知先手按真实队伍身份映射，未知局不猜。

`/v1/analysis` 最小请求示例：

```json
{"us":9256405,"them":7119388,"us_side":0,"patch":"7.41e","as_of":"2026-09-22","first_pick_team":0,"draft":[],"sources":["pro_match"],"top_n":8}
```

观察日期采用 UTC。实时建议仅在当前手属于我方时产生；赛前剧本单独从空 BP 开始规划。所有入口拒绝训练赛来源，API 不训练、不采集、不写库。
