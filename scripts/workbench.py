"""本地 GUI 进程的启动、状态和停止入口，不管理数据库或采集器。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "workbench-runtime.json"
LOG = ROOT / "data" / "workbench-server.log"


def _identity(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _saved():
    if not STATE.exists():
        return None
    value = json.loads(STATE.read_text())
    if value.get("root") != str(ROOT):
        raise RuntimeError("运行记录不属于当前工作区")
    return value


def _owned(value):
    return bool(value and value.get("identity") and _identity(value["pid"]) == value["identity"])


def start(port):
    previous = _saved()
    if _owned(previous):
        print(f'工作台已经运行：{previous["url"]}')
        return
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("先设置 DATABASE_URL，并启动对应的本地数据库")
    if not all((ROOT / "web" / name).is_file() for name in ("index.html", "workbench.css", "workbench.js")):
        raise RuntimeError("工作台页面资源不完整，不能启动")
    ROOT.joinpath("data").mkdir(exist_ok=True)
    command = [sys.executable, "-m", "analysis.server", "--host", "127.0.0.1", "--port", str(port)]
    with LOG.open("a") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError(f"工作台启动失败，请查看 {LOG}")
            try:
                response = httpx.get(url + "v1/catalog", timeout=2, trust_env=False)
                if response.headers.get("x-workbench-pid") != str(child.pid):
                    raise RuntimeError("端口正在被其他服务使用，当前工作台未启动")
                if response.status_code == 200 and "teams" in response.json():
                    # 再次核对自己的子进程，不能将旧端口上的其他服务当成新服务。
                    time.sleep(.1)
                    if child.poll() is not None:
                        raise RuntimeError("端口已被占用，当前工作台未启动")
                    identity = _identity(child.pid)
                    if not identity:
                        raise RuntimeError("无法确认工作台进程身份")
                    value = {"pid": child.pid, "identity": identity, "root": str(ROOT), "url": url}
                    STATE.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
                    print(f"工作台已就绪：{url}")
                    print("当前只运行页面和只读接口，没有启动采集。")
                    return
                if response.status_code == 503:
                    raise RuntimeError("数据库目录不可用，请检查 DATABASE_URL、迁移和数据库状态")
            except (httpx.RequestError, ValueError):
                pass
            time.sleep(.1)
        raise RuntimeError("工作台启动超时")
    except BaseException:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        raise


def stop():
    value = _saved()
    if not _owned(value):
        print("当前工作区没有运行中的工作台，未向其他进程发送信号。")
        return
    os.kill(value["pid"], signal.SIGINT)
    for _ in range(200):
        if not _owned(value):
            STATE.unlink(missing_ok=True)
            print("工作台已停止，数据库保持原状态。")
            return
        time.sleep(.1)
    raise RuntimeError("工作台尚未退出，请检查是否有仍在执行的查询")


def main():
    parser = argparse.ArgumentParser(description="本地分析工作台")
    parser.add_argument("action", choices=("start", "status", "stop"), nargs="?", default="start")
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024 至 65535 之间")
    try:
        if args.action == "start":
            start(args.port)
        elif args.action == "stop":
            stop()
        else:
            value = _saved()
            print(f'工作台运行中：{value["url"]}' if _owned(value) else "工作台未运行")
    except (RuntimeError, OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
