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
| A4 | 数据契约 v1：`Value` / `Policy` / `Playbook` 三组接口 |
| A5 | 分析引擎（线 A）：六维能力项、战队画像、交叉分析 |
| A6 | 序列模型（线 C）：顺序感知的下一手预测 + 基线对照 |
| A7 | 剧本集生成器：按先手/后手分支产出应对方案 |
| A8 | Web 可视化：对手画像卡、BP 推荐面板、剧本速查卡 |
| A9 | 最小回放导入：解析本地 `.dem` 提取对局头信息与 BP，用于训练赛入库 |

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

**结构性不对称（重要）**：对手的训练赛数据无法获得（训练赛保密）。因此 `scrim` 数据**只能流向"自我复盘"分支，永远不得进入对手画像**。这必须在查询层硬性约束，而非依赖调用方自觉。

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

-- 比赛主表
CREATE TABLE matches (
  match_id        BIGINT PRIMARY KEY,
  data_source     TEXT NOT NULL,            -- pro_match | pub_match | scrim
  patch_id        INT REFERENCES patches(patch_id),
  started_at      TIMESTAMPTZ NOT NULL,     -- 统一 UTC
  duration_s      INT,
  league_id       BIGINT,
  series_id       BIGINT,                   -- 系列赛（BO3/BO5）分组
  series_type     SMALLINT,
  first_pick_team SMALLINT,                 -- 0=Radiant 1=Dire，先手方
  radiant_team_id BIGINT,
  dire_team_id    BIGINT,
  radiant_win     BOOLEAN,
  draft_state     TEXT NOT NULL DEFAULT 'pending',
                  -- pending | complete | unavailable
  replay_path     TEXT,                     -- 卷内路径，解析后可置 NULL
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 逐手 BP（这是序列模型的训练数据）
CREATE TABLE draft_actions (
  match_id   BIGINT NOT NULL REFERENCES matches(match_id),
  ord        SMALLINT NOT NULL,             -- 0..23
  is_pick    BOOLEAN NOT NULL,
  team       SMALLINT NOT NULL,             -- 0=Radiant 1=Dire
  hero_id    SMALLINT NOT NULL,
  PRIMARY KEY (match_id, ord)
);

-- 选手×场次（阵容归属的唯一依据）
CREATE TABLE match_players (
  match_id    BIGINT NOT NULL REFERENCES matches(match_id),
  account_id  BIGINT NOT NULL,
  team        SMALLINT NOT NULL,
  hero_id     SMALLINT NOT NULL,
  position    SMALLINT,                     -- 1..5，尽可能推断
  is_radiant  BOOLEAN NOT NULL,
  kills SMALLINT, deaths SMALLINT, assists SMALLINT,
  gpm SMALLINT, xpm SMALLINT,
  last_hits SMALLINT, denies SMALLINT,
  hero_damage INT, hero_healing INT, tower_damage INT,
  obs_placed SMALLINT, sen_placed SMALLINT,
  camps_stacked SMALLINT, runes_pickups SMALLINT,
  PRIMARY KEY (match_id, account_id)
);

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

-- 阵容历史（来自 Liquipedia）
CREATE TABLE rosters (
  team_id BIGINT NOT NULL,
  account_id BIGINT NOT NULL,
  joined_at DATE, left_at DATE,
  position SMALLINT,
  source TEXT NOT NULL,                       -- liquipedia | opendota_snapshot
  PRIMARY KEY (team_id, account_id, joined_at)
);

-- [Phase B 预留，Phase A 只建表不写入]
CREATE TABLE wards (
  match_id BIGINT, ward_type TEXT, team SMALLINT,
  placed_at_s INT, x NUMERIC, y NUMERIC, removed_at_s INT
);
CREATE TABLE teamfights (
  match_id BIGINT, fight_index SMALLINT,
  start_s INT, end_s INT, deaths SMALLINT, winner SMALLINT
);
CREATE TABLE item_timings (
  match_id BIGINT, account_id BIGINT,
  item_id INT, purchased_at_s INT
);
```

### 5.2 采集状态机

```
discovered ──> fetch_draft ──┬── picks_bans 存在 ──> complete
                             │
                             └── 不存在/null ──> pending
                                     │
                                     ├─ 退避重试：+2h, +6h, +12h, +24h, +48h
                                     └─ 超过 7 天仍无 ──> unavailable（UI 标注"数据不完整"）
```

**幂等性要求**：采集器启动时读取各源最后成功位点，回溯补抓。重启不得产生重复数据（依赖主键 upsert）。

---

## 6. 数据契约 v1

这是并行开发的解耦边界。**契约冻结后，引擎与前端可完全独立推进。**

### 6.1 Value —— 局面评估

```
POST /v1/value
{
  "patch": "7.41f",
  "radiant": { "team_id": 7119388,
               "heroes": [{"hero_id": 123, "position": 4}] },
  "dire":    { "team_id": 8261500,
               "heroes": [{"hero_id": 107, "position": 2}] },
  "sources": ["pro_match"],
  "first_pick_team": 0
}

→ 200
{
  "radiant_win_prob": 0.53,
  "confidence": "medium",          // low | medium | high，由样本量决定
  "n_samples": 412,
  "contributions": [               // 必须可解释
    {"factor": "patch_strength",  "delta":  0.021},
    {"factor": "counter_matchup", "delta": -0.014},
    {"factor": "player_comfort",  "delta":  0.038},
    {"factor": "first_pick",      "delta":  0.009}
  ],
  "sources_used": ["pro_match", "pub_match"]
}
```

**要求**：`contributions` 之和必须等于 `radiant_win_prob - 0.5`（允许 ±0.001 浮点误差）。这条不变式是可测的，也是"结论可追溯"的实现方式。

### 6.2 Policy —— 对手下一手预测

```
POST /v1/policy/next
{
  "patch": "7.41f",
  "first_pick_team": 1,
  "draft": [ {"ord":0,"is_pick":false,"team":0,"hero_id":80}, ... ],
  "radiant_team_id": 7119388,
  "dire_team_id": 8261500,
  "sources": ["pro_match"]
}

→ 200
{
  "next_ord": 12,
  "team": 1,
  "is_pick": true,
  "candidates": [
    {"hero_id": 112, "prob": 0.18,
     "reasons": ["该队在此阶段的历史首选", "克制对方已选核心"],
     "evidence_match_ids": [8996973546, 8988636430]}
  ],
  "model": "sequence-v1",
  "baseline": {"frequency_top1": 0.041, "model_top1": null}
}
```

**要求**：`candidates` 概率和为 1.0（±0.001）。`next_ord`/`team`/`is_pick` 由 24 手模板 + `first_pick_team` **确定性推出**，不由模型预测——这是已用真实数据验证的固定结构（7 ban → 2 pick → 3 ban → 6 pick → 4 ban → 2 pick）。

### 6.3 Playbook —— 剧本集（主输出）

```
GET /v1/playbook?us=7119388&them=8261500&patch=7.41f&series_id=...

→ 200
{
  "matchup": {"us": {...}, "them": {...}, "patch": "7.41f",
              "sources_used": ["pro_match"]},
  "bans": {
    "must_ban":    [{"hero_id": 55, "why": "对手签名英雄，我方无人擅长应对",
                     "their_wr": 0.71, "our_wr_against": 0.29, "n": 24}],
    "consider":    [{"hero_id": 77, "why": "...", "if_we_leave_it_open": {...}}],
    "bait_candidates": [{"hero_id": 90, "why": "双方都不擅长，浪费对手 ban 位"}]
  },
  "op_hero_decision": [{
    "hero_id": 83,
    "if_we_pick":    {"wr": 0.58, "n": 12},
    "if_we_ban":     {"wr": 0.50},
    "if_we_leave":   {"wr": 0.44, "their_wr": 0.68, "n": 19,
                      "our_counter_options": [{"hero_id": 36, "wr": 0.61}]},
    "recommendation": "leave_and_counter"
  }],
  "branches": [
    {
      "condition": {"first_pick_team": "them", "their_opening": "teamfight"},
      "plans": [{
        "label": "A1",
        "goal": "拖到中后期，靠分推拉扯",
        "key_picks": [{"priority": 1, "hero_id": 105, "by_ord": 13,
                       "why": "...", "fallback": [67, 19]}],
        "expected_wr": 0.52, "n": 31
      }]
    }
  ],
  "series": {
    "game1_plan": "...",
    "adjustment_rules": [{"if": "对手第 1 局暴露推进体系", "then": "..."}]
  },
  "positions": [
    {"side": "them", "role": 4,
     "notes": [{"kind": "ward", "text": "...", "evidence": [...], "needs": "replay"}],
     "data_available": false}
  ]
}
```

**要求**：
- 每个 `plans[]` 必须含非空 `fallback`（首选被 ban 的路径）——无 fallback 的剧本不允许产出。
- `positions` 中依赖回放的条目必须带 `"data_available": false` 与 `"needs": "replay"`，UI 显式标注"Phase B 提供"，不得静默省略。

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
| | GPM / XPM 分位 | API | 同位置同版本分位 |
| | 分路与游走率 | API | `lane_role` / `is_roaming` |
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
| 眼位/节奏/开雾 | 低 | 中 | **高** |

权重存于配置表，可调。**不得**用单一全局权重——否则会把训练赛的藏招当成真实倾向。

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

**⑤ 的稳健性要求**：选择的不只是期望胜率最高的一手，还要对对手偏离概率分布不敏感。实现方式：对候选手计算"对手按预测分布应对"与"对手按最坏应对"两种情形下的胜率差，差值过大则降权。

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

- 词表：`0..126` = pick 英雄 id，`127..253` = ban 英雄 id，`254..257` = 占位/特殊
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
| 内容 | 采集、存储、六维能力项、战队画像、交叉分析、剧本生成器、Value 接口 | 序列 tokenizer、基线、模型、Policy 接口 |
| 依赖 | 只依赖契约 + 数据库 | 只依赖契约 + 数据集 |
| 能否独立测试 | 是（用 fixtures） | 是（用离线数据集） |
| 完成标志 | `GET /v1/playbook` 对任一 matchup 返回结构完整、不变式通过的响应 | `POST /v1/policy/next` 返回概率和为 1、且优于频率基线 |

**第三条线（可视化）**：前端消费契约的 **mock 数据**先行开发，不等待后端。契约冻结后即可启动。

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
| R9 | 抽帧/回放下载占用带宽与磁盘 | 本地资源耗尽 | 原始回放解析后即删；仅保留结构化结果 |
| R10 | 版本补丁改动为自然语言，无法自动量化 | 版本强度无法用补丁文本直接建模 | Phase A 以**实测胜率变化**代替补丁文本解析；补丁文本仅作展示与人工参考 |

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
| M0 | 仓库骨架 + 契约冻结 | `contracts/openapi.yaml` 提交；三条线的目录边界建立 |
| M1 | 数据地基可用 | 常量/版本表（含字母子版本）灌满；引导数据集 **506 MB 子集**入库并通过异常率校验；`matches`/`draft_actions` 可查 |
| M2 | 采集器常驻运行 | 连续运行 48h 无重复数据；BP 状态机正确标记 `pending`/`complete`/`unavailable` |
| M3 | 六维能力项 + 战队画像 | 对任一战队返回六维百分位；不变式测试通过 |
| M4 | Value 接口可用 | `POST /v1/value` 返回且 `contributions` 求和不变式通过 |
| M5 | 剧本集可用 | `GET /v1/playbook` 返回结构完整，每条 plan 非空 fallback |
| M6 | 可视化可用 | 对手画像卡 + 剧本速查卡可交互；mock 数据切真数据 |
| M7 | 序列模型基线 | 分层频率基线达标并报告 |
| M8 | 序列模型 | 超过基线；分阶段与分版本评估报告完整 |
| M9 | 演示站上线 | 公网可访问，同一 compose |

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

---

## 16. 未验证事项清单

以下事项在实现前必须验证，**不得默认成立**：

1. OpenDota 日配额精确值（观察到 ≥2760，推测 3000）
2. OpenDota 带 API key 的限额（文档仅定性描述）
3. STRATZ 的 GraphQL schema 与 2026 定价（本环境被拦截，未验证）
4. `CDOTAUserMsg_ChatMessage.channel_type` 中除 11 外的取值语义（公开 proto 中无该枚举）
5. 职业选手小号识别无自动化方案（采用种子 + 派对图扩展，需人工确认）
6. manta 在 Apple Silicon 之外平台（尤其 Windows）的解析性能
7. Kaggle 数据集的 `picks_bans.csv` / `draft_timings.csv` **列名与语义**（文件存在、大小已知，但列结构未验证）。入库前必须先抽样检视，确认含 `match_id` / `ord` / `is_pick` / `team` / `hero_id`，且 `ord` 语义与 OpenDota 一致。
8. 先手方 65/35 偏斜的**成因**（推测为赛事选边规则）。若成因是"某阵营系统性获得先手"，则先手特征与阵营特征共线，建模时必须处理。
9. `draft_timings.csv` 的 `extra_time` / `total_time_taken` 是否可用于量化"对手面对摇摆位时的犹豫"（该分析依赖此字段，未验证其填充率）。

**已关闭的未验证项**（曾经存疑，现已实测解决）：
- ✅ `first_pick_team` → 24 手队伍归属映射：全库 1,014 场 **1014/1014 成立**
- ✅ 24 手模板结构：全库验证，异常率约 0.6%
- ✅ Kaggle 数据集许可与规模：**CC0: Public Domain**，总 48.5 GB，所需子集 506 MB
- ✅ DOTA2 回放内语音不可提取：3 个回放中 `CSVMsg_VoiceData` 均为 0 条
