# 指标口径

本文是 ttftkit 的「测量契约」。代码与文档冲突时以本文为准，并当作文档 bug 修代码或修文档。

## 1. 时间轴

一次流式请求的时间轴：

```
t0 ────────────────► t_first ────────────────► t_last ────────────────► t_end
   |<── TTFT ─────>|            |<── decode ──>|         |<── tail ───>|
   |<────────────────── latency ────────────────────────────────────────>|
```

| 符号 | 含义 | ttftkit 字段 |
| --- | --- | --- |
| t0 | 发请求前的一刻（`perf_counter()`） | `started_at` |
| t_first | 首个**非空** `delta.content` 事件到达 | 由 `ttft_ms` 反推 |
| t_last | 最后一个非空 content 事件到达 | 由 `decode_ms` 反推 |
| t_end | 流结束（`[DONE]` 或 `finish_reason` 之后） | — |

- `ttft_ms = t_first − t0`：**包含** DNS、TCP、TLS、排队、prefill、首 token 采样。它不是纯 prefill 时间，不能直接和引擎内部 profiler 的 prefill 耗时对比。
- `decode_ms = t_last − t_first`：解码段（首 token 之后到末 token）。
- `latency_ms = t_end − t0`：请求总耗时，含收尾（usage 块、`[DONE]`）。

## 2. TPOT

```
TPOT = decode_ms / (completion_tokens − 1)
```

**用服务端 `usage.completion_tokens`，不用 SSE 块数。** 原因：服务端可以把多个 token 塞进一个 SSE 块（尤其带 `stream_options.include_usage` 或投机解码时），块数会系统性低估 token 数，把 TPOT 算大。

边界：

| 情况 | 结果 |
| --- | --- |
| `completion_tokens ≥ 2` 且至少 2 个内容事件 | 正常计算 |
| `completion_tokens == 1` | `null`（单个 token 没有「每 token 时间」） |
| 服务端没返回 usage | `null`（不是 0，也不是用块数顶替） |
| 只有 1 个内容事件 | `null`（无法确定首末间隔） |

`null` 会一路传到报告与门禁：门禁把「指标缺失」判为不通过，而不是当作满足阈值。

## 3. 吞吐

```
token 吞吐      = Σ completion_tokens(成功请求) / 测量墙钟时长
请求吞吐        = 成功请求数 / 测量墙钟时长
goodput         = 满足 SLO 的成功请求数 / 测量墙钟时长
```

三个分母都是**测量窗口的墙钟时长**（第一个请求发出 → 最后一个请求结束），不是各请求延迟之和。用延迟之和作分母会把并发重叠的时间重复计入，分母偏大，算出来的是**被低估**的吞吐（不是虚高）；「延迟之和」只有在串行、无并发时才等于墙钟。

**缺计数即不可信**：如果一部分成功请求没有 `completion_tokens`，`output_tokens_per_s` 整体返回 `null`，同时用 `token_usage_coverage` 报出覆盖率（有计数的成功请求 / 成功请求）。不拿部分样本冒充整体。

## 4. 错误率与失败分类

```
error_rate = 失败请求数 / 全部请求数
```

失败请求不参与任何延迟分布（否则超时会把 p99 拉爆，看起来「只是慢」），但**必须留在分母里**——只统计成功请求的错误率是自欺。

失败分类（`summary.errors`，按错误前缀聚合）：

| 分类 | 触发 |
| --- | --- |
| `HTTP 5xx` / `HTTP 4xx` | 状态码 ≥ 400（只记状态码，不记响应体，避免回显 prompt 或凭据） |
| `timeout` | 超过 `--timeout` |
| `invalid json` | `data:` 行不是合法 JSON |
| `invalid event` | 事件不是对象（如 `[]`、`null`），或 `choices` 结构不对 |
| `invalid usage` | `completion_tokens`/`prompt_tokens` 不是非负整数（含字符串、布尔、负数） |
| `unsuccessful finish_reason` | `content_filter` 等非正常结束 |
| `stream ended without finish_reason` | 既没 `finish_reason` 也没 `[DONE]`，连接被截断 |
| `empty content` | 整个流没有任何非空 content |

## 5. 稳定性

| 指标 | 定义 | 抓什么 |
| --- | --- | --- |
| `latency_ms.cv` | 延迟标准差 / 均值（由 `distribution()` 产出，挂在 `latency_ms` 字典里，不是顶层字段） | 抖动大小（含长尾） |
| `degradation_ratio` | 后 1/4 请求的平均延迟 ÷ 前 1/4 | 跑久了变慢：显存泄漏、KV 碎片、GPU 降频、连接池退化 |

