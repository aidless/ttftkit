"""报告渲染：同一份 JSON 生成 Markdown，避免两处口径漂移。"""
from __future__ import annotations

import json
from pathlib import Path


def _fmt(value, digits=1, suffix=""):
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return str(value)


_DIST_FIELDS = ("mean", "p50", "p95", "p99", "count")


def _with_ci(text: str, bounds) -> str:
    """点估计后面挂上 bootstrap 区间；没有区间就只给点估计。"""
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return text
    low, high = bounds
    if low is None or high is None:
        return text
    return f"{text} [{_fmt(low)}, {_fmt(high)}]"


def _dist_line(name, dist, unit="ms"):
    if dist is None:
        return f"| {name} | n/a | n/a | n/a | n/a | n/a |"
    if not isinstance(dist, dict):
        raise ValueError(f"{name} distribution must be an object or null")
    missing = [f for f in _DIST_FIELDS if f not in dist]
    if missing:
        raise ValueError(f"{name} distribution missing {', '.join(missing)}")
    ci = dist.get("ci") if isinstance(dist.get("ci"), dict) else {}
    p50_text = _with_ci(_fmt(dist["p50"]), ci.get("p50"))
    p95_text = _with_ci(_fmt(dist["p95"]), ci.get("p95"))
    return (
        f"| {name} | {_fmt(dist['mean'])}{unit} | {p50_text}{unit} "
        f"| {p95_text}{unit} | {_fmt(dist['p99'])}{unit} | {dist['count']} |"
    )


def _mapping(value, what):
    if not isinstance(value, dict):
        raise ValueError(f"report field {what!r} must be an object")
    return value


