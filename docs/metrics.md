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

## 7. 与其它工具的口径差异

| 工具 | 差异 |
| --- | --- |
| `vllm bench serve` | 同为**客户端视角**：它用 HTTP 请求压端点，TTFT 从客户端发出请求前起算，**含连接与网络**（与 ttftkit 同侧，可直接对照）。真正的「服务端视角、不含网络」是 vLLM 服务端暴露的 Prometheus 指标（如 `vllm:time_to_first_token_seconds`），那是引擎内部计时。细则差异：ttftkit 只认「首个非空 content 块」、缺 usage 即 `null` |
| `genai-perf` | 指标名不同（`time_to_first_token`、`inter_token_latency`），ITL 由它内部按 token 估算 |
| MLPerf Inference | 用 latency constraint 定义吞吐上限（满足 SLO 的最大 QPS），与 ttftkit 的 `goodput_per_s` 思路一致但规模不同 |

**不要**把 ttftkit 的客户端 TTFT 与引擎内部 profiler 的 prefill 时间相减得出「网络延迟」——两者时间基准不同，差值没有意义。

## 8. 不声称的东西

- 不声称测到了 GPU 计算利用率（v0.1 没有 GPU 采样）。
- 不声称 TPOT 等于逐 token ITL（块内 token 时间不可拆）。
- 不声称闭环并发等于线上流量（线上是到达率驱动，ttftkit 是固定 worker 闭环）。
- 不声称跨机器、跨驱动版本的绝对值可比（只做同环境回归）。
