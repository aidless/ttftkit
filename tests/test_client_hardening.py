"""坏协议 / 超时 / 泄密的回归测试：这些是审阅发现的真实缺陷，每条都先能红。

对应审阅意见：M2（choices 非数组导致整批崩溃）、M3（无整请求总时限）、
M4（错误串回显服务端取值）、M5（非字符串 content 被当成内容块）。
"""
import asyncio

import httpx
import pytest

from ttftkit.client import measure_request
from ttftkit.runner import run_benchmark

PAYLOAD = {"model": "tiny", "messages": [{"role": "user", "content": "hi"}], "stream": True}


def run(base, scenario, **kwargs):
    async def _go():
        async with httpx.AsyncClient(base_url=f"{base}/{scenario}") as client:
            return await measure_request(client, PAYLOAD, index=0, **kwargs)

    return asyncio.run(_go())


def test_choices_as_object_is_failure_not_crash(sse_base):
    # 修复前：dict[0] 抛 KeyError('0') 直接冲出 measure_request；修复后：判失败
    rec = run(sse_base, "dict-choices")
    assert rec.ok is False
    assert "choices" in (rec.error or "").lower()


def test_batch_is_not_aborted_by_malformed_choice(sse_base):
    # 修复前：一个坏事件把整个 gather 打断、run_benchmark 抛异常；修复后：逐条记失败
    result = asyncio.run(run_benchmark(
        endpoint=sse_base + "/dict-choices/v1/chat/completions",
        model="tiny", prompt="hi", requests=2, concurrency=2, max_tokens=8, timeout_s=2))
    assert len(result["records"]) == 2
    assert result["summary"]["successful_requests"] == 0
    assert result["summary"]["error_rate"] == 1.0


def test_total_deadline_stops_slow_trickle(sse_base):
    # 修复前：每 40ms 一块、read timeout 0.1s 永不触发 → 0.8s 仍 ok=True
    rec = run(sse_base, "trickle", timeout_s=0.1)
    assert rec.ok is False
    assert "timeout" in (rec.error or "").lower()
    # 必须在远小于整段流（0.8s）的时间内被判超时
    assert rec.latency_ms < 500


@pytest.mark.parametrize("scenario", ["secret-usage", "secret-finish"])
def test_errors_do_not_echo_server_values(sse_base, scenario):
    rec = run(sse_base, scenario)
    assert rec.ok is False
    assert "SECRET" not in (rec.error or "")


def test_non_string_content_is_failure(sse_base):
    # 修复前：content=42 是 truthy，被当成 1 个 content chunk 且 ok=True
    rec = run(sse_base, "bad-content")
    assert rec.ok is False
    assert "content" in (rec.error or "").lower()


def test_no_newline_trickle_hits_total_deadline(sse_base):
    # 服务端每 50ms 写 1 字节且永不发换行：httpx 的 read 超时每块重置，
    # aiter_lines() 不产出任何行 → 只有 asyncio.wait_for 总预算能拦住（否则 30×50ms 才结束，甚至永不结束）
    import time as _t

    start = _t.perf_counter()
    rec = run(sse_base, "trickle-nonl", timeout_s=0.25)
    elapsed = _t.perf_counter() - start
    assert rec.ok is False
    assert "timeout" in (rec.error or "").lower()
    assert elapsed < 1.0, f"总预算未生效，实际耗时 {elapsed:.2f}s"


def test_protocol_error_does_not_echo_server_bytes(sse_base):
    # 修复前：异常文本 f"{exc}" 会把非法 chunk 头里的服务端字节写进 error，
    # 再经 JSON / Markdown 报告外泄（日志注入 + 凭据回显）；修复后：只保留异常类名
    import json

    from ttftkit import report

    result = asyncio.run(run_benchmark(
        endpoint=sse_base + "/bad-chunk/v1/chat/completions",
        model="tiny", prompt="hi", requests=1, max_tokens=8, timeout_s=5))
    rec = result["records"][0]
    assert rec["ok"] is False
    assert "SECRET" not in (rec["error"] or "")
    assert "http error" in (rec["error"] or "").lower()
    blob = json.dumps(result, ensure_ascii=False) + report.markdown(result)
    assert "SECRET" not in blob
