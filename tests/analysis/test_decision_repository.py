from datetime import datetime, timedelta, timezone

from analysis.decision_repository import (
    _PUBLIC_VIEW,
    _ended_by,
    _series_first_pick_team,
    _utc_window,
    build_op_hero_decisions,
)


def samples(hero_id, mode, wins, losses, counter_hero_id=None):
    return [
        {"hero_id": hero_id, "mode": mode, "won": index < wins,
         "counter_hero_id": counter_hero_id}
        for index in range(wins + losses)
    ]


def test_op_decision_degrades_each_short_quantity_and_does_not_invent_matchup():
    rows = (
        samples(55, "pick", 20, 9)
        + samples(55, "ban", 15, 15)
    )
    result = build_op_hero_decisions(rows, min_n=30)
    option = result[0]
    assert option["if_we_pick"] == {"value": None, "reason": "insufficient_samples"}
    assert option["if_we_ban"] == {"wr": .5, "n": 30}
    assert option["if_we_leave"] == {"value": None, "reason": "insufficient_samples"}
    assert option["recommendation"] == "insufficient_data"


def test_op_decision_uses_observed_counter_and_leave_sample():
    rows = (
        samples(55, "pick", 15, 15)
        + samples(55, "ban", 16, 14)
        + samples(55, "leave", 18, 12, counter_hero_id=36)
        + samples(55, "counter", 20, 10, counter_hero_id=36)
    )
    option = build_op_hero_decisions(rows, min_n=30)[0]
    assert option["if_we_leave"] == {
        "our_wr": .6, "their_wr": .4, "n": 30,
        "our_counter_options": [{"hero_id": 36, "wr": 2 / 3, "n": 30}],
    }
    assert option["recommendation"] == "leave_and_counter"


def test_decision_window_uses_utc_instants_instead_of_database_session_date_casts():
    china = timezone(timedelta(hours=8))
    as_of, lower, cutoff = _utc_window("2026-09-22", datetime(2026, 9, 22, 9, 30, tzinfo=china))
    assert as_of.isoformat() == "2026-09-22"
    assert lower.tzinfo == timezone.utc
    assert lower.hour == 0
    assert cutoff == datetime(2026, 9, 22, 1, 30, tzinfo=timezone.utc)
    assert _PUBLIC_VIEW == "opponent_profile_matches_with_pub"


def test_pending_rows_remain_in_snapshot_but_only_finished_rows_are_op_eligible():
    cutoff = datetime(2026, 9, 23, tzinfo=timezone.utc)
    pending = {"started_at": datetime(2026, 9, 22, tzinfo=timezone.utc), "duration_s": None}
    finished = {"started_at": datetime(2026, 9, 22, tzinfo=timezone.utc), "duration_s": 3600}
    assert _ended_by(pending, cutoff) is False
    assert _ended_by(finished, cutoff) is True


def test_series_first_pick_is_mapped_through_historical_team_identity():
    request = {"us": 10, "them": 20, "us_side": 1}
    assert _series_first_pick_team({
        "radiant_team_id": 20, "dire_team_id": 10, "first_pick_team": 0,
    }, request) == 0
    assert _series_first_pick_team({
        "radiant_team_id": 10, "dire_team_id": 20, "first_pick_team": 0,
    }, request) == 1
