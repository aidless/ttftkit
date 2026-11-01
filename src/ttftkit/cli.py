"""命令行入口：run / gate / report。退出码 0=通过 1=不通过 2=用法或输入错误。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shlex
import sys
from pathlib import Path

from .gate import evaluate
from .hardware import command_sampler, detect_sampler
from .report import markdown, write
from .runner import BenchmarkFailed, run_benchmark

DEFAULT_PROMPT = "Explain in one paragraph why KV cache memory grows with context length."


def _sampler_for(args):
    """--gpu 三态：none 明确不采样；command 用给定命令；auto 探测本机工具。"""
    if args.gpu == "none":
        return None
    if args.gpu == "command":
        if not args.gpu_command:
            raise ValueError("--gpu command requires --gpu-command")
        argv = shlex.split(args.gpu_command)
        if not argv:
            raise ValueError("--gpu-command must not be empty")
        return command_sampler(argv)
    return detect_sampler()


def _load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def cmd_run(args) -> int:
    if not math.isfinite(args.sample_interval) or args.sample_interval <= 0:
        raise ValueError(f"--sample-interval must be finite and positive, got {args.sample_interval!r}")
    sampler = _sampler_for(args)
    result = asyncio.run(
        run_benchmark(
            endpoint=args.endpoint,
            model=args.model,
            prompt=args.prompt,
            requests=args.requests,
            concurrency=args.concurrency,
            max_tokens=args.max_tokens,
            warmup=args.warmup,
            timeout_s=args.timeout,
            profile=args.profile,
            api_key=args.api_key,
            slo_ttft_ms=args.slo_ttft,
            slo_tpot_ms=args.slo_tpot,
            sampler=sampler,
            sample_interval_s=args.sample_interval,
        )
    )
    json_path, md_path = write(result, args.output)
    summary = result["summary"]
    failed = summary["requests"] - summary["successful_requests"]
    print(f"报告：{json_path}  {md_path}")
    tps = summary["output_tokens_per_s"]
    tps_text = "n/a" if tps is None else f"{round(tps, 2)}"
    print(
        f"成功 {summary['successful_requests']}/{summary['requests']}，"
        f"错误率 {summary['error_rate']:.3f}，token 吞吐 {tps_text}"
    )
    if failed:
        print(f"ttftkit: {failed} 个请求失败，详见报告中的失败分类", file=sys.stderr)
        return 1
    return 0


def cmd_gate(args) -> int:
    candidate = _load(args.report)
    policy = _load(args.policy)
    baseline = _load(args.baseline) if args.baseline else None
    verdict = evaluate(candidate, policy, baseline)
    for check in verdict["checks"]:
        observed = check.get("observed")
        observed_text = "n/a" if observed is None else f"{observed:g}"
        print(
            f"[{check['status']:4}] {check['metric']:<24} "
            f"观测={observed_text:<12} {check.get('reason', '')}"
        )
    for reason in verdict["reasons"]:
        stream = sys.stderr if verdict["exit_code"] else sys.stdout
        print(f"ttftkit: {reason}", file=stream)
    print(f"结论：exit={verdict['exit_code']}（0=通过 1=不通过 2=配置/可比性错误）")
    return verdict["exit_code"]


def cmd_report(args) -> int:
    print(markdown(_load(args.report)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ttftkit", description="LLM 推理服务性能测量与回归门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="对 OpenAI 兼容端点做一次测量")
    run.add_argument("--endpoint", required=True, help="完整 URL，如 http://host:8000/v1/chat/completions")
    run.add_argument("--model", required=True)
    run.add_argument("--prompt", default=DEFAULT_PROMPT)
    run.add_argument("--requests", type=int, default=8)
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--max-tokens", type=int, default=32)
    run.add_argument("--warmup", type=int, default=0)
    run.add_argument("--timeout", type=float, default=60.0, help="单请求超时（秒，含整条 SSE 流的整体预算）")
    run.add_argument("--profile", default="unspecified", help="部署标识，如 rtx4090-vllm-0.6.3")
    run.add_argument("--slo-ttft", type=float, default=None, help="TTFT SLO(ms), 给出后算 goodput")
    run.add_argument("--slo-tpot", type=float, default=None, help="TPOT SLO(ms), 给出后算 goodput")
    run.add_argument("--output", default="results/run.json")
    run.add_argument("--api-key", default=os.environ.get("TTFTKIT_API_KEY"))
    run.add_argument("--gpu", choices=("auto", "none", "command"), default="auto",
                     help="显存采样：auto=探测 nvidia-smi/rocm-smi，none=不采样，command=用 --gpu-command")
    run.add_argument("--gpu-command", default=None,
                     help="自定义采样命令（须输出 nvidia-smi CSV 格式），配合 --gpu command")
    run.add_argument("--sample-interval", type=float, default=0.25,
                     help="采样间隔（秒），默认 0.25")
    run.set_defaults(func=cmd_run)

    gate = sub.add_parser("gate", help="按策略判定一份报告能否放行")
    gate.add_argument("report")
    gate.add_argument("--policy", required=True)
    gate.add_argument("--baseline", default=None)
    gate.set_defaults(func=cmd_gate)

    report = sub.add_parser("report", help="把 JSON 报告渲染为 Markdown")
    report.add_argument("report")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BenchmarkFailed as exc:
        # 基础设施/服务端失败 → 「本轮不通过」（1），不是用法错误（2）
        print(f"ttftkit: error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"ttftkit: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
