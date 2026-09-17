from tests.conftest import expect_violation

def test_full_ten_player_match_inserts(db, seeded):
    rows = [(1, s, None, 0 if s < 5 else 1, 80) for s in range(10)]
    with db.cursor() as cur:
        cur.executemany("""INSERT INTO match_players(match_id, player_slot, account_id, team, hero_id)
                           VALUES (%s,%s,%s,%s,%s)""", rows)
    assert db.execute("SELECT count(*) FROM match_players WHERE match_id=1").fetchone()[0] == 10

def test_anonymous_players_do_not_collide(db, seeded):
    """规格 §16.1：两个匿名选手同场必须都入库（主键是 player_slot 而非 account_id）。"""
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                  VALUES (1,3,NULL,0,80), (1,8,NULL,1,80)""")
    n = db.execute("SELECT count(*) FROM match_players WHERE match_id=1 AND account_id IS NULL").fetchone()[0]
    assert n == 2

def test_raw_mod_128_normalization_is_rejected(db, seeded):
    """规格 §16.2：raw%128 把 Dire 的 128..132 塌缩成 0..4。"""
    with expect_violation(db):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,0,NULL,1,80)""")

def test_slot_team_mismatch_is_rejected(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id)
                      VALUES (1,5,NULL,0,80)""")

def test_metric_weights_rejects_undefined_metric(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO metric_weights VALUES ('laning','pro_match',1.0,NULL)")

def test_metric_weights_accepts_all_six_spec_keys(db, seeded):
    for m in ["patch_strength","hero_pool","system_pref","bp_tendency","tempo","map_vision"]:
        db.execute("INSERT INTO metric_weights VALUES (%s,'pro_match',1.0,NULL)", (m,))

def test_data_source_rejects_undefined_value(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'ranked',now(),'complete')""")

def test_draft_state_rejects_undefined_value(db, seeded):
    with expect_violation(db):
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state)
                      VALUES (9,'pro_match',now(),'partial')""")

def test_draft_actions_rejects_unknown_hero(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,999)")

def test_draft_actions_rejects_ord_out_of_range(db, seeded):
    with expect_violation(db):
        db.execute("INSERT INTO draft_actions VALUES (1,24,false,0,80)")

def test_rosters_allows_null_joined_at(db, seeded):
    db.execute("INSERT INTO players(account_id,name) VALUES (111,'p')")
    db.execute("INSERT INTO teams(team_id,name) VALUES (10,'A')")
    db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,NULL,'liquipedia')")
    with expect_violation(db):
        db.execute("INSERT INTO rosters(team_id,account_id,joined_at,source) VALUES (10,111,'2020-01-01','liquipedia')")

def test_hero_token_index_rejects_duplicate_dense_index(db, seeded):
    """同快照内两个英雄不能占用同一 dense_index。"""
    db.execute("""INSERT INTO heroes(hero_id,name,localized_name) VALUES (81,'npc_dota_hero_x','X')""")
    with expect_violation(db):
        db.execute("INSERT INTO hero_token_index VALUES (1, 81, 73)")

def test_hero_token_index_rejects_double_index_for_same_hero(db, seeded):
    """同一英雄在同快照内不能有两个索引。"""
    with expect_violation(db):
        db.execute("INSERT INTO hero_token_index VALUES (1, 80, 121)")

def test_hero_token_index_is_versioned(db, seeded):
    db.execute("INSERT INTO constants_snapshot (snapshot_version,n_heroes,n_items) VALUES (2,128,501)")
    db.execute("INSERT INTO hero_token_index VALUES (2, 80, 73)")   # 新快照可复用索引

def test_reinsert_is_idempotent(db, seeded):
    """规格 §16.1：重复灌入不产生重复行。"""
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80)")
    db.execute("INSERT INTO draft_actions VALUES (1,0,false,0,80) ON CONFLICT DO NOTHING")
    assert db.execute("SELECT count(*) FROM draft_actions WHERE match_id=1").fetchone()[0] == 1
