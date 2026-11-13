"""HTTP 服务：静态资源 + REST + SSE 流式输出。

零第三方依赖（标准库 http.server），单进程即可承载完整演示。
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api, config, db
#给 Python 的 mimetypes 模块注册后缀对应的 MIME 类型，一般用于静态文件服务
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

#拿到访问你网页的客户端 IP，用来日志、记录访问来源
def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    return handler.client_address[0] if handler.client_address else ""#如果为空，返回空字符串
#`client_address[0]` 取出 IP 地址
#`handler.client_address` 是一个元组 `(客户端IP, 端口号)`

class KBHandler(BaseHTTPRequestHandler):
    server_version = "KBPlatform/1.0"   # 服务器版本
    protocol_version = "HTTP/1.1"#指定使用的 HTTP 协议版本

    # ---------------- 日志
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("KB_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        """
        给知识库平台的 HTTP 服务加了一个可通过环境变量控制的访问日志开关，
        不开就安静跑，不刷屏。
        """
    # ---------------- 工具
    #统一的响应发送函数，所有接口返回都走这个方法
    def _send(self, resp: api.Response) -> None:
        body = resp.body
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")#**接口响应禁止浏览器缓存**，每次都重新请求
        self.send_header("X-Content-Type-Options", "nosniff")#**防止浏览器嗅探内容类型**
        for k, v in (resp.headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = path[len("/static/"):] if path.startswith("/static/") else path.lstrip("/")
        if rel in ("", "/"):
            rel = "index.html"
        target = (config.STATIC_DIR / rel).resolve()
        try:
            if not str(target).startswith(str(config.STATIC_DIR.resolve())) or not target.is_file():
                raise FileNotFoundError(rel)
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except FileNotFoundError:
            self._send(api.fail("页面不存在", 404, "not_found"))

    # ---------------- 入口
    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.end_headers()

    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}

        if not path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                return self._static(path)
            return self._send(api.fail("方法不允许", 405))

        req = api.Request(method=method, path=path, query=query, headers=headers,
                          body=body, ip=_client_ip(self))
        try:
            result = api.dispatch(req)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            result = api.fail(f"服务异常：{exc}", 500, "internal_error")

        if isinstance(result, api.StreamResponse):
            return self._send_stream(result)
        self._send(result)

    def _send_stream(self, resp: api.StreamResponse) -> None:
        self.send_response(200)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for k, v in (resp.headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            for chunk in resp.generator():
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


class KBServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str = config.HOST, port: int = config.PORT) -> KBServer:
    db.init_db()
    return KBServer((host, port), KBHandler)


def run(host: str = config.HOST, port: int = config.PORT) -> None:
    server = create_server(host, port)
    url = f"http://{host}:{port}"
    print("=" * 68)
    print(f"  {config.APP_NAME} v{config.APP_VERSION}")
    print(f"  访问地址 : {url}")
    print(f"  数据库   : {config.DB_PATH}")
    print(f"  演示账号 : admin/123456（系统管理员）  kadmin/123456（知识管理员）  zhangsan/123456（普通用户）")
    print("=" * 68)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务")
    finally:
        server.server_close()
