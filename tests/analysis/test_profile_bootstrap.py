from decimal import Decimal

from analysis.profile_bootstrap import seed_profile_config


def test_profile_weights_seed_all_sources_without_inventing_global_weight(db):
    seed_profile_config(db)
    values = {(r[0], r[1]): r[2] for r in db.execute("SELECT metric,data_source,weight FROM metric_weights")}
    assert len(values) == 18, "画像启动需要规格定义的六指标三来源权重"
    assert values[("bp_tendency", "pub_match")] == 0
    assert values[("bp_tendency", "pro_match")] == Decimal("1.5")
    assert values[("hero_pool", "pub_match")] == 1
    assert values[("map_vision", "pub_match")] == Decimal("0.25")
    assert values[("map_vision", "pro_match")] == 1
    assert db.execute("SELECT value FROM app_config_kv WHERE key='min_sample_n'").fetchone() == (30,)


def test_profile_bootstrap_is_idempotent_and_preserves_custom_configuration(db):
    db.execute("INSERT INTO metric_weights(metric,data_source,weight) VALUES ('hero_pool','pro_match',0.75)")
    db.execute("INSERT INTO app_config_kv(key,value) VALUES ('min_sample_n','40')")
    seed_profile_config(db)
    seed_profile_config(db)
    assert db.execute("SELECT count(*) FROM metric_weights").fetchone() == (18,)
    assert db.execute("SELECT weight FROM metric_weights WHERE metric='hero_pool' AND data_source='pro_match'").fetchone() == (Decimal("0.75"),)
    assert db.execute("SELECT value FROM app_config_kv WHERE key='min_sample_n'").fetchone() == (40,)
