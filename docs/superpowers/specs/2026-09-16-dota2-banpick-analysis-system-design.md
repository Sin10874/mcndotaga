# DOTA2 Ban/Pick 分析系统 — 设计文档

- **日期**：2026-09-16
- **阶段**：Phase A（公开赛事 scouting + 对手剧本集）
- **状态**：设计已确认，待评审

---

## 1. 目标与定位

构建一个面向 DOTA2 职业战队的 **ban/pick 决策支持系统**。它从公开比赛数据、高分段天梯数据和用户导入的训练赛数据中提取情报，产出**按先手/后手分支组织的应对剧本集（Playbook）**，供教练与队长在赛前准备和赛时 BP 中使用。

**双重定位**：

1. **分析工具** —— 结论必须可信、可追溯、可复现。
2. **内容作品** —— 需要有冲击力的洞察与高质量可视化，用于演示与传播。

两者**并行开发**，通过冻结的数据契约解耦。

**核心差异化**：不做通用 BP 推荐。调研已证明「英雄两两交互」类特征在时间外测试上无信号（预注册研究，23,123 场职业比赛，九个交互候选全部收敛到零附近）。唯一被数据支持的方向是**战队与选手上下文**（加入单个战队强度特征即使 log loss 从 0.684 降至 0.657），唯一的技术空白是**顺序感知的下一手预测**（最好的一篇 top-1 仅 19% 且未公开代码）。

---

## 2. 范围

### 2.1 Phase A 范围内

| 编号 | 交付物 |
|---|---|
| A1 | 常量与版本层：英雄/道具常量、**精确到字母子版本**的版本表 |
| A2 | 采集层：常驻进程 + 状态机，采集职业比赛与天梯数据 |
| A3 | 存储层：Postgres 数据模型，含贯穿全链路的**数据隔离标记** |
| A4 | 数据契约 v1：`Value` / `Policy` / `Playbook` / `Profile` / `Advise` 五组接口（§6） |
| A5 | 分析引擎（线 A）：六维能力项、战队画像、交叉分析 |
| A6 | 序列模型（线 C）：顺序感知的下一手预测 + 基线对照 |
| A7 | 剧本集生成器：按先手/后手分支产出应对方案 |
| A8 | Web 可视化：对手画像卡、BP 推荐面板、剧本速查卡 |
| A9 | 最小回放导入：解析本地 `.dem` 提取对局头信息与 BP，用于训练赛入库 |
| A10 | 决策搜索层与 `/v1/advise`（§8⑤）：离线产出 `plans[]`，在线产出整手建议 |

### 2.2 明确不做（Phase B 及以后）

| 项 | 原因 |
|---|---|
| 眼位坐标、团战切分、装备时间线、经济曲线的**解析与展示** | 数据模型预留，Phase A 不实现解析管道 |
| 沟通/语音分析 | 已实验证实 DOTA2 回放内 `VoiceData` 为 0 条（队内语音走 P2P，不经过游戏服务器）。只能自录音频，属独立子系统 |
| 职业选手小号的**全自动**识别 | 属研究课题。Phase A 采用人工种子名单 + 派对图扩展 |
| 自建比赛数据站点、用户系统、多租户 | 演示阶段不需要 |
| 云端常驻部署 | Phase A 本地 Docker；定型后一次性部署演示站 |

### 2.3 路线图位置

**A（本设计）→ B（自家战队内部平台：训练赛深度解析 + 沟通分析 + 自我复盘）→ D（监控职业选手与小号的天梯录像）**

Phase A 的数据模型为 B 与 D 预留接入点（回放事件表、`player_accounts` 的小号标记字段）。

---

## 3. 关键约束（已实测验证的事实）

这些是设计的硬边界，全部于 2026-09-16 实测确认。

### 3.1 数据源

| 事实 | 数值/结论 | 设计影响 |
|---|---|---|
| OpenDota 免密钥可用 | **60 请求/分钟**硬上限（实测第 61 次返回 429） | 采集器必须内置限流器 |
| `picks_bans` 滞后 | 最新比赛缺席约 **2.6 天** | 必须做状态机 + 退避重试，**永不假设"拉到就有"** |
| Valve `GetMatchDetails` | 自 7.36（2024-05）起返回 500，**至今未修** | 唯一官方兜底已失效，无备选来源 |
| STRATZ | 本环境被 Cloudflare Turnstile 全量拦截 | **不作为依赖**。仅作为未来可选增强 |
| Liquipedia MediaWiki API | 1 请求/2 秒；`action=parse` 1 请求/30 秒；自定义 UA + gzip 强制；CC-BY-SA 3.0 | 仅用于战队阵容历史与赛事元数据，需节流 |
| Liquipedia `teamid` | **等于** OpenDota `team_id`（已用 Team Spirit 验证） | 可直接 join |
| Liquipedia `leagueid` | **等于** OpenDota `leagueid` | 可直接 join |

### 3.2 版本数据

| 事实 | 设计影响 |
|---|---|
| Valve 官方补丁 JSON API（免密钥）：`https://www.dota2.com/datafeed/patchnoteslist?language=english`，**118 个版本含字母子版本** | 版本表的主来源 |
| 补丁详情 API：`.../patchnotes?version=7.41f&language=english&game_mode=DOTA_GAMEMODE_ALL`，`version` 必须是**字符串**；**错误也返回 HTTP 200**，必须检查 `success` 字段 | 解析层必须校验 `success` |
| OpenDota `patch` 字段**丢失子版本**（7.41a–f 全部返回 `patch: 60`） | 必须用 `D2-LRG-Metadata/patchdates.json` 按 `start_time` 还原子版本 |
| 回放头 `CDemoFileHeader.demo_version_name` 携带完整版本（如 `"7.41f"`） | 导入的训练赛取此字段为权威版本 |
| 补丁改动是**自然语言字符串**（如 `"from 60% to 65%"`），非数值字段 | Phase A 只做结构化存储与展示，不做数值解析 |

### 3.3 回放

| 事实 | 数值/结论 |
|---|---|
| 下载 URL | `http://replay{cluster}.valve.net/570/{match_id}_{replay_salt}.dem.bz2`，**无需门票** |
| 实际压缩格式 | **Zstandard**（magic `28 b5 2f fd`），**不是 bzip2**，`bzip2 -d` 会失败 |
| 文件大小 | 约 1.3–1.9 MB/游戏分钟；40 分钟局约 50–90 MB 压缩、90–160 MB 解压 |
| 解析速度 | manta（Go）实测 73.1 MiB → **3.12 秒**，约 15–20 局/分钟/核 |
| 语音 | `CSVCMsg_VoiceData` 在 3 个真实回放中**均为 0 条**；`CSVCMsg_VoiceInit` 1 条。**确认不可提取** |
| 文本聊天 / ping / 画线 | **均存在**（`CDOTAUserMsg_ChatMessage` / `LocationPing` / `MapLine`） |

### 3.4 阵容变更

历史胜率必须按**当场实际出场阵容**归属（`match_players` 的 5 个 `account_id`），不能按组织归属。OpenDota 的 `is_current_team_member` 是**当日快照**，历史场景下会错。Liquipedia 的 `joindate`/`inactivedate` 作为校验依据。

---

## 4. 架构与数据流

```
┌─ 三条数据流 ────────────────────────────────────────────────┐
│ 🅐 公开职业比赛      🅑 天梯单排         🅒 训练赛导入        │
│ OpenDota proMatches  OpenDota players    本地 .dem 文件      │
│ + Kaggle 引导数据集  + proPlayers 种子   （永不上 CDN）      │
└──────────┬───────────────┬──────────────────┬───────────────┘
           └───────────────┼──────────────────┘
                           ↓
┌─ 采集层（常驻进程）─────────────────────────────────────────┐
│ 调度器（幂等·可回溯）→ 限流器（60/min）→ 回放下载（Zstd）    │
│ → 解析队列（manta）                                          │
│ BP 状态机：发现 → 取 picks_bans → 未就绪挂起 → 退避重试 → 完成│
└───────────────────────────┬─────────────────────────────────┘
                            ↓
┌─ 存储层（Postgres + 卷）────────────────────────────────────┐
│ matches / draft_actions / match_players / teams / players /  │
│ rosters / patches / data_source(隔离标记)                    │
│ [预留] wards / item_timings / teamfights / gold_curves       │
└───────────────────────────┬─────────────────────────────────┘
                            ↓
┌─ 分析层 ────────────────────────────────────────────────────┐
│ 线 A（确定性统计）        线 C（模型）                       │
│ 版本强度基线              序列 tokenizer                     │
│ 六维能力项                下一手预测模型                     │
│ 战队画像                  基线对照（频率/逻辑回归）          │
│ 交叉分析                                                     │
│ 剧本生成器                                                   │
└───────────────────────────┬─────────────────────────────────┘
                            ↓
┌─ 数据契约（冻结）→ Web 可视化 → 演示站 ─────────────────────┐
└─────────────────────────────────────────────────────────────┘
```

