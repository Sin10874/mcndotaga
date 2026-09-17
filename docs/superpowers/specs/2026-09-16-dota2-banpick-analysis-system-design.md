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

Phase A 的数据模型为 B 与 D 预留接入点（回放事件表 `wards`/`teamfights`/`item_timings`/`gold_curves`、`players.is_smurf_candidate` 的小号标记字段）。

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
| OpenDota `patch` 字段**丢失子版本**（7.41a–f 全部返回 `patch: 60`） | 必须用 `D2-LRG-Metadata/patchdates.json` 按 `start_time` 还原子版本。**字母映射规则必须在代码里固化**（见下） |

**子版本还原规则（已实测校准 —— 用错规则会让约 20 个版本序列整体错位一个字母，最大偏差 47 天）**

`patchdates.json` 的每条记录形如 `{code, main, add?, dates[]}`。正确映射是：

```
main          -> <code>              (基础版本，如 '7.41')
add           -> <code> + 'a'        (若存在)
dates[0]      -> <code> + 'b'
dates[1]      -> <code> + 'c'
...           -> 依次递增（b, c, d, e, f, ...）
```

**关键**：`dates[]` **从 'b' 起，不是从 'a' 起**。`add` 才是 'a'。

实测校准（对照 Valve `patchnoteslist`）：83 个可核验字母槽中，**34 个日期完全一致**，48 个相差 ±1–2 天（发布与公告的时间差），**仅 1 个异常**（7.22d 差 34 天）。若改用"`dates[]` 从 'a' 起"的读法，**平均误差升至 26.3 天**。另有两处已知例外需在入库时告警：**7.22**（含一个未排序日期）、**7.25**（其 'a' 落在 `dates[0]`）。

**两个来源的分工（不可混用）**：

| 来源 | 覆盖 | 用途 |
|---|---|---|
| Valve `patchnoteslist` | **118 个版本，其中 84 个字母版本，从 7.08 起** | **权威版本清单**。`patches` 表的行集以此为准 |
| `patchdates.json` | **141 个字母槽**（含 Valve 未收录的 6.86–7.07 段），按 OpenDota 的粗粒度 patch id 索引 | **只用于把 OpenDota 的 `patch: 60` 细分到子版本**。其时间戳整体比 Valve 晚约 1 天 |

**入库规则**：以 Valve 的时间戳为准；`patchdates` 的值仅用于填写那些 Valve 列表未覆盖的序列，且**必须与 Valve 交叉校验**，偏差超过 ±2 天则记录告警并以 Valve 为准。M1 的"84 个字母子版本"这一验收数字**来自 Valve**，不可能从 `patchdates` 得出（它有 141 个槽）。
| 回放头**不含补丁号字符串**。实测解码参考比赛回放头：`demo_version_name = "valve_demo_2b"`（这是 **demo 格式**版本，与游戏补丁无关），`demo_version_guid = "8e9d71ab-…"` | **不要**用 `demo_version_name` 判定版本。导入训练赛的版本须由下行的 build 号映射 |
| **可用的版本线索是 build 号**：同一回放头含 `CSVCMsg_ServerInfo.game_dir = "/opt/srcds/dota/dota_v6932/dota8"`，正则 `dota_v(\d+)` 提取 **build 6932**。另有 `CDemoFileHeader.patch_version`(int) 与 `build_num`(int) | A9 的版本判定改用 build 号。6932 落在 7.41e，与参考比赛的实际版本一致（见下条）|
| 补丁改动是**自然语言字符串**（如 `"from 60% to 65%"`），非数值字段 | Phase A 只做结构化存储与展示，不做数值解析 |
| **参考比赛的实际版本是 7.41e，不是 7.41f**。其 `start_time = 1789301470 = 2026-09-13 12:11 UTC`；`patchdates.json` 中 7.41e = 1785456079（2026-07-31）、7.41f = 1789498134（2026-09-15） | §6 的全部示例据此标注为 `7.41e`。**这也是子版本还原逻辑的活体验证**：若按 OpenDota 的 `patch: 60` 只会得到"7.41"，若误用最新版本会得到"7.41f"——两者都错 |

**build 号 → 补丁 的映射来源**：`SteamTracking/GameTracking-Dota2` 的提交按 build 号命名（首个 token 即 build），并与 `game/dota/steam.inf` 的 `ClientVersion` 一致。需自建并维护一张 `build_num → patch` 表（`patches` 表加 `build_min`/`build_max` 两列）。**注意该仓库未声明许可证**（见 §12 R8），因此只用于提取 build 与版本的对应事实，不复制其文件内容。

### 3.3 回放

| 事实 | 数值/结论 |
|---|---|
| 下载 URL | `http://replay{cluster}.valve.net/570/{match_id}_{replay_salt}.dem.bz2`，**无需门票** |
| 实际压缩格式**不统一** | 实测：参考比赛（2026-09-13）为 **Zstandard**（magic `28 b5 2f fd`）；比赛 `8700024338`（2026-02-21）为 **bzip2**（magic `42 5a 68` = `BZh`）。**必须按 magic 嗅探**，不能假定单一格式。误用 `bzip2 -d` 对 zstd 会失败，反之亦然 |
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
│ 调度器（幂等·可回溯）→ 限流器（60/min）→ 回放下载（magic 嗅探 zstd/bzip2）    │
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
3. **对手侧数据路径禁止出现 `scrim`**：`/v1/playbook`（`them` 侧统计）、`/v1/value`（`dire` 侧统计）、**`/v1/profile`（`them` 队伍的画像）**、`/v1/policy/next`、`/v1/advise` 均为对手侧路径，只能走视图，其代码路径不接受 `allow_scrim` 参数。`/v1/profile` 是`them` 画像卡的数据来源，此前遗漏，现补入。
4. **负向测试**（§15）：插入一条 `scrim` 记录后，断言它不出现在任何对手画像接口的响应中。这是本条规则的唯一有效证明方式。

---

## 5. 数据模型

### 5.1 核心表

