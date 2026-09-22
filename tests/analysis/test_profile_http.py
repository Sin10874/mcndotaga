from contextlib import contextmanager
import json
from pathlib import Path
from threading import Thread

import httpx
import pytest

from analysis.profile_service import ProfileServiceError
from analysis.server import make_server


@contextmanager
def running(provider):
    server = make_server("127.0.0.1", 0, provider=provider)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def fixture_profile():
    return json.loads((Path(__file__).parents[2] / "contracts/fixtures/profile__position_unknown.json").read_text())


def test_http_returns_real_provider_body_and_parsed_request():
    body = fixture_profile()
    requests = []

    def provider(query):
        requests.append(query)
        return body

    with running(provider) as base:
        response = httpx.get(base + "/v1/profile?team_id=10232231&patch=7.41e&as_of=2026-09-16")
    assert response.status_code == 200
    assert response.json() == body
    assert requests[0].team_id == 10232231
    assert requests[0].as_of.isoformat() == "2026-09-16"
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix,http_status,code", [
    ("?team_id=1&patch=7.41e&sources=scrim", 403, "source_not_allowed"),
    ("?team_id=1&patch=7.41e&allow_scrim=true", 400, "invalid_request"),
    ("?team_id=1&patch=7.41e&sources=unknown", 400, "invalid_request"),
    ("?team_id=1&patch=7.41e&team_id=2", 400, "invalid_request"),
])
def test_rejected_queries_never_reach_data_provider(suffix, http_status, code):
    calls = []
    with running(lambda query: calls.append(query)) as base:
        response = httpx.get(base + "/v1/profile" + suffix)
    assert response.status_code == http_status
    assert response.json()["error"]["code"] == code
    assert calls == []


@pytest.mark.parametrize("error,http_status,code", [
    (ProfileServiceError("insufficient_data", "缺少可观测 BP 样本"), 200, "insufficient_data"),
    (ProfileServiceError("not_found", "战队不存在"), 404, "not_found"),
    (RuntimeError("postgresql://secret:password@localhost/private SQL"), 503, "upstream_unavailable"),
])
def test_business_and_internal_errors_use_safe_contract(error, http_status, code):
    def provider(query):
        raise error

    with running(provider) as base:
        response = httpx.get(base + "/v1/profile?team_id=1&patch=7.41e")
    assert response.status_code == http_status
    assert response.json()["error"]["code"] == code
    assert "secret" not in response.text
    assert "password" not in response.text
    assert " SQL" not in response.text


def test_health_only_claims_process_health_and_unknown_route_is_not_found():
    calls = []
    with running(lambda query: calls.append(query)) as base:
        health = httpx.get(base + "/health")
        missing = httpx.get(base + "/v1/value")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "scope": "process"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert calls == []


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "HEAD"])
def test_unsupported_methods_keep_json_error_contract(method):
    calls = []
    with running(lambda query: calls.append(query)) as base:
        response = httpx.request(method, base + "/v1/profile")
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    if method == "HEAD":
        assert response.content == b""
    else:
        assert response.json()["error"]["code"] == "invalid_request"
    assert calls == []


def test_private_training_data_cannot_change_public_http_profile(db):
    from analysis.profile_service import build_profile_from_connection
    from tests.analysis.test_profile_service import seed_profile

    seed_profile(db)
    with running(lambda query: build_profile_from_connection(db, query)) as base:
        route = base + "/v1/profile?team_id=1&patch=7.41e&as_of=2026-09-16"
        before = httpx.get(route)
        assert before.status_code == 200
        assert "players" in before.json()
        db.execute("""INSERT INTO matches(match_id,data_source,patch_id,started_at,radiant_team_id,
            dire_team_id,radiant_win,parse_state,game_mode)
            SELECT i,'scrim',1,'2026-09-16T13:00:00Z'::timestamptz,1,2,false,'full',2
            FROM generate_series(201,240) i""")
        db.execute("""INSERT INTO match_players(match_id,player_slot,account_id,team,hero_id,position,stats_available)
            SELECT i,0,77,0,24,1,true FROM generate_series(201,240) i""")
        after = httpx.get(route)
    assert after.status_code == 200
    assert after.content == before.content, "私有训练赛不能影响公开画像的任何响应字段"