### 4.1 数据隔离标记（一等公民）

每条比赛记录带 `data_source` 枚举：

| 值 | 含义 | 默认参与统计 |
|---|---|---|
| `pro_match` | 公开职业比赛 | ✅ 是 |
| `pub_match` | 天梯单排/组排 | ❌ 否，需显式开启 |
| `scrim` | 用户导入的训练赛 | ❌ 否，需显式开启 |

**默认规则**：所有分析接口默认只读 `pro_match`。开启其他来源必须显式传参，且返回值必须携带 `sources_used` 字段，UI 必须显式标注。

**结构性不对称（重要）**：对手的训练赛数据无法获得（训练赛保密）。因此 `scrim` 数据**只能流向"自我复盘"分支，永远不得进入对手画像**。

**强制机制（不能只靠调用方自觉）**：

1. 建只读视图 `matches_pro_only AS SELECT * FROM matches WHERE data_source = 'pro_match'`。
2. 应用层**默认只查视图**。要查基表必须显式调用 `resolve_sources(requested: list[str])`，该函数在 `requested` 含 `scrim` 时抛出，除非调用点带有 `allow_scrim=True` 且调用点属于自我复盘模块（以模块白名单硬编码）。
3. **对手侧数据路径禁止出现 `scrim`**：`/v1/playbook` 的 `them` 侧统计与 `/v1/value` 的对手侧统计只能走视图，其代码路径不接受 `allow_scrim` 参数。
4. **负向测试**（§15）：插入一条 `scrim` 记录后，断言它不出现在任何对手画像接口的响应中。这是本条规则的唯一有效证明方式。

---

## 5. 数据模型

### 5.1 核心表

```sql
-- 版本（含字母子版本）
CREATE TABLE patches (
  patch_id        SERIAL PRIMARY KEY,
  version_name    TEXT NOT NULL UNIQUE,     -- '7.41f'
  base_version    TEXT NOT NULL,            -- '7.41'
  released_at     TIMESTAMPTZ NOT NULL,
  opendota_patch  SMALLINT,                 -- OpenDota 的粗粒度 id，如 60
  is_cm_pool_snapshot JSONB                 -- 该版本的 CM 可用英雄
);

-- 全局配置（单行表）。owned_team_id 是 §4.1 隔离规则的判定依据。
CREATE TABLE app_config (
  id             BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),  -- 强制单行
  owned_team_id  BIGINT REFERENCES teams(team_id),             -- "我方"战队
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 赛事/联赛（"同侪"分层与交手历史的依据，见 §7.3）
CREATE TABLE leagues (
  league_id   BIGINT PRIMARY KEY,
  name        TEXT NOT NULL,
  tier        TEXT,                       -- tier1 | tier2 | qualifier | other
  region      TEXT,
  started_at  DATE, ended_at DATE,
  patch_name  TEXT,                       -- Liquipedia infobox 的 patch 字段
  source      TEXT NOT NULL DEFAULT 'opendota'
);

-- 比赛主表
CREATE TABLE matches (
  match_id        BIGINT PRIMARY KEY,
  data_source     TEXT NOT NULL
                  CHECK (data_source IN ('pro_match','pub_match','scrim')),
  patch_id        INT REFERENCES patches(patch_id),
  started_at      TIMESTAMPTZ NOT NULL,     -- 统一 UTC
  duration_s      INT,
  league_id       BIGINT REFERENCES leagues(league_id),
  series_id       BIGINT,                   -- 系列赛（BO3/BO5）分组
  series_type     SMALLINT,
  first_pick_team SMALLINT CHECK (first_pick_team IN (0,1)),
                  -- 0=Radiant 1=Dire，先手方。由 ord=0 的 team 推出（§8① 已验证）
  radiant_team_id BIGINT REFERENCES teams(team_id),
  dire_team_id    BIGINT REFERENCES teams(team_id),
  radiant_win     BOOLEAN,
  lobby_type      SMALLINT,
  draft_state     TEXT NOT NULL DEFAULT 'pending'
                  CHECK (draft_state IN ('pending','complete','unavailable')),
  n_draft_actions SMALLINT,                 -- 实际手数，正常 24
  anomaly         BOOLEAN NOT NULL DEFAULT false,
                  -- §5.3：偏离 24 手模板时为 true，仍计 draft_state='complete'
  parse_state     TEXT NOT NULL DEFAULT 'unparsed'
                  CHECK (parse_state IN ('unparsed','header_only','full','failed')),
                  -- §7 中依赖 OpenDota parsed 的指标据此降级
  replay_path     TEXT,                     -- 卷内路径；NULL 表示已删或无
  replay_retained BOOLEAN NOT NULL DEFAULT false,
                  -- §5.4：scrim 必须保留；pro 可重新下载故可删
  last_attempt_at TIMESTAMPTZ,
  attempt_count   SMALLINT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON matches (data_source, started_at DESC);
CREATE INDEX ON matches (draft_state) WHERE draft_state <> 'complete';
CREATE INDEX ON matches (league_id);

-- 逐手 BP（这是序列模型的训练数据）
CREATE TABLE draft_actions (
  match_id   BIGINT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  ord        SMALLINT NOT NULL CHECK (ord BETWEEN 0 AND 23),
  is_pick    BOOLEAN NOT NULL,
  team       SMALLINT NOT NULL CHECK (team IN (0,1)),   -- 0=Radiant 1=Dire
  hero_id    SMALLINT NOT NULL,
  PRIMARY KEY (match_id, ord)
);

-- 偏离 24 手模板的比赛（约 0.6%，见 §5.3）。不阻断入库，但必须显式记录。
CREATE TABLE draft_anomalies (
  match_id     BIGINT PRIMARY KEY REFERENCES matches(match_id) ON DELETE CASCADE,
  n_actions    SMALLINT NOT NULL,
  kinds        TEXT[] NOT NULL,   -- 如 {short_draft, type_deviation}
  detail       JSONB,
  detected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 选手×场次（阵容归属的唯一依据）
-- 主键用 player_slot 而非 account_id：天梯局中匿名/私密资料的选手 account_id 可能为
-- NULL，若以 account_id 作主键会同场冲突或丢行，直接偏置 §7 的英雄池与对线指标。
CREATE TABLE match_players (
  match_id    BIGINT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
  player_slot SMALLINT NOT NULL CHECK (player_slot BETWEEN 0 AND 9),
  account_id  BIGINT REFERENCES players(account_id),   -- 可空：匿名选手
  team        SMALLINT NOT NULL CHECK (team IN (0,1)),
  hero_id     SMALLINT NOT NULL,
  position    SMALLINT CHECK (position BETWEEN 1 AND 5),
  is_radiant  BOOLEAN NOT NULL,
  kills SMALLINT, deaths SMALLINT, assists SMALLINT,
  gpm SMALLINT, xpm SMALLINT,
  last_hits SMALLINT, denies SMALLINT,
  hero_damage INT, hero_healing INT, tower_damage INT,
  obs_placed SMALLINT, sen_placed SMALLINT,
  camps_stacked SMALLINT, runes_pickups SMALLINT,
  lane_role   SMALLINT,
  is_roaming  BOOLEAN,
  stats_available BOOLEAN NOT NULL DEFAULT false,
                  -- false 表示该场未被 OpenDota parse，统计列不可用（§7 降级依据）
  PRIMARY KEY (match_id, player_slot)
);
CREATE INDEX ON match_players (account_id) WHERE account_id IS NOT NULL;

CREATE TABLE teams (
  team_id BIGINT PRIMARY KEY,
  name TEXT NOT NULL, tag TEXT,
  liquipedia_slug TEXT,
  rating NUMERIC
);

CREATE TABLE players (
  account_id BIGINT PRIMARY KEY,
  name TEXT,
  is_pro BOOLEAN DEFAULT false,
  is_smurf_candidate BOOLEAN DEFAULT false,   -- 派对图扩展产出，待人工确认
  main_account_id BIGINT                      -- 指向主号（若已确认）
);

-- 阵容历史（来自 Liquipedia）。
-- 用代理键而非 (team_id, account_id, joined_at)：Liquipedia 的 joindate 常缺失，
-- 若把它放进主键会隐式变成 NOT NULL，导致日期未知的记录无法入库。
CREATE TABLE rosters (
  roster_id   BIGSERIAL PRIMARY KEY,
  team_id     BIGINT NOT NULL REFERENCES teams(team_id),
  account_id  BIGINT NOT NULL REFERENCES players(account_id),
  joined_at   DATE,                            -- 可空：Liquipedia 常无
  left_at     DATE,
  position    SMALLINT,
  is_captain  BOOLEAN DEFAULT false,
  source      TEXT NOT NULL,                   -- liquipedia | opendota_snapshot
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 同一队伍同一选手的"当前"记录唯一；历史记录靠 left_at 区分
CREATE UNIQUE INDEX ON rosters (team_id, account_id)
  WHERE left_at IS NULL;

-- 采集器持久化（§5.2 状态机要求"启动时读取各源最后成功位点"）
CREATE TABLE collector_cursors (
  source        TEXT PRIMARY KEY,        -- pro_matches | player_matches:{id} | ...
  last_seen_id  BIGINT,                  -- 位点（如最大 match_id）
  last_seen_at  TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 采集尝试记录（R1 的监控告警据此实现）
CREATE TABLE collector_attempts (
  attempt_id  BIGSERIAL PRIMARY KEY,
  source      TEXT NOT NULL,
  match_id    BIGINT,
  outcome     TEXT NOT NULL CHECK (outcome IN ('ok','not_ready','error','ratelimited')),
  detail      TEXT,
  at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON collector_attempts (outcome, at DESC);

-- 三源权重配置（§7.2）。数值口径必须显式，否则两位工程师会得出不同结果。
CREATE TABLE metric_weights (
  metric      TEXT NOT NULL,   -- patch_strength | hero_pool | system_pref | bp_tendency | map_vision
  data_source TEXT NOT NULL CHECK (data_source IN ('pub_match','scrim','pro_match')),
  weight      NUMERIC NOT NULL CHECK (weight >= 0),  -- 0 = 该源对此指标无贡献
  note        TEXT,
  PRIMARY KEY (metric, data_source)
);
-- 聚合规则：weighted_value = Σ(w_i × v_i × n_i) / Σ(w_i × n_i)
-- 其中 v_i 为该源上的指标值，n_i 为样本量。分母为 0 时返回 insufficient_data。

-- [Phase B 预留，Phase A 只建表不写入]
CREATE TABLE wards (
  match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
  ward_type TEXT NOT NULL, team SMALLINT CHECK (team IN (0,1)),
  placed_at_s INT NOT NULL, x NUMERIC, y NUMERIC, removed_at_s INT,
  PRIMARY KEY (match_id, ward_type, placed_at_s, x, y)
);
CREATE TABLE teamfights (
  match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
  fight_index SMALLINT NOT NULL,
  start_s INT NOT NULL, end_s INT, deaths SMALLINT, winner SMALLINT CHECK (winner IN (0,1)),
  PRIMARY KEY (match_id, fight_index)
);
CREATE TABLE item_timings (
  match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
  account_id BIGINT, item_id INT NOT NULL, purchased_at_s INT NOT NULL,
  PRIMARY KEY (match_id, account_id, item_id, purchased_at_s)
);
CREATE TABLE gold_curves (
  match_id BIGINT REFERENCES matches(match_id) ON DELETE CASCADE,
  minute SMALLINT NOT NULL,
  radiant_gold_adv INT, radiant_xp_adv INT,
  PRIMARY KEY (match_id, minute)
);
```

