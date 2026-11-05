# ttftkit

推理服务变慢时，缺的不是又一个压测器，而是「这次慢了多少、慢到什么程度不许发」的可复核判定。
ttftkit 对一个 OpenAI 兼容端点测量 TTFT、TPOT、吞吐和错误率，把策略文件变成退出码 0 / 1 / 2。
退出码 0 只表示这份报告满足策略里写明的规则；它不证明模型快、生产 SLO 成立，或服务端报的 token 数是真的。

本仓库没有 PyPI 发布，也没有下载量或徽章。安装只针对这份检出：

```bash
python3 -m pip install -e '.[dev]'
```

## 绿灯不证明什么

TTFT / TPOT 门禁通过，**不能**推出下面任何一条。机制和仍开着的洞写在 [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md)。

- 不是引擎内部的 prefill 时间。TTFT 从客户端发请求前算到首个非空 `delta.content`，含连接、排队和网络。
- 不是逐 token 的 ITL。TPOT = `decode_ms / (completion_tokens − 1)`，分母是服务端 `usage`，不是 SSE 块数。
- 不证明服务端的 `usage` 是真的。token 数报大，TPOT 会变小、吞吐会变高，门禁照样可以绿。
- 不证明这次测量发生在什么时候。报告里没有墙钟；`started_at` 是 `perf_counter`。
- 不证明 `summary` 是从 `records` 算出来的。门禁读 summary，不重算记录。
- 不证明和基线比过。只有策略里的 `regressions` 才会用基线的数字。`configs/policy.json` 只有 `limits`，传了 `--baseline` 也不比较。
- 不是线上流量。负载是固定 worker 的闭环并发，不是到达率。
- 不是 GPU 结论。仓库里没有一份真卡采样；`--gpu` 的验收用的是假采样器。
- 不是模型质量，也不是跨机器的排名。
- CI 里 `shadow-gate` 作业变绿，也不等于策略通过。该作业不带 `--strict`，脚本在 FAIL 时仍返回 0；它只检查「打印过逐条读数、输入读得动」。

## 两条路径

### 离线（CI 跑的就是这条，不需要模型）

测试在 `127.0.0.1` 上起一个进程内的 SSE 假服务（`tests/conftest.py`），不连接 Ollama / vLLM。

```bash
python3 -m pytest -q
python3 -m ruff check src tests scripts
python3 scripts/red_check.py
python3 scripts/shadow_gate.py --report baselines/run1.json \
  --baseline baselines/main.json --policy configs/policy.baseline.json --strict
```

`pytest`、`ruff`、`red_check` 不需要网络上的推理服务。`shadow_gate` 读的是仓库里已有的 JSON。不带 `--strict` 时，脚本在规则失败时也返回 0。

### 在线（要你自己先起一个 OpenAI 兼容服务）

CI 不会启动 vLLM 或 Ollama。下面的命令只有在 `$ENDPOINT` 已经在听的时候才有意义。

```bash
python3 -m ttftkit run --endpoint "$ENDPOINT" --model "$MODEL" \
  --requests 8 --concurrency 1 --max-tokens 16 --gpu none \
  --profile "$PROFILE" --output results/run.json
# 绝对上限。configs/policy.json 的 1000 / 100 是未校准的示例，不是 baselines/ 推出来的。
python3 -m ttftkit gate results/run.json --policy configs/policy.json
```

相对基线要用 `configs/policy.baseline.json`，而且候选报告的 `protocol / model / concurrency / max_tokens / requests / prompt_sha256 / profile` 必须和基线完全一致，否则退出码是 2。`baselines/main.json` 只和同目录那 5 次 CPU 跑法是同一个实验。拿一份别的模型、别的并发去配这份基线，比的不是同一次实验。

## OBSERVED 与 inferred

| 说法 | 标签 | 依据 |
| --- | --- | --- |
| 退出码 0 / 1 / 2 的分支、缺指标不当 0、空策略是配置错误 | OBSERVED | `src/ttftkit/gate.py` 与 `tests/test_gate*.py`；回退由 `scripts/red_check.py` 抽查 |
| `baselines/run1.json`–`run5.json` 的表内数字；`main.json` 与 TTFT p50 中位那一次相同 | OBSERVED | 文件在仓库里；`tests/test_doc_drift.py` 重算，不另抄一份数 |
| `results/smoke-ollama.json` 的 TTFT p50 **84.61**、TPOT 均值 **40.72**、吞吐 **23.25** | OBSERVED | 同一文件的 `summary`。该文件无 `hardware` 键，summary 也没有后来补上的三个显存字段（当前 `summarize()` 会把它们写成 `null`） |
| 上述 JSON 的 `summary` 与 `records` 一致 | OBSERVED | `test_checked_in_summaries_match_records` 用当前公式重算 |
| 采集机是 Ryzen 7 5800H、14 GiB、无 GPU、Ollama 0.33.3、日期 2026-10-31 | inferred（作者记录） | 这些字段不在 JSON 里。JSON 里有的是 `config.model = qwen2.5-coder:0.5b`、`config.profile` 那串名字、`config.hardware = "none"`。CI 不重跑 Ollama |
| 生态文档里的 star / license | OBSERVED 快照 | [`docs/ecosystem.md`](docs/ecosystem.md) 写明核实日 2026-10-31。不是 ttftkit 的采用量，这次也没有重拉 |
| `tests/test_hardware.py` 里的 RTX 3060 字符串 | 手写夹具 | 不是 nvidia-smi 日志，也不是性能数字 |

