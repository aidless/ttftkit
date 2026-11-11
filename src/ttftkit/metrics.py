"""汇总统计：纯函数，输入请求记录，输出可 JSON 序列化的字典。

设计约束：
- 分位数用线性插值（与 numpy 默认 method="linear" 一致），便于和别人的报告对齐。
- 缺数据返回 None，绝不返回 0 冒充。
- 吞吐的分母是**测量墙钟时长**，不是各请求延迟之和。
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence

from .models import RequestRecord

MIN_CI_SAMPLES = 20
"""少于这么多条记录时，p95 的置信区间没有意义（8 条记录的 p95 实际上就是最大值）。"""

CI_ITERATIONS = 1000
CI_SEED = 0
CI_LEVEL = 0.95


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数。q 取 0~1。"""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(ordered[low])
    weight = pos - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * weight)


def _quantile_sorted(ordered: Sequence[float], q: float) -> float:
    """对已排序序列取线性插值分位数（避免 bootstrap 里每次重复排序）。"""
    if not ordered:
        raise ValueError("quantile of empty sequence")
    if len(ordered) == 1:
        return float(ordered[0])
    pos = q * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(ordered[low])
    weight = pos - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * weight)


def bootstrap_ci(data: Sequence[float], *, iterations: int = CI_ITERATIONS,
                 seed: int = CI_SEED, level: float = CI_LEVEL) -> dict:
    """分位数的百分位 bootstrap 置信区间。

    纪律（docs/papers.md B2）：种子固定、迭代数写进结果，否则 CI 判定不可复现。
    """
    if not data:
        raise ValueError("bootstrap of empty sequence")
    values = [float(v) for v in data]
    rng = random.Random(seed)
    size = len(values)
    p50s: list[float] = []
    p95s: list[float] = []
    for _ in range(iterations):
        sample = sorted(values[rng.randrange(size)] for _ in range(size))
        p50s.append(_quantile_sorted(sample, 0.50))
        p95s.append(_quantile_sorted(sample, 0.95))
    tail = (1.0 - level) / 2.0
    p50s.sort()
    p95s.sort()
    return {
        "level": level,
        "iterations": iterations,
        "seed": seed,
        "p50": [_quantile_sorted(p50s, tail), _quantile_sorted(p50s, 1.0 - tail)],
        "p95": [_quantile_sorted(p95s, tail), _quantile_sorted(p95s, 1.0 - tail)],
    }


def distribution(values: Iterable[float]) -> dict | None:
    """一列数值的分布。空输入 → None（无证据，不是 0）。"""
    data = [float(v) for v in values if v is not None]
    if not data:
        return None
    mean = sum(data) / len(data)
    if len(data) > 1:
        variance = sum((v - mean) ** 2 for v in data) / (len(data) - 1)  # 样本方差
        stdev = math.sqrt(variance)
    else:
        stdev = 0.0
    return {
        "count": len(data),
        "mean": mean,
        "min": min(data),
        "max": max(data),
        "p50": percentile(data, 0.50),
        "p90": percentile(data, 0.90),
        "p95": percentile(data, 0.95),
        "p99": percentile(data, 0.99),
        # n<20 时 p99 由末位 1–2 个样本主导（0.99 分位秩落在最大两个样本之间），
        # 不是可分位估计，数值也未必等于 max（runD 实测 123.7 vs 123.9）。如实标注，
        # 防止把 max 主导的尾部读数当分位数引用（verdict-matrix 补充发现 3）。
        "p99_basis": "max" if len(data) < 20 else "quantile",
        "stdev": stdev,
        "cv": (stdev / mean) if mean else None,
        # 样本太少时不给区间：宁可说「不知道」，也不要给一个假精确的 ±
        "ci": (bootstrap_ci(data, iterations=CI_ITERATIONS if len(data) <= 500 else 200)
               if len(data) >= MIN_CI_SAMPLES else None),
    }


