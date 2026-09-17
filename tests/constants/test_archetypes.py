import collections
from constants.archetypes import archetypes_for
from constants.dotaconstants import fetch_heroes

SIX = {"initiate","protect","push","teamfight","pickoff","splitpush"}

def test_archetype_mapping_is_total():
    """规格 §16.3：127 个英雄零遗漏（Σ=0 会导致归一化除零）。"""
    heroes = fetch_heroes()
    missing = [h["localized_name"] for h in heroes if not archetypes_for(h["roles"])]
    assert missing == [], f"零命中英雄: {missing}"

def test_eight_historical_heroes_hit_teamfight():
    """上一版合取式规则让这 8 个零命中；其中 105 在契约示例里、135 在参考比赛里。"""
    by_id = {h["id"]: h for h in fetch_heroes()}
    for hid in [11,22,35,72,76,105,135,138]:
        assert "teamfight" in archetypes_for(by_id[hid]["roles"]), f"hero {hid} 未命中 teamfight"

def test_only_produces_known_archetypes():
    for h in fetch_heroes():
        assert set(archetypes_for(h["roles"])) <= SIX

def test_population_matches_the_spec_table():
    """规格 §7 的命中分布。逐规则计数：删任何一条规则、改任何一个条件都会改数。"""
    counts = collections.Counter(a for h in fetch_heroes() for a in archetypes_for(h["roles"]))
    assert dict(counts) == {"teamfight":117, "initiate":55, "protect":44,
                            "pickoff":34, "push":29, "splitpush":23}, f"分布漂移: {dict(counts)}"
    assert sum(counts.values()) == 302   # Σ=302 是 §7:1047 六项之和

def test_archetypes_for_tolerates_missing_or_unknown_roles():
    """`heroes.roles` 是可空列：None 不得抛异常，未知角色不得命中任何原型。"""
    assert archetypes_for(None) == []
    assert archetypes_for([]) == []
    assert archetypes_for(["Unknown"]) == []