5 次基线里 TTFT p95 极差 **143.3%**、p50 **37.8%**、token 吞吐 **46.6%**（定义在 [`baselines/README.md`](baselines/README.md)）。所以 `configs/policy.baseline.json` 的相对容差是 50%–60%：通过只说明落在这次 CPU 小样本的噪声里，不说明服务快。`tests/test_baseline_policy.py` 检查这 5 份报告相对该策略为退出码 0，并把人为放大的 TTFT、腰斩的吞吐、以及一次失败请求判为 1。

## 指标口径

| 指标 | 定义 | 坑 |
| --- | --- | --- |
| TTFT | 发出请求 → 首个非空 `delta.content` | 含连接、排队、prefill；不是纯 prefill |
| TPOT | `decode_ms / (completion_tokens − 1)` | 用服务端 `usage`；缺 usage、少于 2 个 token、或只有 1 个内容事件时是 `null`，不是 0，也不用块数顶上 |
| token 吞吐 | `Σ completion_tokens / 测量墙钟` | 分母是墙钟，不是延迟之和；任一成功请求缺计数则整体 `null` |
| 错误率 | 失败请求 / 全部请求 | 失败请求不进延迟分布，但留在分母里 |
| 稳定性 | `latency_ms.cv`；`degradation_ratio` = 后 1/4 ÷ 前 1/4 延迟 | 后者要至少 4 个成功请求，否则 `null`。它和「机器当时很忙」混在一起 |
| 显存增长 | `mem_used_peak_mb − mem_used_baseline_mb` | 没采到是 `null`，不是 0 |

一个 SSE 块可以含多个 token。块间隔记在 `chunk_gap_ms`，不叫 ITL。推导在 [`docs/metrics.md`](docs/metrics.md)。

## 门禁

```json
{
  "limits":      { "ttft_ms.p95": 800, "tpot_ms.p95": 60, "error_rate": 0.0 },
  "regressions": { "ttft_ms.p95": 10, "output_tokens_per_s": 5 }
}
```

上面这段是规则的形状，**不是**本仓库的校准结果。校准过的那份是 `configs/policy.baseline.json`，只适用于上面那 5 次 CPU 跑法。

- `limits`：延迟和错误率是上限，吞吐类是下限。
- `regressions`：相对基线允许的退化百分比。延迟类越小越好，吞吐类越大越好。
- 退出码：`0` 通过（可以带 WARN）｜`1` 规则不通过、失败数超预算、或缺指标｜`2` 策略或报告无效，或两次测量不可比。
- 缺指标、`NaN`、`inf`、字符串、布尔：不通过。空策略、未知指标名：退出码 2。
- 失败预算默认是 0。要放行偶发失败，必须把 `limits.error_rate` 写出来。

细节在 [`docs/gate.md`](docs/gate.md)。显存采样的边界在 [`docs/hardware.md`](docs/hardware.md)：设备级轮询，同卡上别的进程会混进 `delta`，间隔内的尖峰可以漏掉。真卡读数还没有进这个仓库。

## 计数

`tests/test_doc_drift.py` 用源码和 JSON 复核下表。改了测试、CI 或基线数字而不改这里，测试会失败。

| 项 | 值 |
| --- | --- |
| red_check 用例 | 32 |
| pytest 收集 | 205 |
| CI jobs | tests, shadow-gate |
| CI Python | 3.10, 3.12 |
| red_check 的 CI Python | 3.12 |

job `tests` 在 3.10 和 3.12 上做 `pip install -e '.[dev]'`、`ruff check src tests scripts`、`pytest`、以及 `pip wheel --no-deps`。`red_check.py` 只在 3.12 跑。job `shadow-gate` 用 Python 3.12、`pip install -e .`（没有 dev 依赖），对 `baselines/run1.json`–`run5.json` 跑影子脚本（策略是 `configs/policy.baseline.json`），并要求输出里至少有一条 `[PASS]` 或 `[FAIL]`。没有发布作业。

学习用的 8 个实验在 [`docs/labs.md`](docs/labs.md)。和别的压测工具的差别在 [`docs/ecosystem.md`](docs/ecosystem.md)，那边的 star 是别人的仓库、而且是快照。

## License

Apache-2.0
