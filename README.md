# ttftkit

[![CI](https://github.com/aidless/ttftkit/actions/workflows/ci.yml/badge.svg)](https://github.com/aidless/ttftkit/actions/workflows/ci.yml)

对 OpenAI 兼容推理端点做**可回归的性能测量**：TTFT / TPOT / 吞吐 / 稳定性 → JSON + Markdown 报告 → **发布门禁**（退出码 0/1/2，可直接接 CI）。

压测器不缺，缺的是「这次比上次慢了多少、慢到什么程度不许发」的**准入判定**。ttftkit 只做这件事，并且把每个数字的口径写在代码和文档里。

v0.2 加了两块：**GPU 显存/利用率采样**（`--gpu auto|none|command`，验收标准是「注入已知泄漏必须看得见单调上涨」）与**影子门禁**（先在 CI 里只留读数、不拦人，观察误报后再收紧）。想按 JD 一条条练，见 [`docs/labs.md`](docs/labs.md)。

```bash
# 现已上架 PyPI：
pip install ttftkit
# 或装最新版：pip install -U ttftkit
ttftkit run --endpoint http://127.0.0.1:8000/v1/chat/completions \
    --model Qwen/Qwen2.5-7B-Instruct --requests 32 --concurrency 8 \
    --max-tokens 128 --profile rtx4090-vllm-0.6.3 --output results/run.json
ttftkit gate results/run.json --policy configs/policy.json --baseline baselines/main.json
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
| 显存增长 | `mem_used_peak_mb − mem_used_baseline_mb` | 没采到样时是 `null`，**不是 0**；`0 MB` 会被读成「测到了、没占显存」 |
| 客户端 lag | 测量窗口内事件循环的唤醒延迟 p50/p95/max | 单进程 asyncio 客户端的**自身排队**会混进 TTFT/TPOT；它是「测量上界」的证据，不是服务端性能 |
| 分位数置信区间 | p50/p95 的 percentile bootstrap 95% 区间（种子固定 0） | 样本 < 20 条时给 `null`；8 条记录的 p95 其实就是最大值，套区间是假精确 |

一个 SSE 块可能含多个 token，所以块间隔只作为传输层观测记录（`chunk_gap_ms`），**不叫 ITL**。详细推导与对齐说明见 [`docs/metrics.md`](docs/metrics.md)。

## 门禁

策略文件支持三种规则，可同时使用：

```json
{
  "limits":      { "ttft_ms.p95": 800, "tpot_ms.p95": 60, "error_rate": 0.0 },
  "regressions": { "ttft_ms.p95": 10, "output_tokens_per_s": 5 },
  "invariance":  { "against_baseline": true }
}
```

- `limits`：绝对上限/下限，越界即 FAIL。
- `regressions`：相对基线允许的退化百分比（延迟类越小越好、吞吐类越大越好，方向内置）。
- `invariance`（batch 不变性，v0.4）：要求 `--baseline`，candidate 与 baseline 两份报告的捕获输出**逐字节一致**。基准跑 `concurrency=1`，候选跑目标 batch；跑基准/候选时都要 `capture_outputs=True`（报告里才有 `outputs`）。可比性只要求 `protocol / model / max_tokens / prompt_sha256` 一致——`concurrency / requests` 是 batch 形态本身，允许不同。任何一侧缺 `outputs` 都是「没测」（`2`），不是通过。

> **实弹判决（2026-11-10）**：RTX 3060 6GB / vLLM 0.33.0 / Qwen3-4B-AWQ，c=1 与 c=16 的 8/8 输出逐字节一致（贪心解码口径），闸 PASS。判决书与原始报告见 [results/invariance-2026-11-10/](results/invariance-2026-11-10/verdict.md)，复现脚本 [scripts/invariance_run.py](scripts/invariance_run.py)。

> **采样解码支持（v0.3.0 后）**：`run` 新增 `--temperature/--top-p/--seed`（默认 temperature=0 贪心，与历史行为一致）。采样参数进报告 `config`，gate 的可比性检查会把「不同采样参数」的报告判为不可比——不同解码路径的报告放一起比延迟是假比较。
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

## SLO 负载扫描（v0.3）

「满负载下多少 tok/s」是个容易误导人的数：牺牲延迟换来的吞吐也会被算进去。`scan` 反过来问——**在延迟/错误率约束下，能撑到多少负载**（SLO-qualified goodput，见 [`docs/papers.md`](docs/papers.md) C1）。

```bash
ttftkit scan --endpoint http://127.0.0.1:8000/v1/chat/completions --model Qwen/Qwen2.5-7B-Instruct \
    --levels 1,2,4,8,16 --slo-ttft 800 --slo-tpot 60 --slo-error-rate 0.0 \
    --requests 16 --max-tokens 128 --profile rtx4090-vllm-0.6.3 --output results/scan.json
```

输出：逐级一行（并发 / 是否满足 / req/s / tok/s / TTFT p95 / TPOT p95 / 违反了哪条），加一句「满足 SLO 的最高负载」。三条纪律：

1. **违反的那一级留在报告里**（`stopped_at` + `violations`）——只报好看的数字等于自欺。
2. **一级违反即停**，继续往上扫只会把超时和崩溃混进数据。
3. **没有证据不算通过**：某一级缺 p95（`null`）记为「不满足」，不当作默认达标。

`scan` 报告**不能直接过门禁**（`kind: "scan"` → 退出码 `2`）：它是多级聚合，请挑一级的 `levels[i].summary` 当单次测量再判。

## 显存与 GPU 采样（v0.2）

```bash
# auto：探测 nvidia-smi → rocm-smi，都没有就不采样（报告里明写「未采样」）
ttftkit run --endpoint ... --model ... --gpu auto
# 自定义采样命令（stdout 须是 nvidia-smi 的 CSV 格式）
ttftkit run --endpoint ... --model ... --gpu command \
    --gpu-command "nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv,noheader,nounits"
```

报告新增 `hardware` 段（来源、设备、驱动、`mem_used_baseline_mb` / `mem_used_peak_mb` / `mem_used_delta_mb`、峰值利用率、采样次数与错误数），
summary 里对应三个标量，可直接写进门禁：

```json
{ "limits": { "mem_used_delta_mb": 512 }, "regressions": { "mem_used_peak_mb": 10 } }
```

显存的相对回归要求两侧报告 `config.hardware` 指纹一致（`"设备名/总量MB"`），否则判**不可比**（退出码 `2`）——
24 GB 卡上的数字不能拿去和 6 GB 卡比。细节、三层验证与边界见 [`docs/hardware.md`](docs/hardware.md)。

## 基线来自实测，不是拍的

`baselines/` 里是同一台机器（Ryzen 5800H、CPU-only、ollama `qwen2.5-coder:0.5b`）连跑 5 次的结果，
`baselines/main.json` 取其中 TTFT p50 最接近中位数的那一次。5 次之间的自变异写在 [`baselines/README.md`](baselines/README.md)：
TTFT p95 极差 **143.3%**、p50 37.8%、吞吐 46.6% —— 所以门槛不能按「退化 10% 就拦」拍，`configs/policy.baseline.json` 的阈值就是从这张表推出来的。

CI 里先用**影子模式**（`scripts/shadow_gate.py` 不带 `--strict` 时恒返回 0，只打印逐条 PASS/FAIL/WARN），
观察一段时间误报率再改 `--strict` 上强制门。

## 接 CI

```yaml
- name: Benchmark
  run: |
    ttftkit run --endpoint "$ENDPOINT" --model "$MODEL" --requests 32 --concurrency 8 \
        --max-tokens 128 --profile "$PROFILE" --output results/run.json
- name: Release gate
  run: ttftkit gate results/run.json --policy configs/policy.json --baseline baselines/main.json
# 或者先用影子门禁：只记录读数，不阻断
# SLO 扫描（可选）：只记录，不比绝对值
- name: SLO scan
  run: ttftkit scan --endpoint "$ENDPOINT" --model "$MODEL" --levels 1,2,4,8 \
      --slo-ttft 800 --profile "$PROFILE" --output results/scan.json
- name: Shadow gate (non-blocking)
  continue-on-error: true
  run: python scripts/shadow_gate.py --report results/run.json --baseline baselines/main.json --policy configs/policy.baseline.json
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

> **复算**：上表每个数字都来自 `results/smoke-ollama.json`（schema 里 `summary` 段），
> 用 `python3 -c "import json;d=json.load(open('results/smoke-ollama.json'));print(d['summary']['ttft_ms']['p50'], d['summary']['tpot_ms']['mean'], d['summary']['output_tokens_per_s'])"`
> 即可逐项重取（预期 84.61 / 40.72 / 23.25）。触发该次运行的命令见同目录 `results/README.md`。
> 注意：这是 CPU + 3 样本的一次演示，**不构成任何模型性能结论**。

## 证明什么 / 不证明什么

**证明（全部有源码 + 测试 + 可复算证据支撑）**

- 这套测量与门禁链路**是闭环、可运行、可判定的**：`run` → 报告 → `gate` → 退出码 `0/1/2`，
  在真实本地 SSE 服务上跑通（`tests/` 含真实本地 SSE 服务用例）。
- 门禁**fail-closed**：指标缺失/`NaN`/`inf`/字符串/布尔 → 不通过；空策略/未知指标名 → 配置错误(`2`)；
  相对比较要求协议/模型/并发/token/请求数/prompt 哈希/指纹完全一致，否则不可比。
- 已知退化**会被拦住**：`scripts/red_check.py` 逐条回退 41 处修复，对应用例全部变红（41/41），
  证明这些判定不是摆设。
- **SLO 扫描与客户端污染可判定**：`scan` 违反即停且保留坏级别、`null` 不当通过；`client.lag` 能测到人为阻塞（`time.sleep(0.08)` → `max_ms ≥ 30`），且门禁拒绝「summary 声称 lag 但没有 `client` 块」。
- 硬件块**自洽且可溯源**：`delta == peak − baseline`、负数/超设备总量 → 配置错误、
  `config.hardware` 必须等于用块重算的指纹（防改块绕过可比性门禁）。
- 基线与阈值**来自同机 5 次实测**，不是拍的：`baselines/README.md` 给出 5 次全表 + 复现命令，
  `tests/test_baseline_policy.py` 验证 5 次真实跨跑全 PASS、人为退化全 FAIL（双向，非装饰）。

**不证明（这些是边界，不要过度解读）**

- **真 GPU 读数尚未实测**：本机没有 NVIDIA 用户态库/设备，采样链路只用假采样器（`scripts/fake_gpu.py` 注入已知泄漏）验证过三层。真卡（nvidia-smi / rocm-smi）读数需要在云 GPU 上跑通再写进本 README，不发布未实测数字。
- 不构成**任何模型的性能结论**：README 里的演示样本（CPU、3 请求、ollama 0.5b）只证明链路可用，不证明模型快慢。
- 不做**模型横向比较**：单机、单模型、固定配置，换了配置/机器就不是同一个实验（可比性门禁会判 `2`）。
- 显存是**设备级 + 轮询**：同卡其他进程的变化会混进 `delta`，间隔内的单次尖峰可能被漏掉（见 [`docs/hardware.md`](docs/hardware.md)）。
- 闭环固定并发，**不模拟泊松到达或真实流量分布**；要那个用 `aiperf`。
- TPOT 是**客户端近似**：同一 SSE 块内的 token 时间无法拆分。
- 客户端 lag 只是**上界的一半**：同一台机器上别的进程也在抢 CPU；它不是「服务端慢了多少」的分解。
- `scan` 的结论仍是**闭环**结论（「K 个并发客户端下」），不是生产到达率下的容量；要开环到达率请用 `aiperf` / `guidellm`。
- 分位数置信区间是**分位数本身的不确定性**，不是「下一次测量的预测区间」，别拿它当 SLO 余量。
- **不做分布式多机压测**，不测第三方付费 API 的绝对性能（网络抖动会污染结果）。

## 开发

```bash
python3 -m pytest -q          # 全量测试（含真实本地 SSE 服务）
python3 -m ruff check src tests scripts
python3 scripts/red_check.py  # RED 反证：把修复回退后，对应测试必须变红
```

`red_check.py` 是变异检查的自动化版本：在临时副本上逐条回退修复（TTFT 取值时机、门禁边界、吞吐分母、token 口径、坏 `choices`、总时限、错误串回显、非字符串 `content`、残缺报告、warmup 退出码、`count=0` 带分位数、显存 `null`→`0`、硬件可比性、影子门禁退出码、label 恒 PASS、利用率方向、stop 截断、单样本 delta、零样本块、指纹 `none`、证据链不一致、影子读坏输入、指纹溯源、截断读数、物理不可能值、Collector 参数、lag 空样本 `null`、lag 真能测到阻塞、扫描违反即停、缺证据不算通过、scan 不可过门禁、lag 无来源、bootstrap 种子、小样本不给区间、client 块形状），跑对应用例并断言它**失败**。当前 **41/41** 全部变红——修复若只是摆设，这一步会变绿并报错。

按 JD 练习的路径见 [`docs/labs.md`](docs/labs.md)（10 个实验，每个都有可复跑的通过判据）；这些能力的文献依据与「哪些只是文档、哪些真实现了」见 [`docs/papers.md`](docs/papers.md)。

## License

Apache-2.0
