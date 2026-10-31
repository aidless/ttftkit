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


def _dist_line(name, dist, unit="ms"):
    if dist is None:
        return f"| {name} | n/a | n/a | n/a | n/a | n/a |"
    if not isinstance(dist, dict):
        raise ValueError(f"{name} distribution must be an object or null")
    missing = [f for f in _DIST_FIELDS if f not in dist]
    if missing:
        raise ValueError(f"{name} distribution missing {', '.join(missing)}")
    return (
        f"| {name} | {_fmt(dist['mean'])}{unit} | {_fmt(dist['p50'])}{unit} "
        f"| {_fmt(dist['p95'])}{unit} | {_fmt(dist['p99'])}{unit} | {dist['count']} |"
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
        "## 延迟分布（单位：毫秒）",
        "",
        "| 指标 | 均值 | p50 | p95 | p99 | 样本数 |",
        "| --- | --- | --- | --- | --- | --- |",
        _dist_line("TTFT", summary.get("ttft_ms")),
        _dist_line("TPOT", summary.get("tpot_ms")),
        _dist_line("请求总延迟", summary.get("latency_ms")),
        _dist_line("解码段", summary.get("decode_ms")),
        _dist_line("SSE 块间隔", summary.get("chunk_gap_ms")),
        "",
        "> TPOT = 解码段 ÷ (服务端 completion_tokens − 1)。SSE 块间隔只是传输层观测，"
        "**不是**逐 token ITL；一个块可能含多个 token。",
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


def write(result: dict, path: str | Path) -> tuple[Path, Path]:
    """写 JSON 与同名 Markdown，返回两个路径。"""
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(markdown(result), encoding="utf-8")
    return json_path, md_path
