from datetime import date
import json
from pathlib import Path

import pytest

from analysis.profile_service import parse_profile_query
from db.sources import SourceNotAllowed


def test_query_defaults_are_explicit_and_reproducible():
    query = parse_profile_query("team_id=10232231&patch=7.41e", today=date(2026, 9, 22))
    assert query is not None, "画像请求必须解析为确定的参数，不能缺省整个请求"
    assert query.team_id == 10232231
    assert query.patch == "7.41e"
    assert query.as_of == date(2026, 9, 22)
    assert query.sources == ("pro_match",)


def test_scrim_is_refused_before_any_database_access():
    with pytest.raises(SourceNotAllowed):
        parse_profile_query("team_id=1&patch=7.41e&sources=pro_match,scrim")


@pytest.mark.parametrize("query", [
    "patch=7.41e", "team_id=0&patch=7.41e", "team_id=-1&patch=7.41e",
    "team_id=1&team_id=2&patch=7.41e", "team_id=1&patch=",
    "team_id=9223372036854775808&patch=7.41e", "team_id=1&patch=7.41e&sources=",
    "team_id=1&patch=7.41e&sources=unknown", "team_id=1&patch=7.41e&sources=pro_match,",
    "team_id=1&patch=7.41e&as_of=2026-09-23", "team_id=1&patch=7.41e&as_of=2026-02-30",
    "team_id=1&patch=7.41e&as_of=20260922", "team_id=1&patch=7.41e&allow_scrim=true",
    "team_id=1&patch=7.41e&as_of=0001-01-01",
])
def test_invalid_or_ambiguous_query_is_rejected(query):
    with pytest.raises(ValueError):
        parse_profile_query(query, today=date(2026, 9, 22))


def test_sources_are_normalized_without_broadening():
    query = parse_profile_query("team_id=1&patch=7.41e&sources=pub_match,pro_match,pro_match")
    assert query is not None, "来源列表不能被忽略"
    assert query.sources == ("pro_match", "pub_match")


@pytest.mark.parametrize("mutation", ["source", "identity", "date", "schema", "unknown_role", "archetype"])
def test_invalid_computed_profile_cannot_cross_service_boundary(mutation):
    from analysis.profile_service import ProfileServiceError, validate_profile_body

    body = json.loads((Path(__file__).parents[2] / "contracts/fixtures/profile__position_unknown.json").read_text())
    query = parse_profile_query("team_id=10232231&patch=7.41e&as_of=2026-09-16&sources=pro_match,pub_match")
    if mutation == "source":
        body["sources_used"].append("scrim")
    elif mutation == "identity":
        body["team_id"] = 999
    elif mutation == "date":
        body["as_of"] = "2026-09-17"
    elif mutation == "schema":
        del body["coverage"]["pro_match"]["n_position_unknown"]
    elif mutation == "unknown_role":
        body["players"][0]["dimensions"]["combat"] = {"percentile": 80, "n": 35}
    else:
        body["players"][0]["dimensions"]["hero_archetype"]["teamfight"] = 1
    with pytest.raises(ProfileServiceError) as caught:
        validate_profile_body(body, request=query)
    assert caught.value.code == "upstream_unavailable"


def seed_profile(db):
    from ingest.order_families import resolve_in, SPEC_FAMILY

    db.execute("INSERT INTO teams(team_id,name) VALUES (1,'真实测试队伍'),(2,'对手')")
    db.execute("INSERT INTO patches(patch_id,version_name,base_version,released_at) VALUES (1,'7.41e','7.41','2026-06-01Z')")
    db.execute("INSERT INTO app_config_kv(key,value) VALUES ('min_sample_n','30')")
    for metric in ("hero_pool", "map_vision", "tempo"):
        db.execute("INSERT INTO metric_weights(metric,data_source,weight) VALUES (%s,'pro_match',1)", (metric,))
    for hero_id in range(1, 25):
        db.execute("INSERT INTO heroes(hero_id,name,localized_name,roles) VALUES (%s,%s,%s,ARRAY['Carry','Disabler'])", (hero_id, str(hero_id), str(hero_id)))
    db.execute("INSERT INTO players(account_id,name) VALUES (77,'实际参赛选手')")
    db.execute("""INSERT INTO matches(match_id,data_source,patch_id,started_at,radiant_team_id,dire_team_id,
        radiant_win,draft_state,n_draft_actions,first_pick_team,parse_state,game_mode)
        SELECT i,'pro_match',1,'2026-09-16T12:00:00Z'::timestamptz,1,2,true,'complete',24,0,'full',2 FROM generate_series(101,103) i""")
    db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id,position,stats_available)
        SELECT i,0,77,0,8,NULL,true FROM generate_series(101,103) i""")
    for order in range(24):
        is_pick, team = resolve_in(SPEC_FAMILY, order, 0)
        db.execute("INSERT INTO draft_actions(match_id,ord,is_pick,team,hero_id) SELECT i,%s,%s,%s,%s FROM generate_series(101,103) i", (order, is_pick, team, order + 1))


def test_service_computes_contract_from_real_database_rows(db):
    from analysis.profile_service import build_profile_from_connection, validate_profile_body

    seed_profile(db)
    request = parse_profile_query("team_id=1&patch=7.41e&as_of=2026-09-16")
    body = build_profile_from_connection(db, request)
    assert body is not None, "服务必须计算数据库中的比赛，不返回空结果"
    validate_profile_body(body, request=request)
    assert body["coverage"]["pro_match"]["n_matches"] == 3
    assert body["coverage"]["pro_match"]["n_position_unknown"] == 3
    assert body["players"][0]["account_id"] == 77
    assert body["players"][0]["hero_pool"]["window_games"] == 3
    assert body["players"][0]["hero_pool"]["presence_pick_rate"] == 1
    assert body["players"][0]["dimensions"]["combat"] == {"value": None, "reason": "insufficient_samples"}


def test_database_unavailable_is_classified_without_connection_details():
    from analysis.profile_service import ProfileServiceError, load_profile

    request = parse_profile_query("team_id=1&patch=7.41e")
    with pytest.raises(ProfileServiceError) as caught:
        load_profile("postgresql://dota@127.0.0.1:1/unavailable?connect_timeout=1", request)
    assert caught.value.code == "upstream_unavailable"
    assert "postgresql" not in caught.value.message