```sql
-- 版本（含字母子版本）
CREATE TABLE patches (
  patch_id        SERIAL PRIMARY KEY,
  version_name    TEXT NOT NULL UNIQUE,     -- '7.41e'
  base_version    TEXT NOT NULL,            -- '7.41'
  released_at     TIMESTAMPTZ NOT NULL,
  opendota_patch  SMALLINT,                 -- OpenDota 的粗粒度 id，如 60
  build_min       INT,                      -- 该版本对应的客户端 build 区间下界
  build_max       INT,                      -- 上界（含）。用于把回放头的 build 号映射到版本
  is_cm_pool_snapshot JSONB                 -- 该版本的 CM 可用英雄（版本化的权威来源）
);

-- ===== 常量层（A1 交付物；M1 据此校验"英雄 = 127、道具 = 501"）=====
-- dense_index 是序列模型 token 的基（§10.1）：hero_id 稀疏（1..155，含 9 个 > 126），
-- 不能直接当 token 用，否则 hero 128 作 pick 会解码成"英雄 1 被 ban"。
--
-- 【dense_index 的派生规则 —— 必须固定，否则常量表一刷新 token 就会静默重映射】
--   初次派生：dense_index := 按 hero_id 升序排序后的下标（0-based）
--            断言：dense_index == row_number() OVER (ORDER BY hero_id) - 1
--
--   ⚠ 仅靠"追加"不足以保证稳定：hero_id 在 1..155 中有 28 个空位，
--     若 Valve 日后用其中一个空位发布新英雄，朴素重排会让**其后所有英雄的
--     dense_index 整体位移**，而上述断言仍然通过（因为它只校验内部一致性）。
--   因此稳定性必须靠**冻结快照**而非靠顺序假设：
--     1) dense_index 是**版本化常量快照**的一部分，快照存于 constants_snapshot 表；
--     2) 重派生只在**模型重训**时发生，且必须显式提升 snapshot_version；
--     3) 迁移检查：对快照 N 与 N+1 断言所有**已存在**的 hero_id 其 dense_index 不变，
--        变化必须报错并强制模型版本号同步提升，禁止静默重映射。
-- 常量快照版本（dense_index 稳定性的载体，见下）。必须先于引用它的表创建。
CREATE TABLE constants_snapshot (
  snapshot_version INT PRIMARY KEY,
  fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  n_heroes         SMALLINT NOT NULL,      -- M1 断言 = 127
  n_items          SMALLINT NOT NULL,      -- M1 断言 = 501
  source           TEXT NOT NULL DEFAULT 'dotaconstants'
);

-- 英雄/道具的**规范实体**：不随快照变化，是 draft_actions/match_players 的 FK 目标。
CREATE TABLE heroes (
  hero_id        SMALLINT PRIMARY KEY,
  name           TEXT NOT NULL,          -- npc_dota_hero_*
  localized_name TEXT NOT NULL,
  primary_attr   TEXT,
  roles          TEXT[],                 -- 实测 8 项词表，见 §7 维度 6
  cm_enabled     BOOLEAN                 -- 当前是否在 CM 池。**版本化的权威来源是
                                         -- patches.is_cm_pool_snapshot**，此列仅供快速过滤
);
CREATE TABLE items (
  item_id INT PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,
  dname   TEXT,                          -- 可空：上游 501 个道具中有 10 个 dname 为 null
  cost    INT
);

-- 版本化的**token 索引映射**（序列模型 token 的基，§10.1）。
-- 独立成表而非塞进 heroes，正是为了让 dense_index 可以被冻结、被版本化、
-- 并在迁移时逐 hero_id 对比——这是防止 token 静默重映射的唯一手段。
CREATE TABLE hero_token_index (
  snapshot_version SMALLINT NOT NULL REFERENCES constants_snapshot(snapshot_version),
  hero_id          SMALLINT NOT NULL REFERENCES heroes(hero_id),
  dense_index      SMALLINT NOT NULL,
  PRIMARY KEY (snapshot_version, dense_index),
  UNIQUE (snapshot_version, hero_id)
);

-- ===== 实体（必须先于所有引用它们的表创建）=====
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

-- 全局配置（单行表）。owned_team_id 是 §4.1 隔离规则的判定依据。
CREATE TABLE app_config (
  id             BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),  -- 强制单行
  owned_team_id  BIGINT REFERENCES teams(team_id),             -- "我方"战队
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 可调参数的键值存储。§6.6/§7/§9.1 引用的三个键都落在这里——原设计只说
-- "存于 app_config" 但那张单行表没有放键值的地方，实现者无处可写。
CREATE TABLE app_config_kv (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL,
  note        TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- M1 必须种入的键（缺任一个则相关功能不可用）：
--   'archetype_role_map'   §7   维度6 的 roles→原型 映射表
--   'robustness_lambda'    §6.6 penalized_score 的 λ，默认 1.0
--   'op_decision_min_delta' §9.1 三选一判定的最小差异阈值，默认 0.02
--   'min_sample_n'         §7.3/§9.1 的样本量门槛，默认 30

-- ===== 比赛 =====
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
  first_blood_time INT,                     -- 秒；**这是 /matches/{id} 的直接字段**（实测值 35）
  first_tower_time INT,                     -- 秒；**派生值**，见下方说明
  first_roshan_time INT,                    -- 秒；**派生值**，可空（无肉山则 NULL）
  -- ⚠ 只有 first_blood_time 是 OpenDota 的直接字段。first_tower_time 与
  --   first_roshan_time **必须从 /matches/{id} 的 objectives[] 数组推导**：
  --   取 type='CHAT_MESSAGE_FIRSTBLOOD' 之外，type 为建筑/肉山相关的事件，
  --   按 time 升序取首个。推导逻辑须写单元测试（§15 节奏字段测试）。
  -- 三个字段均仅在对应数据存在时有效；未 parse 的场次为 NULL。
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
  hero_id    SMALLINT NOT NULL REFERENCES heroes(hero_id),
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
              -- 【归一化值】入库规则（**必须照此实现**）：
              --   raw < 128  ->  player_slot = raw            (Radiant, 0-4)
              --   raw >= 128 ->  player_slot = raw - 123      (Dire,    5-9)
              -- 即 128->5, 129->6, 130->7, 131->8, 132->9。
              -- ⚠ 绝不能用 raw % 128：128%128=0、132%128=4，会把 Dire 整体
              --   塌缩到 Radiant 上，并被 slot_team_agree 拒绝或主键冲突。
  account_id  BIGINT REFERENCES players(account_id),   -- 可空：匿名选手
  team        SMALLINT NOT NULL CHECK (team IN (0,1)), -- 侧别的唯一权威字段
  hero_id     SMALLINT NOT NULL REFERENCES heroes(hero_id),
  position    SMALLINT CHECK (position BETWEEN 1 AND 5),
  kills SMALLINT, deaths SMALLINT, assists SMALLINT,
  gpm SMALLINT, xpm SMALLINT,
  last_hits SMALLINT, denies SMALLINT,
  hero_damage INT, hero_healing INT, tower_damage INT,
  damage_taken JSONB,                       -- 上游为**按来源分组的字典**（如 {"npc_dota_hero_x": 12345, ...}），
                                            -- 不是整数。原样存储以保留可追溯性。
  damage_taken_total INT,                   -- = sum(damage_taken 的各值)。§7 维度 3「承伤占比」用此列，
                                            -- 聚合规则显式定义，避免各处自行求和得出不同结果。
  net_worth INT,                            -- §7 维度 3「经济转化率」的分母
  obs_placed SMALLINT, sen_placed SMALLINT,
  observer_kills SMALLINT,                  -- §7 维度 4「反眼数」的输入（上游字段名为 observer_kills）
  sentry_kills SMALLINT,                    -- §7 维度 4「反眼数」（真眼被反）
  camps_stacked SMALLINT, rune_pickups SMALLINT,   -- 注意：上游字段名是 rune_pickups（无 s）
  lane_role   SMALLINT,
  is_roaming  BOOLEAN,
  firstblood_claimed BOOLEAN,               -- §7 维度 5「首杀参与率」的输入
  stats_available BOOLEAN NOT NULL DEFAULT false,
                  -- false 表示该场未被 OpenDota parse，统计列不可用（§7 降级依据）
  PRIMARY KEY (match_id, player_slot),
  -- 归一化 slot 的 0-4/5-9 分段必须与 team 自洽，防止两套侧别编码漂移
  CONSTRAINT slot_team_agree CHECK ((player_slot < 5) = (team = 0))
);
CREATE INDEX ON match_players (account_id) WHERE account_id IS NOT NULL;

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
-- metric 的取值必须与 §7.2 矩阵的行一一对应（含 tempo 与 map_vision 两行）。
CREATE TABLE metric_weights (
  metric      TEXT NOT NULL CHECK (metric IN
                ('patch_strength','hero_pool','system_pref','bp_tendency',
                 'tempo','map_vision')),
  data_source TEXT NOT NULL CHECK (data_source IN ('pub_match','scrim','pro_match')),
  weight      NUMERIC NOT NULL CHECK (weight >= 0),  -- 0 = 该源对此指标无贡献
  note        TEXT,
  PRIMARY KEY (metric, data_source)
);
-- 聚合规则：weighted_value = Σ(w_i × v_i × n_i) / Σ(w_i × n_i)
-- 其中 v_i 为该源上的指标值，n_i 为样本量。分母为 0 或 Σ(w_i × n_i) < 30 时
-- 返回 §6.0 降级形态 insufficient_samples。
-- Phase A 种子：共 6 指标 × 3 源 = 18 行，全部有效。map_vision 用数量口径
-- （API 可得，见 §7 维度 4）；仅坐标/热力图属 Phase B，不影响该行权重取值。

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
  match_id BIGINT NOT NULL,
  player_slot SMALLINT NOT NULL,          -- 与 match_players 的归一化 slot 一致
  item_id INT NOT NULL REFERENCES items(item_id),
  purchased_at_s INT NOT NULL,
  PRIMARY KEY (match_id, player_slot, item_id, purchased_at_s),
  FOREIGN KEY (match_id, player_slot)
    REFERENCES match_players(match_id, player_slot) ON DELETE CASCADE
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

`next_ord` = 已给 `draft` 中的最大 `ord` + 1；**`draft` 为空数组时 `next_ord = 0`**。`draft` 中的每一手都必须与 `resolve()` 一致，否则返回 `invalid_request`。`draft` 已满 24 手时返回 `invalid_request`（无下一手可预测）。

**枚举（单一定义，不得在别处另立）**

| 枚举 | 取值 | 判定规则 |
|---|---|---|
| `confidence` | `low` \| `medium` \| `high` | 按 `n_samples`：< 30 → low；30–199 → medium；≥ 200 → high |
| `recommendation` | `pick` \| `ban` \| `leave_and_counter` \| `insufficient_data` | 见 §9.1 |
| `their_opening` | `teamfight` \| `push` \| `pickoff` \| `splitpush` \| `protect` \| `initiate` \| `unknown` | 由 §7 维度 6 的战队聚合取**权重最大项**；最大项 < 0.25 时归 `unknown` |
| `notes[].kind` | `ward` \| `timing` \| `lane` \| `smoke` \| `combat` \| `resource` \| `communication` | — |
| `Factor`（用于 `contributions[].factor`） | `patch_strength` \| `counter_matchup` \| `player_comfort` \| `first_pick` | 与 `metric_weights.metric` 是**不同**的枚举：前者是 Value 响应的加项分解（4 项），后者是三源权重配置的键（6 项）。**两者的交集恰好只有 `patch_strength` 一个**（`counter_matchup`≠`bp_tendency`，`player_comfort`≠`hero_pool`，`first_pick` 在 metric 侧无对应）。不得互相赋值、不得假设同名即同义 |
| `unavailable_reason` | `needs_replay` \| `insufficient_samples` \| `source_not_allowed` \| `stat_unavailable` | 见下 |
| `error.code` | `insufficient_data` \| `invalid_request` \| `source_not_allowed` \| `not_found` \| `upstream_unavailable` | — |

**`unavailable_reason` 的产生条件（每个取值都必须有明确的生产者，否则枚举形同虚设）**

| reason | 何时产生 | `needs` |
|---|---|---|
| `needs_replay` | 该指标只能从回放解析得到（眼位坐标、10 分钟补刀差、团战、装备时间线） | **必填**，值 `"Phase B"` |
| `insufficient_samples` | 数据存在但样本量低于门槛（同侪 < 30、或 §9.1 的逐量 `n < 30`） | 必须省略 |
| `source_not_allowed` | 请求的来源不含该指标所需的数据（如对手画像路径被拒用 `scrim`） | 必须省略 |
| `stat_unavailable` | **该场比赛 `stats_available = false`**（OpenDota 未 parse），或上游 payload 缺该字段（如 `first_roshan_time` 无肉山、上游 10 个道具 `dname` 为 null）。**判定规则**：`matches.parse_state <> 'full'` 时，一切依赖 OpenDota parse 的统计列（§7 维度 1–5 中标注"需 `stats_available`"的行）必须返回本 reason，**不得返回 0 或 NULL 冒充有值** | 必须省略 |

**优先级**（一条数据可能同时满足多个）：`source_not_allowed` > `stat_unavailable` > `insufficient_samples` > `needs_replay`。

**可选性标记**：本节所有字段默认**必填**。可选字段一律显式标注 `// optional`，且其缺省语义为"该维度无数据"，不得与"值为 0"混淆。