def markdown(result: dict) -> str:
    if not isinstance(result, dict):
        raise ValueError("report must be a JSON object")
    config = _mapping(result.get("config", {}), "config")
    summary = _mapping(result.get("summary", {}), "summary")
    records = result.get("records", [])
    if not isinstance(records, list):
        raise ValueError("report field 'records' must be a list")
    sha = config.get("prompt_sha256")
    sha_text = f"{sha[:16]}…" if isinstance(sha, str) and sha else "?"
    lines = [
        "# ttftkit 性能报告",
        "",
        f"- 模型：`{config.get('model', '?')}`",
        f"- 并发：{config.get('concurrency', '?')}，请求数：{config.get('requests', '?')}，"
        f"max_tokens：{config.get('max_tokens', '?')}，warmup：{config.get('warmup', 0)}",
        f"- 协议：{config.get('protocol', '?')}，部署 profile：`{config.get('profile', '?')}`",
        f"- prompt sha256：`{sha_text}`（原文不入报告）",
        f"- 测量窗口：{_fmt(result.get('duration_s'), 3)} s",
        "",
        "## 汇总",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 成功/总请求 | {summary.get('successful_requests', 0)} / {summary.get('requests', 0)} |",
        f"| 错误率 | {_fmt(summary.get('error_rate'), 4)} |",
        f"| 请求吞吐 | {_fmt(summary.get('requests_per_s'), 3)} req/s |",
        f"| token 吞吐 | {_fmt(summary.get('output_tokens_per_s'), 2)} tok/s |",
        f"| token 计数覆盖率 | {_fmt(summary.get('token_usage_coverage'), 3)} |",
        # goodput 只有在给了 SLO（--slo-ttft/--slo-tpot）时才有定义，否则不展示以免误导。
        *([f"| goodput | {_fmt(summary.get('goodput_per_s'), 3)} req/s |"]
          if summary.get("goodput_per_s") is not None else []),
        f"| 退化比（后1/4 ÷ 前1/4 延迟） | {_fmt(summary.get('degradation_ratio'), 3)} |",
        "",
    ]
    hardware = result.get("hardware")
    if hardware is not None and not isinstance(hardware, dict):
        raise ValueError("report field 'hardware' must be an object or null")
    if isinstance(hardware, dict) and not hardware:
        raise ValueError("report field 'hardware' is an empty object")

    def _mb(value) -> str:
        return "n/a" if value is None else f"{_fmt(value)} MB"

    lines += ["## 硬件（GPU 采样）", ""]
    if hardware is None:
        lines += [
            "未采样：本报告不含显存 / 利用率证据（`--gpu none`，或未探测到 nvidia-smi / rocm-smi）。",
            "",
        ]
    elif not hardware.get("samples"):
        # 采样器在、却一次都没采到：这是坏掉的监控，不是「没有硬件」，必须显式说出来。
        lines += [
            f"采样失败：采样器已探测到（`{hardware.get('source', '?')}`），"
            f"但 {hardware.get('sampler_errors', 0)} 次采样全部失败"
            f"（首个：`{hardware.get('error')}`）。本报告**没有**显存 / 利用率证据。",
            "",
        ]
    else:
        util = hardware.get("utilization_peak_pct")
        util_text = "n/a" if util is None else f"{_fmt(util)}%"
        delta = hardware.get("mem_used_delta_mb")
        delta_text = "n/a（采样不足 2 次，算不出增长）" if delta is None else _mb(delta)
        lines += [
            f"- 来源：`{hardware.get('source', '?')}`，设备：{hardware.get('name', '?')}，"
            f"驱动：{hardware.get('driver', '?')}",
            f"- 显存：起点 {_mb(hardware.get('mem_used_baseline_mb'))} / "
            f"峰值 {_mb(hardware.get('mem_used_peak_mb'))} "
            f"（总量 {_mb(hardware.get('mem_total_mb'))}）",
            f"- 窗口内增长：{delta_text}，峰值利用率：{util_text}",
            f"- 采样 {hardware.get('samples', 0)} 次（间隔 {hardware.get('interval_s')} s），"
            f"采样器错误 {hardware.get('sampler_errors', 0)} 次",
            "",
        ]
        if hardware.get("truncated"):
            lines += [
                "> 采样线程在 `stop()` 超时后仍在运行（`SamplerBusyAtStop`）："
                "峰值与增长可能被**低报**，这里只覆盖已收上来的样本。",
                "",
            ]
        if hardware.get("sampler_errors"):
            lines += [
                f"> 采样器曾失败 {hardware['sampler_errors']} 次（首个：`{hardware.get('error')}`）。"
                "显存数字只覆盖采样成功的时刻，不能当作全程证据。",
                "",
            ]
    client = result.get("client")
    if client is not None and not isinstance(client, dict):
        raise ValueError("report field 'client' must be an object or null")
    lines += ["## 客户端（事件循环延迟）", ""]
    if not isinstance(client, dict):
        lines += ["未记录：本报告不含客户端 lag 证据（较早版本生成的报告）。", ""]
    elif client.get("lag_ms") is None:
        # 测量窗口短于采样间隔时一个样本都采不到：说「没有证据」，不要说「没问题」
        lines += ["未取到 lag 样本（测量窗口短于采样间隔）：本报告不含客户端延迟证据。", ""]
    else:
        lag = client.get("lag_ms")
        if not isinstance(lag, dict):
            raise ValueError("report field 'client.lag_ms' must be an object")
        samples = lag.get("samples")
        lines += [
            f"- 采样 {samples} 次（间隔 {lag.get('interval_s')} s）："
            f"p50 {_fmt(lag.get('p50_ms'), 2)} ms，p95 {_fmt(lag.get('p95_ms'), 2)} ms，"
            f"最大 {_fmt(lag.get('max_ms'), 2)} ms",
            "",
        ]
        ttft = summary.get("ttft_ms")
        ttft_p95 = ttft.get("p95") if isinstance(ttft, dict) else None
        lag_p95 = lag.get("p95_ms")
        if (
            isinstance(ttft_p95, (int, float)) and not isinstance(ttft_p95, bool)
            and isinstance(lag_p95, (int, float)) and not isinstance(lag_p95, bool)
            and ttft_p95 > 0 and lag_p95 > 0.05 * ttft_p95
        ):
            share = 100.0 * lag_p95 / ttft_p95
            lines += [
                f"> 客户端事件循环延迟已占 TTFT p95 的 {share:.1f}%：这一轮延迟是**测量上界**，"
                "其中含客户端自身排队（见 docs/papers.md A1）。判断服务端性能前先降低并发或用多进程客户端。",
                "",
            ]

    lines += [
        "## 延迟分布（单位：毫秒）",
        "",
        "| 指标 | 均值 | p50 [95% CI] | p95 [95% CI] | p99 | 样本数 |",
        "| --- | --- | --- | --- | --- | --- |",
        _dist_line("TTFT", summary.get("ttft_ms")),
        _dist_line("TPOT", summary.get("tpot_ms")),
        _dist_line("请求总延迟", summary.get("latency_ms")),
        _dist_line("解码段", summary.get("decode_ms")),
        _dist_line("SSE 块间隔", summary.get("chunk_gap_ms")),
        "",
        "> TPOT = 解码段 ÷ (服务端 completion_tokens − 1)。SSE 块间隔只是传输层观测，"
        "**不是**逐 token ITL；一个块可能含多个 token。",
        "> p50/p95 后的方括号是 95% bootstrap 置信区间（种子固定为 0）；"
        "样本少于 20 条时不给区间——8 条记录的 p95 其实就是最大值。",
        "",
    ]
    errors = summary.get("errors")
    if errors is None:
        errors = {}
    if not isinstance(errors, dict):
        raise ValueError("summary field 'errors' must be an object")
    if errors:
        lines += ["## 失败分类", "", "| 错误 | 次数 |", "| --- | --- |"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(errors.items(), key=lambda kv: -kv[1])]
        lines.append("")
    if records:
        lines += ["## 逐请求", "", "| # | 成功 | TTFT(ms) | 解码(ms) | 块数 | tokens | 错误 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in records[:50]:
            if not isinstance(r, dict):
                raise ValueError("each record must be an object")
            lines.append(
                f"| {r.get('index')} | {'是' if r.get('ok') else '否'} "
                f"| {_fmt(r.get('ttft_ms'))} | {_fmt(r.get('decode_ms'))} "
                f"| {r.get('content_chunks')} | {_fmt(r.get('output_tokens'), 0)} "
                f"| {r.get('error') or ''} |"
            )
        if len(records) > 50:
            lines.append(f"| … | | | | | | 其余 {len(records) - 50} 条见 JSON |")
        lines.append("")
    return "\n".join(lines)


def write_scan(result: dict, path: str | Path) -> tuple[Path, Path]:
    """写 scan 的 JSON 与同名 Markdown。"""
    from .scan import markdown_scan

    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown_scan(result), encoding="utf-8")
    return json_path, md_path


def write(result: dict, path: str | Path) -> tuple[Path, Path]:
    """写 JSON 与同名 Markdown，返回两个路径。"""
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown(result), encoding="utf-8")
    return json_path, md_path
