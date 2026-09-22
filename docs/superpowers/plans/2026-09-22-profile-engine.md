# M3 画像接口并行实施计划

日期：2026-09-22。延续现有 worktree 与未提交成果，不改冻结响应契约。

## 交付范围

实现真实 PostgreSQL 驱动的 `GET /v1/profile`、严格参数和来源校验、六维画像引擎、中文静态 review。并行补齐职业详情。M3 的数学行为、真实 HTTP 与数据缺失行为分别验收；不将 48 小时采集验收或 M6 产品界面记为完成。

## 文件所有权

- 计算实施者：`analysis/profile_engine.py`、`tests/analysis/test_profile_engine.py`。
- 查询实施者：`analysis/profile_repository.py`、`tests/analysis/test_profile_repository.py`。
- 主控：`analysis/profile_service.py`、`analysis/server.py`、`tests/analysis/test_profile_service.py`、`tests/analysis/test_profile_http.py`、`analysis/profile_report.py`、对应报告测试、本计划与交接及 review 文件。
- 主控补充初始化：`analysis/profile_bootstrap.py`、`tests/analysis/test_profile_bootstrap.py`，只补规格权重和最低样本量，保留既有配置。
- 采集操作员：仅 `data/` 下本轮日志与脱敏操作汇总，不改源代码。

不要写共享 conftest、契约、依赖或旧实现文件。测试使用各自独立 `_test` 库。

## 冻结内部接口

`analysis.profile_repository.load_profile_dataset(conn, *, team_id: int, patch: str, as_of: date, sources: list[str]) -> dict`。

未知战队或版本抛 `ProfileNotFound(ValueError)`；非法参数抛 `ValueError`；拒绝训练赛用既有 `SourceNotAllowed`。SQL 只从公共视图取比赛，不访问 scrim。调用者开启只读、可重复读事务。

数据字典：

- `team_id`、`patch`、`base_version`、`as_of`（ISO 日期字符串）、`sources`（校验后的请求来源）。
- `matches`：同 base_version、近 90 个 UTC 自然日、截止 as_of 当日末尾且实际发生过的全部公共候选比赛。每项包含 matches 原字段，另有 `patch`（version_name）、`base_version`、`tier`（仅 tier1/tier2/qualifier/other 算已知，其他字符串规范化为 NULL）、`players`（完整 match_players 字段及 `name`）、`draft`（ord/is_pick/team/hero_id）。禁止把 premium/professional 自动映射成 tier1/tier2。
- `hero_roles`：整数 hero_id 到 roles 列表。
- `weights`：`metric -> data_source -> float`，来自 metric_weights。
- `min_sample_n`：读取配置且不低于冻结最低值 30。

repository 仅按来源、时间、base_version、职业 CM 过滤。pro_match 的 game_mode 仅允许 2 或 NULL；pub_match 不要求 CM。数据必须可用于同侪过滤，不能仅取目标战队。嵌套 players/draft 必须按 slot/ord 稳定排序，空数组保留。所有计数只算真实存在记录。

`analysis.profile_engine.build_profile(dataset: dict) -> dict` 返回冻结 Profile 形状，并定义 ProfileInsufficientData(ValueError)。引擎不连接数据库、不调用网络、不写文件。

## 计算口径

响应默认呈现近 90 天窗口，目标选手数据限定请求的字母子版本。同侪分母使用同 base_version，绝不能把分子放宽。90 天含 as_of 当日，左边界为 as_of 减 89 日的 UTC 零点。as_of 缺省为 UTC 今天，不接受未来日期。

阵容只来自窗口内目标战队实际参赛行，不读取当前 roster 反推历史。只输出 account_id 非空且大于零的实际选手，不合并匿名账号。英雄池为该选手在所选公开来源、目标字母子版本中的实际出场，不限其当时所属队伍；阵容归属仍由目标队伍参赛决定。

