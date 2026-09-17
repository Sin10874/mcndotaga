from contracts.tools.invariants import (check_value, check_policy, check_advise,
                                        check_playbook, check_profile)

SPEC_VALUE = {"radiant_win_prob": 0.530, "contributions": [
    {"factor":"patch_strength","delta":0.011},{"factor":"counter_matchup","delta":-0.014},
    {"factor":"player_comfort","delta":0.024},{"factor":"first_pick","delta":0.009}]}

def test_spec_value_example_passes():
    assert check_value(SPEC_VALUE) == []

def test_value_detects_the_original_round1_defect():
    """规格一轮抓到的原始错误：求和 0.054 而 radiant_win_prob-0.5 = 0.030。"""
    bad = {**SPEC_VALUE, "contributions": [
        {"factor":"patch_strength","delta":0.021},{"factor":"counter_matchup","delta":-0.014},
        {"factor":"player_comfort","delta":0.038},{"factor":"first_pick","delta":0.009}]}
    assert check_value(bad) != []

def test_value_detects_unrequested_source():
    body = {**SPEC_VALUE, "sources_used": ["pro_match", "pub_match"]}
    assert check_value(body, requested_sources=["pro_match"]) != []

def test_spec_policy_example_passes():
    assert check_policy({"candidates":[{"hero_id":112,"prob":0.180,"reasons":["x"]}],
                         "top_n":10,"other_prob":0.820}) == []

def test_spec_profile_example_passes():
    body = {"players":[{"account_id":1,"dimensions":{"hero_archetype":{
        "initiate":0.24,"protect":0.18,"push":0.17,"teamfight":0.19,"pickoff":0.13,"splitpush":0.09}}}]}
    assert check_profile(body) == []

def test_profile_rejects_five_category_distribution():
    """规格四轮抓到的原始缺陷：只有 5 类且凑巧和为 1.0。"""
    body = {"players":[{"account_id":1,"dimensions":{"hero_archetype":{
        "teamfight":0.32,"push":0.18,"pickoff":0.21,"splitpush":0.11,"protect":0.18}}}]}
    assert check_profile(body) != []

def test_spec_advise_example_passes():
    opts = [{"hero_id":105,"expected_wr":0.552,"robustness_delta":0.04,"penalized_score":0.552,"fallback":[67]},
            {"hero_id":67,"expected_wr":0.548,"robustness_delta":0.03,"penalized_score":0.548,"fallback":[19]},
            {"hero_id":19,"expected_wr":0.556,"robustness_delta":0.22,"penalized_score":0.436,"fallback":[67],"risk_note":"r"}]
    assert check_advise({"options": opts}) == []

def test_advise_handles_offline_branches_shape():
    """offline 模式返回 branches[].plans[]，不含 options —— 不得 KeyError。"""
    body = {"branches": [{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                          "plans":[{"label":"A1","expected_wr":0.52,"robustness_delta":0.04,
                                    "penalized_score":0.52,
                                    "key_picks":[{"hero_id":105,"fallback":[67]}]}]}]}
    assert check_advise(body) == []

def test_playbook_flags_opponent_by_ord_in_own_branch():
    body = {"matchup":{"first_pick_team":0,"side_map":{"us":0,"them":1}},
            "branches":[{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                         "plans":[{"label":"A1",
                                   "key_picks":[{"by_ord":12,"fallback":[1]}]}]}],
            "op_hero_decision":[]}
    assert check_playbook(body) != []   # ord 12 属于后手方

def test_playbook_requires_key_pick_fallback():
    """规格 §6.3：fallback 在 key_picks[] 内，不是 plan 级字段。"""
    body = {"matchup":{"first_pick_team":0,"side_map":{"us":0,"them":1}},
            "branches":[{"branch_id":"A","condition":{"first_pick":"us","their_opening":"teamfight"},
                         "plans":[{"label":"A1","key_picks":[{"by_ord":13}]}]}],
            "op_hero_decision":[]}
    assert check_playbook(body) != []
