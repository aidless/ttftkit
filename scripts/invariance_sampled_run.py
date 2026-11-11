"""采样解码 invariance 实弹：seed 固定时 c=1 与 c=16 的输出是否一致？

与贪心版（invariance_run.py）的唯一差别：temperature=0.7 + seed=1234。
若 PASS，说明 vLLM 0.33.0 在单卡连续批处理下对固定 seed 的采样路径也是
batch 不变的；若 FAIL，捕获的 outputs 就是第一现场证据。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ttftkit.gate import evaluate
from ttftkit.runner import run_benchmark

OUT = Path(__file__).resolve().parent.parent / "results" / "invariance-2026-11-10"
ENDPOINT = "http://127.0.0.1:8123/v1/chat/completions"
MODEL = "Qwen3-4B-AWQ"
PROMPT = "用三句话解释 KV cache 为什么能让 LLM 推理变快。"


def main() -> int:
    print("[1/3] 采样基准轮 concurrency=1 (temperature=0.7, seed=1234) ...")
    baseline = asyncio.run(run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPT,
        requests=8, concurrency=1, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True, temperature=0.7, seed=1234))
    (OUT / "baseline-c1-sampled.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")

    print("[2/3] 采样候选轮 concurrency=16 ...")
    candidate = asyncio.run(run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPT,
        requests=8, concurrency=16, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True, temperature=0.7, seed=1234))
    (OUT / "candidate-c16-sampled.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=1), encoding="utf-8")

    print("[3/3] invariance 闸判定 ...")
    policy = {
        "limits": {"ttft_ms.p99": 2000.0, "tpot_ms.p99": 200.0},
        "invariance": {"against_baseline": True},
    }
    verdict = evaluate(candidate, policy, baseline=baseline)
    (OUT / "verdict-sampled.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")

    exit_code = verdict.get("exit_code")
    bo = {o["index"]: o["text"] for o in baseline.get("outputs", [])}
    co = {o["index"]: o["text"] for o in candidate.get("outputs", [])}
    same = sum(1 for i in bo if bo[i] == co.get(i))
    s1, s2 = baseline["summary"], candidate["summary"]
    lines = [
        "# 采样解码 invariance 实弹判定（2026-11-10）",
        "",
        "- 与贪心版唯一差别：temperature=0.7, seed=1234（其余口径完全一致）",
        f"- 逐字节一致：{same}/{len(bo)}",
        f"- c=1  TTFT p99={s1['ttft_ms']['p99']:.1f}ms  TPOT p99={s1['tpot_ms']['p99']:.1f}ms",
        f"- c=16 TTFT p99={s2['ttft_ms']['p99']:.1f}ms  TPOT p99={s2['tpot_ms']['p99']:.1f}ms",
        f"- **闸结果：exit_code={exit_code}，{'PASS' if exit_code == 0 else 'FAIL'}**",
        "",
    ]
    # 逐请求差异表（FAIL 时的第一现场）
    diffs = [(i, bo[i], co.get(i, "")) for i in sorted(bo) if bo[i] != co.get(i, "")]
    if diffs:
        lines.append("## 逐请求差异（前 3 条）")
        for i, a, b in diffs[:3]:
            lines.append(f"- index={i}: c=1 尾部={a[-60:]!r} / c=16 尾部={b[-60:]!r}")
    (OUT / "verdict-sampled.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"逐字节一致 {same}/{len(bo)}；闸 exit_code={exit_code}")
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
