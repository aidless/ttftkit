# 40 条独立 query × 两个 flag 值：batch 不变性开关到底改变了什么

日期：2026-11-17
机器：RTX 3060 Laptop 6GB（sm_86）/ vLLM 0.33.0 / Qwen3-4B-AWQ / `--enforce-eager`
口径：`temperature=0.7, seed=1234, max_tokens=128`，每请求独立 prompt + `seed+i`，n=40 条**不同** query
闸：stability，`cap=0.2`，判词由 Wilson 95% 区间给出（上界 < cap = 决定性 PASS，下界 > cap = 决定性 FAIL）

## 结论一：flag=1 的决定性 PASS（上游承诺在这台机器上成立）

| 组 | c=4 分歧 | Wilson 95% | c=16 分歧 | Wilson 95% | 判词 |
| --- | --- | --- | --- | --- | --- |
| `VLLM_BATCH_INVARIANT=0`（默认） | 2/40 = 0.050 | [0.014, 0.165] | 2/40 = 0.050 | [0.014, 0.165] | 两组都 `PASS_DECISIVE` |
| `VLLM_BATCH_INVARIANT=1` | **0/40** = 0.000 | [0.000, **0.088**] | **0/40** = 0.000 | [0.000, **0.088**] | 两组都 `PASS_DECISIVE` |

flag=1 的 0/40 把总体分歧率上界压到 0.088（< 0.2），这是**决定性**读数，不是「没测到所以放过」。
它回答的是一个上游没测过的组合：消费级 Ampere（sm_86，不在 tuned matmul 表里）+ AWQ 量化 +
`--enforce-eager`。结论：结构性保证在未调优路径上仍然成立。

## 结论二：我的预注册没命中，登记在此（不许事后改判据）

跑前锁定的是「flag=0 组复现 3/8~4/8 量级 → 期望 15/40 → `FAIL_DECISIVE`」。实测 **2/40**，
方向相反。原因清楚：8 条 query 的 3/8 = 0.375 是小样本点估计，Wilson 当时给的区间 [0.137, 0.694]
本来就判不了，我却把它当成了「总体率约 0.375」去做外推。**这正是预注册要暴露的错**：判据没改，
读数照实写，结论按实测走。

## 结论三：代价是 2.4–2.7× 解码时间（实测量化，不是文档转述）

| 档 | flag=0 吞吐 | flag=1 吞吐 | 变化 | TPOT p50 | TTFT p50 |
| --- | --- | --- | --- | --- | --- |
| c=1 | 15.4 tok/s | 5.7 tok/s | −63% | 61.1 → 175.2 ms | 176.9 → 192.2 ms（+9%） |
| c=4 | 60.4 tok/s | 22.4 tok/s | −63% | 63.3 → 176.4 ms | 241.0 → 349.5 ms |
| c=16 | 180.3 tok/s | 75.5 tok/s | −58% | 68.3 → 174.2 ms | 479.5 → 461.4 ms（持平） |

上游文档说「开启会损性能，是刻意取舍」——这里是它的具体数字：**TPOT 约 2.7×，TTFT 基本不动**
（因为是解码侧矩阵乘被换成固定归约顺序的 kernel）。整轮墙钟 14.5 min → 38 min（2.6×）。

## 结论四：flag 真的生效了（运行时证据，不是静态探针）

`vllm/sampling_params.py:606` 的这条警告只在 `envs.VLLM_BATCH_INVARIANT` 为真时触发：

```python
if (envs.VLLM_BATCH_INVARIANT and self.temperature >= _SAMPLING_EPS and self.seed is None):
    logger.warning_once("Random sampling without an explicit seed may not be batch invariant. ...")
```

差分见证：flag=1 的服务日志里出现 1 条该警告，flag=0 的日志里 0 条
（`results/serve-flag1.log`、`results/serve-flag0.log`）。所以「我传了环境变量」与「代码真的走了那条分支」
在这里是两件事，我取的是后者。

## 口径边界（别把这张表用过头）

- 40 条 query 是同一考点族（KV cache / 连续批处理 / 服务指标 / 部署约束）的不同问法，分歧率是这个族上的量。
- 每组单跑一次；baseline 双跑逐字节一致只排除「服务整体漂移」，不排除冷启动对候选轮的影响。
- **「哪条 query 会翻」不是稳定事实**：8 条那次是 q0/q2/q3，这次 c=4 是 [2,3]、c=16 是 [25,33]。
  能说的是「翻转稀疏（≈5% 量级）且依赖 batch 形态」，不能说是「某几条 query 的问题」。
- 判词只对 cap=0.2 成立；换 cap 要重算区间，不能沿用判词。

## 复现

```bash
export LD_LIBRARY_PATH="$(ls -d $HOME/.local/lib/python3.12/site-packages/nvidia/*/lib | tr '\n' ':')"

# flag=0（默认）
VLLM_USE_FLASHINFER_SAMPLER=0 ~/venvs/vllm/bin/vllm serve /home/liuzewen/models/Qwen3-4B-AWQ \
  --served-model-name Qwen3-4B-AWQ --host 127.0.0.1 --port 8123 --max-model-len 2048 \
  --gpu-memory-utilization 0.90 --enforce-eager --disable-log-requests
SERVER_ENV=VLLM_BATCH_INVARIANT=0 PYTHONPATH=src python3 scripts/invariance_queries40_run.py runE

# flag=1（同一台机、同一个模型，只改一个环境变量）
VLLM_BATCH_INVARIANT=1 VLLM_USE_FLASHINFER_SAMPLER=0 ~/venvs/vllm/bin/vllm serve ...（其余同上）
SERVER_ENV=VLLM_BATCH_INVARIANT=1 PYTHONPATH=src python3 scripts/invariance_queries40_run.py runF
```

原始报告：`baseline-c1a/c1b-queries40-run{E,F}.json`、`candidate-c4/c16-queries40-run{E,F}.json`、
`verdict-queries40-run{E,F}.{json,md}`；服务日志 `results/serve-flag{0,1}.log`。
