"""OpenAI 兼容端点的流式测量客户端。

只负责「一次请求怎么测」，并发编排在 runner.py。
所有时间用 time.perf_counter()（单调时钟），不用 time.time()（会被 NTP 调整）。

对服务端协议的容忍度：只接受 `data: {...}` 的 SSE 事件与 `[DONE]`。
任何形状不对的事件（不是对象、usage 不是非负整数、finish_reason 不是正常结束）
都判为**失败请求**并记录原因——压测工具悄悄跳过坏数据，等于把故障算成性能。
"""
from __future__ import annotations

import json
import time

import httpx

from .models import RequestRecord

DEFAULT_PATH = "/v1/chat/completions"
SUCCESS_FINISH_REASONS = frozenset({"stop", "length"})


def _fail(index: int, t0: float, error: str, latency_ms: float | None = None) -> RequestRecord:
    latency = latency_ms if latency_ms is not None else (time.perf_counter() - t0) * 1000
    return RequestRecord(index=index, ok=False, latency_ms=latency, error=error, started_at=t0)


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
) -> RequestRecord:
    """发一次流式请求并记录计时。

    失败一律返回 ok=False + error 字符串，不抛异常（编排层不该被单点失败打断）。
    """
    t0 = time.perf_counter()
    # 整请求总时限：httpx 的 timeout 是「单次读超时」，每收到一块就重置，
    # 慢速滴流可以永远不过期。这里用绝对 deadline 兜住整个流的读取。
    deadline = t0 + timeout_s
    first_t: float | None = None
    last_t: float | None = None
    content_chunks = 0
    intervals: list[float] = []
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
                if time.perf_counter() > deadline:
                    return _fail(index, t0, "timeout")
                if not line.startswith("data:"):
                    continue  # 空行、注释行（`: keep-alive`）跳过
                data = line[5:].strip()
                if data == "[DONE]":
                    done_seen = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    return _fail(index, t0, f"invalid json: {exc}")

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
        return _fail(index, t0, f"http error: {type(exc).__name__}: {exc}")

    latency_ms = (time.perf_counter() - t0) * 1000
    if content_chunks == 0:
        return _fail(index, t0, "empty content", latency_ms)
    if finish_reason is not None and finish_reason not in SUCCESS_FINISH_REASONS:
        # 同理不内插服务端取值，只报「非正常结束」这一事实
        return _fail(index, t0, "unsuccessful finish_reason", latency_ms)
    if finish_reason is None and not done_seen:
        return _fail(index, t0, "stream ended without finish_reason", latency_ms)

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
    )
