# 交接说明

更新：2026-09-22。当前已推进到 M3 画像来源补齐，真实五维已有可计算结果。旧快照保留，最新位置与测试结果以 `2026-09-22-m3-provenance.json` 为准。

## 工作区与入口

工作目录：`/Users/xinzechao/MCNDOTAGA/.worktrees/plan-1-contract-and-data-foundation`。
分支：`codex/profile-data-provenance`，基线 `655d140`。首次 GitHub 检查点已推送到 `Sin10874/mcndotaga` 的 main，包含职业采集、画像后端、335 项测试和复算脚本。本轮位置与赛事来源在独立分支验收和推送，没有部署。
主目录 `main` 仍是 `c486c0d` 的早期骨架。续做必须进入上述 worktree，先检查未提交文件，不能从 main 误建空白分支覆盖成果。

依次阅读：

1. `docs/superpowers/specs/2026-09-16-dota2-banpick-analysis-system-design.md`。
2. `docs/superpowers/plans/2026-09-22-pro-collector.md`。
3. `docs/superpowers/plans/2026-09-22-profile-engine.md`，这是本轮画像口径的权威补充。
4. `docs/superpowers/plans/2026-09-22-profile-provenance.md` 与 `docs/reviews/2026-09-22-m3-provenance.html`、`2026-09-22-m3-provenance.json`。
5. `docs/reviews/2026-09-22-m3-collector.html`、`2026-09-22-m3-frequency.html`。

## 当前完成程度

| 范围 | 实际状态 |
|---|---|
| M0 契约 | 五资源 schema、17 fixtures 和 OpenAPI 已验证；TS 仍只有枚举与 Resource 联合 |
| M1 地基 | 历史 211,051 场、4,772,342 手 BP；127 英雄、501 道具、118 版本，保留原始数据 |
| M2 职业采集 | 近 90 天发现、增量、详情、断点、持久配额和重试已实现，真实批量补采通过；48 小时连续验收未完成 |
| M2 天梯与 M2b 回放 | 天梯采集、回放下载导入与解析未实现 |
| M3 画像 | 公开画像、只读 HTTP、来源旁表与静态报告已实现；4 队 22 人的 110 个维度中 73 个可计算百分位、37 个明确降级；推断准确率尚无真值验证 |
| M4/M5 | Value 和剧本 API 未实现 |
| M6 | 产品界面未实现，目前提供中文静态 review |
| M7/M8 | 频率基线完成一次独立时间留出评估；未产品化，序列模型未实现，不标记里程碑完成 |
| M9/M10 | 决策层与部署未实现 |

## 数据快照

快照时间：2026-09-22T08:05:31.147438+00:00。

全开发库：211,311 场比赛，4,778,246 手 BP，337 支队伍，2,268 个可识别账号，15,620 条选手参赛记录。
已有选手详情 1,562 场，上轮为 21 场。原始位置 NULL 的参赛记录仍为 15,620 条，没有改写来源字段。本轮用分路与赛后经济推断位置，记录在独立旁表，不从 slot、英雄或当前 roster 猜位置。
近 90 天公共比赛 1,562 场，其中有详情 1,562 场。全队列状态：`{"complete": 1514, "non_cm": 47, "pending": 1}`，本次读回已到期 0 条。剩余重试：`[{"match_id": 8866547078, "next_attempt_at": "2026-09-22T16:34:48.974300+08:00", "status": "pending"}]`。
UTC 当日请求 1,657 次，本轮预算 2,400。账本累计限流 67 次、其他失败尝试 2 次；失败尝试不等于最终失败比赛，详情见 m3-validation.json。

历史 2,048 场手序异常和 6,046 场 pending 是旧库口径，未因此全量排入实时重试队列。比赛总量不等于可计算画像的统计样本量。

本轮来源处理从 2026-06-25 起扫描 3,098 个单侧队伍组，写入同数位置标注与 5 个赛事分类。位置标注内共 15,490 条选手行，其中 11,322 条有推断位置、4,168 条未知。按画像的 7.41 大版本、公开 CM 和 90 天约束过滤后，实际候选池为 1,501 场、15,010 条选手行，10,977 条推断位置有效、4,033 条未知。两个分母不同，不能混用；覆盖率不代表准确率。

## 本轮新增代码与行为