def _degradation_ratio(records: list[RequestRecord]) -> float | None:
    """后 1/4 请求的平均延迟 ÷ 前 1/4，用于发现「跑久了变慢」。"""
    ordered = [r for r in records if r.ok]
    if len(ordered) < 4:
        return None
    quarter = max(1, len(ordered) // 4)
    head = ordered[:quarter]
    tail = ordered[-quarter:]
    head_mean = sum(r.latency_ms for r in head) / len(head)
    tail_mean = sum(r.latency_ms for r in tail) / len(tail)
    if head_mean <= 0:
        return None
    return tail_mean / head_mean


def summarize(
    records: Sequence[RequestRecord],
    seconds: float,
    *,
    slo_ttft_ms: float | None = None,
    slo_tpot_ms: float | None = None,
    hardware: dict | None = None,
    client: dict | None = None,
) -> dict:
    """把一组请求记录汇总成报告里的 `summary` 段。

    seconds: 测量窗口的墙钟时长（秒），必须有限且 > 0。
    hardware: 采集到的硬件块（v0.2+）；未采样时传 None，对应指标一律为 null 而非 0。
    client: 客户端事件循环 lag 块（v0.3+）；同样 null 而非 0。
    """
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        raise ValueError(f"measurement window must be a number, got {seconds!r}")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"measurement window must be finite and > 0, got {seconds!r}")

    total = len(records)
    ok_records = [r for r in records if r.ok]
    with_tokens = [r for r in ok_records if r.output_tokens is not None]

    output_tokens_per_s: float | None
    if not ok_records:
        output_tokens_per_s = None
    elif len(with_tokens) != len(ok_records):
        output_tokens_per_s = None  # 部分成功请求缺计数 → 总体吞吐不可信
    else:
        output_tokens_per_s = sum(r.output_tokens for r in with_tokens) / seconds

    goodput = None
    if slo_ttft_ms is not None or slo_tpot_ms is not None:
        goodput = sum(1 for r in ok_records if _meets(r, slo_ttft_ms, slo_tpot_ms)) / seconds

    return {
        "requests": total,
        "successful_requests": len(ok_records),
        "error_rate": ((total - len(ok_records)) / total) if total else 0.0,
        "requests_per_s": len(ok_records) / seconds,
        "output_tokens_per_s": output_tokens_per_s,
        "token_usage_coverage": (len(with_tokens) / len(ok_records)) if ok_records else 0.0,
        "goodput_per_s": goodput,
        "ttft_ms": distribution(r.ttft_ms for r in ok_records),
        "tpot_ms": distribution(r.tpot_ms for r in ok_records),
        "latency_ms": distribution(r.latency_ms for r in ok_records),
        "decode_ms": distribution(r.decode_ms for r in ok_records),
        "chunk_gap_ms": distribution(gap for r in ok_records for gap in r.chunk_intervals_ms),
        "degradation_ratio": _degradation_ratio(ok_records),
        "mem_used_peak_mb": _hw_metric(hardware, "mem_used_peak_mb"),
        "mem_used_delta_mb": _hw_metric(hardware, "mem_used_delta_mb"),
        "gpu_util_peak_pct": _hw_metric(hardware, "utilization_peak_pct"),
        "client_lag_p95_ms": _client_lag(client),
        "errors": _error_counts(records),
    }


def _hw_metric(hardware: dict | None, key: str) -> float | None:
    """硬件指标缺席时必须是 null：0 会被下游误读成「测得为 0」。"""
    if not isinstance(hardware, dict):
        return None
    value = hardware.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _client_lag(client: dict | None) -> float | None:
    """客户端 lag p95：缺席必须是 null，0 会被误读成「客户端完全没排队」。"""
    if not isinstance(client, dict):
        return None
    lag = client.get("lag_ms")
    if not isinstance(lag, dict):
        return None
    value = lag.get("p95_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _meets(record: RequestRecord, slo_ttft_ms: float | None, slo_tpot_ms: float | None) -> bool:
    if slo_ttft_ms is not None and (record.ttft_ms is None or record.ttft_ms > slo_ttft_ms):
        return False
    return not (
        slo_tpot_ms is not None and (record.tpot_ms is None or record.tpot_ms > slo_tpot_ms)
    )


def _error_counts(records: Sequence[RequestRecord]) -> dict:
    counts: dict[str, int] = {}
    for r in records:
        if r.ok:
            continue
        # 错误串本身已是固定分类（不含服务端取值），保留完整分类以不丢异常类型
        key = (r.error or "unknown").strip()[:80]
        counts[key] = counts.get(key, 0) + 1
    return counts
