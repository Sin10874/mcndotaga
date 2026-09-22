from contextlib import contextmanager
from threading import Thread

import httpx
import pytest

from analysis.profile_service import ProfileServiceError
from analysis.server import make_server


@contextmanager
def running(catalog_provider=None):
    server = make_server("127.0.0.1", 0, provider=lambda request: {"team_id": request.team_id}, catalog_provider=catalog_provider)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_catalog_returns_provider_body_and_keeps_profile_route():
    calls = []
    body = {"teams": [{"team_id": 10, "name": "真实队伍"}], "defaults": {"patch": "7.41e"}}
    def catalog():
        calls.append(True)
        return body
    with running(catalog) as base:
        response = httpx.get(base + "/v1/catalog")
        profile = httpx.get(base + "/v1/profile?team_id=10&patch=7.41e")
    assert response.status_code == 200
    assert response.json() == body
    assert calls == [True]
    assert profile.json() == {"team_id": 10}
    assert response.headers["cache-control"] == "no-store"


def test_catalog_rejects_any_query_before_reading_data():
    calls = []
    with running(lambda: calls.append(True)) as base:
        response = httpx.get(base + "/v1/catalog?sources=scrim")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert calls == []


@pytest.mark.parametrize("configured", [False, True])
def test_catalog_unavailable_is_503_and_does_not_expose_internal_detail(configured):
    def failed():
        raise RuntimeError("postgresql://secret:password@private/database SQL")
    with running(failed if configured else None) as base:
        response = httpx.get(base + "/v1/catalog")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "secret" not in response.text
    assert "password" not in response.text


def test_catalog_expected_service_error_uses_safe_envelope():
    def failed():
        raise ProfileServiceError("upstream_unavailable", "目录暂时不可用")
    with running(failed) as base:
        response = httpx.get(base + "/v1/catalog")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "目录暂时不可用"


@pytest.mark.parametrize("route,mime", [("/", "text/html"), ("/index.html", "text/html"), ("/workbench.js", "text/javascript"), ("/workbench.css", "text/css")])
def test_static_allowlist_serves_gui_assets_with_safe_headers(route, mime, tmp_path, monkeypatch):
    import analysis.server as subject
    # 临时资源验证真实 HTTP 分发，不依赖另一个实施者的页面完成时间。
    assets = {"index.html": "<!doctype html><html lang='zh-CN'><title>战队工作台</title></html>", "workbench.js": "console.log('ready');", "workbench.css": "body{color:black}"}
    for name, content in assets.items():
        (tmp_path / name).write_text(content)
    monkeypatch.setattr(subject, "WEB_ROOT", tmp_path, raising=False)
    with running() as base:
        response = httpx.get(base + route)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(mime)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.text == assets["index.html" if route == "/" else route[1:]]


@pytest.mark.parametrize("route", ["/README.md", "/.env", "/../README.md", "/%2e%2e/README.md", "/web/../.env", "/contracts/openapi.yaml"])
def test_static_routes_never_expose_arbitrary_workspace_files(route):
    with running() as base:
        response = httpx.get(base + route)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
