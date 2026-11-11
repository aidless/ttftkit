# -*- coding: utf-8 -*-
"""gate 对 p99_basis=max 的上限规则给出见证检查（不改判决）的 RED→GREEN 测试。

n<20 时 p99 是 max（verdict-matrix.md 补充发现 3）。上限规则引用 *.p99 且
样本不足时，gate 不改 PASS/FAIL，但必须在 checks 里留一条 basis 见证，
让「用 8 个样本的 p99 当分位数做判决」在判决书里无所遁形。
"""
from __future__ import annotations

from ttftkit.gate import evaluate


def _report(count, *, p99=100.0):
    n = count
    return {
        "schema_version": 1, "kind": "run",
        "config": {
            "protocol": "openai-sse-v1", "model": "m", "concurrency": 1,
            "max_tokens": 32, "requests": n, "warmup": 0,
            "prompt_sha256": "ab" * 32, "profile": "p", "temperature": 0,
            "timeout_s": 60.0, "top_p": None, "seed": None,
            "hardware": None, "client_timing": "asyncio-event-loop",
        },
        "summary": {
            "requests": n, "successful_requests": n, "error_rate": 0.0,
            "ttft_ms": {"count": n, "p50": 50.0, "p99": p99, "p99_basis": "max" if n < 20 else "quantile"},
            "tpot_ms": {"count": n, "p50": 10.0, "p99": p99, "p99_basis": "max" if n < 20 else "quantile"},
        },
    }


def test样本不足_上限规则带basis见证():
    v = evaluate(_report(8, p99=150.0),
                 {"limits": {"ttft_ms.p99": 200.0}}, baseline=None)
    assert v["exit_code"] == 0
    basis = [c for c in v["checks"] if c.get("kind") == "basis"]
    assert len(basis) == 1
    assert basis[0]["status"] == "PASS"
    assert "max" in basis[0]["observed"]


def test样本充足_无basis见证():
    v = evaluate(_report(25, p99=150.0),
                 {"limits": {"ttft_ms.p99": 200.0}}, baseline=None)
    assert v["exit_code"] == 0
    assert [c for c in v["checks"] if c.get("kind") == "basis"] == []


def test不涉及p99的规则_无basis见证():
    v = evaluate(_report(8, p99=150.0),
                 {"limits": {"output_tokens_per_s": 1.0}}, baseline=None)
    assert [c for c in v["checks"] if c.get("kind") == "basis"] == []
