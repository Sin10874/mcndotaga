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