**降级契约（两种层级，勿混用）**

**第一层 · 字段级降级**——响应整体成功（HTTP 200），但个别指标算不出来。该字段一律返回：

```json
{"value": null, "reason": "needs_replay", "needs": "Phase B"}
```

- `reason` 必填，取值见 `unavailable_reason` 枚举。
- `needs` **仅当 `reason == "needs_replay"` 时必填**（`// optional`），值为 `"Phase B"`；其余 reason 下**必须省略该字段**（不得传 null）。§15 的降级契约测试据此断言。
- 绝不允许省略字段，也绝不允许填 0 冒充有值。

**第二层 · 整体不足**——整个响应无法给出结论（如模型未就绪、样本远低于门槛）。返回 **HTTP 200** + 错误信封，`code` 为 `insufficient_data`：

```json
{"error": {"code": "insufficient_data",
           "message": "对手在本版本仅 4 场有效比赛，低于 30 场门槛",
           "detail": {"n_samples": 4, "required": 30}}}
```

**`insufficient_data` 是业务结果，不是传输错误**——因此走 200。§15 的「错误信封测试」断言这一点。

**错误信封（统一形状）**：

```json
{"error": {"code": "<error.code>", "message": "人类可读说明", "detail": {}}}
```

| code | HTTP | 触发条件 |
|---|---|---|
| `insufficient_data` | **200** | 整体样本不足以支撑结论 |
| `invalid_request` | 400 | schema 校验失败；`draft` 与 `resolve()` 不一致 |
| `source_not_allowed` | 403 | 请求了未授权的来源（如对手画像路径请求 `scrim`，见 §4.1） |
| `not_found` | 404 | `team_id` / `patch` 不存在 |
| `upstream_unavailable` | 503 | OpenDota 等上游不可达**且**无缓存可用 |

### 6.1 Value —— 局面评估

```
POST /v1/value
{
  "patch": "7.41e",
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
  "patch": "7.41e",
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
  "sources": ["pro_match"],
  "top_n": 10
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
  "top_n": 10,
  "other_prob": 0.820,
  "model": "sequence-v1",
  "baseline": {"frequency_top1": 0.041, "model_top1": null},
  "sources_used": ["pro_match"]
}
```

上例的 `draft` 是**真实比赛 8996973546 的前 12 手**，`first_pick_team=0`；`next_ord=12` 由模板推出为 `O` 的 pick，即 `team = 1 - 0 = 1` ✓（真实结果正是 Dire 在第 12 手选 Winter Wyvern）。

**不变式（可测）**：
- `sum(candidates[].prob) + other_prob == 1.0`，容差 ±0.001。`candidates` 是 **top-N 截断**（`top_n` 声明 N），`other_prob` 承载未列出的尾部概率。上例：`0.180 + 0.820 = 1.0` ✓
- `len(candidates) <= top_n`；`other_prob >= 0`。`top_n` 是**请求参数**（`// optional`，默认 10，上限 127）；响应回显实际使用的值。示例中 `len(candidates)=1 <= top_n=10` 是合法的——示例刻意只列一条以避免文档过长，**不变式约束的是上界而非等于**
- `(next_ord, team, is_pick) == resolve(max_ord(draft)+1, first_pick_team)`，即 §6.0 的 `resolve()`
- `candidates[].hero_id` 不得与 `draft` 中已出现的 hero 重复
- `baseline.model_top1` 在模型未就绪时为 `null`（`// optional`，语义为"无模型"），前端须能渲染此态
- `reasons` 非空（不得返回无依据的候选）

### 6.3 Playbook —— 剧本集（主输出）

