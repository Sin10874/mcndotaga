"""本机画像 HTTP 服务。"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from urllib.parse import urlsplit

from analysis.profile_service import ProfileServiceError, parse_profile_query
from db.sources import SourceNotAllowed


ERROR_STATUS = {
    "insufficient_data": 200,
    "invalid_request": 400,
    "source_not_allowed": 403,
    "not_found": 404,
    "upstream_unavailable": 503,
}


def make_server(host, port, *, provider):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MCNDOTAGA"

        def write_json(self, http_status, body):
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(http_status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)

        def write_error(self, code, message):
            self.write_json(ERROR_STATUS[code], {"error": {"code": code, "message": message}})

        def do_GET(self):
            try:
                if len(self.path) > 4096:
                    self.write_error("invalid_request", "请求参数过长")
                    return
                route = urlsplit(self.path)
                if route.path == "/health":
                    self.write_json(200, {"status": "ok", "scope": "process"})
                    return
                if route.path != "/v1/profile":
                    self.write_error("not_found", "接口不存在")
                    return
                try:
                    request = parse_profile_query(route.query)
                except SourceNotAllowed:
                    self.write_error("source_not_allowed", "对手画像不允许使用训练赛来源")
                    return
                except ValueError as exc:
                    self.write_error("invalid_request", str(exc))
                    return
                try:
                    body = provider(request)
                    self.write_json(200, body)
                except ProfileServiceError as exc:
                    self.write_error(exc.code, exc.message)
                except Exception:
                    self.write_error("upstream_unavailable", "画像数据暂时不可用，请稍后重试")
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            self.write_error("invalid_request", "该服务仅支持 GET 查询")

        def send_error(self, code, message=None, explain=None):
            # 标准库分派未知方法或畸形请求时也保持统一错误格式。
            self.write_error("invalid_request", "不支持此 HTTP 请求")

        def log_message(self, format, *args):
            # 本机服务不把未经校验的 query 或内部异常写入日志。
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="DOTA2 战队画像本机服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("缺少 DATABASE_URL")
    from analysis.profile_service import load_profile

    server = make_server(args.host, args.port, provider=lambda request: load_profile(dsn, request))
    print(f"画像服务已启动：http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
