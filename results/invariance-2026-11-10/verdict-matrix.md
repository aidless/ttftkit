# invariance/stability 实弹矩阵（2026-11-10，c=4 补档）

| 路径 | baseline | candidate | 闸 | 状态 | 观测 | exit |
|---|---|---|---|---|---|---|
| greedy | c=1 | c=4 | invariance | **PASS** | 8 outputs byte-identical to baseline | 0 |
| greedy | c=1 | c=16 | invariance | **PASS** | 8 outputs byte-identical to baseline | 0 |
| sampled | c=1 | c=4 | stability | **FAIL** | 2/8 outputs diverge (rate 0.250) | 1 |
| sampled | c=1 | c=16 | stability | **PASS** | 1/8 outputs diverge (rate 0.125) | 0 |

## 延迟总表

| 路径 | 并发 | TTFT p99(ms) | TPOT p99(ms) |
|---|---|---|---|
| greedy | c=1 | 141.3 | 52.4 |
| greedy | c=4 | 583.2 | 48.1 |
| greedy | c=16 | 486.3 | 45.0 |
| sampled | c=1 | 121.4 | 47.3 |
| sampled | c=4 | 156.8 | 42.8 |
| sampled | c=16 | 145.5 | 54.8 |

## 口径

- vLLM 0.33.0 / Qwen3-4B-AWQ / enforce-eager / 单卡 RTX 3060 6GB
- 每档 8 请求 warmup 2，max_tokens=128，prompt 固定
- 贪心判 invariance（逐字节一致）；采样(temperature=0.7, seed=1234) 判 stability（分歧率 ≤ 0.2）
- 复现脚本：scripts/invariance_matrix_run.py

## 判决解读

1. **贪心全 PASS（8/8 逐字节一致 × 两档）**：c=4 补档后贪心路径的不变性结论从「单点 c=16」升级为「矩阵级」——RTX 3060 + vLLM 0.33.0 单卡连续批处理下，temperature=0 的输出与 batch 形态无关，invariance 闸可当硬验收闸用。
2. **采样轮 c=4 FAIL / c=16 PASS 不是矛盾，是小样本分歧率的抽样波动**：两档都是「少数输出分叉」（2/8 与 1/8），分叉是机制性的（浮点噪声被采样放大）；0.2 上限恰好卡在 1/8=0.125 与 2/8=0.25 之间，这一格的判决只说明「上限取 0.2 时该轮判红」，不说明 c=4 比 c=16 更不稳——n=8 时相邻档分歧率的标准误差 ~0.1，要区分档间差异得加样本量，不是加并发档。
3. **stability 闸的价值在 FAIL 的方向性**：它不逐字节较真（那是 invariance 的事），但把「分歧率超预算」显式报红并给出首分歧位置，供部署方按业务容忍度调 max_divergence_rate——闸给出的是可谈判的判决，不是二元断言。

## 与既有判决的关系
- verdict.md（贪心 c=1 vs c=16，PASS）与 verdict-sampled.md（采样 c=1 vs c=16，逐字节口径 FAIL 7/8）是本矩阵的前身；本轮把贪心口径补到 c=4 并给采样口径换上 stability 判据。
- 采样轮按逐字节 invariance 判仍是 FAIL（c=4: 6/8 一致，c=16: 7/8 一致）——逐字节一致在 temperature>0 下不是合理判据，这正是 stability 规则存在的理由。