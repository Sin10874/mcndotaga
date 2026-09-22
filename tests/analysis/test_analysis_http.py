from contextlib import contextmanager
from threading import Thread

import httpx

from analysis.server import make_server


@contextmanager
def running(callback=None):
    server = make_server("127.0.0.1", 0, provider=lambda request: {})
    server.analysis_provider = callback or (lambda resource, request: {"resource": resource, "request": request})
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request_body():
    return {"us": 1, "them": 2, "us_side": 0, "patch": "7.41e", "first_pick_team": 0,
            "draft": [], "sources": ["pro_match"], "as_of": "2026-09-22"}


def test_analysis_post_accepts_explicit_sides_and_routes_to_provider():
    with running() as base:
        response = httpx.post(base + "/v1/analysis", json=request_body())
    assert response.status_code == 200
    assert response.json()["resource"] == "analysis"
    assert response.json()["request"]["us_side"] == 0


def test_analysis_rejects_illegal_prefix_and_scrim_before_provider():
    with running() as base:
        body = request_body()
        body["draft"] = [{"ord": 0, "team": 1, "is_pick": True, "hero_id": 80}]
        invalid = httpx.post(base + "/v1/analysis", json=body)
        body = request_body()
        body["sources"] = ["scrim"]
        forbidden = httpx.post(base + "/v1/analysis", json=body)
    assert invalid.status_code == 400
    assert forbidden.status_code == 403


def test_analysis_rejects_ambiguous_json_and_oversize_body():
    with running() as base:
        duplicate = httpx.post(base + "/v1/analysis", content='{"us":1,"us":2}', headers={"content-type": "application/json"})
        oversized = httpx.post(base + "/v1/analysis", content=" " * 65537, headers={"content-type": "application/json"})
    assert duplicate.status_code == 400
    assert oversized.status_code == 400


def test_analysis_business_failure_stays_http_200():
    with running(lambda resource, request: {"error": {"code": "insufficient_data", "message": "模型未就绪"}}) as base:
        response = httpx.post(base + "/v1/analysis", json=request_body())
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "insufficient_data"


def test_analysis_internal_failure_never_exposes_exception():
    def unavailable(resource, request):
        raise RuntimeError("postgresql://private:secret@localhost data SQL")
    with running(unavailable) as base:
        response = httpx.post(base + "/v1/analysis", json=request_body())
    assert response.status_code == 503
    assert "secret" not in response.text
