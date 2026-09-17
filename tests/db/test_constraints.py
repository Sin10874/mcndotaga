from tests.conftest import expect_violation

def test_full_ten_player_match_inserts(db, seeded):
    rows = [(1, s, None, 0 if s < 5 else 1, 80) for s in range(10)]
    with db.cursor() as cur:
        cur.executemany("""INSERT INTO match_players(match_id, player_slot, account_id, team, hero_id)
                           VALUES (%s,%s,%s,%s,%s)""", rows)
    assert db.execute("SELECT count(*) FROM match_players WHERE match_id=1").fetchone()[0] == 10

def test_duplicate_player_slot_is_rejected(db, seeded):
    """规格 §5.1：主键是 (match_id, player_slot)。同一场同一 slot 不能有两行。

    第二个 account_id 必须先存在于 players，否则这一句会被 FK（23503）拒绝，
    测试就变成恒绿的空断言——而它声称守护的正是主键选择。故此处同时钉住
    23505（unique_violation），确保拒绝来自 (match_id, player_slot) 的唯一性。
    """
    db.execute("INSERT INTO players(account_id, name) VALUES (111, 'p')")
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80)""")
    with expect_violation(db, sqlstate="23505"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,3,111,0,80)""")   # 同 slot，不同 account

def test_two_anonymous_players_coexist(db, seeded):
    """匿名选手（account_id IS NULL）在不同 slot 上必须都能入库。"""
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80), (1,8,NULL,1,80)""")
    assert db.execute(
        "SELECT count(*) FROM match_players WHERE match_id=1 AND account_id IS NULL"
    ).fetchone()[0] == 2

def test_raw_mod_128_normalization_is_rejected(db, seeded):
    """规格 §16.2：raw%128 把 Dire 的 128..132 塌缩成 0..4。"""
    with expect_violation(db, sqlstate="23514", constraint="slot_team_agree"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,0,NULL,1,80)""")

def test_slot_team_mismatch_is_rejected(db, seeded):
    """slot 的 0-4/5-9 分段必须与 team 自洽（与 raw%128 同为 slot_team_agree 守护）。"""
    with expect_violation(db, sqlstate="23514", constraint="slot_team_agree"):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,5,NULL,0,80)""")

def test_metric_weights_rejects_undefined_metric(db, seeded):
    """规格 §7：只认六个指标键；'laning' 不是其中之一。"""
    with expect_violation(db, sqlstate="23514", constraint="metric_weights_metric_check"):
        db.execute("INSERT INTO metric_weights VALUES ('laning','pro_match',1.0,NULL)")

def test_metric_weights_accepts_all_six_spec_keys(db, seeded):
    for m in ["patch_strength","hero_pool","system_pref","bp_tendency","tempo","map_vision"]:
        db.execute("INSERT INTO metric_weights VALUES (%s,'pro_match',1.0,NULL)", (m,))

def test_data_source_rejects_undefined_value(db, seeded):
    """规格 §5.4：只有 pro_match/pub_match/scrim 三种来源，'ranked' 不合法。"""
    with expect_violation(db, sqlstate="23514", constraint="matches_data_source_check"):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'ranked',now(),'complete')""")

def test_draft_state_rejects_undefined_value(db, seeded):
    """状态机只有 pending/complete/unavailable，'partial' 不合法。"""
    with expect_violation(db, sqlstate="23514", constraint="matches_draft_state_check"):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'pro_match',now(),'partial')""")

def test_draft_actions_rejects_unknown_hero(db, seeded):
    """hero_id 外键：不存在的英雄必须被 FK 拒绝（不是 CHECK）。"""
    with expect_violation(db, sqlstate="23503", constraint="draft_actions_hero_id_fkey"):
        db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,999)")

def test_draft_actions_rejects_ord_out_of_range(db, seeded):
    """24 手模板：ord 合法区间是 0..23。"""
    with expect_violation(db, sqlstate="23514", constraint="draft_actions_ord_check"):
        db.execute("INSERT INTO draft_actions VALUES (1,24,false,0,80)")

def test_rosters_allows_null_joined_at(db, seeded):
    """两条断言：joined_at 可空能入库（Liquipedia 常缺 joindate），
    但同队同选手不允许第二条 left_at IS NULL。

    此处的唯一性由**部分唯一索引** rosters_team_id_account_id_idx 提供，
    故 PG 报的是索引名而非约束名——这正是 constraint 参数要收元组/索引名的原因。
    """
    db.execute("INSERT INTO players(account_id,name) VALUES (111,'p')")
    db.execute("INSERT INTO teams(team_id,name) VALUES (10,'A')")
    db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,NULL,'liquipedia')")
    with expect_violation(db, sqlstate="23505",
                          constraint="rosters_team_id_account_id_idx"):
        db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,'2020-01-01','liquipedia')")

def test_hero_token_index_rejects_duplicate_dense_index(db, seeded):
    """同快照内两个英雄不能占用同一 dense_index。"""
    db.execute("""INSERT INTO heroes(hero_id,name,localized_name) VALUES (81,'npc_dota_hero_x','X')""")
    with expect_violation(db, sqlstate="23505", constraint="hero_token_index_pkey"):
        db.execute("INSERT INTO hero_token_index VALUES (1, 81, 73)")

def test_hero_token_index_rejects_double_index_for_same_hero(db, seeded):
    """同一英雄在同快照内不能有两个索引（UNIQUE(snapshot_version, hero_id)）。"""
    with expect_violation(db, sqlstate="23505",
                          constraint="hero_token_index_snapshot_version_hero_id_key"):
        db.execute("INSERT INTO hero_token_index VALUES (1, 80, 121)")

def test_hero_token_index_is_versioned(db, seeded):
    db.execute("INSERT INTO constants_snapshot (snapshot_version,n_heroes,n_items) VALUES (2,128,501)")
    db.execute("INSERT INTO hero_token_index VALUES (2, 80, 73)")   # 新快照可复用索引

def test_reinsert_is_idempotent(db, seeded):
    """规格 §16.1：重复灌入不产生重复行。"""
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80)")
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80) ON CONFLICT DO NOTHING")
    assert db.execute("SELECT count(*) FROM draft_actions WHERE match_id=1").fetchone()[0] == 1