`degradation_ratio` 需要至少 4 个成功请求才有定义，否则 `null`。它和「CPU 负载波动」会混在一起，所以只在**同一台机器、同一并发、同一时长**下比较才有意义。

## 6. 分位数

线性插值（与 numpy `method="linear"` 一致）：`pos = q·(n−1)`，在相邻两点间按权重插值。单元素返回自身。

样本量提示：`p99` 在 32 个请求上只是「第 31.7 个」的插值，不构成 99 分位估计。**样本少于 100 时，报告只应引用 p50/p95**，并在结论里写清样本数。这条不是实现限制，是统计事实。

### 6.1 置信区间（v0.3）

点估计不告诉你「换个样本会差多少」。报告给出的 `ci` 是 **percentile bootstrap**：按原样本重采样 1000 次（n > 500 时降到 200），取重采样分布的 2.5% / 97.5% 作为 95% 区间。

- **种子固定为 0**，迭代数写进结果（`ci.seed` / `ci.iterations`）——否则同一份 JSON 每次跑出的门禁判定会变（`docs/papers.md` B2）。
- **样本少于 20 条不给区间**（`ci: null`）。8 条记录的 p95 实际上就是最大值，给它套一个 ±x ms 的区间是假精确。
- 区间是「分位数本身的不确定性」，不是「下一次测量的预测区间」；别拿它当 SLO 缓冲量。

自测：`PYTHONPATH=src python3 -m pytest tests/test_metrics_ci.py -q`。

## 7. 与其它工具的口径差异

| 工具 | 差异 |
| --- | --- |
| `vllm bench serve` | 同为**客户端视角**：它用 HTTP 请求压端点，TTFT 从客户端发出请求前起算，**含连接与网络**（与 ttftkit 同侧，可直接对照）。真正的「服务端视角、不含网络」是 vLLM 服务端暴露的 Prometheus 指标（如 `vllm:time_to_first_token_seconds`），那是引擎内部计时。细则差异：ttftkit 只认「首个非空 content 块」、缺 usage 即 `null` |
| `genai-perf` | 指标名不同（`time_to_first_token`、`inter_token_latency`），ITL 由它内部按 token 估算 |
| MLPerf Inference | 用 latency constraint 定义吞吐上限（满足 SLO 的最大 QPS），与 ttftkit 的 `goodput_per_s` 思路一致但规模不同 |

**不要**把 ttftkit 的客户端 TTFT 与引擎内部 profiler 的 prefill 时间相减得出「网络延迟」——两者时间基准不同，差值没有意义。

## 9. 客户端事件循环延迟（v0.3）

ttftkit 是单进程 asyncio 客户端，所以**压测机自己也会排队**：GIL、事件循环、DNS 解析、TLS 握手都在同一个线程上。高并发下这部分延迟会被算进 TTFT/TPOT，看起来像「服务端变慢了」（`docs/papers.md` A1）。

测量方式：测量窗口内每 10 ms 醒一次，记 `实际唤醒时刻 − 应唤醒时刻`（下限截到 0），报 `p50/p95/max` 与采样次数。

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| `client.lag_ms.p95_ms` | 客户端自身排队 + 被阻塞的时间 | 判断这一轮数字有多可信 |
| `summary.client_lag_p95_ms` | 同上（进门禁的标量） | 用它做上限规则，别让压测机成为瓶颈 |

纪律：

- **它是「测量上界」的证据，不是服务端性能**。`lag_p95 > 5% × TTFT p95` 时报告会打警告，这时该降并发或用多进程客户端，而不是去优化服务端。
- **一个样本都没采到**（测量窗口短于 10 ms 间隔）时 `lag_ms` 为 `null`，报告写「未取到 lag 样本」。**null 不是 0**：0 会被读成「客户端完全没排队」。
- 门禁侧 `client_lag_p95_ms` 必须能追溯到 `client` 块（`gate.py` 的 `_check_client_evidence`），手写一个数字进 summary 会 exit 2。

## 10. 不声称的东西

- 不声称测到了 GPU **计算**利用率：v0.2 采的 `utilization.gpu` 是采样窗口内的繁忙比例，**单个数压掉了「在算什么」**（decode 的小行 GEMM 填不满 SM，见 `docs/papers.md` A3）。要判效率得看 profiler 计数器，不是这一个数。
- 不声称客户端 lag 只来自 ttftkit：同一台机器上别的进程也在抢 CPU（这是「上界」的另一半）。
- 不声称 TPOT 等于逐 token ITL（块内 token 时间不可拆）。
- 不声称闭环并发等于线上流量（线上是到达率驱动，ttftkit 是固定 worker 闭环）。
- 不声称跨机器、跨驱动版本的绝对值可比（只做同环境回归）。
