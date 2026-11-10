"""SSE 客户端：真实 HTTP 服务下的计时、token 计数与错误分类。"""
import asyncio

import httpx
import pytest

from ttftkit.client import measure_request

PAYLOAD = {"model": "tiny", "messages": [{"role": "user", "content": "hi"}], "stream": True}


def run(base, scenario, **kwargs):
    async def _go():
        async with httpx.AsyncClient(base_url=f"{base}/{scenario}") as client:
            return await measure_request(client, PAYLOAD, index=0, **kwargs)

    return asyncio.run(_go())


def test_ttft_and_decode_time_measured_from_real_stream(sse_base):
    rec = run(sse_base, "normal")
    assert rec.ok is True
    # 服务端首块延迟 60ms、之后每 20ms 一块；给宽带宽容忍，但必须落在合理量级
    assert 40 <= rec.ttft_ms <= 400
    assert rec.content_chunks == 3
    assert rec.output_tokens == 5
    assert rec.prompt_tokens == 7
    assert 20 <= rec.decode_ms <= 400
    # TPOT 用服务端 token 数：decode / (5-1)，不是 decode / chunk 数
    assert rec.tpot_ms == pytest.approx(rec.decode_ms / 4)
    assert rec.latency_ms >= rec.ttft_ms + rec.decode_ms


def test_chunk_intervals_are_recorded_but_not_called_itl(sse_base):
    rec = run(sse_base, "normal")
    assert len(rec.chunk_intervals_ms) == 2
    assert all(0 <= gap <= 400 for gap in rec.chunk_intervals_ms)


def test_missing_usage_leaves_token_count_unknown(sse_base):
    rec = run(sse_base, "no-usage")
    assert rec.ok is True
    assert rec.output_tokens is None
    assert rec.tpot_ms is None


def test_invalid_json_is_a_failure_not_a_silent_skip(sse_base):
    rec = run(sse_base, "bad-json")
    assert rec.ok is False
    assert "invalid json" in (rec.error or "").lower()


def test_stream_without_done_or_finish_reason_is_a_failure(sse_base):
    rec = run(sse_base, "cut")
    assert rec.ok is False
    assert "stream ended" in (rec.error or "").lower()


def test_http_error_is_classified(sse_base):
    rec = run(sse_base, "http500")
    assert rec.ok is False
    assert "500" in (rec.error or "")


def test_empty_content_is_a_failure(sse_base):
    rec = run(sse_base, "empty")
    assert rec.ok is False
    assert "empty content" in (rec.error or "").lower()


def test_timeout_is_classified(sse_base):
    rec = run(sse_base, "normal", timeout_s=0.01)
    assert rec.ok is False
    assert "timeout" in (rec.error or "").lower()


class Test文本捕获:
    """capture_text：invariance 闸的证据面。默认关（性能测量不存文本）。"""


def _run_capture(sse_base, scenario="normal", **kwargs):
    async def _go():
        async with httpx.AsyncClient(base_url=f"{sse_base}/{scenario}") as client:
            return await measure_request(client, PAYLOAD, index=0, **kwargs)
    return asyncio.run(_go())


def test默认不捕获_文本为None(sse_base):
    rec = _run_capture(sse_base)
    assert rec.ok is True
    assert rec.output_text is None  # 不捕获不是空串：None=没测，""=测到空输出


def test开启捕获_拼接全部content片段(sse_base):
    rec = _run_capture(sse_base, capture_text=True)
    assert rec.ok is True
    assert rec.output_text == "abc"  # 三个片段 'a'+'b'+'c' 逐字节拼接


def test开启捕获但无内容_空串而非None(sse_base):
    # 'empty' 场景（无任何内容块）：ok=False + error "empty content"。
    # 捕获开着 → output_text 必须是 ""（测了、没内容），绝不是 None（没测）。
    # 这是 invariance 闸「缺证据 ≠ 通过」语义在客户端侧的镜像。
    rec = _run_capture(sse_base, "empty", capture_text=True)
    assert rec.ok is False
    assert "empty content" in (rec.error or "")
    assert rec.output_text == ""
