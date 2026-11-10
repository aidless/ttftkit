# invariance 实弹判定（2026-11-10）

## 判决：PASS —— 贪心解码下 batch 不变成立（8/8 输出逐字节一致）

- 服务：vLLM 0.33.0 / Qwen3-4B-AWQ @ 127.0.0.1:8123（RTX 3060 Laptop 6GB，--max-model-len 2048 --gpu-memory-utilization 0.90 --enforce-eager）
- 两轮共享同一 payload：temperature=0（贪心解码，runner.py:40 硬编码）、max_tokens=128、同一提示词「用三句话解释 KV cache 为什么能让 LLM 推理变快。」
- 基准：concurrency=1, requests=8（results/invariance-2026-11-10/baseline-c1.json）
- 候选：concurrency=16, requests=8（results/invariance-2026-11-10/candidate-c16.json）
- 判据：ttftkit gate invariance 规则（against_baseline），逐字节比较捕获输出（results/invariance-2026-11-10/verdict.json）

## 实测数字

| 指标 | c=1 | c=16 |
|---|---|---|
| TTFT p99 (ms) | 82.93 | 170.05 |
| TPOT p99 (ms) | 39.46 | 40.26 |
| 输出与基准逐字节一致 | — | 8/8 |

闸检查明细：`8 outputs byte-identical to baseline`（PASS）；limits ttft_ms.p99=170.05<=2000（PASS）；tpot_ms.p99=40.26<=200（PASS）。

## 口径边界（诚实声明）

1. 本判定只覆盖**贪心解码**：temperature=0 时 token 选择与随机性无关，batch 不变才有理论预期。temperature>0 的采样路径受连续批处理下的 RNG 状态影响，不在本判定范围内（那是另一条更难的实验）。
2. 单提示词、单模型、单服务配置：样本量 8×2 轮，回答「这条服务配置上不变性是否成立」，不外推到全模型空间。
3. 连续批处理（c=16 > 理论最大并发 7.44×）下 chunked prefill 生效——不变性依然成立，说明 vLLM 0.33.0 的 prefill 调度没有扰动贪心解码路径。

复现：`PYTHONPATH=src python3 scripts/invariance_run.py`（需先按 vllm-serving-card README 起服务于 8123 端口）。
