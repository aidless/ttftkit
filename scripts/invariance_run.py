# -*- coding: utf-8 -*-
"""invariance 实弹脚本：同一服务下 c=1（基准）与 c=16（候选）各测一轮，捕获输出，出闸。

用法：PYTHONPATH=src python3 scripts/invariance_run.py
产物：results/invariance-2026-11-10/{baseline-c1.json, candidate-c16.json, verdict.json, verdict.md}
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ttftkit.runner import run_benchmark
from ttftkit.gate import evaluate

OUT = Path(__file__).resolve().parent.parent / "results" / "invariance-2026-11-10"
ENDPOINT = "http://127.0.0.1:8123/v1/chat/completions"
MODEL = "Qwen3-4B-AWQ"
PROMPT = "用三句话解释 KV cache 为什么能让 LLM 推理变快。"

def main() -> int:
    print("[1/3] 基准轮 concurrency=1 ...")
    baseline = asyncio.run(run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPT,
        requests=8, concurrency=1, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True))
    (OUT / "baseline-c1.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  完成：records={len(baseline.get('records', []))}")

    print("[2/3] 候选轮 concurrency=16 ...")
    candidate = asyncio.run(run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPT,
        requests=8, concurrency=16, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True))
    (OUT / "candidate-c16.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  完成：records={len(candidate.get('records', []))}")

    print("[3/3] invariance 闸判定 ...")
    policy = {
        "limits": {"ttft_ms.p99": 2000.0, "tpot_ms.p99": 200.0},
        "invariance": {"against_baseline": True},
    }
    verdict = evaluate(candidate, policy, baseline=baseline)
    (OUT / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")

    exit_code = verdict.get("exit_code")
    passed = exit_code == 0
    lines = ["# invariance 实弹判定（2026-11-10）", "",
             "- 服务：vLLM 0.33.0 / Qwen3-4B-AWQ @ 127.0.0.1:8123（RTX 3060 Laptop 6GB）",
             f"- 提示词（两轮完全一致）：{PROMPT}",
             "- max_tokens=128，requests=8，warmup=2",
             "- 基准：concurrency=1；候选：concurrency=16",
             ""]
    for c in verdict.get("checks", []):
        lines.append(f"- [{c.get('status', '?')}] {c.get('name', c.get('id', ''))}: "
                     f"observed={c.get('observed')} reason={c.get('reason')}")
    lines.append("")
    lines.append(f"**闸结果：exit_code={exit_code}，{'PASS' if passed else 'FAIL'}**")
    (OUT / "verdict.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"闸结果：exit_code={exit_code}，{'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
