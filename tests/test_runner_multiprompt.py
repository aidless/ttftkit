# -*- coding: utf-8 -*-
"""多 prompt 独立样本口径（prompts 参数 + 每请求独立 seed）的 RED→GREEN 测试。

实测依据（verdict-matrix.md 补充发现 2）：单 prompt × 单 seed 的 8 个请求是
同一 query 的 8 份拷贝——divergence_rate 的分母是槽位数不是独立 query。
stability 的 query 级口径要求每请求换 prompt/seed。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ttftkit.runner import run_benchmark


class _CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bodies: list[dict] = []

    def do_POST(self):
        raw_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(raw_len)
        type(self).bodies.append(json.loads(body.decode("utf-8")))
        chunks = [
            b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n',
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

    def log_message(self, *args):
        pass


@pytest.fixture()
def capture_server():
    _CaptureHandler.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _run(base, **kwargs):
    return asyncio.run(run_benchmark(
        endpoint=f"{base}/normal/v1/chat/completions",
        model="tiny", prompt="hi", requests=4, concurrency=2,
        max_tokens=8, timeout_s=5, **kwargs))


def testprompts数量与requests不符_拒绝(capture_server):
    with pytest.raises(ValueError):
        _run(capture_server, prompts=["a", "b"])   # requests=4


def test每请求用自己的prompt与seed加index(capture_server):
    prompts = ["问一", "问二", "问三", "问四"]
    _run(capture_server, prompts=prompts, seed=1000)
    bodies = _CaptureHandler.bodies
    assert len(bodies) == 4
    sent_prompts = sorted(b["messages"][0]["content"] for b in bodies)
    assert sent_prompts == sorted(prompts)
    sent_seeds = sorted(b["seed"] for b in bodies)
    assert sent_seeds == [1000, 1001, 1002, 1003]


def testconfig的prompt_sha256是逐请求哈希串联(capture_server):
    prompts = ["问一", "问二", "问三", "问四"]
    rep = _run(capture_server, prompts=prompts, seed=1000)
    expect = hashlib.sha256(
        "".join(hashlib.sha256(p.encode()).hexdigest() for p in prompts).encode()
    ).hexdigest()
    assert rep["config"]["prompt_sha256"] == expect


def test缺省单prompt_seed不变_历史行为(capture_server):
    _run(capture_server, seed=1234)
    assert all(b["seed"] == 1234 for b in _CaptureHandler.bodies)
    assert all(b["messages"][0]["content"] == "hi" for b in _CaptureHandler.bodies)
