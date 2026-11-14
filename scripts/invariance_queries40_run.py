"""40 条独立 prompt 的查询级分歧率实弹（把 0.2 这一档做成决定性判决）。

为什么要有这一支
----------------
8 条 query 的口径（invariance_queries_run.py）三次跨跑读数稳定在 c=4 3/8、
c=16 4/8，但 Wilson 95% 区间宽到跨越阈值：3/8 的下界 0.137、4/8 的
下界 0.215，只有 c=16 勉强越线。gate 因此对 PASS 附了 power 见证
（「8 样本判不了 0.2」）。这一支把样本量铺到 40 条**不同** query，
让区间窄到能给出决定性判决：

    n=40 时 0/40 的上界 = 0.088，3/40 的上界 = 0.199，4/40 的下界 = 0.040
    → 若真实分歧率 ≈ 0.375（8 样本读数），期望 15/40，区间远离 0.2 → 决定性 FAIL。
    → 若真实分歧率为 0，0/40 上界 0.088 < 0.2 → 决定性 PASS。

口径
----
- 40 条 prompt 是同一考点（KV cache / 连续批处理 / 推理服务）的**不同问法**，
  不是同一句的拷贝——divergence_rate 的分母是独立 query 数。
- 每请求 seed+i 独立种子（runner 的 prompts 分支）。
- c=1 baseline 跑两遍（round 1 与 round 4），用于验证 baseline 跨跑逐字节可复现；
  两遍不一致 = 服务状态漂移，本跑读数作废（脚本显式判红）。
- 判定用 stability 闸（分歧率 ≤ 上限），不用 invariance（采样下逐字节一致是错误预期）。

产出（OUT 下，带 tag 防覆盖）
----------------------------
baseline-c1a-queries40{S}.json / baseline-c1b-queries40{S}.json
candidate-c4-queries40{S}.json / candidate-c16-queries40{S}.json
verdict-queries40{S}.json / verdict-queries40{S}.md

用法：python3 scripts/invariance_queries40_run.py <tag>
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ttftkit.gate import decisiveness, evaluate, wilson_interval
from ttftkit.runner import run_benchmark

OUT = Path(__file__).resolve().parent.parent / "results" / "invariance-2026-11-10"
ENDPOINT = "http://127.0.0.1:8123/v1/chat/completions"
MODEL = "Qwen3-4B-AWQ"
TAG = sys.argv[1] if len(sys.argv) > 1 else ""
S = f"-{TAG}" if TAG else ""
CAP = 0.2
N = 40

# 40 条同域不同问法：同一考点（KV cache / 连续批处理 / 推理服务指标与部署），
# 每条都是独立 query，避免「同一句拷贝 N 份」把分歧率分母做假。
PROMPTS = [
    "用三句话解释 KV cache 为什么能让 LLM 推理变快。",
    "KV cache 在自回归生成里省掉了什么重复计算？三句话说明。",
    "为什么 LLM 每生成一个 token 不用重算全部注意力？和 KV cache 什么关系？",
    "连续批处理（continuous batching）对推理吞吐为什么有帮助？三句话。",
    "预填充（prefill）和解码（decode）两个阶段的计算有什么区别？三句话说明。",
    "batch 变大时显存里 KV cache 的占用怎么变化？三句话解释。",
    "TTFT 和 TPOT 分别衡量什么？它们为什么会随并发变化？",
    "PagedAttention 解决了 KV cache 的什么问题？三句话说明。",
    "为什么长上下文推理的显存瓶颈主要落在 KV cache 而不是权重？",
    "KV cache 的显存占用怎么估算？给出三句话的公式思路。",
    "为什么 beam search 会成倍放大 KV cache 的显存需求？",
    "把 KV cache 量化到 int8 会带来什么代价？三句话说明。",
    "为什么推理服务要区分 SLO 交互档和吞吐档？三句话。",
    "批处理为什么会让尾延迟变差？三句话解释。",
    "显存不足时的抢占（preemption）怎么工作？三句话说明。",
    "为什么 chunked prefill 能改善长 prompt 的尾延迟？三句话。",
    "prefix caching 命中时省下的是哪一段计算？三句话说明。",
    "为什么 speculative decoding 能提速但会改变输出分布？三句话。",
    "温度参数如何影响推理服务的可复现性？三句话说明。",
    "top-p 采样和 top-k 采样的差别是什么？三句话。",
    "为什么固定随机种子也不能保证批处理下输出完全一致？三句话。",
    "浮点归约顺序为什么会让 logits 产生微小差异？三句话说明。",
    "当两个 token 的 logits 几乎并列时，微小扰动会怎样改变输出？三句话。",
    "为什么贪心解码在批处理下更稳定？三句话说明。",
    "如何测量推理服务的 P99 延迟才有意义？三句话说明样本量要求。",
    "为什么样本量小于 20 时 P99 不能当分位数估计？三句话。",
    "吞吐（tokens/s）和每 token 成本是什么关系？三句话。",
    "为什么单位正确率成本比单位 token 成本更能反映部署经济性？三句话。",
    "单卡 6GB 上部署 4B 量化模型的主要约束是什么？三句话。",
    "LoRA 适配器挂载为什么比重训便宜？三句话说明。",
    "多 LoRA 共享基座时显存怎么分配？三句话说明。",
    "为什么 vLLM 的显存利用率参数设太高会启动失败？三句话。",
    "enforce-eager 模式对性能有什么影响？三句话说明。",
    "CUDA graph 捕获能省掉哪部分开销？三句话。",
    "为什么基准测试必须先 warmup 再测量？三句话说明。",
    "冷启动状态会怎样污染推理延迟的读数？三句话。",
    "为什么分布式推理的首 token 延迟通常更高？三句话。",
    "张量并行的通信开销出现在哪个阶段？三句话说明。",
    "如何判断一次推理基准的结果是可复现的？三句话。",
    "如果两次基准的读数差异很大，应该先怀疑什么？三句话。",
]
assert len(PROMPTS) == N, f"prompts 需要 {N} 条，实际 {len(PROMPTS)}"
assert len(set(PROMPTS)) == N, "prompts 有重复：分母就不是独立 query 数了"


async def one_round(concurrency: int) -> dict:
    return await run_benchmark(
        endpoint=ENDPOINT, model=MODEL, prompt=PROMPTS[0],
        prompts=PROMPTS,  # 独立样本口径：每请求自己的 prompt + seed+i
        requests=N, concurrency=concurrency, max_tokens=128,
        warmup=2, timeout_s=120.0, profile="rtx3060-vllm0.33.0-awq",
        capture_outputs=True, temperature=0.7, seed=1234)


def dump(obj: dict, name: str) -> None:
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    print(f"[1/5] baseline c=1（第一次，{N} 条独立 prompt）…", flush=True)
    b1 = asyncio.run(one_round(1))
    dump(b1, f"baseline-c1a-queries40{S}.json")

    policy = {
        "limits": {"ttft_ms.p99": 2000.0, "tpot_ms.p99": 200.0},
        "stability": {"against_baseline": True, "max_divergence_rate": CAP},
    }

    verdicts: dict[str, dict] = {}
    candidates: dict[str, dict] = {}
    for step, c in enumerate([4, 16], start=2):
        print(f"[{step}/5] candidate c={c} …", flush=True)
        cand = asyncio.run(one_round(c))
        dump(cand, f"candidate-c{c}-queries40{S}.json")
        candidates[f"c{c}"] = cand
        verdicts[f"c{c}"] = evaluate(cand, policy, baseline=b1)

    print("[4/5] baseline c=1（第二次，验证跨跑可复现）…", flush=True)
    b2 = asyncio.run(one_round(1))
    dump(b2, f"baseline-c1b-queries40{S}.json")

    # baseline 跨跑逐字节比对：不一致说明服务状态漂移，本跑的分歧率不可解读。
    o1 = {o["index"]: o["text"] for o in b1.get("outputs", [])}
    o2 = {o["index"]: o["text"] for o in b2.get("outputs", [])}
    baseline_drift = sorted(set(o1) ^ set(o2)) or [i for i in o1 if i in o2 and o1[i] != o2[i]]
    baseline_reproducible = not baseline_drift

    # 逐 query 翻转明细 + Wilson 区间 + 判词
    rows = []
    for key, cand in candidates.items():
        co = {o["index"]: o["text"] for o in cand.get("outputs", [])}
        flipped = sorted(i for i in o1 if i in co and o1[i] != co[i])
        v = verdicts[key]
        stab = next((ch for ch in v["checks"] if ch["kind"] == "stability"), None)
        k = len(flipped)
        lo, hi = wilson_interval(k, N)
        rows.append({
            "candidate": key,
            "concurrency": int(key[1:]),
            "diverged": k,
            "total": N,
            "rate": k / N,
            "wilson_low": lo,
            "wilson_high": hi,
            "decisiveness": decisiveness(k, N, CAP),
            "gate_exit": v["exit_code"],
            "gate_reasons": v["reasons"],
            "power_witness": [ch for ch in v["checks"] if ch["kind"] == "power"],
            "flipped_indices": flipped,
            "divergence_text": stab["observed"] if stab else None,
        })

    report = {
        "tag": TAG or "canonical",
        "policy": policy,
        "n_query": N,
        "cap": CAP,
        "baseline_reproducible": baseline_reproducible,
        "baseline_drift_indices": baseline_drift,
        "baseline_ttft_ms_p50": b1["metrics"]["ttft_ms"]["p50"],
        "baseline_ttft_ms_p99": b1["metrics"]["ttft_ms"]["p99"],
        "baseline_tpot_ms_p50": b1["metrics"]["tpot_ms"]["p50"],
        "baseline_ttft_ms_p50_rerun": b2["metrics"]["ttft_ms"]["p50"],
        "baseline_ttft_ms_p99_rerun": b2["metrics"]["ttft_ms"]["p99"],
        "rows": rows,
    }
    dump(report, f"verdict-queries40{S}.json")

    lines = [
        f"# 40 条独立 query 的采样分歧率（2026-11-10 起，tag={TAG or 'canonical'}）",
        "",
        f"- 口径：temperature=0.7、seed=1234、max_tokens=128、每请求独立 prompt + seed+i，"
        f"n={N} 条**不同** query。",
        f"- 判定：stability 闸，分歧率 ≤ {CAP} 为 PASS；判词由 Wilson 95% 区间给出"
        "（上界 < cap = PASS 决定性，下界 > cap = FAIL 决定性）。",
        f"- baseline 跨跑逐字节可复现：**{'是' if baseline_reproducible else '否'}**"
        + ("" if baseline_reproducible else f"（漂移槽位 {baseline_drift} → 本跑分歧率不可解读，作废）"),
        (f"- baseline c=1 TTFT：p50 {report['baseline_ttft_ms_p50']} ms / "
         f"p99 {report['baseline_ttft_ms_p99']} ms（重跑 p50 {report['baseline_ttft_ms_p50_rerun']} / "
         f"p99 {report['baseline_ttft_ms_p99_rerun']}）。"),
        "",
        "| 候选 | 分歧/总数 | 分歧率 | Wilson 95% | 闸退出码 | 判词 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['candidate']} | {r['diverged']}/{r['total']} | {r['rate']:.3f} "
            f"| [{r['wilson_low']:.3f}, {r['wilson_high']:.3f}] | {r['gate_exit']} | {r['decisiveness']} |")
    lines += ["", "## 逐 query 翻转槽位", ""]
    for r in rows:
        lines.append(f"- {r['candidate']}：{r['flipped_indices'] or '无'}"
                     + (f"　（power 见证：{r['power_witness']}）" if r["power_witness"] else ""))
    lines += [
        "",
        "## 口径边界",
        "",
        "- 40 条是同一考点的不同问法，覆盖 KV cache / 连续批处理 / 服务指标 / 部署约束四类；",
        "  分歧率因此是「该考点族」的量，不能外推到整个语料分布。",
        "- 每次跑仍受服务状态影响；baseline 双跑一致只能排除「服务整体漂移」，不能排除冷启动对候选轮的影响。",
        "- 判词只对 cap=0.2 这一档成立；换 cap 需重算区间，不能沿用判词。",
    ]
    (OUT / f"verdict-queries40{S}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    if not baseline_reproducible:
        print("!! baseline 跨跑不一致 → 读数作废", flush=True)
        return 4
    return 0 if all(r["gate_exit"] == 0 for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
