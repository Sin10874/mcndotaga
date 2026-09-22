from datetime import datetime, timedelta, timezone
import pytest
from models.value_model import probability_parts, train_value_model


def rows(n=100):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [{"match_id": i, "patch": "7.41e", "base_version": "7.41",
             "started_at": start + timedelta(hours=i*3),
             "ended_at": start + timedelta(hours=i*3+1),
             "data_source": "pro_match", "radiant_win": i % 4 != 0,
             "first_pick_team": i % 2, "radiant_team_id": 1, "dire_team_id": 2,
             "radiant_heroes": [1,2,3,4,5], "dire_heroes": [6,7,8,9,10],
             "players": []} for i in range(n)]


def test_probability_contributions_are_exact_and_unavailable_factor_stays_zero():
    prob, parts = probability_parts({"hero:1": 1.2, "first_pick": -.3},
                                    {"patch_strength": {"hero:1": 1}, "first_pick": {"first_pick": 1},
                                     "player_comfort": {}, "counter_matchup": {}})
    assert prob > .5
    assert sum(parts.values()) == pytest.approx(prob-.5, abs=1e-12)
    assert parts['counter_matchup'] == 0


def test_train_time_holdout_does_not_fit_future_labels():
    original = rows()
    changed = [dict(r, radiant_win=not r['radiant_win']) if i >= 80 else r for i,r in enumerate(original)]
    a = train_value_model({'matches': original}, patch='7.41e', sources=['pro_match'])
    b = train_value_model({'matches': changed}, patch='7.41e', sources=['pro_match'])
    assert a['weights'] == b['weights']
    assert a['metadata']['train_n'] == 80
    assert a['metadata']['validation_n'] == 20
    assert a['metadata']['train_end'] < a['metadata']['validation_start']
    assert a['metadata']['validation']['log_loss'] != b['metadata']['validation']['log_loss']


def test_time_split_keeps_same_timestamp_together_and_excludes_running_match():
    data = rows()
    data[79] = dict(data[79], started_at=data[80]['started_at'], ended_at=data[80]['ended_at'])
    data[78] = dict(data[78], ended_at=data[80]['started_at'] + timedelta(hours=1))
    result = train_value_model({'matches':data}, patch='7.41e', sources=['pro_match'])
    assert result['metadata']['train_n'] == 78
    assert result['metadata']['validation_n'] == 21
    assert result['metadata']['excluded_overlap_n'] == 1


def test_exact_patch_sample_minimum_cannot_borrow_other_patch():
    data = rows(70) + [dict(r, patch='7.41d') for r in rows(100)]
    with pytest.raises(ValueError, match='75'):
        train_value_model({'matches':data}, patch='7.41e', sources=['pro_match'])
