# MCNDOTAGA

DOTA2 职业比赛 BP 与战队画像分析系统。当前提供数据采集、历史手序校验、公开画像 API 和可复算的频率基线评估。

## 当前状态

- 已实现：数据契约、PostgreSQL 地基、职业采集器、断点与限流、画像计算与 HTTP 查询，可追溯的历史位置推断与逐赛事层级映射。
- 已验证：393 项全量测试通过。
- 真实结果：4 支队伍、22 名选手的 110 个维度中，73 个可以计算百分位，37 个按冻结契约降级。公开查询插入训练赛前后保持一致。
- 数据快照：2026-09-22 本地库共 211,311 场比赛，1,562 场有选手详情。数据库和原始数据不随代码发布。
- 尚待补齐：位置真值和推断准确率验收、更多赛事证据、48 小时采集验收、Value、剧本、产品界面及序列模型。

[最新画像报告](docs/reviews/2026-09-22-m3-provenance.html) · [旧画像快照](docs/reviews/2026-09-22-m3-profile.html) · [采集报告](docs/reviews/2026-09-22-m3-collector.html) · [频率基线报告](docs/reviews/2026-09-22-m3-frequency.html) · [交接说明](docs/superpowers/HANDOFF.md)

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
python -m analysis.server --port 8016
```

接口：`GET /v1/profile?team_id=...&patch=...&as_of=YYYY-MM-DD&sources=pro_match`。新数据库需要先收集真实比赛，不能把空库或样本不足视为完整画像。

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

测试会删除并重建指定测试库，名称必须以 `_test` 结尾。不要指向开发库；并行运行使用不同测试库名。缺少历史数据时完整验收会失败，不会假装通过。

阶段频率基线采用严格时间切分，只用于评估，尚未接入推理服务。需要包含历史比赛的数据库及 `psql`：

```bash
python scripts/evaluate_frequency_baseline.py --dsn "$DATABASE_URL"
```

原始数据、数据库卷、凭据、回放、运行日志均留在本地。仓库中的常量缓存是公开测试快照，审阅报告只包含公开比赛的筛选结果与聚合指标。
