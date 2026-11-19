# 交叉格判决（2026-11-19，tag=runG）：flag=1 × LoRA adapter —— 确定性成立，延迟不成立

**一句话**：`VLLM_BATCH_INVARIANT=1` 叠在 LoRA adapter 上，**batch 不变性仍然成立（0/40，上界 0.088，决定性 PASS）**；
但同一份报告里**延迟闸 FAIL**（c=4 的 TTFT p99 4752.3 ms 越过 2000 ms，TPOT p99 259–261 ms 越过 200 ms）。
两件事不同轴，不能合成一个「交叉格 PASS / FAIL」。

## 这一格为什么必须单独测

上游文档实现细节第 6 条说 LoRA 的 shrink reduction 被拆成 8 个固定 K 块、按固定顺序求和——
**这说明设计意图是「LoRA 也确定」，但没有给出消费级卡 + AWQ 量化 + enforce-eager 的实测读数**。
同时 F3（flag=1 的代价）与 F7（LoRA 可服务）各自成立，不代表叠加后同时成立：
两条都往「kernel 走慢路径」的方向加成本，叠加处最容易先崩延迟。

## 环境（与 F1/F2/F3 同底物，只多一项 LoRA）

- 硬件：RTX 3060 Laptop **6GB**（sm_86，**不在** vLLM tuned matmul 表里 → 走 default 配置）
- 引擎：vLLM 0.33.0，`--enforce-eager`、`--max-model-len 2048`、`--max-lora-rank 64`
- 底模：`Qwen3-4B-AWQ`（AWQ 4-bit）；adapter：`p1v2`（`paper_agent/outputs/tmlr-qwen3-4b-p1v2`）
- 服务端声明：`VLLM_BATCH_INVARIANT=1`（写进报告 `config.server_env`，闸据它判可比性）
- 解码：`temperature=0.7, seed=1234, max_tokens=128`；每请求独立 prompt + `seed+i`；n=40 条不同 query
- 证据：`baseline-c1a-queries40-runG.json`、`candidate-c4-queries40-runG.json`、`candidate-c16-queries40-runG.json`、`verdict-queries40-runG.{json,md}`、服务日志 `results/serve-flag1-lora.log`
- 复现：`MODEL_NAME=p1v2 SERVER_ENV=VLLM_BATCH_INVARIANT=1 python3 scripts/invariance_queries40_run.py <tag>`

## 读数

| 档位 | 分歧/总数 | 分歧率 | Wilson 95% | 吞吐 (tok/s) | TTFT p50 / p99 (ms) | TPOT p50 / p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| c=1 baseline（第一次） | — | — | — | 4.56 | 531.8 / 928.8 | 219.6 / 265.0 |
| c=1 baseline（第二次） | — | — | — | 4.72 | 360.7 / 786.4 | 194.3 / 259.9 |
| c=4 | **0/40** | 0.000 | **[0.000, 0.088]** | 18.78 | 487.9 / **4752.3** | 191.9 / 259.4 |
| c=16 | **0/40** | 0.000 | **[0.000, 0.088]** | 60.89 | 665.3 / 1597.5 | 201.9 / 261.0 |

- **baseline 跨跑逐字节可复现：是**（`baseline_reproducible: true`）——这是 0/40 能被采信的前提，不是附注。
- 判词：`PASS_DECISIVE`（上界 0.088 < cap 0.2）。同一次 `evaluate()` 里 limits 检查给出 FAIL，
  两条结论都照写：`gate_exit=1` 的来源是延迟，不是分歧率。
- 逐 query 翻转槽位：c=4 无、c=16 无（40 条全同）。

## 与另两格的对照（同一底物、同一套 40 条 query）

| 组合 | 分歧率 | 判词 | 吞吐 c=16 (tok/s) | 结论 |
| --- | --- | --- | --- | --- |
| flag=0，底模（F2/F3） | 2/40 = 0.050 | PASS_DECISIVE | 180.33 | 默认档有稀疏分叉 |
| flag=1，底模（F3） | 0/40 | PASS_DECISIVE | 75.46 | 买确定性花 2.39× |
| **flag=1，LoRA**（本格） | **0/40** | **PASS_DECISIVE** | **60.89** | 确定性保住，延迟越线 |

flag=1 的代价在 LoRA 上再叠一层：c=16 吞吐 75.46 → 60.89（−19%），c=1 5.74 → 4.56（−21%）。

## 口径边界（别把这一格读大）

1. **只对 cap=0.2 成立**；换 cap 要重算 Wilson 区间，不能沿用判词。
2. **只对这一个 adapter（p1v2）成立**。LoRA 的确定性依赖 rank 与 target modules 的归约形状；
   换 rank 或换 target 集合需要重跑，不能外推。
3. **延迟 FAIL 是这台机器这个配置的读数**：6GB 卡把权重挤到显存边缘、`--enforce-eager` 关掉 CUDA graph、
   sm_86 无 tuned matmul，三者叠加。换 24GB 卡 + 允许 CUDA graph 后 TTFT 尾部大概率改善，
   **但那时的读数必须重测，不能拿这条当预期**。
4. **「0 分歧」不等于「逐 token 概率分布相同」**：判的是这 40 条 query × 128 token 的输出文本，
   概率层面的差异（若存在）被采样掩盖，本判决无权断言。

## 对作品集的三条落点

- 私有化部署的「确定性」卖点可以写：**LoRA 挂载 + batch-invariant 同时成立**（有实测，0/40，上界 0.088）。
- 但不能同时承诺「交互式延迟达标」：本机同配置下 TTFT p99 4.75s。要卖延迟，得换卡换配置重测。
- 定价口径（接 `cost-per-correct`）：flag=1 + LoRA 的 c=16 吞吐 60.89 tok/s
  → GPU 分钟/1M out tokens ≈ 220.88 × (75.46 / 60.89) ≈ **273.8**（同一台机的相对折算；
  这一格没做 1 Hz 功率采样，所以只报 GPU 分钟，不报电费）。