- `analysis/profile_repository.py`：公共 view 批量查询，同 base_version 的 90 UTC 自然日窗口，目标限定字母版本，匿名行保留，不读取训练赛。
- `analysis/profile_engine.py`：英雄池、五个位置维度、六项英雄原型和当前顺序族 BP 倾向。目标阵容来自窗口内真实参赛记录，个人统计可含所选来源的跨队出场。
- `analysis/profile_service.py`：严格 query、source、日期和响应校验。每次请求开启 repeatable read/read only，SQL 最长 15 秒；不泄露连接或数据库异常。
- `analysis/server.py`：`GET /v1/profile`，严格错误信封；`/health` 只说明进程存活，不冒充数据库健康。默认只监听 127.0.0.1。
- `analysis/profile_bootstrap.py`：显式补齐 18 个规格权重和 min_sample_n，保留用户已有配置；开发库已初始化。API 请求不会偷偷补配置。
- `analysis/profile_report.py`：真实 HTTP 结果生成自包含中文 HTML，可展开选手，包含英雄名称、降级原因和 BP 分母。
- `ingest/opendota.py`：真实补采发现无有效 Retry-After 时旧回退 1 秒会反复撞限。本轮改成 60 秒、120 秒、240 秒的请求内退避，并持久保存冷却；正值有效 header 仍优先。7 个行为 RED 转 GREEN，独立审查通过。
- 采集报告文案随 M3 更新，旧 HTML 快照不重写。
- `analysis/position_inference.py`：保守整队 2/1/2 分路规则，核心经济三项严格领先并满足队内名次，辅助仅在四项经济同序时区分。并列、交叉、缺解析或已有原始位置均按计划拒绝或保留未知。
- `analysis/profile_annotations.py` 与迁移 `003_profile_annotations.sql`：默认 dry run，显式 apply 写旁表；保存方法版本、逐行原因和证据，重复输入不刷新时间，调用者控制事务。
- `config/league_tiers.json`：5 个逐 ID 核定的赛事，官方 URL 与分类理由随配置保存。PGL Wallachia Season 9 暂无明确 tier1/tier2 项目边界，保留未知。
- repository 只使用完整合法且当前指纹匹配的位置标注，原始位置优先。赛事名变化后旧 tier 旁表不生效，原始 canonical tier 始终优先。

## 不能丢失的画像口径

完整计算约定见 M3 实施计划，特别注意：

- 有效英雄池是 signature 与 comfortable 的并集；不能用全部出现过的英雄，否则 presence 会恒为 1。只在合法完整 CM BP 中观察对手 ban，分母为零或有效池为空时整体 insufficient_data，不填 0。
- insufficient_data 使用 HTTP 200 的 ErrorEnvelope，这是冻结契约。位置未知时五维为 insufficient_samples；有位置但解析统计缺失时为 stat_unavailable。
- premium/professional 不是 tier1/tier2 的同义词。只允许逐赛事官方证据映射，未审赛事保留未知；未知目标层级的同侪只允许 tier1/tier2。
- 位置使用赛后分路与经济推断，仅用于历史画像，不能当成真值或同场赛前 BP 特征。缺位置真值时不得宣称推断准确率；即使有位置，样本不足仍须降级。
- 同侪限定位置、base_version、层级、90 天、full parse、stats_available 与来源。匿名行能参与单场对线/战斗/地图统计，不能用于跨场英雄池或 tempo。
- hero_pool/map_vision/tempo 用各自来源权重检查至少 30 个加权样本。tempo 三个子指标分别统计实际可算账号行的权重，再取最小值；匿名行不能凑门槛。
- 近似相等百分位不得同时计入 lower 与 equal。多子指标中位秩百分位等权合成，0.5 向上取整；必需子指标缺失则整维降级。
- BP 倾向只接受当前 spec_6_0_24 族；首阶段 ord 0 至 6，本队第一手 pick 不要求本队拿全局先手。绝不能把它套到所有历史顺序族。
- sources_used 包含实际贡献目标队伍或目标选手的公开来源；coverage 仍只算目标队伍。训练赛来源始终拒绝。

## 本轮验证证据

首批 GitHub 检查点复验 335 passed，346.85 秒。来源补齐后全量 393 passed，254.17 秒，独立测试库 `mcndotaga_provenance_final_test`，日志 `data/m3-provenance-final-pytest.log`。
本轮再次实际启动 CLI HTTP 服务，请求 4 支真实队伍，均返回通过冻结 schema 和不变式的画像。共 22 名选手，73 个维度有百分位、37 个仍为 insufficient_samples。Level UP esports 的 5 人、Klim Sani4 的 6 人可计算完整五维；其余选手只提供当前有证据的维度。拒绝训练赛、未知队伍、超早日期三条实际 HTTP 错误路径通过，临时服务退出码 0。
开发库 dry run 没有写入，apply 后原始 position 和 tier 的全表指纹不变；再次 apply 的位置与赛事写入均为 0，created_at 指纹不变。独立只读代码审查未发现阻塞项，并补充了合法 Dire 侧的回归。
真实 DB/HTTP 测试额外插入 40 场 scrim 后，公开画像响应字节完全相同。匿名同侪、跨来源、权重、空池和非法 BP 边界已有回归。