### 5.2 采集状态机

```
discovered ──> fetch_draft ──┬── picks_bans 存在 ──> complete
                             │
                             └── 不存在/null ──> pending
                                     │
                                     ├─ 重试时刻：t+2h, t+8h, t+20h, t+44h，
                                     │            之后每 24h 一次
                                     ├─ t+168h（7 天）仍无 ──> unavailable
                                     └─ t+720h（30 天）最后一次补抓
                                        （OpenDota 偶有延迟回填；成功则翻回 complete）
```

重试阶梯必须在 7 天内真正触达 `unavailable` 阈值——原设计（+2h/+6h/+12h/+24h/+48h，累计 92h）永远到不了 7 天，状态机的后 3 天没有定义。

**持久化**：`collector_cursors` 存各源最后成功位点（启动时据此回溯补抓）；`collector_attempts` 记录每次尝试与结果，是 R1（数据断流告警）的唯一数据来源。`matches.attempt_count` / `last_attempt_at` 便于直接查询。

**幂等性要求**：重启不得产生重复数据。所有写入用主键 upsert（`ON CONFLICT DO UPDATE`）。`draft_actions` 先按 `match_id` 删除再整体插入，避免残留旧手。

**限流与配额**：OpenDota 实测 **60 请求/分钟**；日配额观察到 ≥2760（推测 3000）。**日配额而非分钟配额决定回填排期**，采集器必须同时跟踪两者并暴露为指标。

### 5.3 异常比赛的处理

约 0.6% 的真实比赛偏离 24 手模板（1,014 场中 6 场仅 23 手，个别 `ord` 类型反转）。

| 情形 | 处理 |
|---|---|
| 入库 | **不阻断**。写入实际拿到的 `draft_actions`，设 `n_draft_actions`、`anomaly=true`，并在 `draft_anomalies` 记一行 |
| UI | 标注"该场 BP 结构异常"，相关统计默认**排除**，可显式包含 |
| 序列模型（§10.1） | **默认排除**出训练与评估；`anomaly=false` 是入模的硬条件 |
| 模板推出的队伍归属 | 仍然按 `first_pick_team` + 模板推导——该规则在 1,014 场上零反例，与手数异常无关 |

### 5.4 回放保留策略（按来源区分）

| 来源 | 可否重新获取 | 保留策略 |
|---|---|---|
| `pro_match` | ✅ Valve CDN 可重复下载（URL 由 `match_id` + `replay_salt` 决定） | 解析成功后**可删**，省磁盘 |
| `pub_match` | ✅ 同上 | 可删 |
| `scrim` | ❌ **CDN 上不存在，原始文件是唯一副本** | **必须永久保留**，`replay_retained=true` |

原设计"解析完就删"对训练赛数据是**破坏性**的——它会直接断送 Phase B 承诺的深度解析（眼位、团战、装备）。`scrim` 的 `.dem` 是用户导入的私有资产，删掉不可恢复。

---

## 6. 数据契约 v1

这是并行开发的解耦边界。**契约冻结后，引擎与前端可完全独立推进。**

契约的唯一权威来源是 `contracts/openapi.yaml`（M0 交付）。本节是该文件的规范性说明；两者冲突时以本节为准并立即修正 yaml。

**本节所有示例均取自真实比赛 `8996973546`**（Dawn Bulls `10251056` vs Klim Sani4 `10232231`，RES Unchained 预选赛 EU，series `1141522`，24 手 BP 与最终胜负均为真值）。这是刻意的：示例可被 §15 的「模板推导测试」直接校验，避免文档示例与规则各自漂移——评审正是靠算术核对发现了上一版示例违反自身不变式。

### 6.0 全局约定

**队伍编码**——整个系统只允许两种表示，且必须显式区分：

| 场景 | 表示 | 说明 |
|---|---|---|
| 数据层与 Value/Policy 的**数值字段** | `0` = Radiant，`1` = Dire | 与 OpenDota `picks_bans.team` 一致 |
| Playbook 的**语义字段** | `"us"` / `"them"` | 相对视角，由 `?us=` 参数决定 |

**禁止**在数值字段里出现 `"us"`/`"them"`，也禁止在语义字段里出现 `0`/`1`。Playbook 响应必须同时返回 `side_map: {"us": 0|1, "them": 0|1}`，让前端能把两者对齐。

**模板推导（Policy 的确定性部分）**——`next_ord`、`team`、`is_pick` **不由模型预测**，由 §8① 的已验证模板 + `first_pick_team` 推出。伪代码：

```
F = first_pick_team;  O = 1 - F
TEMPLATE = [  # 24 项，(is_pick, 归属)  归属 ∈ {F, O}
  (0,F),(0,F),(0,O),(0,O),(0,F),(0,O),(0,O),          # ord 0-6   ban
  (1,F),(1,O),                                        # ord 7-8   pick
  (0,F),(0,F),(0,O),                                  # ord 9-11  ban
  (1,O),(1,F),(1,F),(1,O),(1,O),(1,F),                # ord 12-17 pick
  (0,F),(0,O),(0,F),(0,O),                            # ord 18-21 ban
  (1,F),(1,O),                                        # ord 22-23 pick
]
def resolve(ord, first_pick_team):
    is_pick, who = TEMPLATE[ord]
    return is_pick, (first_pick_team if who is F else 1 - first_pick_team)
```

