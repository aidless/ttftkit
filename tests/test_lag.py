"""客户端事件循环延迟：把「客户端自己排队」这件事变成可读的数。

依据 docs/papers.md A1（arXiv 2605.24217）：单进程 asyncio 客户端在高并发下会把
自身排队时间算进 TTFT/TPOT。我们无法在这里换成多进程，但可以**把上界标出来**。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from ttftkit.lag import LagMonitor, lag_stats


def test_lag_stats_empty_is_none_not_zero():
    assert lag_stats([], interval_s=0.01) is None


def test_lag_stats_reports_percentiles():
    stats = lag_stats([1.0, 2.0, 3.0, 100.0], interval_s=0.01)
    assert stats["samples"] == 4
    assert stats["max_ms"] == 100.0
    assert 2.0 <= stats["p50_ms"] <= 3.0
    assert stats["p95_ms"] > 50.0
    assert stats["interval_s"] == 0.01


def test_monitor_samples_an_idle_loop():
    async def main():
        monitor = LagMonitor(interval_s=0.005)
        await monitor.start()
        await asyncio.sleep(0.06)
        return await monitor.stop()

    stats = asyncio.run(main())
    assert stats is not None
    assert stats["samples"] >= 3
    assert stats["p95_ms"] >= 0.0


def test_monitor_detects_a_blocked_event_loop():
    """同步阻塞事件循环必须被记成大的 lag —— 否则这个监控是装饰。"""

    async def main():
        monitor = LagMonitor(interval_s=0.005)
        await monitor.start()
        await asyncio.sleep(0.02)
        time.sleep(0.08)  # noqa: ASYNC251 - 这里就是要同步阻塞事件循环（被测行为）
        await asyncio.sleep(0.02)
        return await monitor.stop()

    stats = asyncio.run(main())
    assert stats is not None
    assert stats["max_ms"] >= 30.0


def test_run_reports_client_lag_in_the_canonical_shape(sse_base):
    """真实 run 报告里 lag 必须落在 client.lag_ms，并且进 summary。

    这里钉的是一个曾经真实发生的静默缺陷：runner 把 `lag_stats()` 的平铺结果直接塞进
    `report["client"]`，而 metrics/gate/report 都按 `client["lag_ms"]["p95_ms"]` 读——
    结果是 `summary.client_lag_p95_ms` 恒为 null、报告永远写「未取到 lag 样本」，
    整套客户端污染观测在真实运行里是死的，但单测因为「null 分支」而全绿。
    """
    from ttftkit.runner import run_benchmark

    result = asyncio.run(run_benchmark(
        endpoint=f"{sse_base}/normal/v1/chat/completions",
        prompt="count to three", model="tiny", requests=6, concurrency=3,
        max_tokens=4, warmup=0,
        timeout_s=10.0, profile="test",
    ))
    client = result["client"]
    assert isinstance(client, dict), "run 报告必须有 client 块"
    assert isinstance(client.get("lag_ms"), dict), "lag 统计必须在 client.lag_ms 下，不许平铺"
    lag = client["lag_ms"]
    assert lag["samples"] > 0, "测量窗口足够长时必须采到样本"
    assert result["summary"]["client_lag_p95_ms"] == lag["p95_ms"]


def test_stop_before_start_is_a_programming_error():
    async def main():
        monitor = LagMonitor()
        with pytest.raises(ValueError):
            await monitor.stop()

    asyncio.run(main())


def test_second_stop_returns_none():
    async def main():
        monitor = LagMonitor(interval_s=0.002)
        await monitor.start()
        await asyncio.sleep(0.01)
        assert await monitor.stop() is not None
        assert await monitor.stop() is None

    asyncio.run(main())


def test_interval_must_be_positive_finite():
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            LagMonitor(interval_s=bad)
