"""OpenAI 兼容端点的流式测量客户端。

只负责「一次请求怎么测」，并发编排在 runner.py。
所有时间用 time.perf_counter()（单调时钟），不用 time.time()（会被 NTP 调整）。

对服务端协议的容忍度：只接受 `data: {...}` 的 SSE 事件与 `[DONE]`。
任何形状不对的事件（不是对象、usage 不是非负整数、finish_reason 不是正常结束）
都判为**失败请求**并记录原因——压测工具悄悄跳过坏数据，等于把故障算成性能。
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from .models import RequestRecord

DEFAULT_PATH = "/v1/chat/completions"
SUCCESS_FINISH_REASONS = frozenset({"stop", "length"})


def _fail(index: int, t0: float, error: str, latency_ms: float | None = None,
          output_text: str | None = None) -> RequestRecord:
    latency = latency_ms if latency_ms is not None else (time.perf_counter() - t0) * 1000
    return RequestRecord(index=index, ok=False, latency_ms=latency, error=error,
                         started_at=t0, output_text=output_text)


def _count(value, what: str) -> int:
    """usage 里的计数必须是非负整数（bool 不算）。

    错误串只带字段名，绝不内插服务端回传的取值——那些值可能回显 prompt 或凭据。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid usage field: {what}")
    return value


async def measure_request(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    index: int = 0,
    timeout_s: float = 60.0,
    path: str = DEFAULT_PATH,
    capture_text: bool = False,
) -> RequestRecord:
    """发一次流式请求并记录计时。

    失败一律返回 ok=False + error 字符串，不抛异常（编排层不该被单点失败打断）。

    整请求总时限用 `asyncio.wait_for` 兜底：httpx 的 timeout 是「单次读超时」，
    每收到一块就重置，服务端若慢速滴流且**永不发换行**，`aiter_lines()` 不会产出任何行，
    循环里的 deadline 检查永远执行不到——只有外层的总预算能拦住这种挂起。
    """
    t0 = time.perf_counter()
    try:
        return await asyncio.wait_for(
            _measure_once(client, payload, index=index, t0=t0, timeout_s=timeout_s,
                          path=path, capture_text=capture_text),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        return _fail(index, t0, "timeout")


async def _measure_once(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    index: int,
    t0: float,
    timeout_s: float,
    path: str,
    capture_text: bool = False,
) -> RequestRecord:
    first_t: float | None = None
    last_t: float | None = None
    content_chunks = 0
    intervals: list[float] = []
    # 捕获缓冲：只有 capture_text=True 才收文本；性能测量默认不存文本
    # （报告体积与敏感面都小一个量级），invariance 闸开捕获时才付这个成本。
    text_parts: list[str] | None = [] if capture_text else None
    output_tokens: int | None = None
    prompt_tokens: int | None = None
    finish_reason: str | None = None
    done_seen = False

    try:
        async with client.stream(
            "POST", path, json=payload, timeout=httpx.Timeout(timeout_s)
        ) as response:
            if response.status_code >= 400:
                # 只记状态码：响应体可能回显 prompt 或凭据，不进报告
                return _fail(index, t0, f"HTTP {response.status_code}")

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue  # 空行、注释行（`: keep-alive`）跳过
                data = line[5:].strip()
                if data == "[DONE]":
                    done_seen = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    # 只报位置，不回显原始文本（可能含服务端控制字符/敏感串）
                    return _fail(index, t0, f"invalid json at line {exc.lineno} column {exc.colno}")

                try:
                    if not isinstance(event, dict):
                        return _fail(index, t0, "invalid event: not an object")
                    now = time.perf_counter()
                    usage = event.get("usage") or {}
                    if usage:
                        if usage.get("completion_tokens") is not None:
                            output_tokens = _count(usage["completion_tokens"], "completion_tokens")
                        if usage.get("prompt_tokens") is not None:
                            prompt_tokens = _count(usage["prompt_tokens"], "prompt_tokens")

                    choices = event.get("choices")
                    if choices is None:
                        choices = []
                    if not isinstance(choices, list):
                        return _fail(index, t0, "invalid event: choices is not a list")
                    if not choices:
                        continue  # 只带 usage 的收尾块
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        return _fail(index, t0, "invalid event: choice is not an object")
                    delta = choice.get("delta")
                    if delta is not None and not isinstance(delta, dict):
                        return _fail(index, t0, "invalid event: delta is not an object")
                    finish = choice.get("finish_reason")
                    if finish:
                        finish_reason = str(finish)
                    content = (delta or {}).get("content")
                    if content is not None and not isinstance(content, str):
                        # 多模态数组、数字等都不是「文本内容块」，判失败而不是当一个 chunk
                        return _fail(index, t0, "invalid event: content is not a string")
                    if content:
                        content_chunks += 1
                        if text_parts is not None:
                            text_parts.append(content)
                        if first_t is None:
                            first_t = now
                        else:
                            intervals.append((now - last_t) * 1000)
                        last_t = now
                except (AttributeError, TypeError, KeyError, IndexError) as exc:
                    return _fail(index, t0, f"invalid event: {type(exc).__name__}")
                except ValueError as exc:
                    return _fail(index, t0, str(exc))
    except httpx.TimeoutException:
        return _fail(index, t0, "timeout")
    except httpx.HTTPError as exc:
        # 协议层异常文本里可能夹带服务端字节（如非法的 chunk 头），只保留异常类名
        return _fail(index, t0, f"http error: {type(exc).__name__}")

    latency_ms = (time.perf_counter() - t0) * 1000
    captured = "".join(text_parts) if text_parts is not None else None
    if content_chunks == 0:
        return _fail(index, t0, "empty content", latency_ms, output_text=captured)
    if finish_reason is not None and finish_reason not in SUCCESS_FINISH_REASONS:
        # 同理不内插服务端取值，只报「非正常结束」这一事实
        return _fail(index, t0, "unsuccessful finish_reason", latency_ms, output_text=captured)
    if finish_reason is None and not done_seen:
        return _fail(index, t0, "stream ended without finish_reason", latency_ms, output_text=captured)

    assert first_t is not None and last_t is not None  # content_chunks > 0 保证
    return RequestRecord(
        index=index,
        ok=True,
        latency_ms=latency_ms,
        ttft_ms=(first_t - t0) * 1000,
        decode_ms=(last_t - first_t) * 1000,
        content_chunks=content_chunks,
        output_tokens=output_tokens,
        prompt_tokens=prompt_tokens,
        chunk_intervals_ms=tuple(intervals),
        started_at=t0,
        output_text=captured,
    )
