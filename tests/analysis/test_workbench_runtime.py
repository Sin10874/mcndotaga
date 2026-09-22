import json

import pytest

from scripts import workbench as runtime


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "data" / "state.json")
    monkeypatch.setattr(runtime, "LOG", tmp_path / "data" / "server.log")
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid.invalid/test")
    return tmp_path


def save_state(root):
    runtime.STATE.write_text(json.dumps({"root": str(root), "pid": 12345, "identity": "owned process", "url": "http://127.0.0.1:8016/"}))


def test_missing_gui_stops_before_creating_a_process(sandbox, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("不完整GUI不应该启动服务")
    monkeypatch.setattr(runtime.subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="页面资源"):
        runtime.start(8016)


def test_stale_pid_never_receives_stop_signal(sandbox, monkeypatch):
    save_state(sandbox)
    calls = []
    monkeypatch.setattr(runtime, "_identity", lambda pid: "unrelated process")
    monkeypatch.setattr(runtime.os, "kill", lambda *args: calls.append(args))
    runtime.stop()
    assert calls == []


def test_foreign_workspace_record_is_rejected(sandbox, monkeypatch):
    save_state(sandbox / "other")
    calls = []
    monkeypatch.setattr(runtime.os, "kill", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="不属于"):
        runtime.stop()
    assert calls == []


def test_stop_only_signals_matching_owned_process_then_removes_record(sandbox, monkeypatch):
    save_state(sandbox)
    identities = iter(["owned process", None])
    calls = []
    monkeypatch.setattr(runtime, "_identity", lambda pid: next(identities))
    monkeypatch.setattr(runtime.os, "kill", lambda *args: calls.append(args))
    runtime.stop()
    assert calls == [(12345, runtime.signal.SIGINT)]
    assert not runtime.STATE.exists()


def test_start_failure_only_terminates_the_new_child(sandbox, monkeypatch):
    (sandbox / "web").mkdir()
    for name in ("index.html", "workbench.css", "workbench.js"):
        (sandbox / "web" / name).write_text("test asset")
    calls = []
    class Child:
        pid = 12345
        def poll(self):
            return None
        def terminate(self):
            calls.append("terminate child")
        def wait(self, timeout):
            calls.append("wait child")
    class Response:
        status_code = 503
        headers = {"x-workbench-pid": "12345"}
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(runtime.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(runtime.os, "kill", lambda *args: calls.append("unexpected kill"))
    with pytest.raises(RuntimeError, match="数据库目录不可用"):
        runtime.start(8016)
    assert calls == ["terminate child", "wait child"]
    assert not runtime.STATE.exists()


def test_existing_service_cannot_be_mistaken_for_new_child(sandbox, monkeypatch):
    (sandbox / "web").mkdir()
    for name in ("index.html", "workbench.css", "workbench.js"):
        (sandbox / "web" / name).write_text("test asset")
    calls = []
    class Child:
        pid = 12345
        def poll(self):
            return None
        def terminate(self):
            calls.append("terminate new child")
        def wait(self, timeout):
            calls.append("wait new child")
    class Response:
        status_code = 200
        headers = {"x-workbench-pid": "99999"}
        def json(self):
            return {"teams": []}
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(runtime.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(runtime, "_identity", lambda pid: "still importing")
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="其他服务"):
        runtime.start(8016)
    assert calls == ["terminate new child", "wait new child"]
    assert not runtime.STATE.exists()