```
GET /v1/playbook?us=10251056&them=10232231&patch=7.41e&series_id=1141522&sources=pro_match

→ 200
{
  "matchup": {
    "us":   {"team_id": 10251056, "name": "Dawn Bulls", "tag": "DB"},   // ProfileRef，见 §6.4
    "them": {"team_id": 10232231, "name": "Klim Sani4", "tag": "KS"},
    "patch": "7.41e",
    "first_pick_team": 0,
    "side_map": {"us": 0, "them": 1}
  },
  "sources_used": ["pro_match"],
  "coverage": {
    "pro_match": {"n_matches": 42, "n_stat_available": 38},
    "pub_match": {"n_matches": 0,  "n_stat_available": 0}
  },
  "data_quality": {
    "n_pending_draft": 1,
    "n_unavailable_draft": 0,
    "n_anomalous_draft": 2,
    "oldest_pending_hours": 31,
    "note": "1 场 BP 数据尚未就绪（OpenDota 约 2.6 天滞后），未计入统计"
  },
  "bans": {
    "must_ban": [{"hero_id": 55, "why": "对手签名英雄，我方无人擅长应对",
                  "their_wr": 0.71, "our_wr_against": 0.29, "n": 24}],
    "consider": [{"hero_id": 77, "why": "...",
                  "if_we_leave_it_open": {
                    "hero_id": 77,
                    "if_we_pick":  {"wr": 0.47, "n": 33},
                    "if_we_ban":   {"wr": 0.49, "n": 58},
                    "if_we_leave": {"our_wr": 0.55, "their_wr": 0.45, "n": 40,
                                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
                    "recommendation": "leave_and_counter"}}],
    "bait_candidates": [{"hero_id": 90, "why": "双方都不擅长，浪费对手 ban 位"}]
  },
  "op_hero_decision": [{
    "hero_id": 83,
    "if_we_pick":  {"wr": 0.44, "n": 34},
    "if_we_ban":   {"wr": 0.50, "n": 62},
    "if_we_leave": {"our_wr": 0.58, "their_wr": 0.42, "n": 41,
                    "our_counter_options": [{"hero_id": 36, "wr": 0.61, "n": 31}]},
    "recommendation": "leave_and_counter"
  }],
  "branches": [
    {
      "branch_id": "A",
      "applies_to_game": 1,
      "condition": {"first_pick": "us", "their_opening": "teamfight"},
      "plans": [{
        "label": "A1",
        "goal": "拖到中后期，靠分推拉扯",
        "key_picks": [{"priority": 1, "hero_id": 105, "by_ord": 13,
                       "why": "...", "fallback": [67, 19]}],
        "expected_wr": 0.52, "n": 31,
        "robustness_delta": 0.04,
        "penalized_score": 0.52
      }]
    },
    {
      "branch_id": "B",
      "applies_to_game": 2,
      "condition": {"first_pick": "them", "their_opening": "push"},
      "plans": [{
        "label": "B1",
        "goal": "对手先手时抢下反制核心",
        "key_picks": [{"priority": 1, "hero_id": 19, "by_ord": 12,
                       "why": "...", "fallback": [67]}],
        "expected_wr": 0.51, "n": 31,
        "robustness_delta": 0.05,
        "penalized_score": 0.51
      }]
    }
  ],
  "series": {
    "series_id": 1141522,
    "games": [
      {"game_no": 1, "first_pick_team": 0},
      {"game_no": 2, "first_pick_team": 1,
       "note": "第 1 局的负者获得第 2 局先手权；该值在第 1 局结束后才能确定，未确定时为 null——此时不得产出指向该局的 them 分支"}
    ],
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
- 每个 `plans[]` 的 `key_picks` **非空**，且其中**每一项**都必须有非空 `fallback`（该首选的替代路径）。**无 fallback 的 key_pick 不得产出**——这是产品硬要求（§13 补充事项 2），不是建议。（`fallback` 位于 `key_picks[]` 内，不是 plan 级字段；上一版不变式误写成 plan 级，与本节示例自相矛盾。）
- `branches[].condition.first_pick` ∈ {`"us"`,`"them"`}，且所有（2 先手 × 7 体系）组合必须被覆盖或有显式 `unknown` 分支。
- **先手方与 side_map 的自洽规则（可判定）**：定义 `us_has_first_pick := (side_map["us"] == first_pick_team)`，并记 `expected := "us" if us_has_first_pick else "them"`。该字段的合法性分两种情形：
  - **单局剧本**（响应不含 `series`）：`matchup.first_pick_team` 是本局已知值，故**所有**分支必须 `condition.first_pick == expected`。
  - **系列赛剧本**（响应含 `series`）：BO3/BO5 中先手权会易手，故分支**允许**取 `us` 与 `them` 两者，但此时每个分支必须带 `applies_to_game`（整数，≥1）指向 `series.games[]` 中的某一局，且该局的 `first_pick_team` 推导出的 `expected` 必须与该分支一致；该局 `first_pick_team` 为 `null` 时无法推导，**不得**作为 `applies_to_game` 的目标。
  - **示例验算**：`side_map.us = 0`、`first_pick_team = 0` → `expected = "us"`。分支 A 标 `"us"` 且 `applies_to_game = 1`（第 1 局 `first_pick_team = 0` → `expected = "us"` ✓）；分支 B 标 `"them"` 且 `applies_to_game = 2`（第 2 局 `first_pick_team = 1` → `expected = "them"` ✓）。两分支的 `key_picks[].by_ord` 亦与所属局自洽：ord 13 在 `first_pick_team = 0` 时属我方，ord 12 在 `first_pick_team = 1` 时属我方 ✓。本示例同时由 `Playbook` schema 与 `contracts/tools/invariants.py` 的 `check_playbook` 校验（Task 6 起）；**示例与规则冲突时一律以规则为准**。
- **`by_ord` 是绝对手数（0–23），不是分支内的相对序号。** 其归属方由 §6.0 的 `resolve(by_ord, first_pick_team)` 确定性决定，且**必须与所属分支自洽**：`first_pick: "us"` 的分支里，每个 `by_ord` 按 `resolve()` 都必须落在我方手上。示例验算：`first_pick_team = 0` 时 `resolve(13) = (pick, team 0)` = 我方 ✓（真实数据里 ord 13 正是 team 0 选 Techies）。不自洽则该 plan 为 `invalid_request`。
- `op_hero_decision[].recommendation` ∈ §6.0 枚举；`n` 不足以支撑时必须是 `insufficient_data` 而非猜测。
- `if_we_leave` 中 `our_wr + their_wr == 1.0`（±0.001）——这是**同一批比赛的两个视角**，我方赢了就是对方输了。`n` 为此批比赛的场次数，两个比率共用。示例：`0.58 + 0.42 = 1.0` ✓
- `if_we_pick`/`if_we_ban`/`if_we_leave` 中的**每个胜率各有一个 `n`**；**§9.1 的 `n >= 30` 门槛逐量判定**，不是逐条目判定。任一量不足即该量降级，且 `recommendation` 必须为 `insufficient_data`。
- **示例一致性验算**（§9.1 规则）：`n` 全部 ≥ 30 ✓；`max(0.44, 0.50, 0.58) = 0.58`（`if_we_leave`）→ 取 `leave` 分支；`0.58 − 0.50 = 0.08 > 0.02` ✓；`wr_counter 0.61 > wr_leave 0.58` ✓ → `recommendation = "leave_and_counter"` ✓。本例的业务含义是：该 OP 英雄在我方手里只得 0.44，但放给对手后我方有反制手段、胜率反升至 0.58——**放比抢更好**
- `consider[].if_we_leave_it_open` 与 `op_hero_decision[]` **同形**（`OpHeroOption`），不得各写一套。
- `matchup.side_map` 必须含 `us`/`them` 两个键且取值互异（0 与 1 各一），并与 `first_pick_team` 一起用于上一条的自洽判定。
- `series_id` 为 `// optional`；缺省时不返回 `series` 对象（而非返回 null 字段）。
- 所有依赖回放的指标使用 §6.0 的统一降级形态，**不得**静默省略字段。

### 6.4 Profile —— 战队与选手画像

M3 与 A8 的对手画像卡依赖此资源。这是 Value 与 Playbook 的共用底座。

**`ProfileRef`（§6.3 引用的形状）**——精简引用，只含识别信息，不含统计：

```json
{"team_id": 10232231, "name": "Klim Sani4", "tag": "KS", "logo_url": null}
```

`logo_url` 为 `// optional`。`ProfileRef` **不含** `sources_used`/`coverage`/`dimensions`——那些只在完整 Profile 响应里出现。前端需要完整画像时另调 `/v1/profile`。

**字段定义（M3 引擎不得自行发明）**：

| 字段 | 定义 |
|---|---|
| `hero_pool.signature[].pct` | 该英雄场次占该选手**同窗口总出场**（`window_games`）的百分比（整数 0–100）。**示例验算**：`window_games=48`，hero 55 出场 12 → `12/48 = 25%` ✓ |
| `hero_pool.comfortable` | 场次 ≥ 3 且胜率 ≥ 0.50，但**不满足** signature 三条阈值（场次 ≥ 5、胜率 ≥ 0.60、占比 ≥ 10%）的英雄。**示例验算**：hero 77（8 场 / 50% / 17%）胜率低于 0.60 故不属 signature ✓。**注意**：hero 55（12 场 / 75% / 25%）三条全满足，必须落在 `signature` 而非 `comfortable` |
| `hero_pool.window_games` | 该选手在该窗口内的总出场数。它是 `pct` 的分母，也是 `effective_count` 的上界校验依据（`effective_count <= window_games / 3`） |
| `hero_pool.effective_count` | 满足"场次 ≥ 3 且胜率 ≥ 0.50"的英雄个数（§7.3）。**示例验算**：`14 <= 48/3 = 16` ✓ |
| `hero_pool.presence_pick_rate` | 该选手的英雄被放出（未被对手 ban）时他选中其中之一的比率。分母＝该选手参与且其英雄池中至少一个英雄未被 ban 的场次数；分子＝其中他选了池内英雄的场次数 |
| `dimensions.*.percentile` | 该选手在 §7.3 定义的**同侪集合**中的百分位（0–100，整数） |
| `dimensions.hero_archetype` | 六类原型权重（含 `initiate`），各项之和为 1.0（±0.001） |

**百分位的分子与分母是**有意**不同的population**：分子用**请求的字母子版本**（本示例中是 `7.41e`，因为版本强度必须精确），分母用 **`base_version`**（如 `7.41`，因为同侪样本量必须足够）。这是刻意的取舍，不是 bug——实现时不得"修正"为同一个版本。