`next_ord` = 已给 `draft` 中的最大 `ord` + 1。请求中 `draft` 的每一手都必须与 `resolve()` 一致，否则返回 `invalid_request`。

**枚举（单一定义，不得在别处另立）**

| 枚举 | 取值 | 判定规则 |
|---|---|---|
| `confidence` | `low` \| `medium` \| `high` | 按 `n_samples`：< 30 → low；30–199 → medium；≥ 200 → high |
| `recommendation` | `pick` \| `ban` \| `leave_and_counter` \| `insufficient_data` | 见 §9.1 |
| `their_opening` | `teamfight` \| `push` \| `pickoff` \| `splitpush` \| `protect` \| `unknown` | 由 §7 维度 6 的战队聚合取最大权重项；任一项权重 < 0.25 时归 `unknown` |
| `notes[].kind` | `ward` \| `timing` \| `lane` \| `smoke` \| `combat` \| `resource` \| `communication` | — |
| `unavailable_reason` | `needs_replay` \| `insufficient_samples` \| `source_not_allowed` \| `stat_unavailable` | 见下 |
| `error.code` | `insufficient_data` \| `invalid_request` \| `source_not_allowed` \| `not_found` \| `upstream_unavailable` | — |

**可选性标记**：本节所有字段默认**必填**。可选字段一律显式标注 `// optional`，且其缺省语义为"该维度无数据"，不得与"值为 0"混淆。

**降级契约（统一形态）**：任何**算不出来的指标**一律返回

```json
{"value": null, "reason": "needs_replay", "needs": "Phase B"}
```

而不是省略字段、也不是填 0。前端据此渲染"暂不可用"态。理由枚举见上表。

**错误信封**：所有非 2xx 响应体固定为

```json
{"error": {"code": "insufficient_data", "message": "人类可读说明",
           "detail": {"n_samples": 4, "required": 30}}}
```

`insufficient_data` 用于样本不足以支撑结论（**不是** 5xx——它是正常的业务结果）。`upstream_unavailable` 用于 OpenDota 等上游不可达且无缓存。

### 6.1 Value —— 局面评估

```
POST /v1/value
{
  "patch": "7.41f",
  "radiant": { "team_id": 10251056,
               "heroes": [{"hero_id": 123, "position": 4}] },
  "dire":    { "team_id": 10232231,
               "heroes": [{"hero_id": 107, "position": 2}] },
  "sources": ["pro_match"],
  "first_pick_team": 0
}

→ 200
{
  "radiant_win_prob": 0.530,
  "confidence": "high",
  "n_samples": 412,
  "contributions": [
    {"factor": "patch_strength",  "delta":  0.011},
    {"factor": "counter_matchup", "delta": -0.014},
    {"factor": "player_comfort",  "delta":  0.024},
    {"factor": "first_pick",      "delta":  0.009}
  ],
  "sources_used": ["pro_match"]
}
```

**不变式（可测）**：
- `sum(contributions[].delta) == radiant_win_prob - 0.5`，容差 ±0.001。上例：`0.011 - 0.014 + 0.024 + 0.009 = 0.030 == 0.530 - 0.5` ✓
- `sources_used` ⊆ `sources`（请求值）。要引入未请求的来源必须返回 `source_not_allowed`，**不得**静默扩大。
- `position` 可省略（`// optional`）；省略时按 §7 的位置推断规则处理。

**`counter_matchup` 的语义边界（与 §1 一致）**：§1 否证的是**通用英雄两两交互特征**（无战队/选手条件、跨全样本估计）。本项允许且仅允许**上下文条件化**的对位效应——即"该选手在这个对位上、在该战队体系下的历史表现"。实现上必须由 §7 的条件化统计给出，**不得**退化回一个全局 127×127 克制矩阵。这是 Value 语义的关键约束。

### 6.2 Policy —— 对手下一手预测

```
POST /v1/policy/next
{
  "patch": "7.41f",
  "first_pick_team": 0,
  "radiant_team_id": 10251056,
  "dire_team_id": 10232231,
  "draft": [
    {"ord":0, "is_pick":false, "team":0, "hero_id":80},
    {"ord":1, "is_pick":false, "team":0, "hero_id":62},
    {"ord":2, "is_pick":false, "team":1, "hero_id":83},
    {"ord":3, "is_pick":false, "team":1, "hero_id":33},
    {"ord":4, "is_pick":false, "team":0, "hero_id":90},
    {"ord":5, "is_pick":false, "team":1, "hero_id":77},
    {"ord":6, "is_pick":false, "team":1, "hero_id":55},
    {"ord":7, "is_pick":true,  "team":0, "hero_id":123},
    {"ord":8, "is_pick":true,  "team":1, "hero_id":107},
    {"ord":9, "is_pick":false, "team":0, "hero_id":53},
    {"ord":10,"is_pick":false, "team":0, "hero_id":9},
    {"ord":11,"is_pick":false, "team":1, "hero_id":106}
  ],
  "sources": ["pro_match"]
}

→ 200
{
  "next_ord": 12,
  "team": 1,
  "is_pick": true,
  "candidates": [
    {"hero_id": 112, "prob": 0.180,
     "reasons": ["该队在此阶段的历史首选", "克制对方已选核心"],
     "evidence_match_ids": [8996973546, 8988636430]}
  ],
  "model": "sequence-v1",
  "baseline": {"frequency_top1": 0.041, "model_top1": null},
  "sources_used": ["pro_match"]
}
```

上例的 `draft` 是**真实比赛 8996973546 的前 12 手**，`first_pick_team=0`；`next_ord=12` 由模板推出为 `O` 的 pick，即 `team = 1 - 0 = 1` ✓（真实结果正是 Dire 在第 12 手选 Winter Wyvern）。

**不变式（可测）**：
- `sum(candidates[].prob) == 1.0`，容差 ±0.001
- `(next_ord, team, is_pick) == resolve(max_ord(draft)+1, first_pick_team)`，即 §6.0 的 `resolve()`
- `candidates[].hero_id` 不得与 `draft` 中已出现的 hero 重复
- `baseline.model_top1` 在模型未就绪时为 `null`（`// optional`，语义为"无模型"），前端须能渲染此态
- `reasons` 非空（不得返回无依据的候选）

### 6.3 Playbook —— 剧本集（主输出）

```
GET /v1/playbook?us=10251056&them=10232231&patch=7.41f&series_id=1141522&sources=pro_match

→ 200
{
  "matchup": {
    "us":   {"team_id": 10251056, "name": "Dawn Bulls"},   // ProfileRef，见 §6.4
    "them": {"team_id": 10232231, "name": "Klim Sani4"},
    "patch": "7.41f",
    "first_pick_team": 0,
    "side_map": {"us": 0, "them": 1}
  },
  "sources_used": ["pro_match"],
  "coverage": {
    "pro_match": {"n_matches": 42, "n_stat_available": 38},
    "pub_match": {"n_matches": 0,  "n_stat_available": 0}
  },
  "bans": {
    "must_ban": [{"hero_id": 55, "why": "对手签名英雄，我方无人擅长应对",
                  "their_wr": 0.71, "our_wr_against": 0.29, "n": 24}],
    "consider": [{"hero_id": 77, "why": "...",
                  "if_we_leave_it_open": { /* OpHeroOption，与 op_hero_decision[] 同形 */ }}],
    "bait_candidates": [{"hero_id": 90, "why": "双方都不擅长，浪费对手 ban 位"}]
  },
  "op_hero_decision": [{
    "hero_id": 83,
    "if_we_pick":  {"wr": 0.58, "n": 12},
    "if_we_ban":   {"wr": 0.50, "n": 0},
    "if_we_leave": {"wr": 0.44, "their_wr": 0.68, "n": 19,
                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 9}]},
    "recommendation": "leave_and_counter"
  }],
  "branches": [
    {
      "branch_id": "A",
      "condition": {"first_pick": "them", "their_opening": "teamfight"},
      "plans": [{
        "label": "A1",
        "goal": "拖到中后期，靠分推拉扯",
        "key_picks": [{"priority": 1, "hero_id": 105, "by_ord": 13,
                       "why": "...", "fallback": [67, 19]}],
        "expected_wr": 0.52, "n": 31,
        "robustness_delta": 0.04
      }]
    }
  ],
  "series": {
    "series_id": 1141522,
    "game1_plan": "...",
    "adjustment_rules": [{"if": "对手第 1 局暴露推进体系", "then": "..."}]
  },
  "positions": [
    {"side": "them", "role": 4,
     "notes": [{"kind": "ward", "text": "...", "evidence": [],
                "value": null, "reason": "needs_replay", "needs": "Phase B"}]}
  ]
}
```

