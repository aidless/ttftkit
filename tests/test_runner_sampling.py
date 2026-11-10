# -*- coding: utf-8 -*-
"""采样参数支持（temperature / top_p / seed）的 RED→GREEN 测试。

设计动机：invariance 实弹判决（2026-11-10）只覆盖贪心解码——runner 硬编码
temperature=0。要回答「采样解码下 batch 是否不变」，测量端必须能把
temperature/top_p/seed 传给服务端；采样参数还必须进报告 config，让 gate 的
可比性检查把「不同采样参数的报告」判为不可比，而不是假比较。
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ttftkit.runner import run_benchmark


class _CaptureHandler(BaseHTTPRequestHandler):
    """回放固定 SSE 流，并记录收到的请求体 JSON。"""

    protocol_version = "HTTP/1.1"
    bodies: list[dict] = []  # 类属性，测试内替换

    def do_POST(self):
        raw_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(raw_len)
        type(self).bodies.append(json.loads(body.decode("utf-8")))
        chunks = [
            b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":7,"completion_tokens":5}}\n\n',
            b"data: [DONE]\n\n",
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture()
def capture_server():
    _CaptureHandler.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _run(base, **kwargs):
    return asyncio.run(run_benchmark(
        endpoint=f"{base}/normal/v1/chat/completions",
        model="tiny", prompt="hi", requests=2, concurrency=1,
        max_tokens=8, timeout_s=5, **kwargs,
    ))


def test默认仍是贪心_temperature0(capture_server):
    _run(capture_server)
    assert _CaptureHandler.bodies, "服务端应至少收到一个请求体"
    assert all(b.get("temperature") == 0 for b in _CaptureHandler.bodies)


def test显式temperature与seed进请求体(capture_server):
    _run(capture_server, temperature=0.7, seed=1234)
    assert _CaptureHandler.bodies, "服务端应至少收到一个请求体"
    assert all(b.get("temperature") == 0.7 for b in _CaptureHandler.bodies)
    assert all(b.get("seed") == 1234 for b in _CaptureHandler.bodies)


def test非法temperature被拒(capture_server):
    with pytest.raises(ValueError):
        _run(capture_server, temperature=-0.1)


def testtemperature上界校验(capture_server):
    with pytest.raises(ValueError):
        _run(capture_server, temperature=2.5)


def testseed非负校验(capture_server):
    with pytest.raises(ValueError):
        _run(capture_server, seed=-1)


def test报告config带采样参数_可比性生效(capture_server):
    report = _run(capture_server, temperature=0.7, seed=99)
    assert report["config"]["temperature"] == 0.7
    assert report["config"]["seed"] == 99
    # 默认路径 config 也要带（字段存在，值=0/None），保证两份报告可比
    report0 = _run(capture_server)
    assert report0["config"]["temperature"] == 0
