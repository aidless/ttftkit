"""汇总统计：纯函数，输入请求记录，输出可 JSON 序列化的字典。

设计约束：
- 分位数用线性插值（与 numpy 默认 method="linear" 一致），便于和别人的报告对齐。
- 缺数据返回 None，绝不返回 0 冒充。
- 吞吐的分母是**测量墙钟时长**，不是各请求延迟之和。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from .models import RequestRecord


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
        "stdev": stdev,
        "cv": (stdev / mean) if mean else None,
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
) -> dict:
    """把一组请求记录汇总成报告里的 `summary` 段。

    seconds: 测量窗口的墙钟时长（秒），必须有限且 > 0。
    hardware: 采集到的硬件块（v0.2+）；未采样时传 None，对应指标一律为 null 而非 0。
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