频率基线使用 15,196 场当前合法手序族，12,156 训练、3,040 留出，共 72,960 个下一手预测。Top5：随机精确期望 4.34%，全局频率 7.47%，阶段加 pick/ban 分层 12.26%。前 8 手 Top1/3 和 7.41f Top1 存在负结果，不隐去。
按时间切分且同刻不跨界，留出集未用于调参；主控手算反序样本、随机期望并复核各分母。当前 127 英雄词表未重建历史当时可选池，所以不是严格的历史线上回放。脚本在 `scripts/evaluate_frequency_baseline.py`，脱敏结果在 review；未接入推理 API。

自动浏览器视觉未验证。此前工具拒绝本地 file URL，没有换浏览器或代理绕过。静态 HTML 的数据、转义、结构与禁用排版做机械检查；这些不等于视觉验收。48 小时稳定性、位置推断准确率与生产部署尚未验收。

## 运行命令

Homebrew PostgreSQL 数据保存在本工作区 `data/postgres`，端口 55439，仅监听本机；data 和原始 Kaggle 文件均不提交。本轮临时 API、采集进程和独占数据库均已停止，后续按下列命令恢复。

```bash
cd /Users/xinzechao/MCNDOTAGA/.worktrees/plan-1-contract-and-data-foundation
/opt/homebrew/bin/pg_ctl -D "$PWD/data/postgres" -o '-p 55439 -k /tmp -h 127.0.0.1' -l "$PWD/data/postgres.log" start
export DATABASE_URL=postgresql://dota@127.0.0.1:55439/mcndotaga_dev
.venv/bin/python -m db.migrate
.venv/bin/python -m analysis.profile_bootstrap
.venv/bin/python -m analysis.profile_annotations --since 2026-06-25
.venv/bin/python -m analysis.profile_annotations --since 2026-06-25 --apply
.venv/bin/python -m analysis.server --port 8016
```

在另一终端可查询 `http://127.0.0.1:8016/v1/profile?team_id=7119388&patch=7.41e&as_of=2026-09-22&sources=pro_match`。HTTP 示例是历史快照，不能默认改成当前新版本就有足够样本。

```bash
DATABASE_URL=postgresql://dota@127.0.0.1:55439/mcndotaga_dev .venv/bin/python -m ingest.collector --once --max-pages 5 --max-details 100 --daily-limit 2400
TEST_DATABASE_URL=postgresql://dota@127.0.0.1:55439/mcndotaga_provenance_final_test .venv/bin/python -m pytest -q
/opt/homebrew/bin/pg_ctl -D "$PWD/data/postgres" stop -m fast
```

测试库会 DROP 重建，必须用 `_test` 后缀，绝不能指向 dev。并行测试用不同库名，不要依赖未显式设置的默认 localhost:5432 测试库。
常驻采集去掉 `--once`，默认间隔 300 秒。本轮没有留下常驻任务。先明确观测周期再做 48 小时真实验收，不能用假时钟测试替代。
新采集记录需要显式重跑来源标注，API 不自动写入。默认赛事配置会验证全部 5 个 ID 与名称，库内缺少任一赛事时整次失败，不会部分写入；新库应先采集赛事或向函数传入针对该库核定的配置。

## 后续顺序

1. 按队列到期时间补抓剩余详情，验证本轮限流退避的长期效果，安排完整 48 小时观测。
2. 建立可核查的位置真值样本，验证现有推断并扩大逐赛事证据覆盖。不要为凑百分位降低 30 个加权样本门槛，也不要把覆盖率写成准确率。旧数据缺口审计保留在 `docs/reviews/2026-09-22-m3-data-gaps.json`，它是旁表补齐前的快照。
3. 继续 Value、剧本与产品界面，按冻结契约拆分不重叠写集。频率基线先产品化并保留留出测试，再推进序列模型。
4. 回放与自我复盘独立推进授权边界。`allow_scrim=True` 尚未连真实身份，不得开放对手侧或无身份的私密读取。

历史手序入口、常量映射与版本日期位置保持权威：只从 `ingest/order_families.py` 用 `family_for/is_legal/resolve_in`；版本 dates 不能排序；英雄新增须检查 token index 跨快照稳定性。上轮 bootstrap 防覆盖和 ord 校验修复必须保留。
