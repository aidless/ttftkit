"""固定 worker 的闭环负载；warmup 在测量窗口之外。"""
import asyncio
import hashlib
import math
import time
from dataclasses import asdict
from urllib.parse import urlsplit

import httpx

from .client import measure_request
from .metrics import summarize
from .models import RequestRecord


class BenchmarkFailed(Exception):
    """测量过程中基础设施失败（warmup 请求失败等）→ CLI 退出码 1，而非「用法错误 2」。"""


async def run_benchmark(*, endpoint, model, prompt, requests=8, concurrency=1,
                        max_tokens=32, warmup=0, timeout_s=60, profile='unspecified', api_key=None,
                        slo_ttft_ms=None, slo_tpot_ms=None):
    for key, value in [('requests', requests), ('concurrency', concurrency), ('max_tokens', max_tokens)]:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f'{key} must be a positive integer')
    if not isinstance(warmup, int) or warmup < 0:
        raise ValueError('warmup must be a nonnegative integer')
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('timeout must be finite and positive')
    url = urlsplit(endpoint)
    bad_endpoint = (url.scheme not in ('http', 'https') or not url.hostname
                    or url.username or url.password or url.query or url.fragment)
    if bad_endpoint:
        raise ValueError('endpoint must be http(s), without userinfo, query or fragment')
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
               'max_tokens': max_tokens, 'temperature': 0, 'stream': True,
               'stream_options': {'include_usage': True}}
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    async with httpx.AsyncClient(headers=headers, limits=limits, trust_env=False) as client:
        for i in range(warmup):
            rec = await measure_request(client, payload, index=i, path=endpoint, timeout_s=timeout_s)
            if not rec.ok:
                # 基础设施失败（如服务端 500）属于「本轮不通过」，不是用法错误
                raise BenchmarkFailed(f'warmup failed: {rec.error}')
        workers = min(concurrency, requests)
        queue: asyncio.Queue = asyncio.Queue()
        for i in range(requests):
            queue.put_nowait(i)
        for _ in range(workers):
            queue.put_nowait(None)  # 每个 worker 收到一个 sentinel 后退出
        records = []

        async def worker():
            while True:
                index = await queue.get()
                if index is None:
                    return
                try:
                    rec = await measure_request(client, payload, index=index,
                                                path=endpoint, timeout_s=timeout_s)
                except Exception as exc:  # 兜底：单请求的意外异常绝不能中断整批
                    rec = RequestRecord(index=index, ok=False, latency_ms=0.0,
                                        error=f'internal error: {type(exc).__name__}')
                records.append(rec)

        start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(workers)))
        duration = time.perf_counter() - start
    records.sort(key=lambda r: r.started_at)
    config = {'protocol': 'openai-sse-v1', 'model': model, 'concurrency': concurrency,
              'max_tokens': max_tokens, 'requests': requests, 'warmup': warmup,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'profile': profile, 'temperature': 0, 'timeout_s': timeout_s}
    return {'schema_version': 1, 'config': config, 'duration_s': duration,
            'records': [asdict(r) for r in records],
            'summary': summarize(records, duration, slo_ttft_ms=slo_ttft_ms, slo_tpot_ms=slo_tpot_ms)}