**不变式（可测）**：
- 每个 `plans[]` 必须有**非空** `fallback`（首选被 ban 的路径）。**无 fallback 的 plan 不得产出**——这是产品硬要求（§13 补充事项 2），不是建议。
- `branches[].condition.first_pick` ∈ {`"us"`,`"them"`}，且所有 2×系统类型 的组合必须被覆盖或有显式 `unknown` 分支。
- `op_hero_decision[].recommendation` ∈ §6.0 枚举；`n` 不足以支撑时必须是 `insufficient_data` 而非猜测。
- `consider[].if_we_leave_it_open` 与 `op_hero_decision[]` **同形**（`OpHeroOption`），不得各写一套。
- `matchup.side_map` 必须与 `first_pick_team` 自洽：`side_map[us] == first_pick_team` 当且仅当 `first_pick` 为 `"us"`。
- `series_id` 为 `// optional`；缺省时不返回 `series` 对象（而非返回 null 字段）。
- 所有依赖回放的指标使用 §6.0 的统一降级形态，**不得**静默省略字段。

### 6.4 Profile —— 战队与选手画像

M3 与 A8 的对手画像卡依赖此资源。这是 Value 与 Playbook 的共用底座。

```
GET /v1/profile?team_id=10232231&patch=7.41f&as_of=2026-09-16&sources=pro_match,pub_match

→ 200
{
  "team_id": 10232231,
  "patch": "7.41f",
  "as_of": "2026-09-16",
  "sources_used": ["pro_match", "pub_match"],
  "coverage": {"pro_match": {"n_matches": 42, "n_stat_available": 38},
               "pub_match": {"n_matches": 310, "n_stat_available": 298}},
  "players": [{
    "account_id": 123456,
    "name": "...",
    "role": 4,
    "hero_pool": {
      "signature":   [{"hero_id": 55, "games": 12, "wr": 0.75, "pct": 92}],
      "comfortable": [{"hero_id": 77, "games": 8,  "wr": 0.62, "pct": 78}],
      "effective_count": 14,
      "presence_pick_rate": 0.81
    },
    "dimensions": {
      "hero_pool":     {"percentile": 88, "n": 120},
      "laning":        {"percentile": 71, "n": 120},
      "combat":        {"percentile": 64, "n": 120},
      "map_vision":    {"value": null, "reason": "needs_replay", "needs": "Phase B"},
      "tempo":         {"percentile": 55, "n": 120},
      "hero_archetype":{"teamfight": 0.32, "push": 0.18, "pickoff": 0.21,
                        "splitpush": 0.11, "protect": 0.18}
    }
  }],
  "team_bp_tendency": {
    "first_phase_ban_freq": [{"hero_id": 55, "freq": 0.42, "n": 31}],
    "first_pick_freq":      [{"hero_id": 123, "freq": 0.28, "n": 25}],
    "ban_by_phase":         [{"ord": 9, "hero_id": 53, "freq": 0.31, "n": 29}]
  }
}
```

**约定**：
- `dimensions.*.percentile` 为**同位置、同版本、同 tier 赛事**内的百分位（`同侪` 定义见 §7.3）。样本不足时返回 §6.0 降级形态，**不返回绝对值**。
- `role` 为 `// optional`；推断不出时为 `null`，此时 `dimensions` 中所有依赖位置的项返回 `insufficient_samples`。
- `hero_archetype` 各项之和为 1.0（±0.001）。
- `as_of` 让画像可回溯——所有窗口型指标（近 90 天 / 本版本 / 生涯）都相对它计算。

### 6.5 不返回的内容（Phase A 边界，机器可检）

以下字段在 Phase A **必须不存在**于任何响应中。前端不得依赖它们；若需要，先改本节。

| 缺失内容 | 原因 | 替代 |
|---|---|---|
| 眼位坐标、热力图 | 需回放解析（Phase B） | `map_vision` 返回 `needs_replay` |
| 10 分钟补刀差/经验差 | 需回放解析 | 用 GPM/XPM 分位替代（`laning` 维度） |
| 开雾次数/时机 | 需回放解析 | 不提供 |
| 团战切分与胜负 | 需回放解析（A9 只解头信息与 BP） | 不提供 |
| 装备时间线 | 需回放解析 | 不提供 |
| 沟通/语音 | 回放内 0 条语音（§3.3），只能自录 | 不提供 |

### 6.6 Advise —— 整手建议（决策层 ⑤ 的接口）

线 C 的交付物 A10。离线路径产出 Playbook 的 `plans[]`，在线路径服务赛时 BP。

```
POST /v1/advise
{
  "patch": "7.41f",
  "us": 10251056, "them": 10232231,
  "first_pick_team": 0,
  "draft": [ /* 同 §6.2 结构，当前已发生的所有手 */ ],
  "sources": ["pro_match"]
}

→ 200
{
  "next_ord": 13,
  "team": 0,
  "is_pick": true,
  "options": [
    {"hero_id": 105, "expected_wr": 0.552, "robustness_delta": 0.04,
     "why": "对手按预测分布应对时最优；换招后仍不劣于 0.51",
     "fallback": [67, 19],
     "counterparty_plan": "对手若抢 105，我方案转为 ..."},
    {"hero_id": 67,  "expected_wr": 0.541, "robustness_delta": 0.02,
     "why": "...", "fallback": [19, 105], "counterparty_plan": "..."}
  ],
  "assumptions": {"opponent_model": "sequence-v1", "value_model": "value-v1"},
  "sources_used": ["pro_match"]
}
```

**不变式（可测）**：
- `next_ord`/`team`/`is_pick` 同样由 §6.0 的 `resolve()` 确定性推出
- `options` 按 `expected_wr` 降序；每项必须有非空 `fallback` 与非空 `counterparty_plan`
- 每项必须有 `robustness_delta`；> 0.10 的项必须带 `risk_note` 字段
- `options` 中出现过的 hero 必须已被 §6.0 的 `resolve()` 判定为可行动作（即不与 `draft` 重复）
- 模型未就绪时返回 `insufficient_data`，**不得**退化为"返回英雄胜率榜"——那是被 §1 否证的做法

---

## 7. 分析引擎：六维能力项

所有指标均为**同位置、同版本、同侪内的百分位**，不输出绝对值。位置判定优先用 STRATZ 风格的位置推断；退化时按 `lane_role` 与补刀/经济结构启发式判定。

| 维度 | 指标 | 数据来源 | 公式/口径 |
|---|---|---|---|
| **1 英雄池** | 场次/胜率（三个窗口：近 90 天 / 本版本 / 生涯） | API | 分窗口统计 |
| | 签名英雄 | API | 场次 ≥ 5 且胜率 ≥ 60%，且占该选手出场 ≥ 10% |
| | 有效英雄数（广度） | API | 满足"场次 ≥ 3 且胜率 ≥ 50%"的英雄个数 |
| | presence→pick 率 | API | 该英雄被放出时该选手的被选率 |
| **2 对线与发育** | 10 分钟补刀差 / 经验差 | 回放（Phase B） | Phase A 以 GPM/XPM 分位替代 |
| | GPM / XPM 分位 | API（需 `stats_available`） | 同位置同版本同 tier 分位 |
| | 分路与游走率 | API（需 `stats_available`） | `lane_role` / `is_roaming`。**注意**：这两列由 OpenDota 的解析管道产出，未 parse 的场次为 NULL；R2 所说"lane 数据留待回放层"指的是**10 分钟补刀/经验差**，与本节的分路标注不是同一件事 |
| **3 战斗** | 参团率 | API | 击杀参与 / 队伍总击杀 |
| | KDA | API | (K+A)/max(1,D) |
| | 伤害占比 | API | 英雄伤害 / 队伍总英雄伤害 |
| | 承伤占比 | API | 仅 3 号位口径 |
| | 治疗量 | API | 仅 4/5 号位口径 |
| | 经济转化率 | API | 英雄伤害 / 净经济 |
| **4 地图与视野** | 真假眼数量、反眼数、堆野数、神符 | API | 数量口径 |
| | 眼位坐标与热力图 | 回放（Phase B） | 坐标需回放 |
| **5 节奏与稳定性** | 近期状态滑动窗口 | API | 近 20 场加权胜率，指数衰减 |
| | 胜负方差 | API | 窗口内胜率的二项方差 |
| | 首杀参与率 | API | `firstblood_claimed` 归属 |
| **6 英雄类型偏好** | 先手/反手/推进/团战/抓单/分推 倾向 | 常量 + API | 基于 dotaconstants `heroes.roles` 打标 + 选手英雄池加权 |