- 签名与熟练英雄严格按规格阈值分类，互斥；pct 采用四舍五入，0.5 向上。
- presence_pick_rate 的池为 signature 与 comfortable 的并集，即有效英雄集合。不能用全部已出场英雄，否则比率会恒为 1。有效池为空时同样返回整体 insufficient_data。仅合法完整 CM BP 且有对手禁用信息的参赛场次可计分母；分母为零且该选手确有参赛记录时，抛 ProfileInsufficientData(ValueError)，接口返回 insufficient_data；冻结字段不允许 null，不能填 0 冒充已观测比率。不得把缺 BP 当作英雄全部放出。
- hero_archetype 使用现有 constants.archetypes.archetypes_for 与英雄出场频率，加权后归一化。现有 app_config_kv 只存代码出处，保留这一已知延迟，不声称配置可执行。
- role 仅取目标字母版本实际记录的已知 position 众数；并列或全部未知返回 NULL。不从 slot、英雄或当前 roster 猜位置。
- coverage 按目标队伍的目标字母版本比赛计数。n_stat_available 为有该队选手可用统计的去重场次；n_position_unknown 为该队至少一条 position NULL 选手记录的去重场次。两来源始终存在，未请求来源全部为零。
- 目标无 role 时五个位置维度一律 insufficient_samples，原型仍返回。已知 role 但没有可用 parsed 统计时，相应维度 stat_unavailable。
- 同侪严格限定位置、base_version、层级、90 天、stats_available 和请求来源。目标 tier 使用目标队伍该版本窗口内的已知层级集合；全未知时仅允许 tier1/tier2。同侪不足 30 返回 insufficient_samples。
- 同侪为 match_players 行，输出 n 为实际有效行数，不能以英雄数或战队数代替。

规格列出多个子指标但未定义合成方式，本轮采用如下显式、可测的实现约定，不修改响应形状：每个子指标先计算目标均值在同侪行中的百分位，使用中位秩处理同值；同一维度取各子指标百分位的等权均值，再四舍五入为整数。分母不足或任一必需子指标不可算时整维降级，不把缺值记零。n 取各子指标有效同侪数的最小值。

- hero_pool：有效英雄数；同侪每条行以该账号同位置与同侪窗口内的有效英雄数为值。
- laning：GPM、XPM。
- combat：KDA、击杀参与率、英雄伤害占比、英雄伤害/净经济；3 号位再计承伤占比，4/5 号位再计治疗量。队伍分母仅在五人相关字段完整时计算，零分母为不可算。
- map_vision：obs_placed、sen_placed、observer_kills、sentry_kills、camps_stacked、rune_pickups 六项。
- tempo：近 20 场半衰期 7 场的加权胜率、1 减胜负二项方差、首杀归属率。同侪按账号计算再映射回行，保持同侪行定义。
- hero_pool、map_vision、tempo 应用各自 source 权重，目标与同侪均检查加权样本总量至少 30；laning/combat 无独立权重配置，当前对允许来源等权，不借用其他 metric 的配置。
- BP 倾向仅统计目标队伍当前 spec_6_0_24 族的完整、非异常 CM 比赛。首阶段为 ord 0 至 6；first_pick_freq 是该队自身第一手 pick，不要求该队拿全局先手；ban_by_phase 按绝对 ord 分组，分母为该队实际拥有该 ban 位的比赛数。不混入旧手序族。
- sources_used 列出实际贡献目标战队或目标选手画像的来源，包括这些选手在其他队伍和路人局的记录；不能把只用于同侪的来源冒充目标贡献。coverage 仍按目标队伍计数。

## 接口与验证

首次启动前显式执行 `python -m analysis.profile_bootstrap` 种入18行规格权重；只读HTTP请求不会悄悄创建配置。

服务使用 Python 标准库 HTTP，默认仅监听 127.0.0.1，数据库 DSN 从 DATABASE_URL 读取。只提供 profile 与 health 路径，不加依赖、不写生产服务配置。整体 insufficient_data 按规格返回 HTTP 200 错误信封。异常按冻结 ErrorEnvelope 返回，不泄露 DSN、SQL、内部异常文本。

RED 必须证明业务行为缺失。重点覆盖时间回溯、字母版本与 base_version 区别、跨 tier/未知位置/未 parse 过滤、来源隔离、匿名账号、阈值边界、半衰期、样本不足、BP 顺序族与分母。最后主控运行全量回归、实际 HTTP 请求、冻结 schema 与 check_profile 校验，交付脱敏证据。
