"""SLO 负载扫描：多级并发，逐级判 SLO，报「满足 SLO 的最高负载」。

依据 docs/papers.md C1（OpenDC-Infer-LC，EMNLP 2026 Industry）：吞吐必须在延迟约束
下比较，否则会奖励「牺牲延迟换吞吐」。本模块的纪律：

1. 违反 SLO 的那一级**留在结果里**（`stopped_at` + `violations`），不许只报好看的数字；
2. 一级违反即停（papers.md C2 的「预算一致」：继续往上扫只会把崩溃/超时混进数据）；
3. 没有证据（p95 为 null）不算满足 SLO——null 绝不冒充通过。

注意：这仍是**闭环**扫描（papers.md A2），结论只回答「在 K 个并发客户端下」，
不是生产到达率下的容量。
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence


def _check_levels(levels: Sequence[int]) -> list[int]:
    if not levels:
        raise ValueError("levels must not be empty")
    seen: list[int] = []
    for level in levels:
        if isinstance(level, bool) or not isinstance(level, int) or level <= 0:
            raise ValueError(f"levels must be positive integers, got {level!r}")
        if seen and level <= seen[-1]:
            raise ValueError(f"levels must be strictly ascending, got {seen[-1]} then {level}")
        seen.append(level)
    return seen


def _check_slo(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number or null, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return float(value)


def _p95(summary: dict, metric: str) -> float | None:
    dist = summary.get(metric)
    if not isinstance(dist, dict):
        return None
    value = dist.get("p95")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def violations(summary: dict, *, slo_ttft_ms: float | None, slo_tpot_ms: float | None,
               slo_error_rate: float | None) -> list[str]:
    """这一级违反了哪些 SLO。空列表 = 满足。"""
    found: list[str] = []
    if slo_error_rate is not None:
        rate = summary.get("error_rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            found.append("error_rate missing (no evidence)")
        elif rate > slo_error_rate + 1e-12:
            found.append(f"error_rate {rate:.4f} > {slo_error_rate:.4f}")
    for metric, bound, label in (("ttft_ms", slo_ttft_ms, "ttft_ms.p95"),
                                 ("tpot_ms", slo_tpot_ms, "tpot_ms.p95")):
        if bound is None:
            continue
        value = _p95(summary, metric)
        if value is None:
            found.append(f"{label} missing (no evidence)")
        elif value > bound + 1e-9:
            found.append(f"{label} {value:.1f} > {bound:.1f}")
    return found


def scan(run_level: Callable[[int], dict], levels: Sequence[int], *,
         slo_ttft_ms: float | None = None, slo_tpot_ms: float | None = None,
         slo_error_rate: float | None = 0.0) -> dict:
    """按并发级别升序扫，返回 scan 报告。

    run_level(concurrency) 必须返回一份普通 run 报告（含 `summary`）。
    """
    ordered = _check_levels(levels)
    slo_ttft_ms = _check_slo("slo_ttft_ms", slo_ttft_ms)
    slo_tpot_ms = _check_slo("slo_tpot_ms", slo_tpot_ms)
    slo_error_rate = _check_slo("slo_error_rate", slo_error_rate)
    if slo_ttft_ms is None and slo_tpot_ms is None and slo_error_rate is None:
        raise ValueError("scan needs at least one SLO bound (ttft / tpot / error_rate)")

    rows: list[dict] = []
    first_config: dict = {}
    stopped_at: int | None = None
    best: dict | None = None
    for level in ordered:
        report = run_level(level)
        if not isinstance(report, dict):
            raise ValueError(f"run_level({level}) must return a report object")
        summary = report.get("summary")
        if not isinstance(summary, dict):
            raise ValueError(f"run_level({level}) report has no summary")
        if not first_config:
            config = report.get("config")
            first_config = config if isinstance(config, dict) else {}
        bad = violations(summary, slo_ttft_ms=slo_ttft_ms, slo_tpot_ms=slo_tpot_ms,
                         slo_error_rate=slo_error_rate)
        rows.append({
            "concurrency": level,
            "satisfied": not bad,
            "violations": bad,
            "summary": summary,
        })
        if bad:
            stopped_at = level
            break
        best = summary

    return {
        "schema_version": 1,
        "kind": "scan",
        "config": {
            "levels": ordered,
            "slo": {
                "ttft_ms_p95": slo_ttft_ms,
                "tpot_ms_p95": slo_tpot_ms,
                "error_rate": slo_error_rate,
            },
            **{key: first_config.get(key) for key in
               ("protocol", "model", "max_tokens", "prompt_sha256", "profile")
               if key in first_config},
        },
        "levels": rows,
        "slo_rps": best.get("requests_per_s") if best else None,
        "slo_output_tokens_per_s": best.get("output_tokens_per_s") if best else None,
        "stopped_at": stopped_at,
        "all_levels_satisfied": stopped_at is None,
    }


def markdown_scan(result: dict) -> str:
    """scan 报告的 Markdown：先把 SLO 写清楚，再列每一级。"""
    if not isinstance(result, dict):
        raise ValueError("scan report must be a JSON object")
    config = result.get("config")
    if not isinstance(config, dict):
        raise ValueError("scan report field 'config' must be an object")
    rows = result.get("levels")
    if not isinstance(rows, list):
        raise ValueError("scan report field 'levels' must be a list")
    slo = config.get("slo") if isinstance(config.get("slo"), dict) else {}

    def _slo_text(key: str, unit: str) -> str:
        value = slo.get(key)
        return "未约束" if value is None else f"{value:g}{unit}"

    def _fmt(value, digits=1, suffix=""):
        if value is None:
            return "n/a"
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:,.{digits}f}{suffix}"
        return str(value)

    lines = [
        "# ttftkit SLO 负载扫描",
        "",
        f"- 模型：`{config.get('model', '?')}`，部署 profile：`{config.get('profile', '?')}`",
        f"- SLO：TTFT p95 ≤ {_slo_text('ttft_ms_p95', ' ms')}，"
        f"TPOT p95 ≤ {_slo_text('tpot_ms_p95', ' ms')}，"
        f"错误率 ≤ {_slo_text('error_rate', '')}",
        f"- 扫描级别（并发）：{', '.join(str(row.get('concurrency')) for row in rows)}",
        "",
        "| 并发 | 满足 SLO | req/s | tok/s | TTFT p95 | TPOT p95 | 违反项 |",
        "| ---: | :---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("scan report level rows must be objects")
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        ttft = (summary.get("ttft_ms") or {}).get("p95") if isinstance(summary.get("ttft_ms"), dict) else None
        tpot = (summary.get("tpot_ms") or {}).get("p95") if isinstance(summary.get("tpot_ms"), dict) else None
        bad = row.get("violations") or []
        lines.append(
            f"| {row.get('concurrency')} | {'是' if row.get('satisfied') else '否'} "
            f"| {_fmt(summary.get('requests_per_s'), 3)} | {_fmt(summary.get('output_tokens_per_s'), 2)} "
            f"| {_fmt(ttft)} ms | {_fmt(tpot)} ms | {'；'.join(bad) if bad else '—'} |"
        )
    lines += [
        "",
        f"- 满足 SLO 的最高负载：**{_fmt(result.get('slo_rps'), 3)} req/s**"
        f"（token：{_fmt(result.get('slo_output_tokens_per_s'), 2)} tok/s）"
        + ("；没有任何级别满足 SLO" if result.get("slo_rps") is None else ""),
        f"- 首次违反 SLO 的并发：{result.get('stopped_at') if result.get('stopped_at') else '未出现'}"
        f"（{'全部级别满足' if result.get('all_levels_satisfied') else '已在该级停止扫描'}）",
        "",
        "> 这是**闭环**扫描：回答「在 K 个并发客户端下」，不是生产到达率下的容量"
        "（见 docs/papers.md A2）。",
        "",
    ]
    client = result.get("client")
    if isinstance(client, dict):
        lag = client.get("lag_ms")
        if isinstance(lag, dict):
            lines += [f"- 客户端事件循环延迟 p95：{_fmt(lag.get('p95_ms'), 2)} ms", ""]
    return "\n".join(lines)
