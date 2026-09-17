"""`constants/_http.py` 的缓存语义：命中不打网络、刷新走原始字节、坏缓存必须响亮报错。

三个测试全部用 tmp_path + monkeypatch，既不碰真实缓存目录也不打网络。
"""
import json, pytest
from constants import _http

def test_cache_hit_does_not_touch_the_network(tmp_path, monkeypatch):
    monkeypatch.setattr(_http, "CACHE", tmp_path)
    monkeypatch.delenv("REFRESH_NETWORK", raising=False)
    (tmp_path / "x.json").write_text('{"cached": 1}', encoding="utf-8")
    def boom(*a, **k): raise AssertionError("cache hit 仍然打了网络")
    monkeypatch.setattr(_http.httpx, "get", boom)
    assert _http.fetch_json("https://example.invalid/x", "x.json") == {"cached": 1}

def test_refresh_bypasses_cache_and_sends_the_custom_ua(tmp_path, monkeypatch):
    monkeypatch.setattr(_http, "CACHE", tmp_path)
    (tmp_path / "x.json").write_text('{"stale": 1}', encoding="utf-8")
    monkeypatch.setenv("REFRESH_NETWORK", "1")
    seen = {}
    class R:
        content = b'{"fresh": 2}'
        def raise_for_status(self): pass
        def json(self): return {"fresh": 2}
    def fake_get(url, **kw):
        seen.update(url=url, **kw); return R()
    monkeypatch.setattr(_http.httpx, "get", fake_get)
    assert _http.fetch_json("https://example.invalid/x", "x.json") == {"fresh": 2}
    assert seen["headers"] == {"User-Agent": _http.UA}
    assert seen["timeout"] == 60
    assert json.loads((tmp_path / "x.json").read_text()) == {"fresh": 2}

def test_corrupt_cache_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(_http, "CACHE", tmp_path)
    monkeypatch.delenv("REFRESH_NETWORK", raising=False)
    (tmp_path / "x.json").write_text('{"1": {"id": 1, "na', encoding="utf-8")
    with pytest.raises(RuntimeError, match="x.json"):
        _http.fetch_json("https://example.invalid/x", "x.json")
