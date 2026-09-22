# 画像位置与赛事层级来源补齐

基线：GitHub main 的 `655d140`，已有 335 项测试通过。实施分支：`codex/profile-data-provenance`。

## 目标与边界

使用现有分路、补刀和经济记录，为历史画像提供可复算的位置推断；按明确赛事 ID 和官方证据建立本地赛事层级。保留原始 position 和 leagues.tier，不将推断冒充提供方字段，不改冻结 Profile 响应形状。

位置推断使用赛后信息，仅用于已完成历史比赛的画像归组。不能作为同场赛前 BP 预测特征。覆盖率不能当作准确率，本库没有位置真值。

## 文件所有权

- 位置实施者：`analysis/position_inference.py`、`tests/analysis/test_position_inference.py`。
- 来源存储实施者：`db/migrations/003_profile_annotations.sql`、`analysis/profile_annotations.py`、`tests/analysis/test_profile_annotations.py`。
- 主控：本计划、`config/league_tiers.json`、repository 与对应测试、报告、文档、真实验收和 GitHub 推送。
- 审查者只读。不得写共享 conftest、契约、旧采集器或其他实施者文件。

## 位置纯函数接口

`METHOD_VERSION = "team_lane_economy_v1"`。

`team_input_fingerprint(match: dict, rows: list[dict]) -> str`：SHA-256，稳定排序的规范 JSON。包含 match_id、data_source、parse_state，以及每行 match_id、player_slot、team、account_id、hero_id、position、stats_available、lane_role、last_hits、net_worth、gpm、xpm。整队输入变化必须使标注失效；不依赖名称和无关字段。

`infer_team_positions(match: dict, rows: list[dict]) -> list[dict]`：按 slot 稳定排序。每项含 player_slot、position（整数或 NULL）、reason（中文）、method_version、evidence_level（规则名或 unknown）、evidence（使用的分路与经济证据）。不得修改输入。

保守 v1 规则：

1. 单场单侧恰好五人，slot 唯一且侧别一致，full parse，全部 stats_available，四项经济均为有限非负数。异常时整组未知。
2. 已有任何 position 时停止该组启发式，保留原始字段由查询层优先使用，不把已有值称为新 provider 证据。
3. lane_role 必须为 1、2、3，整队形状恰好为 2、1、2。其他形状整组未知。
4. 唯一中路推断 2 号位。优势路、劣势路各自两人中，补刀、净资产、GPM 三项全部严格更高者可推断为 1、3 号位；三项队内名次分别须前 3、前 4。并列、交叉或不满足门槛则相应位置保留未知。
5. 1、2、3 均确定后，剩余两人仅在四项经济排序完全一致时分为 4、5 号位，否则两人都未知。
6. evidence_level 使用 unique_mid、core_economy、support_economy、unknown，不输出伪装成概率的 confidence。

## 存储与防过期

新增两个旁表，保持原始字段不变：

- `profile_position_annotations`：match_id、team 为主键，保存 input_fingerprint、method_version、annotations JSONB、created_at。外键只指向 matches，避免旧采集器替换 match_players 时无意删除来源记录；查询时必须重新核对整队 fingerprint，过期标注不生效。
- `profile_league_tiers`：league_id 为主键，保存 verified_name、canonical_tier、source_url、method_version、evidence JSONB、reviewed_at。不修改 leagues.tier。

写入函数 `apply_profile_annotations(conn, *, sources=("pro_match",), since=None, tier_config=None, dry_run=True) -> dict`。拒绝 scrim；读取公共视图，调用者控制事务，默认 dry run 不写库。配置默认读取 `config/league_tiers.json`，验证重复 ID、已存在 ID、赛事名称一致、合法层级、HTTPS 证据 URL、reviewed_at、method_version。任何条目无效必须在写入前失败，不能部分应用。重复相同输入不更新 created_at，也不增加记录。

CLI 读取 DATABASE_URL，默认只报告 dry run，传 `--apply` 才提交；支持 `--since YYYY-MM-DD`。不改迁移器与依赖。

## 查询整合

repository 原始 canonical tier 优先；原标签非 canonical 时，只有 verified_name 与当前赛事名一致才采用旁表层级。未标注保持未知，禁止全局将 premium/professional 替换为 tier1/tier2。

加载选手和位置旁表后按 match/team 重新计算 fingerprint，只接受当前 METHOD_VERSION、正确指纹和完整合法的每 slot 标注。原始 position 非空优先；推断只补 NULL。校验每个 slot、位置范围和非空位置唯一性。输入身份、统计或同队选手变化后旧结果整体失效。内部保留 position_source，报告明确历史规则推断；冻结 HTTP body 不添加新字段。

## 验收

先业务 RED，再 GREEN。位置须覆盖经济交叉、并列、非标准分路、缺解析、混队、slot 反转、匿名和已有位置。存储须覆盖 dry run、事务原子性、幂等、scrim 不写、来源字段保留、非法配置。查询须验证过期指纹失效、原值优先、赛事名称变化、未审赛事未知。

最后独立审查、全量测试、开发库 dry run 与应用读回、4 支队伍真实 HTTP 和冻结契约检查，生成新静态报告。原报告作为旧快照保留。首批 main 已发布；新增内容验收后提交并推到实施分支，不自动合并到 main。
