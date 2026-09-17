import pytest
from shared.draft_template import TEMPLATE, resolve, first_pick_team_from_actions

def test_template_shape_matches_spec():
    assert len(TEMPLATE) == 24
    assert sum(1 for is_pick, _ in TEMPLATE if not is_pick) == 14
    assert sum(1 for is_pick, _ in TEMPLATE if is_pick) == 10

def test_sides_are_balanced():
    assert sum(1 for ip, w in TEMPLATE if not ip and w == "F") == 7
    assert sum(1 for ip, w in TEMPLATE if not ip and w == "O") == 7
    assert sum(1 for ip, w in TEMPLATE if ip and w == "F") == 5
    assert sum(1 for ip, w in TEMPLATE if ip and w == "O") == 5

def test_phase_structure_is_7_2_3_6_4_2():
    """规格 §8① 的固定阶段结构。"""
    phases, cur, cnt = [], TEMPLATE[0][0], 0
    for is_pick, _ in TEMPLATE:
        if is_pick == cur:
            cnt += 1
        else:
            phases.append(cnt); cur, cnt = is_pick, 1
    phases.append(cnt)
    assert phases == [7, 2, 3, 6, 4, 2]

def test_resolve_ord_13_is_first_pick_team_when_they_lead():
    """规格 §6.3 示例：first_pick_team=0 时 ord 13 属于 team 0 的 pick。"""
    assert resolve(13, 0) == (True, 0)
    assert resolve(13, 1) == (True, 1)

def test_resolve_ord_0_is_a_ban_by_first_pick_team():
    """规格 §8① 已验证：ord 0 的队伍就是先手方。"""
    assert resolve(0, 0) == (False, 0)
    assert resolve(0, 1) == (False, 1)

def test_resolve_ord_12_is_opponent_pick():
    assert resolve(12, 0) == (True, 1)

def test_first_pick_team_from_actions():
    assert first_pick_team_from_actions([{"ord": 0, "team": 1}, {"ord": 1, "team": 1}]) == 1

def test_resolve_rejects_out_of_range():
    with pytest.raises(ValueError):
        resolve(24, 0)

def test_first_pick_team_from_actions_requires_ord_zero():
    with pytest.raises(ValueError):
        first_pick_team_from_actions([{"ord": 1, "team": 0}])
