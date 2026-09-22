from datetime import datetime, timezone

from ingest.collector_report import render_report


def snapshot():
    return {
        "generated_at": "2026-09-22T04:00:00+00:00",
        "window_days": 90,
        "counts": {"matches": 100, "complete": 80, "pending": 12,
                   "unavailable": 8, "non_cm": 0, "anomalies": 2,
                   "draft_actions": 1920, "teams": 14, "players": 70,
                   "detailed_matches": 20, "position_unknown": 200},
        "metrics": {"latest_bp_lag_hours": 12.5, "retry_success_rate": None,
                    "retry_attempts": 0, "requests_today": 11, "daily_limit": 3000,
                    "unavailable_rate": .08},
        "cursors": [{"source": "pro_matches", "last_seen_id": 12345,
                     "updated_at": "2026-09-22T04:00:00+00:00"}],
        "recent_matches": [{"match_id": 12345, "started_at": "2026-09-22T01:00:00+00:00",
            "radiant": "<script>alert('x')</script>", "dire": "队伍 B", "league": "联赛 A",
            "patch": "7.41e", "draft_state": "complete", "game_mode": 2,
            "anomaly": False, "n_draft_actions": 24, "n_players": 10,
            "draft": [{"ord": 0, "hero": "Lone Druid", "is_pick": False, "team": 0}]}],
        "secret": "should-never-be-rendered",
    }


def test_report_contains_real_counts_and_honest_acceptance_boundary():
    html = render_report(snapshot())
    assert "职业比赛采集状态" in html
    assert "1,920" in html
    assert "48 小时" in html and "未验收" in html
    assert "六维画像" in html and "尚未接入" in html
    assert "样本不足" in html


def test_report_escapes_upstream_names_and_excludes_unselected_payload_fields():
    html = render_report(snapshot())
    assert "&lt;script&gt;alert" in html
    assert "<script>alert('x')</script>" not in html
    assert "should-never-be-rendered" not in html
    assert "Lone Druid" in html


def test_unavailable_alert_has_a_real_threshold_and_empty_metrics_stay_unknown():
    data = snapshot()
    html = render_report(data)
    assert "超过 5%" in html
    data["metrics"]["unavailable_rate"] = .05
    data["metrics"]["latest_bp_lag_hours"] = None
    html = render_report(data)
    assert "超过 5%" not in html
    assert "尚无完整 BP" in html


def test_report_is_standalone_and_has_no_forbidden_typography():
    html = render_report(snapshot())
    assert '<html lang="zh-CN">' in html
    assert 'name="viewport"' in html
    assert 'src="http' not in html and 'href="http' not in html
    assert all(s not in html for s in ("——", "—", "–", "<em>", "<i>", "font-style: italic"))


def test_snapshot_only_reads_public_matches_and_excludes_non_cm_from_bp_rates(db):
    from ingest.collector_report import collect_snapshot
    now = datetime(2026, 9, 22, 4, tzinfo=timezone.utc)
    db.execute("INSERT INTO heroes (hero_id,name,localized_name) VALUES (80,'lone_druid','Lone Druid')")
    db.execute("INSERT INTO teams (team_id,name) VALUES (1,'公开队'), (2,'私密队')")
    for mid, source, state, mode, team in [
        (11, 'pro_match', 'complete', 2, 1),
        (12, 'pro_match', 'pending', 2, 1),
        (13, 'pro_match', 'unavailable', 2, 1),
        (14, 'pro_match', 'unavailable', 1, 1),
        (15, 'scrim', 'complete', 2, 2),
        (16, 'pub_match', 'pending', 2, None),
    ]:
        db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state,game_mode,
                         radiant_team_id,n_draft_actions)
                      VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                   (mid, source, now, state, mode, team, 1 if state == 'complete' else None))
    db.execute("INSERT INTO draft_actions VALUES(11,0,false,0,80),(15,0,false,0,80)")
    db.execute("UPDATE matches SET anomaly=true WHERE match_id=14")
    db.execute("""INSERT INTO match_players(match_id,player_slot,team,hero_id)
                  VALUES (11,0,0,80),(15,0,0,80)""")
    for mid, outcome, hour in [(11, 'not_ready', 1), (11, 'ok', 2), (12, 'not_ready', 1),
                                (12, 'error', 2)]:
        db.execute("INSERT INTO collector_attempts(source,match_id,outcome,at) VALUES ('match_details',%s,%s,%s)",
                   (mid, outcome, now.replace(hour=hour)))
    db.execute("INSERT INTO collector_attempts(source,outcome,at) VALUES ('pro_matches','ok',%s)", (now,))
    got = collect_snapshot(db, now=now)
    assert got["counts"]["matches"] == 4
    assert got["counts"]["complete"] == 1
    assert got["counts"]["pending"] == 1
    assert got["counts"]["unavailable"] == 1
    assert got["counts"]["non_cm"] == 1
    assert got["counts"]["anomalies"] == 0
    assert got["counts"]["teams"] == 1
    assert got["counts"]["draft_actions"] == 1
    assert got["counts"]["position_unknown"] == 1
    assert got["metrics"]["unavailable_rate"] == 1 / 3
    assert got["metrics"]["requests_today"] == 5
    assert got["metrics"]["retry_attempts"] == 2
    assert got["metrics"]["retry_success_rate"] == .5
    assert {m['match_id'] for m in got['recent_matches']} == {11, 12, 13, 14}
    assert '私密队' not in render_report(got)
    assert got['recent_matches'][0]['started_at'].endswith('+00:00')


def test_empty_database_report_does_not_fabricate_health(db):
    from ingest.collector_report import collect_snapshot
    got = collect_snapshot(db, now=datetime(2026, 9, 22, tzinfo=timezone.utc))
    assert got['metrics']['latest_bp_lag_hours'] is None
    assert got['metrics']['unavailable_rate'] is None
    assert got['metrics']['retry_success_rate'] is None
    assert got['counts']['matches'] == 0
    assert '尚未发现公开比赛' in render_report(got)


def test_queue_count_includes_discovery_before_match_details_exist(db):
    from ingest.collector_report import collect_snapshot
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    db.execute("""INSERT INTO collector_match_queue(match_id,discovered_at,status,next_attempt_at)
                  VALUES (999,%s,'pending',%s)""", (now, now))
    got = collect_snapshot(db, now=now)
    assert got['counts']['matches'] == 0
    assert got['metrics']['pending_queue'] == 1


def test_structurally_invalid_bp_does_not_look_like_fresh_usable_data(db):
    from ingest.collector_report import collect_snapshot
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    db.execute("""INSERT INTO matches(match_id,data_source,started_at,draft_state,
                  game_mode,anomaly,n_draft_actions)
                  VALUES (123,'pro_match',%s,'complete',2,true,0)""", (now,))
    got = collect_snapshot(db, now=now)
    assert got['counts']['complete'] == 1
    assert got['counts']['anomalies'] == 1
    assert got['metrics']['latest_bp_lag_hours'] is None
