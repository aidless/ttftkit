# 40 条独立 query 的采样分歧率（2026-11-10 起，tag=runE）

- 口径：temperature=0.7、seed=1234、max_tokens=128、每请求独立 prompt + seed+i，n=40 条**不同** query。
- 服务端配置声明：`{'VLLM_BATCH_INVARIANT': '0'}`。VLLM_BATCH_INVARIANT=1 时 batch 改变输出才是缺陷，=0（默认）时是设计行为（vLLM docs/features/batch_invariance.md），读数必须带着这一行才可归因。
- 判定：stability 闸，分歧率 ≤ 0.2 为 PASS；判词由 Wilson 95% 区间给出（上界 < cap = PASS 决定性，下界 > cap = FAIL 决定性）。
- baseline 跨跑逐字节可复现：**是**
- baseline c=1 TTFT：p50 176.85526449895406 ms / p99 292.5134721995527 ms（重跑 p50 164.84190700066392 / p99 269.7727920097168）。

| 候选 | 分歧/总数 | 分歧率 | Wilson 95% | 闸退出码 | 判词 |
| --- | --- | --- | --- | --- | --- |
| c4 | 2/40 | 0.050 | [0.014, 0.165] | 0 | PASS_DECISIVE |
| c16 | 2/40 | 0.050 | [0.014, 0.165] | 0 | PASS_DECISIVE |

## 逐 query 翻转槽位

- c4：[2, 3]
- c16：[25, 33]

## 口径边界

- 40 条是同一考点的不同问法，覆盖 KV cache / 连续批处理 / 服务指标 / 部署约束四类；
  分歧率因此是「该考点族」的量，不能外推到整个语料分布。
- 每次跑仍受服务状态影响；baseline 双跑一致只能排除「服务整体漂移」，不能排除冷启动对候选轮的影响。
- 判词只对 cap=0.2 这一档成立；换 cap 需重算区间，不能沿用判词。
