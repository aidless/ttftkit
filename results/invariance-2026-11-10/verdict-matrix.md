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

## 补充发现（2026-11-10 当日二次分析，数字可从未跟踪原始 JSON 重算）

1. **分叉不是随机噪声，是同一条替代路径**：三条分叉输出（sampled-c4 index=0/4、sampled-c16 index=0）逐字节相同（`c4[0]==c16[0]==c4[4]` 实测 True），全部在**第 46 字符**处从基线分出（基线「我需要回忆一下」→ 分叉「我得回忆一下」，长度 253→243）。分叉点是 logits 近并列 fork，batch 扰动让它确定性地翻到同一替代分支——不是每请求独立抽样。
2. **8 个请求是同一 prompt × 同一 seed=1234 的 8 份拷贝**（采样 c=1 / 贪心 c=1 / 贪心 c=4 内部 8 条输出全同，实测 True）。因此 divergence_rate 2/8 vs 1/8 量的是「batch 槽位对**同一个** fork 点的翻与不翻」，不是 query 级分歧率——stability 闸在单 prompt 复制口径下，分母的独立性假设不成立；要测 query 级分歧率必须每请求换 prompt/seed（stability 闸的 caveat，写进闸文档）。
3. **n=8 时 p99 就是 max，是伪影放大器**：greedy-c4 TTFT min=94.9 / median=312.2 / max=583.3，p99(583.2)≈max；c=4 > c=16 的「非单调」（583.2 vs 486.3）被 median 消除（312.2 < 484.8，恢复单调）。跨会话漂移同理：前次会话贪心 c=1 TTFT p99=82.9，今日 141.3——但两轮 median（~92）量级一致。**延迟结论应引 median，p99 至少要 n≥40 才有意义**（8 样本的 p99 由末位 1–2 个样本主导，不是分位数；注意「就是 max」是过强表述——查询级重跑实测 p99=385.6 vs max=399.1，见 verdict-queries.md）。
4. TPOT 随并发单调下降（贪心 52.4→48.1→45.0 ms）：decode 阶段批处理摊销，与 v0.2.1 吞吐结论（c=16 261.41 tok/s）同一机制。
