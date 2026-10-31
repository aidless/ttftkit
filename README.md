# ttftkit

对 OpenAI 兼容推理端点做**可回归的性能测量**：TTFT / TPOT / 吞吐 / 稳定性 → JSON + Markdown 报告 → **发布门禁**（退出码 0/1/2，可直接接 CI）。

压测器不缺，缺的是「这次比上次慢了多少、慢到什么程度不许发」的**准入判定**。ttftkit 只做这件事，并且把每个数字的口径写在代码和文档里。

```bash
pip install -e .
ttftkit run --endpoint http://127.0.0.1:8000/v1/chat/completions \
    --model Qwen/Qwen2.5-7B-Instruct --requests 32 --concurrency 8 \
    --max-tokens 128 --profile rtx4090-vllm-0.6.3 --output results/run.json
ttftkit gate results/run.json --policy configs/policy.json --baseline results/baseline.json
```

## 为什么不用现成的

| 工具 | 强项 | ttftkit 的定位 |
| --- | --- | --- |
| `vllm bench serve` | 打满单机吞吐，指标全 | 引擎自带，换 SGLang/TGI 就要换一套命令 |
| `aiperf` / `genai-perf` | 大规模、贴近生产流量 | 重；判定仍要自己写脚本 |
| `llmperf` | 简单可用 | 无基线对比、无门禁语义 |

ttftkit 刻意做小：**只测一个端点、只做闭环并发、只输出可比对的报告**，然后把「能不能发」变成退出码。生态地图见 [`docs/ecosystem.md`](docs/ecosystem.md)。

## 指标口径（容易测错的地方）

| 指标 | 定义 | 坑 |
| --- | --- | --- |
| TTFT | 发出请求 → 首个非空 `delta.content` 事件 | 包含连接、排队、prefill；不是纯 prefill 时间 |
| TPOT | `decode_ms / (completion_tokens − 1)` | 用服务端 `usage` 的 token 数，**不是 SSE 块数**；缺 usage 时是 `null`，不是 0 |
| token 吞吐 | `Σ completion_tokens / 测量墙钟时长` | 分母是墙钟，不是各请求延迟之和；任一成功请求缺计数则整体 `null` |
| 错误率 | 失败请求 / 全部请求 | 失败请求不参与延迟统计，但**一定要进分母** |
| 稳定性 | `latency_ms.cv`、`degradation_ratio`（后 1/4 ÷ 前 1/4 延迟） | 后者专抓跑久了变慢（显存泄漏、KV 碎片、降频） |

一个 SSE 块可能含多个 token，所以块间隔只作为传输层观测记录（`chunk_gap_ms`），**不叫 ITL**。详细推导与对齐说明见 [`docs/metrics.md`](docs/metrics.md)。

## 门禁

策略文件支持两种规则，可同时使用：

```json
{
  "limits":      { "ttft_ms.p95": 800, "tpot_ms.p95": 60, "error_rate": 0.0 },
  "regressions": { "ttft_ms.p95": 10, "output_tokens_per_s": 5 }
}
```

- `limits`：绝对上限/下限，越界即 FAIL。
- `regressions`：相对基线允许的退化百分比（延迟类越小越好、吞吐类越大越好，方向内置）。
- 退出码：`0` 通过（可能带 WARN 贴边提示）｜`1` 有规则不通过、失败数超过预算（默认 0）、或缺指标｜`2` 策略/报告无效，或两次测量不可比。

**fail-closed 的地方**（这些都是踩过的坑，不是设计洁癖）：

- 指标缺失、`NaN`、`inf`、字符串 `"100"`、布尔 `true` → 不通过，绝不当 0 或当通过。
- 空策略 `{}`、未知指标名（拼错）→ 配置错误（`2`），否则等于什么也没检查。
- 相对比较要求 `protocol / model / concurrency / max_tokens / requests / prompt_sha256 / profile` 完全一致，否则两次不是同一个实验。
- 报告里 `requests` 为 0 或 `successful_requests > requests` → 无证据可判。
- 分布指标的 `count` 少于成功请求数（例如一半请求缺 usage 导致 TPOT 只算了一半）→ 不通过。
- **基线也要可判定**：baseline 缺 `successful_requests`、`requests=0`、或分布 `count` 不可用 → 配置错误（`2`），不能拿 1 个样本当基准。
- 失败预算：默认任何失败都拦；若策略写了 `limits.error_rate`，则按该预算判定（预算内的小比例失败放行）。

策略写法与取舍见 [`docs/gate.md`](docs/gate.md)。

## 接 CI

```yaml
- name: Benchmark
  run: |
    ttftkit run --endpoint "$ENDPOINT" --model "$MODEL" --requests 32 --concurrency 8 \
        --max-tokens 128 --profile "$PROFILE" --output results/run.json
- name: Release gate
  run: ttftkit gate results/run.json --policy configs/policy.json --baseline baselines/main.json
```

`run` 自身在**有请求失败时返回 1**，所以即使不配 `gate` 也不会把坏结果当成功。`gate` 则按策略里的失败预算判（默认 0，即任何失败都不放行）。

## 一次真实运行（本机 CPU，仅演示链路）

Ryzen 5800H / 无 GPU / ollama `qwen2.5-coder:0.5b`，3 请求、并发 1、`max_tokens=16`：

| 指标 | 值 |
| --- | --- |
| TTFT p50 | 84.6 ms |
| TPOT 均值 | 40.7 ms/token |
| token 吞吐 | 23.25 tok/s |
| token 计数覆盖率 | 1.0 |
| 错误率 | 0 |

这是**链路可用性演示**，不是任何模型的性能结论：样本 3 个、CPU 推理、无 GPU 参与。

## 局限（v0.1 明确不做）

- 没有 GPU 显存采样（`nvidia-smi`/DCGM 接入步骤见 [`docs/roadmap.md`](docs/roadmap.md)）。
- 闭环固定并发，不模拟泊松到达或真实流量分布；要那个用 `aiperf`。
- TPOT 是客户端近似：同一 SSE 块内的 token 时间无法拆分。
- 不做分布式多机压测，不测第三方付费 API 的绝对性能（网络抖动会污染结果）。

## 开发

```bash
python3 -m pytest -q          # 全量测试（含真实本地 SSE 服务）
python3 -m ruff check src tests
python3 scripts/red_check.py  # RED 反证：把修复回退后，对应测试必须变红
```

`red_check.py` 是变异检查的自动化版本：在临时副本上逐条回退修复（TTFT 取值时机、门禁边界、吞吐分母、token 口径、坏 `choices`、总时限、错误串回显、非字符串 `content`、残缺报告、warmup 退出码），跑对应用例并断言它**失败**。修复若只是摆设，这一步会变绿并报错。

## License

Apache-2.0
