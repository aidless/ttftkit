# 实测结论索引（每条都能复算）

这个仓的结论不写在散文里，写在这里：**一句结论 + 数字 + 证据文件 + 复现命令**。
不是从别处转述的，全部在 RTX 3060 Laptop 6GB（sm_86）/ vLLM 0.33.0 / Qwen3-4B-AWQ 上跑出来的；
换了卡、换了量化、换了引擎版本，数字会变，结论要重跑。

配置基线（除特别说明外）：`--enforce-eager`、`temperature=0.7`、`seed=1234`、`max_tokens=128`、
每请求独立 prompt + `seed+i`。

## F1 贪心解码下 batch 不变成立

c=1 vs c=16，8 请求：**8/8 输出逐字节一致**（闸 `invariance` exit 0）。
代价：TTFT p99 82.93 → 170.05 ms，TPOT p99 39.46 → 40.26 ms（TPOT 基本不动是预期——变的是调度与批内位置，不是每 token 的计算量）。
证据：`results/invariance-2026-11-10/verdict.md`；复现：`scripts/invariance_run.py`。

## F2 采样解码（默认档 flag=0）有稀疏分叉，但「多频繁」取决于分母

| 口径 | 读数 | 证据 |
|---|---|---|
| 8 条 query × 3 次独立跑 | c=4 3/8 = 0.375、c=16 4/8 = 0.500，**三次读数完全一致**（槽位 q0,q2,q3 / q0,q2,q3,q4） | `verdict-queries-multirun.md` |
| 40 条 query × 1 跑 | c=4 2/40 = 0.050、c=16 2/40 = 0.050，Wilson [0.014, 0.165] | `verdict-queries40-runE.md` |
| 单 prompt 8 份拷贝 | c=4 2/8、c=16 1/8 | `verdict-matrix.md` |

**同一台机器，8 条 query 读 0.375、40 条 query 读 0.050。** 差异不是服务变了，是分母大小——
8 条里翻 3 条，样本率被放大 7 倍。任何「分歧率 X%」的说法都必须带分母。
翻转的 query **不稳定**（8 条那次是 q0/q2/q3，40 条那次 c=4 是 [2,3]、c=16 是 [25,33]），
所以不能反推「某几条 query 天生有问题」。

## F3 打开 `VLLM_BATCH_INVARIANT=1` 是决定性 PASS，代价可定价

| 组 | c=4 | c=16 |
|---|---|---|
| flag=0（默认） | 2/40 = 0.050，[0.014, 0.165] | 2/40 = 0.050，[0.014, 0.165] |
| flag=1 | **0/40**，上界 **0.088** | **0/40**，上界 **0.088** |

上界 0.088 < cap 0.2 = 决定性 PASS（不是「没测到就放过」）。这是上游文档没覆盖的组合：
消费级 Ampere（sm_86，**不在** tuned matmul 表里，`_get_tuned_matmul_arch_family` 返回 None）+ AWQ + `--enforce-eager`。

代价（同一份报告里现算）：吞吐 c=1 15.43 → 5.74 tok/s、c=4 60.41 → 22.37、c=16 180.33 → 75.46；
折成 GPU 分钟/1M output tokens：c=16 **92.43 → 220.88（2.39×）**、c=1 1080.09 → 2904.20（2.69×）。
TTFT 基本不动（c=1 176.9 → 192.2 ms，c=16 479.5 → 461.4 ms），TPOT p50 61.1 → 175.2 ms。
证据：`verdict-queries40-pair.md`、`results/serve-flag{0,1}.log`、`flag-activation-evidence.txt`。

## F4 闸有四类规则、两类见证，判词只对声明的 cap 成立

规则：`limits`（延迟/吞吐上限）、`regressions`（相对基线退化）、`invariance`（逐字节一致）、
`stability`（分歧率 ≤ cap）。见证：`basis`（p99 在 n<20 时是 max 主导，自动挂）、`power`（PASS 但上界仍 > cap 时挂，不改判决）。
配置可比性键：`protocol / model / max_tokens / prompt_sha256 / server_env`——**服务端 flag 不同即判不可比（exit 2）**，
否则会把「配置差异」误报成「实现违反不变性」。
证据：`src/ttftkit/gate.py`、`docs/gate.md`。

## F5 小样本的功效边界（跑之前先算，别跑完再解释）

Wilson 95%、cap=0.2：n=8 时 0/8 上界 0.324、3/8 下界 0.137 —— **两个方向都不决定性**；
n=16 的零分歧才能把上界压到 0.2 以下；想判「约 0.2 这一档」要 n≈40（0/40 上界 0.088、3/40 上界 0.199、4/40 上界 0.231）。
**重复跑同一组 query 不能把分母乘起来**——分母是不同 query 的条数。
证据：`src/ttftkit/gate.py` 的 `wilson_interval()` / `decisiveness()`、`tests/test_gate_wilson.py`。

## F6 n<20 时 p99 不是分位数，是末位样本

`metrics` 会打 `p99_basis: "max" | "quantile"`（阈值 n<20）。实测 p99=123.7 vs max=123.9 ——
说「p99 就是 max」过强，准确说法是**由末位 1–2 个样本主导**。所以延迟结论引 **p50**；
`c=4` 曾在 n=8 下被判得比 `c=16` 还慢，那是 max 伪影，不是调度反常。
证据：`src/ttftkit/metrics.py`、`verdict-queries.md`「口径」节。

## F7 LoRA adapter 挂载可用，成本按 1 Hz 功率实测

vLLM `--enable-lora --lora-modules` 挂 QLoRA adapter：p1v2 验收 8/8 成功、limits 闸 PASS
（TTFT p99 684.4 ms、TPOT p99 177.1 ms），成本 **¥0.345 / 1M output tokens**（1 Hz `nvidia-smi` 采样、¥0.6/kWh）。
证据：`results/invariance-2026-11-10/deploy-verdict.md`、`deploy-p1v2-bench.md`。

## 不要这样引用（三条踩过的坑）

1. **别把 flag=0 的分叉当缺陷**：`VLLM_BATCH_INVARIANT` 默认 0（`vllm/envs.py:93`），分叉是设计行为；
   要么报「默认档的分歧率」，要么先开 flag=1 再谈不变性。
2. **别把 8 条 query 的 3/8 当总体率**：本仓自己犯过——按 3/8 预注册「40 条期望 15/40 FAIL」，
   实测 2/40，方向相反（`verdict-queries40-pair.md` 结论二）。判据不改，读数照写。
3. **别把 n<20 的 p99 当分位数**（见 F6），也别用「哪条 query 会翻」做任何稳定断言（见 F2）。
