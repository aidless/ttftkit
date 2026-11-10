# -*- coding: utf-8 -*-
"""invariance/stability 实弹矩阵：补 c=4 档 + 用 stability 闸重判采样轮。

矩阵设计（2026-11-10）：
- 贪心（temperature=0）：c=1 / c=4 / c=16 三档，invariance 逐字节一致。
- 采样（temperature=0.7, seed=1234）：c=1 / c=4 / c=16 三档，stability 分歧率。
c=1 与 c=16 已有判决（verdict.md / verdict-sampled.md），本轮补 c=4 并用
新 stability 闸对既有口径（c=1 baseline）重判 c=4 与 c=16 两档。
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
LEVELS = (1, 4, 16)


def measure(conc, temperature, seed=None, label=""):
    rep = asyncio.run(run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPT,
        requests=8, concurrency=conc, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True, temperature=temperature, seed=seed))
    path = OUT / f"{label}-c{conc}.json"
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    s = rep["summary"]
    print(f"{label} c={conc}: TTFT p99={s['ttft_ms']['p99']:.1f}ms TPOT p99={s['tpot_ms']['p99']:.1f}ms")
    return rep


def main() -> int:
    rows = []
    # 贪心：c=1 baseline（已有 verdict.md 用过同口径，本轮重测保证同服务同批证据）
    greedy = {c: measure(c, 0.0, label="greedy") for c in LEVELS}
    # 采样：c=1 baseline
    sampled = {c: measure(c, 0.7, seed=1234, label="sampled") for c in LEVELS}

    # 判定 1：贪心 invariance（c=1 → c=4 / c=16）
    for cand_c in (4, 16):
        v = evaluate(greedy[cand_c], {"invariance": {"against_baseline": True}},
                     baseline=greedy[1])
        inv = [c for c in v["checks"] if c["rule"] == "invariance"][0]
        rows.append(("greedy", 1, cand_c, "invariance", inv["status"],
                     inv["observed"], v["exit_code"]))

    # 判定 2：采样 stability（c=1 → c=4 / c=16，上限 0.2）
    for cand_c in (4, 16):
        v = evaluate(sampled[cand_c],
                     {"stability": {"against_baseline": True, "max_divergence_rate": 0.2}},
                     baseline=sampled[1])
        st = [c for c in v["checks"] if c["rule"] == "stability"][0]
        rows.append(("sampled", 1, cand_c, "stability", st["status"],
                     st["observed"], v["exit_code"]))

    lines = [
        "# invariance/stability 实弹矩阵（2026-11-10，c=4 补档）",
        "",
        "| 路径 | baseline | candidate | 闸 | 状态 | 观测 | exit |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r[0]} | c={r[1]} | c={r[2]} | {r[3]} | **{r[4]}** | {r[5]} | {r[6]} |")
    lines += [
        "",
        "## 延迟总表",
        "",
        "| 路径 | 并发 | TTFT p99(ms) | TPOT p99(ms) |",
        "|---|---|---|---|",
    ]
    for label, reps in (("greedy", greedy), ("sampled", sampled)):
        for c, rep in reps.items():
            s = rep["summary"]
            lines.append(f"| {label} | c={c} | {s['ttft_ms']['p99']:.1f} | {s['tpot_ms']['p99']:.1f} |")
    lines += [
        "",
        "## 口径",
        "",
        "- vLLM 0.33.0 / Qwen3-4B-AWQ / enforce-eager / 单卡 RTX 3060 6GB",
        "- 每档 8 请求 warmup 2，max_tokens=128，prompt 固定",
        "- 贪心判 invariance（逐字节一致）；采样(temperature=0.7, seed=1234) 判 stability（分歧率 ≤ 0.2）",
        "- 复现脚本：scripts/invariance_matrix_run.py",
    ]
    (OUT / "verdict-matrix.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "verdict-matrix.json").write_text(
        json.dumps([{"path": r[0], "baseline_c": r[1], "candidate_c": r[2],
                     "rule": r[3], "status": r[4], "observed": r[5], "exit_code": r[6]}
                    for r in rows], ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines[:16]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
