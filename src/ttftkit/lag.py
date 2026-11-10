"""客户端事件循环延迟（event-loop lag）采样。

为什么要测：ttftkit 是单进程 asyncio 客户端。高并发时，事件循环自己被回调/解析/
DNS/TLS 占满，请求从「服务端已返回」到「客户端处理到」之间的等待会被算进 TTFT 与
TPOT。见 docs/papers.md A1（arXiv 2605.24217）。

怎么测：每秒若干次 `sleep(interval)`，醒来后看**超时了多少**。空闲循环接近 0；
同步阻塞 80ms 就会记下 ~80ms。这不是服务端指标，是**测量可信度的元指标**：
lag 接近或超过 TTFT 时，这一轮的延迟数字主要是客户端在排队。
"""
from __future__ import annotations

import asyncio
import math

from .metrics import percentile

DEFAULT_INTERVAL_S = 0.01
DEFAULT_MAX_SAMPLES = 5000


def lag_stats(samples: list[float], *, interval_s: float) -> dict | None:
    """把 lag 样本汇总成报告块；没有样本返回 None（不是 0）。"""
    values = [float(v) for v in samples]
    if not values:
        return None
    return {
        "samples": len(values),
        "interval_s": interval_s,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values),
    }


class LagMonitor:
    """在测量窗口内持续采样事件循环延迟。

    用法：`await start()` → 跑负载 → `await stop()`。
    `stop()` 之后必须能看到样本，所以第一次 `stop()` 应返回 dict；重复 stop 返回 None。
    """

    def __init__(self, *, interval_s: float = DEFAULT_INTERVAL_S,
                 max_samples: int = DEFAULT_MAX_SAMPLES) -> None:
        if not isinstance(interval_s, (int, float)) or isinstance(interval_s, bool):
            raise ValueError(f"interval_s must be a number, got {interval_s!r}")
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError(f"interval_s must be finite and positive, got {interval_s!r}")
        if not isinstance(max_samples, int) or isinstance(max_samples, bool) or max_samples < 2:
            raise ValueError(f"max_samples must be an integer >= 2, got {max_samples!r}")
        self.interval_s = float(interval_s)
        self.max_samples = max_samples
        self._samples: list[float] = []
        self._task: asyncio.Task | None = None
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        if self._started and not self._stopped:
            raise ValueError("LagMonitor is already running")
        self._started = True
        self._stopped = False
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stopped and len(self._samples) < self.max_samples:
            t0 = loop.time()
            await asyncio.sleep(self.interval_s)
            lag_ms = (loop.time() - t0 - self.interval_s) * 1000.0
            # 负值来自时钟精度，夹到 0 而不是把噪声当结论
            self._samples.append(max(0.0, lag_ms))

    async def stop(self) -> dict | None:
        if not self._started:
            raise ValueError("LagMonitor was not started")
        if self._stopped:
            return None
        self._stopped = True
        # 让 _loop 在下一次 sleep 返回后退出；取消更干脆，但要保留已有样本
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        return lag_stats(self._samples, interval_s=self.interval_s)