```
GET /v1/profile?team_id=10232231&patch=7.41e&as_of=2026-09-16&sources=pro_match,pub_match

→ 200
{
  "team_id": 10232231,
  "patch": "7.41e",
  "as_of": "2026-09-16",
  "sources_used": ["pro_match", "pub_match"],
  "coverage": {"pro_match": {"n_matches": 42, "n_stat_available": 38,
                             "n_position_unknown": 5},
               "pub_match": {"n_matches": 310, "n_stat_available": 298,
                             "n_position_unknown": 12}},
  "players": [{
    "account_id": 123456,
    "name": "示例选手",
    "role": 4,
    "hero_pool": {
      "window_games": 48,
      "signature":   [{"hero_id": 55, "games": 12, "wr": 0.75, "pct": 25}],
      "comfortable": [{"hero_id": 77, "games": 8,  "wr": 0.50, "pct": 17}],
      "effective_count": 14,
      "presence_pick_rate": 0.81
    },
    "dimensions": {
      "hero_pool":     {"percentile": 88, "n": 120},
      "laning":        {"percentile": 71, "n": 120},
      "combat":        {"percentile": 64, "n": 120},
      "map_vision":    {"percentile": 47, "n": 120},
      "tempo":         {"percentile": 55, "n": 120},
      "hero_archetype":{"initiate": 0.24, "protect": 0.18, "push": 0.17,
                        "teamfight": 0.19, "pickoff": 0.13, "splitpush": 0.09}
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
| 眼位**坐标**、热力图 | 需回放解析（Phase B） | `positions[].notes[kind=ward]` 返回 `needs_replay`。**注意 `map_vision` 指标本身在 Phase A 可用**（数量口径，见 §7 维度 4），不要误降级它 |
| 10 分钟补刀差/经验差 | 需回放解析 | 用 GPM/XPM 分位替代（`laning` 维度） |
| 开雾次数/时机 | 需回放解析 | 不提供 |
| 团战切分与胜负 | 需回放解析（A9 只解头信息与 BP） | 不提供 |
| 装备时间线 | 需回放解析 | 不提供 |
| 经济/经验曲线（`gold_curves` 表） | 表已建但 Phase A 不写入 | 不提供；Playbook 中不得出现相关字段 |
| 沟通/语音 | 回放内 0 条语音（§3.3），只能自录 | 不提供 |

### 6.6 Advise —— 整手建议（决策层 ⑤ 的接口）

线 C 的交付物 A10。离线路径产出 Playbook 的 `plans[]`，在线路径服务赛时 BP。

```
POST /v1/advise
{
  "mode": "realtime",
  "patch": "7.41e",
  "us": 10251056, "them": 10232231,
  "first_pick_team": 0,
  "draft": [ /* 同 §6.2 结构，当前已发生的所有手 */ ],
  "sources": ["pro_match"]
}
```

**`mode` 决定返回哪个形状**——此前两种用途共用一个端点却只定义了一种响应，实现者必须自行发明另一种，现明确：

| `mode` | 用途 | 返回字段 |
|---|---|---|
| `"realtime"` | 赛时 BP，当前手建议 | `options[]`（下述形状）+ `next_ord`/`team`/`is_pick` |
| `"offline"` | 赛前，产出 Playbook 的 `branches[].plans[]` | **`branches[]`**，每项为 `{branch_id, condition, plans[]}`——`plans[]` 元素形状与 §6.3 完全相同。**离线结果必须带 `branch_id` 与 `condition`**，否则多分支结果无法归属（§6.3 的 `plans[]` 因已嵌在 `branches[]` 内故不需要） |

`mode="offline"` 时请求必须带 `branches` 参数：字符串数组，元素为 `"<先手>:<体系>"`，其中 **`<先手>` ∈ {`us`, `them`}**（对应 `condition.first_pick`），**`<体系>` ∈ §6.0 的 `their_opening` 取值**。例：`["us:teamfight", "them:push"]`。

**前缀到 `condition` 的映射是恒等的**：`"us:teamfight"` → `condition = {"first_pick": "us", "their_opening": "teamfight"}`。响应中每个 `branches[].condition` 必须能反解回请求里的某个字符串，**该双射是"每个请求值都必须出现"这条不变式的判定依据**。

缺省 `mode` 为 `realtime`；缺省 `branches` 为**全部先手×体系组合**（2 × 7 = 14 个分支，含 `unknown`）。

**稳健性降权必须显式化（否则与"按 expected_wr 降序"冲突）**：`options[]` 与 `plans[]` 的元素都须带两个分数：

| 字段 | 含义 |
|---|---|
| `expected_wr` | 对手按预测分布应对时的期望胜率（原始值，不降权） |
| `penalized_score` | **排序依据**：`expected_wr − λ × max(0, robustness_delta − 0.10)`。λ 存于 `app_config`（键 `robustness_lambda`，默认 1.0） |

**排序按 `penalized_score` 降序，不是 `expected_wr`。** 前端展示时可并示两者，让教练看到"这一手原始胜率最高但因对手易换招而被降权"。

```
→ 200  (mode="realtime")
{
  "next_ord": 13,
  "team": 0,
  "is_pick": true,
  "options": [
    {"hero_id": 105, "expected_wr": 0.552, "robustness_delta": 0.04,
     "penalized_score": 0.552,
     "why": "对手按预测分布应对时最优；换招后仍不劣于 0.51",
     "fallback": [67, 19],
     "counterparty_plan": "对手若抢 105，我方案转为 ..."},
    {"hero_id": 67,  "expected_wr": 0.548, "robustness_delta": 0.03,
     "penalized_score": 0.548,
     "why": "...", "fallback": [19, 105], "counterparty_plan": "..."},
    {"hero_id": 19,  "expected_wr": 0.556, "robustness_delta": 0.22,
     "penalized_score": 0.436,
     "risk_note": "原始胜率最高，但对手一旦不按预测出牌，本方案会明显劣化",
     "why": "...", "fallback": [67, 105], "counterparty_plan": "..."}
  ],
  "assumptions": {"opponent_model": "sequence-v1", "value_model": "value-v1"},
  "sources_used": ["pro_match"]
}
```

**不变式（可测）**：
- `next_ord`/`team`/`is_pick` 同样由 §6.0 的 `resolve()` 确定性推出
- **`options` 按 `penalized_score` 降序**（不是 `expected_wr`）。每项必须有非空 `fallback` 与非空 `counterparty_plan`
- `penalized_score == expected_wr − λ × max(0, robustness_delta − 0.10)`，λ 取 `app_config.robustness_lambda`（±0.001）。示例验算：hero 19 的 `0.556 − 1.0×(0.22−0.10) = 0.436` ✓
- 每项必须有 `robustness_delta`；`> 0.10` 的项必须带 `risk_note` 字段
- `options` 中出现过的 hero 必须已被 `resolve()` 判定为可行动作（即不与 `draft` 重复）
- `mode="offline"` 时返回 `branches[]`（含 `branch_id`/`condition`），不返回 `options[]`；每个请求的 `branches` 参数值都必须出现在响应中
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
| **4 地图与视野** | 真假眼数量、反眼数、堆野数、神符 | API（需 `stats_available`） | 数量口径，**字段名以上游为准**：`obs_placed` / `sen_placed`（插眼数）、`observer_kills` / `sentry_kills`（反眼数）、`camps_stacked`、**`rune_pickups`**（无 s）。**这些在 Phase A 可用**，故 `map_vision` 指标的权重非零（见 §7.2） |
| | 眼位坐标与热力图 | 回放（Phase B） | 坐标需回放。**这是 `map_vision` 之下的一个子能力，不是整个指标**——§7.2 的权重针对的是上行的数量口径 |
| **5 节奏与稳定性** | 近期状态滑动窗口 | API | 近 20 场加权胜率，指数衰减 |
| | 胜负方差 | API | 窗口内胜率的二项方差 |
| | 首杀参与率 | API | `firstblood_claimed` 归属 |
| **6 英雄类型偏好** | 六类原型权重 | 常量 + API | 基于 dotaconstants `heroes.roles` 打标 + 选手英雄池加权。六类即 §6.0 的 `hero_archetype` 词表：`initiate`(先手) / `protect`(反手) / `push`(推进) / `teamfight`(团战) / `pickoff`(抓单) / `splitpush`(分推)。**中文名与枚举值的对应是规范性的**，实现不得另立第三套命名 |

**英雄原型映射（§7 维度 6 的实现规则）**

dotaconstants 的 `heroes.roles` 词表实测只有 **8 项**：`{Carry, Disabler, Durable, Escape, Initiator, Nuker, Pusher, Support}`——**没有 `Jungler`，也没有任何一项等于 §6.0 的六类原型**。必须显式映射。

**映射规则（按序求值，命中即计，一个英雄可命中多个）**：

| 原型 | 命中条件 |
|---|---|
| `initiate`（先手） | `Initiator` |
| `push`（推进） | `Pusher` |
| `pickoff`（抓单） | `Escape` AND `Nuker` |
| `splitpush`（分推） | `Carry` AND `Escape` AND NOT `Pusher` |
| `protect`（反手） | `Support` AND (`Nuker` OR `Durable`) |
| `teamfight`（团战） | `Durable` OR `Disabler` OR (`Carry` AND `Nuker`) |

**完备性保证**：上表**按构造是完备的**——任何未命中前五项的英雄，其 `roles` 必然落在 `Carry`/`Nuker`/`Disabler`/`Durable`/`Escape`/`Support` 之中；其中 `Carry`+`Nuker`、`Disabler`、`Durable` 三者至少居其一（否则该英雄无任何输出或控制定位，不存在），故必然命中 `teamfight`。**实测 127 个英雄零遗漏**，命中分布：`teamfight` 117、`initiate` 55、`protect` 44、`pickoff` 34、`push` 29、`splitpush` 23。

> 上一版规则（`teamfight` = `Durable` AND (`Nuker` OR `Disabler`)）会让 **8 个英雄零命中**：Shadow Fiend(11)、Zeus(22)、Sniper(35)、Gyrocopter(72)、Outworld Devourer(76)、**Techies(105)**、**Dawnbreaker(135)**、Muerta(138)。其中 105 出现在 §6.3/§6.6 示例里、135 出现在参考比赛里，且 `Σ=0` 会导致归一化除零。放宽为析取式后全部归入 `teamfight`（语义正确：均为远程法核/团战输出）。

**计分与归一化**：`hero_archetype[a] = Σ_h(该选手英雄池中 h 的出场占比 × I[h 命中 a])`，六项再归一化到和为 1.0。归一化前先断言 `Σ > 0`（映射完备性由上述构造保证，该断言是运行时兜底）。

该映射存于 **`app_config_kv`**（键 `archetype_role_map`），可调而不改代码。**测试要求**：断言 127 个英雄每一个至少命中一个原型，且断言那 8 个历史零命中英雄现在命中 `teamfight`。

**位置推断**：优先用 OpenDota `/matches/{id}` 中已有的 `player_slot` 与位置推断字段（上游对已 parse 的场次直接给出 1–5 的位置），仅在缺失时退化为启发式（按 `lane_role` 与经济结构判定）。**不依赖 STRATZ**（§12 R2）。

### 7.1 战队级聚合
- **BP 倾向**：先抢什么（各 BP 阶段的首选分布）、何时 ban 什么（按 `ord` 分段的 ban 分布）
- **体系倾向**：由六维中"英雄类型偏好"聚合到战队层
- **节奏**：平均比赛时长、平均首杀时间、平均一塔时间
- **交手历史**：与特定对手的 BP 历史与胜负

### 7.2 三源权重矩阵（按指标配置，非全局权重）

| `metric` 键 | 指标 | 天梯 | 训练赛 | 正式比赛 |
|---|---|---|---|---|
| `patch_strength` | 版本英雄强度 | **高** | — | 中 |
| `hero_pool` | 选手英雄池 | **高**（会不会玩） | 中 | **高**（高压下敢不敢拿） |
| `system_pref` | 体系偏好 | 无 | 中（⚠ 藏招偏差） | **最高** |
| `bp_tendency` | BP 倾向 | **无数据** | 低（试阵容） | **最高** |
| `tempo` | 节奏（比赛时长/首杀/一塔时间） | 低 | 中 | **高** |
| `map_vision` | 眼位 / 视野 | 低 | 中 | **高** |

**`metric` 键必须与 §5.1 的 `metric_weights.metric` CHECK 约束逐一对应**（共 6 个）。矩阵的行数 = CHECK 的取值数 = 6。

**数值口径（必须显式，否则两位工程师会算出不同结果）**：`最高`=1.5、`高`=1.0、`中`=0.5、`低`=0.25、`无`/`无数据`=0。

权重落在 §5.1 的 `metric_weights` 表（`metric × data_source → weight`），可调。**不得**用单一全局权重——否则会把训练赛的藏招当成真实倾向。

**聚合规则**：`value = Σ(wᵢ × vᵢ × nᵢ) / Σ(wᵢ × nᵢ)`；分母为 0 或 Σ(wᵢ × nᵢ) < 30 时返回 §6.0 降级形态 `insufficient_samples`。

**Phase A 六个指标全部有效**，共 18 行种子数据。`map_vision` 在 Phase A 使用**数量口径**（眼/反眼/堆野/神符计数，来自 API，见 §7 维度 4），因此其权重按上表正常取值；**仅坐标与热力图**留待 Phase B，那不影响本指标在 Phase A 的可计算性。**开雾不是任何指标**——原表把它与眼位并列是笔误，已删除。

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

**`position IS NULL` 的处理**：此类行**不进入任何同侪集合**（因为位置是分组的第一维）。为防止它们无声消失，`/v1/profile` 的 `coverage` 必须额外返回 `n_position_unknown`，UI 据此提示"部分场次因位置未知未计入"。若目标选手自身的 `position` 为 NULL，则其**依赖位置的维度**（`hero_pool`/`laning`/`combat`/`map_vision`/`tempo`）返回 `insufficient_samples`；**`hero_archetype` 不依赖位置，仍然返回**。

**窗口与阈值（可测）**：

| 指标 | 规则 |
|---|---|
| 签名英雄 | 场次 ≥ 5 且胜率 ≥ 0.60 且占该选手同窗口出场 ≥ 10% |
| 有效英雄数 | 满足"场次 ≥ 3 且胜率 ≥ 0.50"的英雄个数 |
| 近期状态 | 近 20 场指数衰减加权胜率，半衰期 7 场 |

### 7.4 条件化对位统计（§6.1 `counter_matchup` 的唯一合法来源）

§1 否证的是**通用**英雄两两交互特征。为让 §6.1 的 `counter_matchup` 可实现且不与 §1 冲突，此处定义其唯一合法口径——**必须在以下三个条件上同时条件化**：

```
对 (英雄 A, 英雄 B, 选手 P, 战队 T, base_version V)：
  n     = 在 V 内，P 使用 A 且对位 B 的场次数
  wr    = 上述场次的胜率
  delta = wr - (P 使用 A 的总体胜率)      # 相对该选手自身的基线，不是全局基线
