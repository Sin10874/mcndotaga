from constants.dotaconstants import fetch_heroes, fetch_items, derive_token_index

def test_hero_count_is_127():
    assert len(fetch_heroes()) == 127

def test_item_count_is_501():
    assert len(fetch_items()) == 501

def test_exactly_nine_hero_ids_exceed_126():
    ids = sorted(h["id"] for h in fetch_heroes())
    assert [i for i in ids if i > 126] == [128,129,131,135,136,137,138,145,155]

def test_token_index_is_dense_and_zero_based():
    m = derive_token_index(fetch_heroes())
    assert sorted(m.values()) == list(range(127))

def test_hero_135_maps_to_121():
    """规格 §16.2 用真实比赛验证过的具体映射。"""
    assert derive_token_index(fetch_heroes())[135] == 121

def test_ten_items_have_null_dname():
    assert sum(1 for i in fetch_items() if not i.get("dname")) == 10

def test_roles_vocabulary_is_the_measured_eight():
    """规格 §7 维度 6：词表只有 8 项，没有 Jungler。"""
    vocab = {r for h in fetch_heroes() for r in (h.get("roles") or [])}
    assert vocab == {"Carry","Disabler","Durable","Escape",
                     "Initiator","Nuker","Pusher","Support"}
