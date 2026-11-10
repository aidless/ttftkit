"""数据契约：一次请求的原始测量记录。

这里只有数据和派生属性，不做任何 IO，也不做汇总统计（那在 metrics.py）。
所有时间单位都是毫秒，除 `started_at` 用 perf_counter 秒。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """一次流式请求的测量结果。

    字段口径（不要混用）：
    - latency_ms: 发请求到流结束的墙钟时间
    - ttft_ms: 发请求到**首个非空 content 事件**的时间（含连接与排队）
    - decode_ms: 首个内容事件到最后一个内容事件的时间差
    - content_chunks: 收到多少个带内容的 SSE 事件（**不是 token 数**）
    - output_tokens: 服务端 usage 报告的 completion_tokens；缺失时 None（不是 0）
    """

    index: int
    ok: bool
    latency_ms: float
    ttft_ms: float | None = None
    decode_ms: float | None = None
    content_chunks: int = 0
    output_tokens: int | None = None
    prompt_tokens: int | None = None
    chunk_intervals_ms: tuple[float, ...] = ()
    error: str | None = None
    started_at: float = 0.0
    # 捕获的输出文本（capture_text=True 时才有）：invariance 闸的证据面。
    # None = 没开捕获（没测）；"" = 开了捕获但没收到内容。两者语义不同。
    output_text: str | None = None

    @property
    def tpot_ms(self) -> float | None:
        """平均每 token 解码时间 = decode_ms / (completion_tokens - 1)。

        少于 2 个 token、缺 usage、或只有一个内容事件时无定义 → None。
        """
        if not self.ok or self.output_tokens is None or self.output_tokens < 2:
            return None
        if self.decode_ms is None or self.content_chunks < 2:
            return None
        return self.decode_ms / (self.output_tokens - 1)