```

**约束（可测）**：
- `n >= 30` 才输出，否则降级 `insufficient_samples`
- **必须**减去该选手/战队自身的基线胜率（`delta` 口径），**不得**直接输出全局对位胜率
- **禁止**实现为一张全局 127×127 的克制矩阵——那正是 §1 判为无信号的形态
- 无足够样本时，`counter_matchup` 贡献项取 0 并在 `detail` 中标注，**不得**用全局矩阵回退填充

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
| 里程碑 | **M9**（§14：决策层可用） |
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

**数值判定规则（必须显式，否则两人会得出不同结论）**：

记 `wr_pick` = 我方拿的胜率、`wr_ban` = 我方 ban 后剩余局面胜率、`wr_leave` = 我方放的胜率、`their_wr` = 对手拿的胜率、`wr_counter` = 我方反制选项的最高胜率。全部要求 `n >= 30`，否则该项按 §6.0 降级。

```
if 任一项 n < 30:                      -> insufficient_data
elif max(wr_pick, wr_ban, wr_leave) - second_max < 0.02:  -> insufficient_data   # 差异在噪声内
elif wr_pick 为最大:                    -> pick
elif wr_ban  为最大:                    -> ban
else:                                   -> leave_and_counter
                                        （并要求 wr_counter > wr_leave，否则退化为 ban）