### 7.1 战队级聚合

- **BP 倾向**：先抢什么（各 BP 阶段的首选分布）、何时 ban 什么（按 `ord` 分段的 ban 分布）
- **体系倾向**：由六维中"英雄类型偏好"聚合到战队层
- **节奏**：平均比赛时长、平均首杀时间、平均一塔时间
- **交手历史**：与特定对手的 BP 历史与胜负

### 7.2 三源权重矩阵（按指标配置，非全局权重）

| 指标 | 天梯 | 训练赛 | 正式比赛 |
|---|---|---|---|
| 版本英雄强度 | **高** | — | 中 |
| 选手英雄池 | **高**（会不会玩） | 中 | **高**（高压下敢不敢拿） |
| 体系偏好 | 无 | 中（⚠ 藏招偏差） | **最高** |
| BP 倾向 | **无数据** | 低（试阵容） | **最高** |
| 节奏（比赛时长/首杀/一塔时间） | 低 | 中 | **高** |

**数值口径（必须显式，否则两位工程师会算出不同结果）**：`最高`=1.5、`高`=1.0、`中`=0.5、`低`=0.25、`无`/`无数据`=0。

权重落在 §5.1 的 `metric_weights` 表（`metric × data_source → weight`），可调。**不得**用单一全局权重——否则会把训练赛的藏招当成真实倾向。

**聚合规则**：`value = Σ(wᵢ × vᵢ × nᵢ) / Σ(wᵢ × nᵢ)`；分母为 0 或 Σ(wᵢ × nᵢ) < 30 时返回 §6.0 降级形态 `insufficient_samples`。

**Phase A 的权重表实际只有四行有效**：`map_vision` 指标整个 Phase A 不可计算（需回放），其权重行在 M1 建表时插入但值为 0，并在 `note` 中标注 `Phase B`。**开雾不是任何指标**——原表把它与眼位并列是笔误，已删除。

### 7.3 「同侪」的定义（所有百分位的分母）

§7 每个指标都归一化为百分位，但"同侪"此前未定义——不定义就无法实现，也无法测试。**同侪 = 满足以下全部条件的 `match_players` 行集合**：

| 维度 | 条件 |
|---|---|
| 位置 | `position` 相同（`role` 一致） |
| 版本 | 同一 `base_version`（如 `7.41`；**不细到字母子版本**，否则样本太少） |
| 赛事层级 | 同 `leagues.tier`；目标战队无 tier 数据时退化为 `tier1 ∪ tier2` |
| 时间窗 | 与目标指标相同的窗口（近 90 天 / 本版本 / 生涯） |
| 数据质量 | `stats_available = true` |
| 来源 | 与目标指标的 `sources` 一致 |

**样本量门槛**：同侪集合 < 30 条时，该指标返回 §6.0 降级形态 `insufficient_samples`，不返回百分位。

**窗口与阈值（可测）**：

| 指标 | 规则 |
|---|---|
| 签名英雄 | 场次 ≥ 5 且胜率 ≥ 0.60 且占该选手同窗口出场 ≥ 10% |
| 有效英雄数 | 满足"场次 ≥ 3 且胜率 ≥ 0.50"的英雄个数 |
| 近期状态 | 近 20 场指数衰减加权胜率，半衰期 7 场 |

---

## 8. 决策栈（五层）

| 层 | 名称 | 回答 | 归属 |
|---|---|---|---|
| ① | 约束与规则 | 哪些是硬性事实？ | 确定性，双方共用 |
| ② | 局面评估 Value | 这局面我赢面多少？ | **线 A** |
| ③ | 选手与战队条件 | 在**这些人**手里值多少？ | **线 A** |
| ④ | 对手建模 Policy | 对面下一步干什么？ | **线 C** |
| ⑤ | 搜索与决策 Search | 我该怎么走？ | **线 C** |

**① 的具体内容**（全部确定性，不需模型）：

**24 手模板已完整验证**（OpenDota 全库 1,014 场比赛，零例外）。令 `F` = 先手方（`first_pick_team`），`O` = 后手方：

| ord | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 类型 | BAN | BAN | BAN | BAN | BAN | BAN | BAN | PICK | PICK | BAN | BAN | BAN |
| 队伍 | F | F | O | O | F | O | O | F | O | F | F | O |

| ord | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 类型 | PICK | PICK | PICK | PICK | PICK | PICK | BAN | BAN | BAN | BAN | PICK | PICK |
| 队伍 | O | F | F | O | O | F | F | O | F | O | F | O |

- 阶段结构：**7 ban → 2 pick → 3 ban → 6 pick → 4 ban → 2 pick**
- 双方完全对称：**F 与 O 各 7 个 ban、各 5 个 pick**
- 队伍归属仅由 `first_pick_team` 决定，两种情形精确互为镜像。

**已解决的验证**：原风险 R4（"先 ban 的队伍是否等于先 pick 的队伍"）在全库 1,014 场上验证为 **1014/1014 成立，零反例**。此依赖可硬编码。

**先手方分布不均衡（影响模型评估）**：同一窗口内 Dire 获先手 658 场、Radiant 356 场（**65% / 35%**）。原因未查明（推测与赛事的选边规则有关，非 Valve 强制）。因此：
- 镜像增广成立（结构精确镜像），数据量可翻倍
- 但**评估时必须处理这个类别不平衡**，且"先手方"特征与"阵营"特征存在共线性，建模时不可同时无条件引入而不做共线性检查

**其余确定性约束**：
- CM 英雄池（`cm_enabled`）
- 位置结构：每队 1–5 号位各一；摇摆位英雄具多位置可能性

**⑤ 的稳健性要求**：选择的不只是期望胜率最高的一手，还要对对手偏离概率分布不敏感。实现方式：对每个候选手计算"对手按预测分布应对"与"对手按最坏应对"两种情形下的胜率差，即 `robustness_delta`；该值 > 0.10 时在输出中降权并标注"对手若换招则本方案失效"。

**⑤ 的归属与交付物（此前缺失，现补齐）**：

| 项 | 内容 |
|---|---|
| 归属 | **线 C**（依赖 Policy 的概率分布与 Value 的评估，两者都已由线 A 提供） |
| 接口 | `POST /v1/advise`（见 §6.6）——离线产出 Playbook 的 `plans[]`，在线产出单步建议 |
| 交付物 | A10（§2.1） |
| 里程碑 | M10（§14） |
| 测试 | §15「决策稳健性测试」 |

**与 §9.1 `recommendation` 的分工**：§9.1 的 `recommendation` 是**单英雄层面**的三选一（抢/ban/放反制），由线 A 的确定性统计给出；§8⑤ 的 `advise` 是**整手层面**的搜索结论，由线 C 给出，且必须引用前者的结论作为评估输入。两者不重叠：前者回答"这个英雄怎么办"，后者回答"这一手我出谁"。

---

## 9. 交叉分析（四象限）

| 象限 | 定义 | 动作 |
|---|---|---|
| **高危区** | 对手签名英雄 ∧ 我方无人擅长应对 | 必 ban 候选（先算"放了会怎样"） |
| **机会区** | 我方签名英雄 ∧ 对手不会应对 | 抢，或留到后手（先算对手会花几手 ban 它） |
| **硬碰硬** | 双方都强 | 决定先手权价值的关键，先手胜率差的主要来源 |
| **可放区** | 双方都不强 | 浪费对手 ban 位的诱饵（除非是版本 OP 英雄） |

### 9.1 OP（"bug"）英雄的量化决策

不得只标 T0。必须给出三个可算的量与结论：

1. 我方拿 → 胜率与样本量
2. 我方 ban → 剩余局面胜率
3. 我方放 → 对手拿的胜率 + 我方反制选项与胜率

结论枚举：`pick` | `ban` | `leave_and_counter` | `insufficient_data`

---

## 10. 序列模型（线 C）

### 10.1 任务定义

24 手 CM 序列 → token 序列。位置 token 由 §8① 确定性给定，**模型只预测英雄**。

