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
