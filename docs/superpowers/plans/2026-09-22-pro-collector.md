# 职业比赛增量采集实施计划

日期：2026-09-22。基线：`2c244a7`，工作目录为现有 `plan-1-contract-and-data-foundation` worktree。

## 本轮目标

恢复并复验 M0、M1，在原规格第 5.2、5.3、14、15 节之上实现 M2 的职业比赛采集路径。用真实 OpenDota 请求把战队、选手、逐手 BP、解析统计接到 PostgreSQL；提供可重复运行、可停止的命令和中文静态状态报告。48 小时连续运行须真实观测，本轮代码与短时实测不得代替该验收。

本轮不扩展模型、六维画像 API、天梯种子采集、训练赛回放导入和发布。接口响应契约、冻结常量和历史顺序族保持现有定义。

## 写入边界

- `ingest/opendota.py`、`ingest/collector_state.py`、`ingest/live_store.py`、`ingest/collector.py`：HTTP、重试规则、详情入库、调度 CLI。
- `db/migrations/002_collector.sql`：仅在需要持久化重试或回填进度时新增表或字段，不重写 001。
- `tests/collector/`：有针对性的行为测试，数据库使用本轮独立测试库。
- `ingest/collector_report.py`、`tests/test_collector_report.py`：只读状态汇总与自包含 HTML。
- 本计划、`docs/superpowers/HANDOFF.md`、`docs/reviews/`：说明、证据、续做入口。

## 行为与验收

1. `/proMatches` 分页发现，默认限制近 90 天。分页必须持久化，单轮页数或配额耗尽后重启能继续，中途失败不得把高水位推进到缺页之后。
2. 单场详情以事务写入队伍、赛事、选手与比赛，BP 先删后写。历史合法族沿用 `ingest.order_families`。异常仍入库并显式记录。缺 BP 不得标成 complete，也不得抹掉已完整的 BP。
3. 公共采集不得覆盖或读取训练赛内容。已有 scrim 的相同 match_id 必须拒绝。阵容只用当场选手；未知位置保留 NULL。匿名账号保留 10 个 slot，Dire 原始 slot 128 到 132 映射为 5 到 9。
4. 重试以首次发现为 t0：2、8、20、44 小时，随后每 24 小时，168 小时仍缺 BP 标为 unavailable，720 小时最后一次补抓，可恢复 complete。网络故障与缺 BP 分开记录，不将 HTTP 错误冒充 unavailable。
5. 请求间隔和持久化计数同时限制每分钟与每日预算，重启不能清零。429 遵守 Retry-After，错误请求也消耗预算。使用请求超时和有限工作批次；SIGINT 可退出。
6. 保留真实统计缺失。解析状态与 stats_available 由实际 parsed 数据决定；承伤总量由字典求和，一塔事件排除兵营，首肉山由 objectives 推导。不要推测 1 到 5 号位。
7. 每轮可查看 BP 最新滞后、pending、重试成功率、UTC 日配额消耗、unavailable 比率及超过 5% 的告警。HTML 区分历史数据、增量数据与尚未实现的分析能力。
8. 先跑行为 RED，再实现 GREEN。覆盖状态机时间边界、源隔离、幂等、分页中断恢复、限流、错误重试与匿名选手。主控重跑旧套件和新套件，并对真实公共 API 做有上限的短时采集。

## 交付证据

全量测试、契约校验、真实 API 的请求结果与数据库读回、一次重启后的无重复验证、中文 HTML 浏览器检查。报告明确记录连续 48 小时验收仍未完成。

## 基线审查发现与必要前置修复

- `ingest/load_bootstrap.py` 与对应测试：历史导入原来会重写其他来源或 live 补齐的场次。增加写入所有权条件及受保护场次计数，保护范围包含比赛行、BP 子表和异常记录。
- `ingest/order_families.py` 与对应测试：原判定会接受重复 ord 且缺另一手的输入。要求 ord 为整数、唯一且完整，仍接受顺序打乱但 ord 齐全的输入。
- 老 pending 不直接灌入采集队列。职业采集从近 90 天列表发现，仅对本次发现的比赛排队；保留历史缺失记录原有状态。
