"""真实本地 SSE 服务：验证客户端的计时与错误分类，不用 mock 顶替协议。"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# 每个场景是 [(延迟秒, 原始字节), ...]；None 表示 HTTP 500
SCENARIOS: dict[str, list[tuple[float, bytes]] | None] = {
    "normal": [
        (0.06, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
        (0.02, b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'),
        (0.02, b'data: {"choices":[{"delta":{"content":"c"}}]}\n\n'),
        (0.00, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
               b'"usage":{"prompt_tokens":7,"completion_tokens":5}}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    "no-usage": [
        (0.02, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
        (0.02, b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'),
        (0.00, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    "bad-json": [
        (0.02, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
        (0.02, b"data: {oops\n\n"),
    ],
    "cut": [
        (0.02, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
    ],
    "empty": [
        (0.02, b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    # choices 被发成对象而不是数组：坏协议不应让整批崩溃
    "dict-choices": [
        (0.01, b'data: {"choices":{"0":{"delta":{"content":"a"}}}}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    # 慢速滴流：每块间隔都小于 read timeout，只有「整请求总时限」能拦住
    "trickle": [(0.04, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n')] * 20
    + [(0.00, b"data: [DONE]\n\n")],
    # 无换行慢速滴流：aiter_lines() 永不产出整行，只有外层总预算能拦住挂起
    "trickle-nonl": [(0.05, b"x")] * 30,
    # 非字符串 content（多模态数组/数字）不是文本内容块
    "bad-content": [
        (0.01, b'data: {"choices":[{"delta":{"content":42}}]}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    # 服务端把敏感取值塞进 usage / finish_reason，客户端不得回显进报告
    "secret-usage": [
        (0.01, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
        (0.00, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
               b'"usage":{"completion_tokens":"PROMPT_SECRET_987"}}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    "secret-finish": [
        (0.01, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
        (0.00, b'data: {"choices":[{"delta":{},"finish_reason":"INTERNAL_SECRET_987"}]}\n\n'),
        (0.00, b"data: [DONE]\n\n"),
    ],
    # 非法 chunk 头：httpx 抛 RemoteProtocolError，其文本会夹带服务端字节
    "bad-chunk": [
        (0.01, b"ZZZ_SECRET_TOKEN\r\n"),
    ],
    "http500": None,
}

# congest：服务端延迟随在飞请求数线性增长（用于验证 SLO 扫描真的会在高并发下被打破）
_CONGEST_SCRIPT: list[tuple[float, bytes]] = [
    (0.06, b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'),
    (0.02, b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n'),
    (0.00, b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
           b'"usage":{"prompt_tokens":7,"completion_tokens":5}}\n\n'),
    (0.00, b"data: [DONE]\n\n"),
]
_INFLIGHT = 0
_INFLIGHT_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        name = self.path.strip("/").split("/")[0]
        inflight = 1
        if name == "congest":
            global _INFLIGHT
            with _INFLIGHT_LOCK:
                _INFLIGHT += 1
                inflight = _INFLIGHT
            script = _CONGEST_SCRIPT
        else:
            script = SCENARIOS[name]
        if script is None:
            body = b'{"error":"boom"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        raw = name == "bad-chunk"  # 该场景故意写非法分块，用于验证协议错误不回显字节
        try:
            if name == "congest":
                # 并发越高、排队越久：每个在飞请求先等 inflight*30ms
                time.sleep(0.03 * inflight)
            for delay, chunk in script:
                time.sleep(delay)
                if raw:
                    self.wfile.write(chunk)
                else:
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            if not raw:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if name == "congest":
                with _INFLIGHT_LOCK:
                    _INFLIGHT -= 1

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture(scope="module")
def sse_base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 需要真实等待的计时测试")