- 词表（**四个特殊 token 在此定义，不得留空**）：

  | 范围 | 含义 |
  |---|---|
  | `0..126` | `hero_id` 0..126 作为 **pick** 出现 |
  | `127..253` | `hero_id` 0..126 作为 **ban** 出现 |
  | `254` | `PAD` —— 序列短于 24 手时右侧填充 |
  | `255` | `BOS` —— 序列起始标记 |
  | `256` | `UNK_HERO` —— `hero_id` 落在常量表之外（新英雄上线而 dotaconstants 未更新） |
  | `257` | `MASK` —— 训练时的掩码位，不参与 loss |

- **异常序列**：`anomaly = true` 的场次（§5.3，约 0.6%）**默认排除**出训练与评估。这是入模的硬条件，不是偏好。
- **部分序列**：`draft` 不足 24 手时（实时 BP 场景），右侧以 `PAD` 补齐到 24；loss 只在非 `PAD` 位置回传。
- **手数/类型不编码进 token**——由 §6.0 的 `resolve()` 确定性给出，作为独立的 position embedding 输入。模型只学"落到哪个英雄"。
- 输入：前缀 token + 战队 id embedding + 选手 id embedding（该手所属队伍的实际出场阵容）+ 版本 embedding
- 输出：下一手英雄的概率分布

### 10.2 训练数据

- **引导**：Kaggle `bwandowando/dota-2-pro-league-matches-2023`（**CC0: Public Domain**，usability 1.0，2026-09-14 更新）
  - **总大小 48.5 GB，但所需文件合计仅 506 MB（1.04%）** —— 必须**按文件选择性下载**，绝不整包拉取。其中 `players.csv` 单文件即 41 GB（占 85%），与 Phase A 无关。
  - 所需文件：`draft_timings.csv`（240.7 MB / 10 个年度目录）、`picks_bans.csv`（198.8 MB / 19 个目录）、`main_metadata.csv`（66.7 MB / 19 个目录）
  - **已知缺口**：`draft_timings.csv` 仅存在于 19 个目录中的 10 个，而 `picks_bans.csv` 覆盖 19 个。**以 `picks_bans.csv` 为 BP 序列的权威来源**，`draft_timings.csv` 仅作补充（含思考耗时，可用于后续"摇摆位造成的对手犹豫"分析）。
  - 下载需 Kaggle 凭据（`kaggle.json`）；凭据只存本地 `.env`，已在 `.gitignore` 中排除。
- **增量**：自建采集库中的 `draft_actions`
- **镜像增广**：因两队顺序精确镜像，可将每场镜像为另一种先手情形（**数据量翻倍**）

### 10.3 评估协议（严格遵守）

1. **必须按时间切分**（chronological split），禁止随机切分。随机切分会造成泄漏，是这一领域最常见的错误。
2. **必须报告基线对照**：随机基线（top-1 ≈ 0.8%）、全局频率基线、按 BP 阶段分层的频率基线。
3. **必须分阶段报告**：前 8 手 / 9–16 手 / 17–24 手分别报告 top-1/3/5。
4. **必须报告 patch 敏感性**：按版本切片评估，验证跨版本退化。
5. 目标：**超过频率基线且在前 8 手达到可用水平**。参照已发表最好结果：top-1 19% / top-3 38% / top-5 49%；前 8 手 top-3 达 66%。**不追求超越该数字**，只追求复现并加入战队/选手条件后有可测提升。

### 10.4 交付顺序

先做**基线**（按 BP 阶段分层的历史频率 + 战队条件），再做模型。原因：基线本身就能产出可用的"对手下一手"情报，且是判断模型是否真有增益的唯一标尺。

---

## 11. 并行开发切分

**先冻结 §6 契约，再开两条 worktree 线。**

| | 线 A · 画像与统计引擎 | 线 C · 序列模型 |
|---|---|---|
| 语言 | Python | Python |
| 内容 | 采集、存储、六维能力项、战队画像、交叉分析、剧本生成器、Value 接口、Profile 接口、`/v1/playbook` | 序列 tokenizer、基线、模型、Policy 接口、**决策搜索（§8⑤）与 `/v1/advise`** |
| 依赖 | 只依赖契约 + 数据库 | 只依赖契约 + 数据集 |
| 能否独立测试 | 是（用 fixtures） | 是（用离线数据集） |
| 完成标志 | `GET /v1/playbook` 对任一 matchup 返回结构完整、不变式通过的响应 | `POST /v1/policy/next` 返回概率和为 1、且优于频率基线 |

**第三条线（可视化）**：前端消费契约的 **mock 数据**先行开发，不等待后端。契约冻结后即可启动。

**M0 必须同时交付 `contracts/fixtures/`**：每个接口至少一份合法样例响应，且**必须覆盖边界情形**——`confidence: "low"`、`anomaly: true`、`draft_state: "unavailable"`、`positions[].data_available: false`、`op_hero_decision[].recommendation: "insufficient_data"`。fixture 必须通过契约 schema 校验（否则前端会对着非法数据开发，等真接口上线时才发现字段名不一致）。

**冲突规避**：三条线不共享可写文件。线 A 拥有 `db/` 与 `analysis/`，线 C 拥有 `models/`，前端拥有 `web/`。契约文件 `contracts/openapi.yaml` 由**单人**维护（冻结期内只读）。

---

## 12. 风险与未验证事项

| 编号 | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | OpenDota `picks_bans` 滞后扩大或字段变更 | 数据断流 | 状态机 + 监控告警；`unavailable` 状态显式暴露给 UI |
| R2 | STRATZ 不可用（已确认本环境被拦截） | 损失位置与 lane 数据 | **不依赖**。位置判定退化为启发式；lane 数据留待回放层 |
| R3 | 训练赛回放可能来自**旧版本**或非标准 lobby 设置 | 污染统计 | 导入时读 `demo_version_name` 强制校验；非标准设置需人工确认 |
| R4 | ~~"先 ban 的队伍 = 先 pick 的队伍" 仅验证 3 场~~ **已关闭** | — | 全库 1,014 场验证 **1014/1014 成立，零反例**。规则可硬编码。见 §8① |
| R5 | Liquipedia 限流严格（`action=parse` 1 次/30 秒） | 阵容历史采集慢 | 只采集目标战队（数十支），一次采完缓存；不追求全量 |
| R6 | 序列模型可能无法超过频率基线 | 线 C 无产出 | 先交付基线（本身可用）；模型不达标则如实报告为负结果，**这本身也是内容**（该领域公开代码极少，负结果有参考价值） |
| R7 | 演示视频中模型预测错误 | 观感受损 | 演示话术定位为"提供概率分布与依据"，不承诺单点命中；主推前 8 手的高准确区间 |
| R8 | 数据来源合规 | 传播风险 | dotaconstants/OpenDota = MIT；Liquipedia = CC-BY-SA 3.0（**必须标注来源**）；Fandom = **CC BY-NC-SA，不使用**；GameTracking-Dota2 **无许可证声明，不使用其内容** |
| R9 | 回放下载占用带宽与磁盘 | 本地资源耗尽 | **按来源区分保留策略（§5.4）**：`pro_match`/`pub_match` 可从 Valve CDN 重下，解析后即删；**`scrim` 是唯一副本，必须永久保留**。原"解析完就删"一刀切会破坏 Phase B |
| R10 | 版本补丁改动为自然语言，无法自动量化 | 版本强度无法用补丁文本直接建模 | Phase A 以**实测胜率变化**代替补丁文本解析；补丁文本仅作展示与人工参考 |
| R11 | R1（数据断流）的缓解措施此前无落点 | 采集出问题无人知道 | 观测性为**独立交付项**：`collector_attempts` 表 + 四个指标（`picks_bans` 最新滞后小时数、`pending` 队列长度、重试成功率、日配额消耗）+ `unavailable` 占比超 5% 时告警。归属线 A，随 M2 交付 |
| R12 | 回填排期未做配额预算 | 首次全量回填可能撞日配额被封 | **日配额（约 3000）而非分钟配额决定排期**。M1 引导数据集已覆盖 2016–2026 历史，采集器只需**增量**回填近 90 天。预算公式：`比赛数 × 端点数 ÷ 日配额`，超限则分日推进并落 `collector_cursors` |

---

## 13. 部署

| 阶段 | 形态 |
|---|---|
| 开发 | 本地 Mac，Docker Compose（Postgres + 采集器 + API + Web） |
| 演示 | 云主机，**同一份 compose**，仅改连接串与环境变量 |

**跨平台硬性要求**（Windows 用户需能直接运行）：
- 一律 `pathlib`，禁止字符串拼路径
- 所有文件读写显式 `encoding="utf-8"`
- 假设文件系统**大小写敏感**（Mac/Windows 不敏感，Linux 敏感）
- `.gitattributes` 强制 LF
- 时间统一存 UTC，仅展示层转本地
- 回放临时文件放**容器内部卷**，不挂宿主机 bind mount
- 用 **Postgres 不用 SQLite**（SQLite 在容器挂载卷上会锁坏）

