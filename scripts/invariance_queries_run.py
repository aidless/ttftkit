"""查询级分歧率实弹：多 prompt 独立样本口径重跑采样矩阵。

与采样单 prompt 版（invariance_sampled_run.py）的差别：
- prompts=8 条同域改写（每请求独立 query，seed+i 独立种子）——
  修掉「单 prompt × 单 seed 的 N 请求是同一 query 拷贝」的分歧率分母失真
  （verdict-matrix 补充发现 2 的实测闭环）。
- 用 stability 闸（分歧率 ≤ 上限）判定，不用 invariance（采样下逐字节
  一致是错误预期）。

产出：baseline-c1-queries.json / candidate-c{4,16}-queries.json /
verdict-queries.{md,json}。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ttftkit.gate import _min_n_zero_divergence, _wilson_upper, evaluate
from ttftkit.runner import run_benchmark

OUT = Path(__file__).resolve().parent.parent / "results" / "invariance-2026-11-10"
ENDPOINT = "http://127.0.0.1:8123/v1/chat/completions"
MODEL = "Qwen3-4B-AWQ"
# 每次跑给一个 tag（如 runC），避免后一次覆盖前一次的证据；跨跑对照见 multirun 判决书。
TAG = sys.argv[1] if len(sys.argv) > 1 else ""
S = f"-{TAG}" if TAG else ""

# 8 条同域改写：同一考点（KV cache / 连续批处理）的不同问法。
# 用改写而非无关题目，是为了和单 prompt 版保持可比（量的是同一 fork 域）。
PROMPTS = [
    "用三句话解释 KV cache 为什么能让 LLM 推理变快。",
    "KV cache 在自回归生成里省掉了什么重复计算？三句话说明。",
    "为什么 LLM 每生成一个 token 不用重算全部注意力？和 KV cache 什么关系？",
    "连续批处理（continuous batching）对推理吞吐为什么有帮助？三句话。",
    "预填充（prefill）和解码（decode）两个阶段的计算有什么区别？三句话说明。",
    "batch 变大时显存里 KV cache 的占用怎么变化？三句话解释。",
    "TTFT 和 TPOT 分别衡量什么？它们为什么会随并发变化？",
    "PagedAttention 解决了 KV cache 的什么问题？三句话说明。",
]


async def one_round(concurrency: int) -> dict:
    return await run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPTS[0],
        prompts=PROMPTS,  # 独立样本口径：每请求自己的 prompt + seed+i
        requests=8, concurrency=concurrency, max_tokens=128,
        warmup=2, timeout_s=60.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True, temperature=0.7, seed=1234)


def main() -> int:
    print("[1/4] 查询级基准轮 concurrency=1 (temperature=0.7, seed=1234, 8 条独立 prompt) ...")
    baseline = asyncio.run(one_round(1))
    (OUT / f"baseline-c1-queries{S}.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")

    policy = {
        "limits": {"ttft_ms.p99": 2000.0, "tpot_ms.p99": 200.0},
        "stability": {"against_baseline": True, "max_divergence_rate": 0.2},
    }
    rows, verdicts = [], {}
    for idx, c in enumerate([4, 16], start=2):
        print(f"[{idx}/4] 查询级候选轮 concurrency={c} ...")
        cand = asyncio.run(one_round(c))
        (OUT / f"candidate-c{c}-queries{S}.json").write_text(
            json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
        v = evaluate(cand, policy, baseline=baseline)
        verdicts[f"c{c}"] = v
        rows.append((c, cand, v))

    (OUT / f"verdict-queries{S}.json").write_text(
        json.dumps({"policy": policy, "tag": TAG or "canonical", "verdicts": verdicts},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    bo = {o["index"]: o["text"] for o in baseline.get("outputs", [])}
    lines = [
        "# 查询级分歧率判定（2026-11-10，多 prompt 独立样本口径）",
        "",
        "- 口径：8 条同域改写 prompt（每请求独立 query，seed+i 独立种子），",
        "  temperature=0.7，max_tokens=128——分歧率分母从此是独立 query 数。",
        "- 判据：stability 闸，max_divergence_rate=0.2（与单 prompt 版同一上限）。",
        "",
    ]
    worst = 0
    worst_k = 0
    for c, cand, v in rows:
        co = {o["index"]: o["text"] for o in cand.get("outputs", [])}
        diff = [i for i in sorted(bo) if bo[i] != co.get(i)]
        rate = len(diff) / len(bo)
        worst = max(worst, rate)
        worst_k = max(worst_k, len(diff))
        s1, s2 = baseline["summary"], cand["summary"]
        lines += [
            f"## c=1 vs c={c}",
            f"- 分歧 {len(diff)}/{len(bo)}（rate={rate:.3f}）→ stability exit_code={v.get('exit_code')}"
            f"（{'PASS' if v.get('exit_code') == 0 else 'FAIL'}）",
            f"- c=1  TTFT p50={s1['ttft_ms']['p50']:.1f}ms"
            f"（n<20 的 p99={s1['ttft_ms']['p99']:.1f} 由末位样本主导，"
            f"实测未必等于 max={s1['ttft_ms']['max']:.1f}，只引 p50）",
            f"- c={c} TTFT p50={s2['ttft_ms']['p50']:.1f}ms"
            f"（p99={s2['ttft_ms']['p99']:.1f}，max={s2['ttft_ms']['max']:.1f}）",
        ]
        if diff:
            lines.append(f"- 分歧槽位 index={diff}（每槽一条独立 query）")
        for check in v.get("checks", []):
            if check.get("kind") == "power":
                lines.append(f"- 功效见证：{check['observed']}")
    cap = policy["stability"]["max_divergence_rate"]
    upper0 = _wilson_upper(0, len(bo))
    upper1 = _wilson_upper(1, len(bo))
    need = _min_n_zero_divergence(cap)
    lines += [
        "",
        "## 与单 prompt 版对照",
        "- 单 prompt 拷贝口径（verdict-matrix）：c=4 分歧 2/8=0.250 / c=16 分歧 1/8=0.125，",
        "  分母是同槽拷贝的翻与不翻；",
        "- 本轮查询级口径：分歧槽位即独立 query 翻转数，rate 直接可读为 query 级分歧率。",
        f"- 本轮最差档 rate={worst:.3f}（上限 {cap:g}）。",
        "",
        "## 统计功效：小样本的点估计不是达标证据",
        f"- n={len(bo)} 时 0/{len(bo)} 的 Wilson 95% 上界 = {upper0:.3f} > 上限 {cap:g}；"
        f"1/{len(bo)} 的上界 = {upper1:.3f}；"
        f"本轮最差档 {worst_k}/{len(bo)} 的上界 = {_wilson_upper(worst_k, len(bo)):.3f}。",
        "  PASS 只说明点估计在限内，不足以声称 rate ≤ 上限；FAIL 同理只是本轮点估计越线。",
        f"- 零分歧下把 Wilson 上界压到 {cap:g} 以下需 n≥{need} 条独立 query"
        "（gate 已自动附 power 见证，",
        "  不改判决但写进 checks）。要判定这一档，实验应扩到该样本量以上的不同 prompt。",
        "- 分歧率对 query 组合和单轮 batch 时序都敏感（同一组 prompt 重跑读数会变），",
        "  报告必须声明口径与跑次；跨跑对照见 verdict-queries-multirun.md。",
        "",
        "## p99 口径修正（本轮实测反例）",
        "- 旧表述「n<20 时 p99 就是 max」过强：本轮 c=1 的 TTFT "
        f"p99={baseline['summary']['ttft_ms']['p99']:.1f} 而 "
        f"max={baseline['summary']['ttft_ms']['max']:.1f}，两者并不相等。准确说法是 p99 由末位",
        "  1–2 个样本主导、不是可分位估计；p99_basis=\"max\" 是伪影标记，延迟结论只引 p50。",
    ]
    (OUT / f"verdict-queries{S}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[6:]))
    return 0 if all(v.get("exit_code") == 0 for v in verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
