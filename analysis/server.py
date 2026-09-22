"""本机画像 HTTP 服务。"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from analysis.profile_service import ProfileServiceError, parse_profile_query
from db.sources import SourceNotAllowed


ERROR_STATUS = {
    "insufficient_data": 200,
    "invalid_request": 400,
    "source_not_allowed": 403,
    "not_found": 404,
    "upstream_unavailable": 503,
}
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
STATIC_ROUTES = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/workbench.css": ("workbench.css", "text/css"),
    "/workbench.js": ("workbench.js", "text/javascript"),
    "/analysis-workbench.js": ("analysis-workbench.js", "text/javascript"),
    "/analysis-workbench.css": ("analysis-workbench.css", "text/css"),
}


def make_server(host, port, *, provider, catalog_provider=None, analysis_provider=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MCNDOTAGA"

        def write_bytes(self, http_status, encoded, content_type):
            self.send_response(http_status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Workbench-Pid", str(os.getpid()))
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://cdn.steamstatic.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)

        def write_json(self, http_status, body):
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.write_bytes(http_status, encoded, "application/json")

        def write_error(self, code, message):
            self.write_json(ERROR_STATUS[code], {"error": {"code": code, "message": message}})

        def do_GET(self):
            try:
                if len(self.path) > 4096:
                    self.write_error("invalid_request", "请求参数过长")
                    return
                route = urlsplit(self.path)
                if route.path in STATIC_ROUTES:
                    filename, content_type = STATIC_ROUTES[route.path]
                    try:
                        encoded = (WEB_ROOT / filename).read_bytes()
                    except OSError:
                        self.write_error("upstream_unavailable", "工作台资源暂时不可用")
                        return
                    self.write_bytes(200, encoded, content_type)
                    return
                if route.path == "/health":
                    self.write_json(200, {"status": "ok", "scope": "process"})
                    return
                if route.path == "/v1/analysis/catalog":
                    if route.query:
                        self.write_error("invalid_request", "分析目录不接受查询参数")
                        return
                    self.dispatch_analysis("catalog", {})
                    return
                if route.path == "/v1/playbook":
                    from analysis.analysis_request import parse_analysis_request
                    try:
                        fields = parse_qs(route.query, keep_blank_values=True, max_num_fields=12)
                        if any(len(value) != 1 for value in fields.values()):
                            raise ValueError("参数不能重复")
                        body = {key: value[0] for key, value in fields.items()}
                        for key in ("us", "them", "us_side", "first_pick_team", "series_id", "top_n"):
                            if key in body:
                                body[key] = int(body[key])
                        if "sources" in body:
                            body["sources"] = body["sources"].split(",")
                        request = parse_analysis_request(body, "playbook")
                    except SourceNotAllowed:
                        self.write_error("source_not_allowed", "公开剧本不允许使用训练赛来源")
                        return
                    except (ValueError, TypeError):
                        self.write_error("invalid_request", "剧本需要合法队伍、版本以及明确的我方阵营和先手阵营")
                        return
                    self.dispatch_analysis("playbook", request)
                    return
                if route.path == "/v1/catalog":
                    if route.query:
                        self.write_error("invalid_request", "目录接口不接受查询参数")
                        return
                    if catalog_provider is None:
                        self.write_error("upstream_unavailable", "工作台目录暂时不可用")
                        return
                    try:
                        self.write_json(200, catalog_provider())
                    except ProfileServiceError as exc:
                        self.write_error(exc.code, exc.message)
                    except Exception:
                        self.write_error("upstream_unavailable", "工作台目录暂时不可用，请稍后重试")
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

        def dispatch_analysis(self, resource, request):
            callback = self.server.analysis_provider
            if callback is None:
                self.write_error("upstream_unavailable", "对局分析服务暂时不可用")
                return
            try:
                body = callback(resource, request)
                code = body.get("error", {}).get("code")
                self.write_json(ERROR_STATUS.get(code, 200), body)
            except ProfileServiceError as exc:
                self.write_error(exc.code, exc.message)
            except SourceNotAllowed:
                self.write_error("source_not_allowed", "公开分析不允许使用训练赛来源")
            except Exception:
                self.write_error("upstream_unavailable", "分析暂时不可用，请稍后重试")

        def do_POST(self):
            from analysis.analysis_request import parse_analysis_request
            routes = {"/v1/analysis": "analysis", "/v1/value": "value", "/v1/policy/next": "policy", "/v1/advise": "advise", "/v1/playbook": "playbook"}
            route = urlsplit(self.path)
            if route.path not in routes or route.query:
                self.write_error("invalid_request", "不支持此分析接口或查询参数")
                return
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("JSON 字段不能重复")
                    result[key] = value
                return result
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536 or self.headers.get_content_type() != "application/json":
                    raise ValueError("请求必须是 64 KB 以内的 JSON")
                self.connection.settimeout(15)
                body = json.loads(self.rfile.read(length), object_pairs_hook=unique_object,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 不允许非有限数值")))
                request = parse_analysis_request(body, routes[route.path])
            except SourceNotAllowed:
                self.write_error("source_not_allowed", "公开分析不允许使用训练赛来源")
                return
            except (ValueError, UnicodeError, OSError):
                self.write_error("invalid_request", "请求格式、队伍、日期或 BP 顺序无效")
                return
            self.dispatch_analysis(routes[route.path], request)

        def send_error(self, code, message=None, explain=None):
            # 标准库分派未知方法或畸形请求时也保持统一错误格式。
            self.write_error("invalid_request", "不支持此 HTTP 请求")

        def log_message(self, format, *args):
            # 本机服务不把未经校验的 query 或内部异常写入日志。
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.analysis_provider = analysis_provider
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="DOTA2 战队画像本机服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("缺少 DATABASE_URL")
    from analysis.profile_service import load_profile
    from analysis.workbench_catalog import load_catalog
    from analysis.analysis_service import load_analysis

    server = make_server(
        args.host, args.port,
        provider=lambda request: load_profile(dsn, request),
        catalog_provider=lambda: load_catalog(dsn),
        analysis_provider=lambda resource, request: load_analysis(dsn, resource, request),
    )
    print(f"画像服务已启动：http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
