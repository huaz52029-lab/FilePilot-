"""测试用的本地 HTTP 服务器。

支持多种服务器行为，用于覆盖真实的下载场景：

* ``range``       正确支持 Range（206 + Content-Range）
* ``norange``     不支持 Range（忽略 Range，返回 200）
* ``lying``       声称 Accept-Ranges: bytes，但实际忽略 Range 请求
* ``badrange``    返回错误的 Content-Range 起点
* ``redirect``    302 跳转到目标文件
* ``missing``     404
* ``flaky``       连续返回若干次 503 后恢复正常
* ``signed``      模拟 GitHub Release 的临时签名地址：未带令牌的原始 URL 返回 302，
                  令牌下载若干次后过期并返回 ``403 jwt:expired``（用于验证重新建立连接）
* ``unknown``     不返回 Content-Length（分块/连接关闭），且不支持 Range
* ``slow``        按小块慢速发送，便于测试暂停 / 取消
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_CONTENT_TYPE = "application/octet-stream"


@dataclass
class ServerState:
    """服务器运行状态与请求记录。"""

    files: dict[str, bytes] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    modified: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    signed_generation: dict[str, int] = field(default_factory=dict)
    signed_remaining: dict[str, int] = field(default_factory=dict)
    signed_ttl: dict[str, int] = field(default_factory=dict)
    signed_redirects: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, path: str, range_header: str | None) -> None:
        with self.lock:
            self.requests.append((path, range_header))

    def ranges_for(self, path: str) -> list[str | None]:
        with self.lock:
            return [header for recorded, header in self.requests if recorded == path]

    def take_failure(self, path: str) -> bool:
        """消耗一次故障注入。"""
        with self.lock:
            remaining = self.failures.get(path, 0)
            if remaining <= 0:
                return False
            self.failures[path] = remaining - 1
            return True

    def signed_token(self, path: str) -> int:
        """返回当前有效的临时令牌（重新请求原始 URL 会拿到最新令牌）。"""
        with self.lock:
            self.signed_redirects += 1
            return self.signed_generation.get(path, 1)

    def consume_signed_token(self, path: str) -> bool:
        """消耗一次令牌额度；额度用尽返回 False 表示令牌失效。"""
        with self.lock:
            remaining = self.signed_remaining.get(path, 0)
            if remaining <= 0:
                return False
            self.signed_remaining[path] = remaining - 1
            return True

    def expire_signed_token(self, path: str) -> int:
        """让当前令牌失效并生成新令牌。"""
        with self.lock:
            generation = self.signed_generation.get(path, 1) + 1
            self.signed_generation[path] = generation
            self.signed_remaining[path] = self.signed_ttl.get(path, 1)
            return generation


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FilePilotTestServer/1.0"

    # -- 工具 --------------------------------------------------------------
    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - 覆盖父类
        """静默处理日志（测试输出保持干净）。"""

    def _send(self, status: int, *, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    # -- HTTP 方法 ---------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802 - HTTP 方法名
        self._handle(send_body=False)

    def do_GET(self) -> None:  # noqa: N802 - HTTP 方法名
        self._handle(send_body=True)

    def _handle(self, *, send_body: bool) -> None:
        path = urlparse(self.path).path
        range_header = self.headers.get("Range")
        self.state.record(path, range_header)
        mode = self.state.modes.get(path, "range")
        payload = self.state.files.get(path, b"")

        if mode == "missing" or path not in self.state.files:
            self._send(404, body=b"not found")
            return

        if mode == "redirect":
            target = self.state.modes.get(f"{path}:target", "/file.bin")
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if mode == "signed":
            query = parse_qs(urlparse(self.path).query)
            token = (query.get("token") or [None])[0]
            current = self.state.signed_generation.get(path, 1)
            if token is None:
                # 未带令牌：返回新的临时地址（等价于 GitHub Release 的 302 跳转）
                self.send_response(302)
                self.send_header("Location", f"{path}?token={self.state.signed_token(path)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if token != str(current):
                # 令牌已过期：这正是 GitHub 返回 618 jwt:expired 的场景
                self._send(403, body=b"jwt:expired: token is no longer valid")
                return
            is_real_download = bool(
                self.command == "GET"
                and range_header
                and not range_header.replace(" ", "").endswith("-0")
            )
            if is_real_download:
                if not self.state.consume_signed_token(path):
                    self.state.expire_signed_token(path)
                    self._send(403, body=b"jwt:expired: token expired during download")
                    return
                if self.state.signed_remaining.get(path, 0) <= 0:
                    # 本次请求成功后令牌立即失效，模拟短时效签名
                    self.state.expire_signed_token(path)
            mode = "range"

        if mode == "flaky-download":
            # 只对真正的下载请求注入故障：探测用的 bytes=0-0 与 HEAD 正常放行
            actual_download = bool(
                range_header and not range_header.replace(" ", "").endswith("-0")
            )
            if self.command == "GET" and actual_download and self.state.take_failure(path):
                self._send(503, body=b"temporarily unavailable")
                return
            mode = "range"

        if mode == "flaky" or (mode == "flaky-get" and self.command == "GET"):
            if self.state.take_failure(path):
                self._send(503, body=b"temporarily unavailable")
                return
            mode = "range"

        if mode == "unknown":
            # 无 Content-Length：用连接关闭表示结束，且忽略 Range
            self.send_response(200)
            self.send_header("Content-Type", DEFAULT_CONTENT_TYPE)
            self.send_header("Connection", "close")
            self.end_headers()
            if send_body:
                for index in range(0, len(payload), 4096):
                    self.wfile.write(payload[index : index + 4096])
            self.close_connection = True
            return

        common_headers = {
            "Content-Type": DEFAULT_CONTENT_TYPE,
            "Server": "FilePilotTestServer",
        }
        if mode in {"range", "lying", "badrange", "flaky", "flaky-download", "slow"}:
            common_headers["Accept-Ranges"] = "bytes"
        if path in self.state.etags:
            common_headers["ETag"] = self.state.etags[path]
        if path in self.state.modified:
            common_headers["Last-Modified"] = self.state.modified[path]

        if mode == "lying":
            # 声称支持 Range，但忽略 Range 请求返回完整内容
            self._send(200, headers=common_headers, body=payload)
            return

        if mode == "badrange" and range_header:
            start, end = self._parse_range(range_header, len(payload))
            wrong_start = min(len(payload) - 1, start + 8)
            chunk = payload[wrong_start : min(end + 1, len(payload))]
            headers = dict(common_headers)
            headers["Content-Range"] = f"bytes {wrong_start}-{wrong_start + len(chunk) - 1}/{len(payload)}"
            self._send(206, headers=headers, body=chunk)
            return

        if range_header and mode in {"range", "slow"}:
            start, end = self._parse_range(range_header, len(payload))
            if start >= len(payload):
                self._send(
                    416,
                    headers={"Content-Range": f"bytes */{len(payload)}"},
                    body=b"",
                )
                return
            chunk = payload[start : end + 1]
            headers = dict(common_headers)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
            if mode == "slow" and send_body:
                self.send_response(206)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                step = max(1, len(chunk) // 32)
                for index in range(0, len(chunk), step):
                    self.wfile.write(chunk[index : index + step])
                    self.wfile.flush()
                    time.sleep(0.1)
                return
            self._send(206, headers=headers, body=chunk)
            return

        if mode == "slow" and send_body:
            headers = dict(common_headers)
            self.send_response(200)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            step = max(1, len(payload) // 32)
            for index in range(0, len(payload), step):
                self.wfile.write(payload[index : index + step])
                self.wfile.flush()
                time.sleep(0.1)
            return

        self._send(200, headers=common_headers, body=payload)

    @staticmethod
    def _parse_range(value: str, total: int) -> tuple[int, int]:
        """解析 ``bytes=start-end``；``end`` 缺省时为文件末尾。"""
        try:
            spec = value.split("=", 1)[1].strip()
            start_text, _, end_text = spec.partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else total - 1
        except (IndexError, ValueError):
            return 0, total - 1
        return max(0, start), min(end, total - 1)


class TestHTTPServer(ThreadingHTTPServer):
    """线程化测试服务器。"""

    __test__ = False  # 避免 pytest 误将其收集为测试类

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.state = ServerState()

    # -- 便捷注册 ----------------------------------------------------------
    def register(
        self,
        path: str,
        payload: bytes,
        *,
        mode: str = "range",
        etag: str | None = None,
        modified: str | None = None,
        failures: int = 0,
    ) -> str:
        """注册一个路径并返回完整 URL。"""
        self.state.files[path] = payload
        self.state.modes[path] = mode
        if etag:
            self.state.etags[path] = etag
        if modified:
            self.state.modified[path] = modified
        if failures:
            self.state.failures[path] = failures
        return self.url(path)

    def register_redirect(self, path: str, target: str) -> str:
        """注册 302 跳转（目标需已注册）。"""
        self.state.files[path] = b""
        self.state.modes[path] = "redirect"
        self.state.modes[f"{path}:target"] = target
        return self.url(path)

    def register_signed(
        self,
        path: str,
        payload: bytes,
        *,
        token_ttl: int = 2,
        etag: str | None = None,
    ) -> str:
        """注册“临时签名地址”模式，返回**稳定的原始 URL**。

        原始 URL 每次请求都会 302 到一个带 ``token`` 的临时地址；
        令牌的下载额度用尽后返回 ``403 jwt:expired``，
        与 GitHub Release 的 ``618 jwt:expired`` 行为一致。
        """
        self.state.files[path] = payload
        self.state.modes[path] = "signed"
        self.state.signed_generation[path] = 1
        self.state.signed_ttl[path] = max(1, token_ttl)
        self.state.signed_remaining[path] = max(1, token_ttl)
        if etag:
            self.state.etags[path] = etag
        return self.url(path)

    def url(self, path: str) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}{path}"

    def reset_requests(self) -> None:
        with self.state.lock:
            self.state.requests.clear()


@contextmanager
def serve() -> TestHTTPServer:  # type: ignore[valid-type]
    """启动测试服务器（with 语句自动关闭）。"""
    server = TestHTTPServer()
    thread = threading.Thread(target=server.serve_forever, name="test-http", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3.0)