```

**0.02 阈值的来源**：职业比赛单英雄胜率在 30 场样本下的标准误约为 0.09，取 0.02 作为"差异不可辨"的下界是保守选择；该阈值存于 `app_config`（键 `op_decision_min_delta`），可调。

---

## 10. 序列模型（线 C）

### 10.1 任务定义

24 手 CM 序列 → token 序列。位置 token 由 §8① 确定性给定，**模型只预测英雄**。

- **词表 = 258 个 token**。**token 基于 `hero_token_index.dense_index`（0..126 稠密），不是 `hero_id`。** `hero_id` 稀疏（1..155，其中 9 个 > 126：128,129,131,135,136,137,138,145,155），直接用原始 id 会把 hero 128 的 pick 解码成"英雄 1 被 ban"，把 hero 128 的 ban 映射到 255（与 `BOS` 冲突），hero 155 的 ban 溢出到 282（越界）。**示例比赛 8996973546 本身就含 hero_id 135。** 映射由 `hero_token_index` 表提供（按 `snapshot_version` 冻结），入库时即固化。

  | 范围 | 含义 |
  |---|---|
  | `0..126` | `dense_index` 0..126 作为 **pick** 出现 |
  | `127..253` | `dense_index` 0..126 作为 **ban** 出现 |
  | `254` | `PAD` —— 序列短于 24 手时右侧填充 |
  | `255` | `BOS` —— 序列起始标记 |
  | `256` | `UNK_HERO` —— `hero_id` 不在 `heroes` 中（新英雄上线而常量表未更新） |
  | `257` | `MASK` —— **仅用于掩码语言建模（MLM）式预训练目标**：随机遮蔽序列中的若干位置，被遮蔽处替换为 `MASK`，loss 只在该位置回传。因果（自回归）训练目标下不使用此 token |

- **`MASK` 的应用点**：仅在可选的自监督预训练阶段使用；§10.3 的评估协议只涉及自回归的下一手预测，不含 `MASK`。若不做预训练，`MASK` 在数据中永不出现，但仍在词表内以保持 token id 稳定。

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
| 内容 | 采集、存储、六维能力项、战队画像、交叉分析、剧本生成器、Value 接口、Profile 接口、`/v1/playbook`、**A9 最小回放导入（manta + magic 嗅探 + build 号映射）** | 序列 tokenizer、基线、模型、Policy 接口、**决策搜索（§8⑤）与 `/v1/advise`** |
| 依赖 | 只依赖契约 + 数据库 | 只依赖契约 + 数据集。**注意**：§8⑤ 的决策搜索需要 Value（线 A），但对**契约桩**编程即可——M0 的 fixtures 就是该桩；线 C 不得等待线 A 的真实实现 |
| 能否独立测试 | 是（用 fixtures） | 是（用离线数据集 + `contracts/fixtures/` 中的 Value 响应桩） |
| 完成标志 | `GET /v1/playbook` 与 `GET /v1/profile` 对任一 matchup 返回结构完整、不变式通过的响应 | `POST /v1/policy/next` 返回概率和（含 `other_prob`）为 1、且优于频率基线；`POST /v1/advise` 通过 §6.6 不变式 |

**第三条线（可视化）**：前端消费契约的 **mock 数据**先行开发，不等待后端。契约冻结后即可启动。

**M0 必须同时交付 `contracts/fixtures/`**：每个接口至少一份合法样例响应，且必须覆盖以下边界情形。**字段名以下表为准**（此前列的几个字段名在契约里并不存在）：

| fixture | 覆盖的契约字段 |
|---|---|
| `value__low_confidence.json` | `confidence: "low"`、`n_samples < 30` |
| `profile__map_vision_counts_only.json` | `map_vision` **在 Phase A 返回真实数量口径的百分位**（非降级），且与 `coverage.n_position_unknown > 0` 并存，证明"位置未知不降级数量口径指标"。**坐标条目的 `needs_replay` 降级由 `playbook__positions_phase_b.json` 覆盖**（§6.4 的字段表是穷尽性的、§6.5 又把眼位坐标列为 Phase A 禁止字段，故 Profile 响应不含 `notes[]`——本行此前误把 Playbook 的 `positions[].notes[]` 写进了 Profile fixture） |
| `profile__position_unknown.json` | `coverage.n_position_unknown > 0`；**五个位置相关维度**为 `insufficient_samples`，而 **`hero_archetype` 仍返回六项且和为 1.0**（§7.3：该维度不依赖位置）。此 fixture 专门锁住这个区分，防止实现者把六维一起降级 |
| `playbook__draft_incomplete.json` | `data_quality.n_pending_draft > 0` 与 `n_unavailable_draft > 0`（这是 §12 R1 承诺的"UI 标注数据不完整"的载体） |
| `playbook__anomalous.json` | `data_quality.n_anomalous_draft > 0` |
| `playbook__positions_phase_b.json` | `positions[].notes[].value = null` + `reason = "needs_replay"` |
| `playbook__op_insufficient.json` | `op_hero_decision[].recommendation = "insufficient_data"`、`if_we_ban` 为降级形态 |
| `policy__no_model.json` | `baseline.model_top1 = null`（模型未就绪态，M6 必须能渲染） |
| `advise__offline.json` | `mode: "offline"` → `branches[].plans[]`（非 `options[]`） |
| `advise__robustness_penalized.json` | 含一条 `robustness_delta > 0.10` 的 option：断言其 `penalized_score < expected_wr`、带 `risk_note`、且排序被降到其后 |
| `error__insufficient_data.json` | **HTTP 200** + `error.code = "insufficient_data"` |
| `error__source_not_allowed.json` | HTTP 403 + `error.code = "source_not_allowed"` |

fixture 必须通过契约 schema 校验（否则前端会对着非法数据开发，等真接口上线时才发现字段名不一致）。

**冲突规避**：三条线不共享可写文件。线 A 拥有 `db/` 与 `analysis/`，线 C 拥有 `models/`，前端拥有 `web/`。契约文件 `contracts/openapi.yaml` 由**单人**维护（冻结期内只读）。

---

## 12. 风险与未验证事项

| 编号 | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | OpenDota `picks_bans` 滞后扩大或字段变更 | 数据断流 | 状态机 + 监控告警；`unavailable` 状态显式暴露给 UI |
| R2 | STRATZ 不可用（已确认本环境被拦截） | 损失位置与 lane 数据 | **不依赖**。位置判定退化为启发式（§7.3 的 `position IS NULL` 路径）；**10 分钟补刀/经验差**留待回放层。注意 `lane_role`/`is_roaming` 来自 OpenDota 的解析管道，Phase A **可用**（见 §7 维度 2 的说明），不属本风险 |
| R3 | 训练赛回放可能来自**旧版本**或非标准 lobby 设置 | 污染统计 | 导入时**从 `CSVCMsg_ServerInfo.game_dir` 提取 build 号**（`dota_v(\d+)`）映射到版本——**不要用 `demo_version_name`，它是 demo 格式版本**（§3.2）；非标准 lobby 设置需人工确认 |
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
| M2b | 最小回放导入可用（A9） | 对 3 个真实回放（含至少 1 个 bzip2、1 个 zstd）完成导入：① magic 嗅探正确识别两种压缩；② 从 `CSVCMsg_ServerInfo.game_dir` 提取 build 号并映射到正确版本（**build 6932 → 7.41e**）；③ 解析出 24 手 BP 且与 OpenDota 的真实数据逐手一致；④ `scrim` 来源的 `.dem` 被保留（`replay_retained=true`），`pro_match` 的可删 |
| M3 | 画像引擎可用 | `GET /v1/profile` 对任一战队返回六维：**五个位置相关维度**（`hero_pool`/`laning`/`combat`/`map_vision`/`tempo`）各给 `percentile` 或 `reason`（不得两者皆空、不得返回绝对值）；**`hero_archetype` 返回六项分布且和为 1.0**（它是分布不是百分位，不受"不得返回绝对值"约束）；签名英雄/有效英雄数按 §7.3 阈值可复算 |
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
| **隔离测试** | 默认查询不返回 `pub_match`/`scrim`。**scrim 负向测试必须有可观测量**：先记录 `GET /v1/profile?team_id=<them>` 与 `GET /v1/playbook` 的完整响应作为基线，插入一条属于该对手的 `scrim` 记录，再次请求，断言两次响应**逐字节相同**（时间戳字段除外）。仅断言"不含 scrim" 不足以证明隔离生效 |
| **时间切分测试** | 模型评估流程断言训练集时间戳全部早于验证集（防泄漏） |
| **固定装置** | 用 3 场已知比赛（`8996973546` / `8988636430` / `8988557865`）作为黄金样本，断言 BP 序列逐手匹配 |
| **模板推导测试** | 对全库每一场断言 `(ord, team, is_pick)` 与 §6.0 的 `resolve(ord, first_pick_team)` 一致（`anomaly=false` 的场次必须零反例）；契约示例中的 `draft` 必须与该场真实数据一致——**防止示例与规则再次漂移** |
| **降级契约测试** | 对每个可能不可计算的指标断言返回的是 §6.0 降级形态（`value: null` + `reason` ∈ 枚举），而非字段缺失或 0。**`needs` 的断言必须条件化**：`reason == "needs_replay"` 时 `needs` 必须存在且为 `"Phase B"`；**其余 reason 下 `needs` 必须不存在**（不是 null）。反例保护：`playbook__positions_phase_b.json`（应有 `needs`）与 `playbook__op_insufficient.json`（不应有 `needs`）必须分别通过 |
| **player_slot 归一化测试** | 造一场真实布局的比赛（Radiant 原始 slot 0-4、Dire 原始 128-132），断言入库后为 0-4 / 5-9，且 Dire 的 5 行**不与 Radiant 主键冲突**。**专门断言 `128 % 128 == 0` 这种错误实现会被 `slot_team_agree` 拒绝**——这是 §5.1 修正的回归保护 |
| **回放导入测试** | 对一个 zstd 回放与一个 bzip2 回放断言：magic 嗅探分流正确；`dota_v(\d+)` 提取的 build 号映射到预期版本；解析出的 24 手 BP 与 OpenDota 数据逐手一致；未识别压缩格式时**报错而非静默产出空数据** |
| **枚举闭包测试** | 扫描全部 fixture 与接口样例，断言每个枚举字段的取值都在 §6.0 定义的集合内——**包括** `contributions[].factor`、`their_opening`、`hero_archetype`、`unavailable_reason`、`error.code`、`metric_weights.metric`（须与 §7.2 矩阵行一一对应）；枚举未在客户端硬编码（由 schema 生成 TS 类型） |
| **token 映射测试** | 取一个 `dense_index` 与 `hero_id` 不相等的英雄（如 hero_id 135 → dense_index 索引），断言序列 token 用的是 `dense_index` 而非 `hero_id`；断言 hero_id 128/155 不再产生越界或与 `BOS` 冲突的 token。这是 §10.1 修正的唯一验证 |
| **模板实例化测试** | 对 §6.0 `resolve()` 的 24 项断言 ban/pick 计数为 14/10、F 与 O 各 7 ban 各 5 pick；对两种 `first_pick_team` 各跑一遍 |
| **条件化对位测试** | 断言 `counter_matchup` 使用的是**减去选手自身基线后的 delta**，而非全局对位胜率；断言项目里不存在 127×127 的全局克制矩阵；`n < 30` 时该项取 0 且标注，不回退填充 |
| **版本归属测试** | 三类用例，缺一不可：① **边界**——造两场 `start_time` 分别落在 7.41e 与 7.41f 边界两侧的比赛，断言 `patch_id` 不同；② **字母映射正确性**——对一个 **`add` 存在**的序列（7.41）与一个 **`add` 缺失**的序列各跑一遍，断言 `dates[0]` 映射到 **`'b'`** 而非 `'a'`。**这是唯一能抓住"整体错位一个字母"的用例**，第 ① 类抓不到（它是 `add` 存在的序列，两种读法都能通过边界断言）；③ **交叉校验**——断言 `patches` 的行集来自 Valve（118 个版本 / 84 个字母版本），而非 `patchdates`（141 个槽位） |
| **阵容归属测试** | 造一场跨阵容变更的比赛，断言历史胜率按**当场出场阵容**归属而非当前阵容；断言 `rosters` 可插入 `joined_at IS NULL` 的行 |
| **匿名选手测试** | 造一场含两个 `account_id IS NULL` 的天梯局，断言 `match_players` 正确入库 10 行（验证主键用 `player_slot` 而非 `account_id`） |
| **指标口径测试** | 对 §7.3 的每个阈值（签名英雄 ≥5 场/≥60%/≥10%、有效英雄 ≥3 场/≥50%、近期状态半衰期 7）造边界样本断言取舍；断言同侪 < 30 时返回 `insufficient_samples` |
| **画像算术测试** | 断言 `pct == round(games / window_games * 100)`；断言 `effective_count <= window_games / 3`；断言 `signature` 与 `comfortable` **互斥**且 `signature` 条目满足全部三条阈值。**§6.4 的 hero 55 与 hero 77 是这两个方向的边界用例，必须原样作为测试输入** |
| **权重聚合测试** | 断言 §7.2 的 `value = Σ(wᵢvᵢnᵢ)/Σ(wᵢnᵢ)` 结果；断言 `map_vision` 权重为 0 时不参与 |
| **决策稳健性测试** | `advise` 的每个 option 必含 `robustness_delta`；构造一个 `robustness_delta > 0.10` 的用例断言其带 `risk_note` 且排序被降权 |
| **错误信封测试** | 断言 `insufficient_data` 返回 200 系（业务结果）而非 5xx；断言 `source_not_allowed` 在请求未授权的来源时触发 |
| **采集配额测试** | 断言限流器在 60 请求/分钟内不触发 429；断言日配额计数与 `collector_attempts` 一致 |
| **原型完备性测试** | 对 127 个英雄断言每个至少命中一个原型（`Σ > 0`，防归一化除零）；**并把 8 个历史零命中英雄（11/22/35/72/76/105/135/138）作为回归用例**，断言它们命中 `teamfight` |
| **Phase A 缺席测试** | 扫描 §6.5「不返回的内容」清单中的每一项，断言其在**任何**接口响应中都不存在对应字段。这条使 §6.5 从文档承诺变成机器可检的规则 |
| **§9.1 判定复算测试** | 以 §6.3 的 `op_hero_decision` 为输入，独立复算 `recommendation`，断言结果等于 `leave_and_counter`。防止示例与规则再次漂移 |
| **空 draft 测试** | `POST /v1/policy/next` 传 `draft: []`，断言 `next_ord == 0` 且不报错；传满 24 手时断言返回 `invalid_request` |
| **错误码覆盖测试** | 逐个断言 `invalid_request`(400) / `not_found`(404) / `upstream_unavailable`(503) / `source_not_allowed`(403) / `insufficient_data`(200) 都能被真实触发，且响应体符合 §6.0 的错误信封 |
| **同侪过滤测试** | 断言百分位的分母**只**包含满足 §7.3 全部六个条件的行：位置相同、同 `base_version`、同 tier、同窗口、`stats_available=true`、同 `sources`。特别断言 `position IS NULL` 的行与跨 tier 的行**不进入**分母 |
| **reason 生产者测试** | 对 §6.0 的四个 `unavailable_reason` 各构造一个触发条件，断言都真的会产出该 reason（防止定义了却永不产生的枚举） |

---

## 16. 实证验证记录（2026-09-16）

本节记录**已实际执行**的验证及其结果，供实现者判断哪些结论可依赖、哪些仍是假设。

### 16.1 §5.1 的 DDL 已在 PostgreSQL 16 上执行

```
22 张表，全部 CREATE TABLE / CREATE INDEX 成功，exit 0
```

**约束探针 15/15 通过**（用一个临时 PG 实例逐条实测）：

| 探针 | 结果 |
|---|---|
| 完整 10 人局入库（Radiant 0-4 + Dire 5-9） | ✅ 10 行 |
| 含 2 个 `account_id IS NULL` 的匿名选手 | ✅ 主键不冲突 |
| **`raw % 128` 的错误归一化被 `slot_team_agree` 拒绝** | ✅ 拒绝 |
| `slot` 与 `team` 不自洽 | ✅ 拒绝 |
| `metric_weights.metric` 接受 `tempo`、拒绝 `laning` | ✅ |
| `data_source` / `draft_state` 拒绝未定义值 | ✅ |
| `draft_actions.hero_id` 引用不存在的英雄 | ✅ 拒绝（FK 生效） |
| `ord = 24` 越界 | ✅ 拒绝 |
| `rosters` 接受 `joined_at IS NULL` | ✅ |
| 同队同选手第二条 `left_at IS NULL` | ✅ 被部分唯一索引拒绝 |
| `hero_token_index` 同快照内 `dense_index` 重复 / 同英雄双索引 | ✅ 均拒绝 |
| 新快照可复用同一 `dense_index` | ✅ 版本化生效 |
| 重复灌入不产生重复行（`ON CONFLICT DO NOTHING`） | ✅ |

### 16.2 真实比赛 8996973546 端到端灌入

| 验证项 | 结果 |
|---|---|
| 逐手与 §6.0 模板一致（team + is_pick，24 手） | ✅ `team_ok=24, type_ok=24` |
| 手数分布 | ✅ 14 ban / 10 pick / 共 24 |
| `player_slot` 归一化 | ✅ `rows=10, radiant=5, dire=5, agree=10` |
| 稀疏 `hero_id` 的 `dense_index` 映射 | ✅ 24 个英雄的 `dense_index` **全部 ≠ `hero_id`** |

**`raw % 128` 的具体后果（实测数字）**：

```
原始 Radiant slots : [0, 1, 2, 3, 4]
原始 Dire    slots : [128, 129, 130, 131, 132]
正确归一化          : [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]   ✓
错误的 raw % 128    : [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]   ✗ 5 组主键冲突
```

### 16.3 其他已实测确认的事实

| 事实 | 证据 |
|---|---|
| 24 手模板与队伍归属 | OpenDota 全库 1,014 场，`first_ban_team == first_pick_team` **1014/1014** |
| 模板异常率 | 约 0.6%（1,014 场中 6 场仅 23 手） |
| 先手方分布 | Dire 658 / Radiant 356（65%/35%），非 50/50 |
| `hero_id` 稀疏性 | 127 个英雄，id 1..155，**9 个 > 126**（128,129,131,135,136,137,138,145,155） |
| 原型映射完备性 | 127 个英雄**零遗漏**；8 个历史零命中英雄（11/22/35/72/76/105/135/138）均归 `teamfight` |
| 回放压缩格式 | 参考比赛 zstd (`28b52ffd`)；`8700024338` bzip2 (`425a6839`) |
| 回放头版本线索 | `demo_version_name = "valve_demo_2b"`（非补丁号）；`game_dir` 含 `dota_v6932` |
| 子版本字母映射 | `add`='a'、`dates[]` 从 'b' 起；83 槽中 34 个日期精确一致，平均误差 1.2 天；错误读法平均误差 26.3 天 |
| 回放内语音 | `CSVCMsg_VoiceData` 在 3 个真实回放中均为 **0 条** |
| 常量表规模 | 英雄 127、道具 501（其中 **10 个 `dname` 为 null**） |
| Valve 版本清单 | 118 个版本、**84 个字母版本**、从 7.08 起 |

### 16.4 仍未验证（实现前须自行确认）

见 §17。其中最需优先处理的是 Kaggle CSV 的**列名与 `order` 起始值**（§17 第 7 项）——它是 M1 的前置。

## 17. 未验证事项清单

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
