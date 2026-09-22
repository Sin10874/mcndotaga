from analysis.analysis_repository import load_matchup_summary
import pytest


@pytest.fixture
def summary_dsn(dsn):
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO teams(team_id,name) VALUES (8801,'我方'),(8802,'对手'),(8803,'第三方')")
        patch = conn.execute("INSERT INTO patches(version_name,base_version,released_at) VALUES ('7.40z','7.40','2026-01-01') RETURNING patch_id").fetchone()[0]
        for match_id, source, start, radiant, dire, won in (
            (88001, 'pro_match', '2026-01-10', 8801, 8803, True),
            (88002, 'pro_match', '2026-01-11', 8802, 8803, False),
            (88003, 'scrim', '2026-01-11', 8801, 8802, True),
            (88004, 'pro_match', '2026-01-15', 8801, 8802, True),
            (88005, 'pub_match', '2026-01-11', 8801, 8803, False),
        ):
            conn.execute("""INSERT INTO matches(match_id,data_source,started_at,patch_id,radiant_team_id,dire_team_id,radiant_win)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""", (match_id,source,start,patch,radiant,dire,won))
        conn.execute("UPDATE matches SET duration_s = 3600 WHERE match_id BETWEEN 88001 AND 88005")
    yield dsn
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM matches WHERE match_id BETWEEN 88001 AND 88006")
        conn.execute("DELETE FROM patches WHERE version_name='7.40z'")
        conn.execute("DELETE FROM teams WHERE team_id BETWEEN 8801 AND 8803")


def test_summary_scopes_patch_dates_sources_and_does_not_invent_meetings(summary_dsn):
    dsn = summary_dsn
    request = {"us":8801,"them":8802,"patch":"7.40z","as_of":"2026-01-12","sources":["pro_match"]}
    result = load_matchup_summary(dsn, request)
    assert result["teams"]["us"]["n_matches"] == 1
    assert result["teams"]["us"]["win_rate"] == 1.
    assert result["teams"]["them"]["win_rate"] == 0.
    assert result["head_to_head"] == {"n_matches":0,"games":[]}
    result = load_matchup_summary(dsn, {**request, "sources":["pro_match","pub_match"]})
    assert result["teams"]["us"]["n_matches"] == 2
    assert result["teams"]["us"]["win_rate"] == .5


def test_summary_never_uses_result_of_game_ending_after_cutoff(summary_dsn):
    dsn = summary_dsn
    import psycopg
    with psycopg.connect(dsn) as conn:
        patch = conn.execute("SELECT patch_id FROM patches WHERE version_name='7.40z'").fetchone()[0]
        conn.execute("""INSERT INTO matches(match_id,data_source,started_at,duration_s,patch_id,radiant_team_id,dire_team_id,radiant_win)
                        VALUES (88006,'pro_match','2026-01-12 23:50:00+00',3600,%s,8801,8802,True)""", (patch,))
    result = load_matchup_summary(dsn, {"us":8801,"them":8802,"patch":"7.40z","as_of":"2026-01-12","sources":["pro_match"]})
    assert result["teams"]["us"]["n_matches"] == 2
    assert result["teams"]["us"]["n_outcomes"] == 1
    assert result["head_to_head"]["games"][0]["winner"] is None