**解析永不进云**：回放解析在本地完成，只把结构化结果导入云端库。

---

## 14. 里程碑

| # | 里程碑 | 验收标准 |
|---|---|---|
| M0 | 仓库骨架 + 契约冻结 | `contracts/openapi.yaml` 提交且通过 schema 校验；`contracts/fixtures/` 覆盖 §11 列出的全部边界情形**且每个 fixture 均通过契约校验**；三条线的目录边界建立 |
| M1 | 数据地基可用 | 常量表英雄 = 127、道具 = 501；版本表含 **84 个字母子版本**；引导数据集 506 MB 子集入库，且 `anomaly=true` 的场次占比 **< 2%**；`matches`/`draft_actions`/`leagues` 可查 |
| M2 | 采集器常驻运行 | 连续运行 48h，`matches` 无重复 `match_id`；`collector_cursors` 位点持续推进；构造一场无 `picks_bans` 的比赛，断言其 `draft_state='pending'` 且在 t+168h 后翻为 `unavailable` |
| M3 | 画像引擎可用 | `GET /v1/profile` 对任一战队返回六维；**每维要么给出 `percentile`，要么给出 `reason`**（不得两者皆空、不得返回绝对值）；签名英雄/有效英雄数按 §7.3 阈值可复算 |
| M4 | Value 接口可用 | `POST /v1/value` 的 `contributions` 求和不变式通过（±0.001）；`sources_used ⊆ sources` |
| M5 | 剧本集可用 | `GET /v1/playbook` 通过全部 §6.3 不变式；**每个 `plans[]` 的 `fallback` 非空**；`coverage` 字段与实际库内计数一致 |
| M6 | 可视化可用 | 对手画像卡、剧本速查卡、**BP 推荐面板**三者可交互；能渲染 §11 列出的全部降级/空/错误态；数据源从 mock 切到真接口后无字段改动 |
| M7 | 序列模型基线 | 分层频率基线在**按时间切分**的留出集上报告 top-1/3/5，且 top-1 **≥ 全局频率基线**（数值随报告给出，非"达标"） |
| M8 | 序列模型 | 在分阶段（前 8 / 9–16 / 17–24 手）与分版本两个维度上均报告 top-1/3/5；**至少一个阶段优于 M7 基线**，否则如实记为负结果并保留基线交付 |
| M9 | 决策层可用 | `POST /v1/advise` 通过 §6.6 全部不变式；`robustness_delta` 对每个 option 均存在 |
| M10 | 演示站上线 | 公网可访问，同一 compose，`GET /v1/playbook` 对至少 3 组真实 matchup 返回非空剧本 |

---

## 15. 测试策略

| 层次 | 内容 |
|---|---|
| **契约测试** | 所有接口的请求/响应 schema 校验；概率归一化不变式；`contributions` 求和不变式 |
| **数据完整性测试** | **不得**断言"每场必须 24 行、14 ban + 10 pick"——实测约 **0.6%** 的真实比赛偏离模板（1,014 场中 6 场仅 23 手，个别 ord 类型反转）。改为：① 断言偏离率低于 2% 的**统计阈值**；② 逐场偏离则记录到 `draft_anomalies` 并标记 `draft_state='complete'` 但附加 `anomaly=true`；③ 队伍归属与 `first_pick_team` 一致（此条**可硬断言**，1,014/1,014 成立） |
| **幂等性测试** | 采集器重复运行不产生重复行；中断后重启可回溯补齐 |
| **隔离测试** | 默认查询不返回 `pub_match`/`scrim`；`scrim` 数据不出现在对手画像接口中（负向断言） |
| **时间切分测试** | 模型评估流程断言训练集时间戳全部早于验证集（防泄漏） |
| **固定装置** | 用 3 场已知比赛（`8996973546` / `8988636430` / `8988557865`）作为黄金样本，断言 BP 序列逐手匹配 |
| **模板推导测试** | 对全库每一场断言 `(ord, team, is_pick)` 与 §6.0 的 `resolve(ord, first_pick_team)` 一致（`anomaly=false` 的场次必须零反例）；契约示例中的 `draft` 必须与该场真实数据一致——**防止示例与规则再次漂移** |
| **降级契约测试** | 对每个可能不可计算的指标断言返回的是 §6.0 降级形态（`value: null` + `reason` ∈ 枚举），而非字段缺失或 0；断言 `needs` 字段存在 |
| **枚举闭包测试** | 扫描全部 fixture 与接口样例，断言每个枚举字段的取值都在 §6.0 定义的集合内；枚举未在客户端硬编码（由 schema 生成 TS 类型） |
| **版本归属测试** | 造两场 `start_time` 分别落在 7.41e 与 7.41f 边界两侧的比赛，断言 `patch_id` 不同——这是 `patchdates.json` 还原逻辑（含 `add` 不在 `dates[]` 内的陷阱）的唯一验证 |
| **阵容归属测试** | 造一场跨阵容变更的比赛，断言历史胜率按**当场出场阵容**归属而非当前阵容；断言 `rosters` 可插入 `joined_at IS NULL` 的行 |
| **匿名选手测试** | 造一场含两个 `account_id IS NULL` 的天梯局，断言 `match_players` 正确入库 10 行（验证主键用 `player_slot` 而非 `account_id`） |
| **指标口径测试** | 对 §7.3 的每个阈值（签名英雄 ≥5 场/≥60%/≥10%、有效英雄 ≥3 场/≥50%、近期状态半衰期 7）造边界样本断言取舍；断言同侪 < 30 时返回 `insufficient_samples` |
| **权重聚合测试** | 断言 §7.2 的 `value = Σ(wᵢvᵢnᵢ)/Σ(wᵢnᵢ)` 结果；断言 `map_vision` 权重为 0 时不参与 |
| **决策稳健性测试** | `advise` 的每个 option 必含 `robustness_delta`；构造一个 `robustness_delta > 0.10` 的用例断言其带 `risk_note` 且排序被降权 |
| **错误信封测试** | 断言 `insufficient_data` 返回 200 系（业务结果）而非 5xx；断言 `source_not_allowed` 在请求未授权的来源时触发 |
| **采集配额测试** | 断言限流器在 60 请求/分钟内不触发 429；断言日配额计数与 `collector_attempts` 一致 |

---

## 16. 未验证事项清单

以下事项在实现前必须验证，**不得默认成立**：

1. OpenDota 日配额精确值（观察到 ≥2760，推测 3000）
2. OpenDota 带 API key 的限额（文档仅定性描述）
3. STRATZ 的 GraphQL schema 与 2026 定价（本环境被拦截，未验证）
4. `CDOTAUserMsg_ChatMessage.channel_type` 中除 11 外的取值语义（公开 proto 中无该枚举）
5. 职业选手小号识别无自动化方案（采用种子 + 派对图扩展，需人工确认）
6. manta 在 Apple Silicon 之外平台（尤其 Windows）的解析性能
7. Kaggle 数据集的 `picks_bans.csv` / `draft_timings.csv` **列名与语义**。风险已降低：数据集描述明确声明**字段定义依循 OpenDota API 文档、数据源为 `api.opendota.com`**，因此列结构应等同 OpenDota 的 `picks_bans`（`match_id, is_pick, hero_id, team, order`）。仍需入库前抽样确认，**特别要确认 `order` 是否从 0 起**（OpenDota 的 `ord` 从 0 起；若 CSV 从 1 起，模板映射会整体错位一位）。
8. 先手方 65/35 偏斜的**成因**（推测为赛事选边规则）。若成因是"某阵营系统性获得先手"，则先手特征与阵营特征共线，建模时必须处理。
9. `draft_timings.csv` 的 `extra_time` / `total_time_taken` 是否可用于量化"对手面对摇摆位时的犹豫"（该分析依赖此字段，未验证其填充率）。

**已关闭的未验证项**（曾经存疑，现已实测解决）：
- ✅ `first_pick_team` → 24 手队伍归属映射：全库 1,014 场 **1014/1014 成立**
- ✅ 24 手模板结构：全库验证，异常率约 0.6%
- ✅ Kaggle 数据集许可与规模：**CC0: Public Domain**，总 48.5 GB，所需子集 506 MB
- ✅ DOTA2 回放内语音不可提取：3 个回放中 `CSVMsg_VoiceData` 均为 0 条
