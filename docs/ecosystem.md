# 生态地图：LLM 推理性能测试（对应 JD）

> 核实时间：2026-10-31（America/Los_Angeles）。
> 文中每一个 star 数、license、提交时间都来自当次实际请求，**没有一项凭记忆填写**。数据来源与取证方式：
> - **star / license / pushed_at**：全部取自 `https://api.github.com/repos/<owner>/<repo>` 的实时 JSON（`stargazers_count` / `license.spdx_id` / `pushed_at` / `archived`），表中日期列的 `†` 即表示该项来自该端点。
> - **交叉校验**：另用 `https://github.com/<owner>/<repo>` 页面内嵌 JSON（`stargazerCount`、`license.spdxId`）独立取数一遍，vLLM 两通道一致（HTML `stargazerCount=95970` 与 API `stargazers_count` 同量级）；两轮取数相隔约 20 分钟，star 数的微小漂移（如 vLLM 95,970→95,971）属正常增长，表中以最终一次 API 值为准。
> - **命令与指标定义**：取自对应仓库的源码 / README 原文（每条给出文件与行号），非凭记忆。
>
> 本文档只做"可核实的存在性 + 定位 + 能学到什么"，不复制这些项目的性能结论。表里的 star 是这些上游仓库的快照，不是 ttftkit 的采用量、下载量或依赖数。ttftkit 自己没有 PyPI 发布。

---

## 0. 一页结论（哪些必须会、哪些了解即可）

**必须会（面试会被追问，且本机就能上手）**

| 优先级 | 项目 | 学它解决 JD 的哪一条 |
| --- | --- | --- |
| P0 | `vllm bench serve`（vllm-project/vllm）★95,971 | 职责①⑤：会跑官方 benchmark、会读 TTFT/TPOT/ITL/goodput 指标 |
| P0 | `ai-dynamo/aiperf` ★838 | 职责①②：指标化压测的正确姿势（OpenAI 兼容端点 + 单请求级指标）|
| P0 | `NVIDIA/dcgm-exporter` ★1,920（+ 其底层 `NVIDIA/DCGM` ★810）| 职责②④：GPU 显存/利用率/ECC 的指标口径与采集 |
| P0 | `benchmark-action/github-action-benchmark` ★1,269 | 职责③⑤：把 benchmark 接进 CI 并"退化即失败" |
| P1 | `ionelmc/pytest-benchmark` ★1,460 | 职责③：pytest 体系内的性能回归 |
| P1 | `mlcommons/inference` ★1,656（规则见 `mlcommons/inference_policies`）| 职责⑤：发布准入/latency constraint 的行业权威口径 |
| P1 | `vllm-project/guidellm` ★1,735 | 职责①②：SLO 驱动的 sweep 与 goodput |

**了解即可（知道它是什么、能说清取舍）**：`model_analyzer` ★530、`perf_analyzer`（genai-perf 宿主）★164、`asv` ★1,023、`locust` ★28,254 / `k6` ★32,090、`production-stack` ★2,704、`opentelemetry-collector` ★7,722、`nvtop` ★11,113、`py-spy` ★15,619、`TorchProfiler`（pytorch/pytorch ★105,030）、`perf`（torvalds/linux ★253,943）、`llama.cpp` ★132,952、`TensorRT-LLM` ★14,982。

**明确不要投入学习成本的**

- `huggingface/text-generation-inference` ★10,883 —— **已归档（archived）**，最后提交 2026-03-21。
- `ray-project/llmperf` ★1,127 —— **已归档（archived）**，最后提交 2024-12-09；新工作用 aiperf / guidellm。
- `NVIDIA/nsight-systems`、`NVIDIA/nsight-compute` —— **GitHub 上不存在这两个仓库**（Nsight 是 NVIDIA 专有工具，只有官网文档），见 §5。

---

## 1. 按 JD 职责分组

### 1.1 推理引擎自带 benchmark（JD 职责 ①⑤；加分项 vLLM/SGLang）

| 仓库 | ★ | License | pushed_at（API）| 定位 |
| --- | --- | --- | --- | --- |
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | 95,971 | Apache-2.0 | 2026-10-31T14:21:25Z † | 高吞吐推理/服务引擎，自带 `vllm bench serve/latency/throughput` |
| [sgl-project/sglang](https://github.com/sgl-project/sglang) | 38,321 | Apache-2.0 | 2026-10-31T14:30:59Z † | 高性能服务框架，benchmark 在 `python/sglang/benchmark/serving.py` |
| [NVIDIA/TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) | 14,982 | Apache-2.0（LICENSE 首行自述；GitHub 因自定义 LICENSE 识别为 NOASSERTION） | 2026-10-31T14:32:01Z † | NVIDIA 编译式推理栈，自带 `trtllm-bench` |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 132,952 | MIT | 2026-10-31T11:34:11Z † | C/C++ 本地推理，`llama-bench` 是单机基线首选 |
| [huggingface/text-generation-inference](https://github.com/huggingface/text-generation-inference) | 10,883 | Apache-2.0 | 2026-03-21T11:34:22Z † | ⚠ **已归档**，只作为"历史形态"读文档 |

**学什么（能直接抄进面试/工作）**

- vLLM 的指标不是文档里的一句话，而是**代码里的公式**：`vllm/benchmarks/serve.py:655-657`
  ```python
  if output_len > 1:
      latency_minus_ttft = outputs[i].latency - outputs[i].ttft
      tpot = latency_minus_ttft / (output_len - 1)
  ```
  同一文件 `serve.py:342-370` 的 `BenchmarkMetrics` dataclass 把 `ttft/tpot/itl/e2el` 全部做成 mean/median/std/percentile 四套，这说明"指标体系"落地形态就是**分位数 + 均值**，而不是单点值。
- 吞吐口径：`request_throughput`、`output_throughput`、`total_token_throughput` 三者分开（同上 dataclass），报告时不能混用。
- goodput：`serve.py:1868` 的 `--goodput` help 原文给出的 SLO 语义是 `"KEY:VALUE"`，key 只能是 `ttft/tpot/e2el`（毫秒），并指向 **DistServe 论文 https://arxiv.org/pdf/2401.09670** —— 这是"goodput 是什么"最省事的权威引文。
- SGLang 与 vLLM **TPOT 公式完全相同**（`python/sglang/benchmark/serving.py:1172` 同样是 `(latency - ttft) / (output_len - 1)`），这条一致性可以直接写进你的指标定义文档。

**动手命令（flag 均取自源码 argparse，未凭空编写）**

```bash
# vLLM：对已启动的 OpenAI 兼容服务压测（客户端侧）
vllm bench serve --model Qwen/Qwen2.5-7B-Instruct \
  --backend openai --base-url http://127.0.0.1:8000 \
  --num-prompts 1000 --request-rate 10 \
  --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,90,95,99 \
  --goodput ttft:200 tpot:50 \
  --save-result --result-dir ./results

# vLLM：让服务端开启 profile 后，客户端标记要抓的一段
vllm bench serve --model Qwen/Qwen2.5-7B-Instruct --backend openai \
  --base-url http://127.0.0.1:8000 --profile   # 服务端需 --profiler-config

# SGLang（旧导入路径已弃用，实现在 sglang.benchmark.serving）
python -m sglang.bench_serving --backend sglang \
  --dataset-name sharegpt --num-prompts 1000 --request-rate 10
```

- 值得读源码的部分：`serve.py` 里 `--request-rate`（固定到达率，开环）与 `--max-concurrency`（闭环）的差别 —— 这正是"你的压测到底在模拟什么流量"的核心，也是 ttftkit 必须写清楚的边界。
- 不值得读：调度器/内核实现，与测试岗位无关。

### 1.2 独立压测工具（JD 职责 ①②；加分项 模型压测）

| 仓库 | ★ | License | pushed_at（API）| 定位 |
| --- | --- | --- | --- | --- |
| [ai-dynamo/aiperf](https://github.com/ai-dynamo/aiperf) | 838 | Apache-2.0 | 2026-10-31T03:48:02Z † | NVIDIA 新一代指标化压测，genai-perf 的官方后继 |
| [triton-inference-server/perf_analyzer](https://github.com/triton-inference-server/perf_analyzer) | 164 | BSD-3-Clause | 2026-10-19T21:37:52Z † | `genai-perf` 就在本仓库 `genai-perf/` 子目录 |
| [triton-inference-server/model_analyzer](https://github.com/triton-inference-server/model_analyzer) | 530 | Apache-2.0 | 2026-10-23T20:47:10Z † | 自动扫配置（batch/concurrency/instance）找最优，出报告 |
| [vllm-project/guidellm](https://github.com/vllm-project/guidellm) | 1,735 | Apache-2.0 | 2026-10-31T03:58:46Z † | SLO 导向的 sweep，输出 JSON/CSV/HTML |
| [ray-project/llmperf](https://github.com/ray-project/llmperf) | 1,127 | Apache-2.0 | 2024-12-09T01:52:28Z † | ⚠ **已归档**，勿新接入 |
| [locustio/locust](https://github.com/locustio/locust) | 28,254 | MIT | 2026-10-30T18:29:21Z † | Python 通用负载，可写自定义 SSE client |
| [grafana/k6](https://github.com/grafana/k6) | 32,090 | AGPL-3.0 | 2026-10-29T10:20:02Z † | Go/JS 通用负载，CI 友好（AGPL 注意合规） |

**学什么**

- **genai-perf 的真实归属**：它不是独立仓库，而在 `triton-inference-server/perf_analyzer` 的 `genai-perf/` 子目录（本次实测 `genai-perf/README.md` 返回 200），指标定义见该 README 第 489–496 行（§2 引用）。NVIDIA 已把新方向转到 **aiperf**，所以"学 genai-perf 概念、用 aiperf 干活"是当前正确姿势。
- aiperf 的指标名更直白：`Time to First Output Token`、`Output Token Throughput Per User`、`Output Token Throughput (tokens/sec)`，外加 `goodput` 教程（SLO 驱动的有效吞吐）。它对 ttftkit 最有参考价值的是**"单个请求一行指标 + 全分布"**的输出结构。
- guidellm 的 sweep 语义值得抄：`--profile kind=sweep` + `--constraint kind=max_duration,seconds=30`，一次扫出"最大吞吐点"和"各速率点"。
- locust/k6 的价值只在**到达模型**（固定 RPS、阶梯、尖峰），它们不懂 token，别指望它们算 TTFT。
- 另核实两项（不单列，了解即可）：[triton-inference-server/server](https://github.com/triton-inference-server/server)（★11,121，BSD-3-Clause，pushed_at 2026-10-30T23:56:20Z）—— 被 `perf_analyzer` 压测的服务端本体；[ai-dynamo/nixl](https://github.com/ai-dynamo/nixl)（★1,345，NOASSERTION，pushed_at 2026-10-30T22:28:48Z）—— KV 传输库，与"测性能"关系较远。

**动手命令**

```bash
# aiperf（对任意 OpenAI 兼容端点，含 streaming）
aiperf profile --model Qwen/Qwen2.5-7B-Instruct --streaming --endpoint-type chat \
  --url http://localhost:8000 --concurrency 5 --request-count 10

# genai-perf（在 perf_analyzer 仓库内安装使用）
genai-perf profile -m gpt2 --backend tensorrtllm --streaming

# guidellm：一次 sweep 找最大性能
guidellm run --backend kind=openai_http,target=http://localhost:8000 \
  --profile kind=sweep --constraint kind=max_duration,seconds=30 \
  --data kind=synthetic_text,prompt_tokens=256,output_tokens=128
```

### 1.3 指标与可观测（JD 职责 ②④；加分项 Prometheus/Grafana/GPU 监控）

| 仓库 | ★ | License | pushed_at（API）| 定位 |
| --- | --- | --- | --- | --- |
| [NVIDIA/DCGM](https://github.com/NVIDIA/DCGM) | 810 | Apache-2.0 | 2026-10-27T01:47:22Z † | 数据中心 GPU 遥测/健康检查的底层库与工具 |
| [NVIDIA/dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) | 1,920 | Apache-2.0 | 2026-09-18T21:23:51Z † | 把 DCGM 指标暴露成 Prometheus 文本格式 |
| [prometheus/node_exporter](https://github.com/prometheus/node_exporter) | 13,887 | Apache-2.0 | 2026-10-29T18:15:08Z † | 机器侧指标（CPU/内存/网络），压测时排除主机瓶颈 |
| [grafana/grafana](https://github.com/grafana/grafana) | 77,598 | AGPL-3.0 | 2026-10-31T14:06:55Z † | 可视化；不产生指标，只消费 |
| [vllm-project/production-stack](https://github.com/vllm-project/production-stack) | 2,704 | Apache-2.0 | 2026-10-27T06:45:57Z † | vLLM 的 K8s 参考部署，自带 Prometheus + Grafana dashboard |
| [open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector) | 7,722 | Apache-2.0 | 2026-10-31T14:37:02Z † | 指标/追踪统一采集管道（vLLM 亦支持 OTel trace） |
| [Syllo/nvtop](https://github.com/Syllo/nvtop) | 11,113 | GPL-3.0（GitHub 显示 NOASSERTION） | 2026-10-04T13:42:35Z † | 终端里的 GPU 进程/显存实时监控，排障神器 |

**学什么（真实指标名，可直接写进 dashboard）**

从 `dcgm-exporter` 仓库 `etc/default-counters.csv` 实测：默认采集里有 **23 个 `DCGM_FI_PROF_*`** 剖析类指标，以及 `DCGM_FI_DEV_*` 基础类，例如：

- 利用率/显存：`DCGM_FI_DEV_GPU_UTIL`、`DCGM_FI_DEV_FB_USED`、`DCGM_FI_DEV_FB_FREE`、`DCGM_FI_DEV_MEM_COPY_UTIL`
- 温度/功耗：`DCGM_FI_DEV_GPU_TEMP`、`DCGM_FI_DEV_MEMORY_TEMP`、`DCGM_FI_DEV_ENFORCED_POWER_LIMIT`
- 健康：`DCGM_FI_DEV_ECC_SBE_AGG_TOTAL`、`DCGM_FI_DEV_ECC_DBE_AGG_TOTAL`、`DCGM_FI_DEV_XID_ERRORS`
- 剖析：`DCGM_FI_PROF_GR_ENGINE_ACTIVE`、`DCGM_FI_PROF_DRAM_ACTIVE`、`DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`、`DCGM_FI_PROF_PCIE_RX_BYTES`/`_TX_BYTES`

这条线对 JD 职责②的意义：**"显存"不是 `nvidia-smi` 拍一次快照，而是 `FB_USED` 的时间序列峰值 + 是否有 OOM/XID**。这条区分能直接回答"你怎么测显存"。

**动手命令**

```bash
# 采集（Prometheus 文本格式，端口 9400）
dcgm-exporter --collectors /etc/dcgm-exporter/default-counters.csv
curl -s localhost:9400/metrics | grep -E 'DCGM_FI_DEV_FB_USED|DCGM_FI_PROF_GR_ENGINE_ACTIVE'
# 人肉看显存/进程
nvtop
```

### 1.4 profiler / 性能分析（JD 职责 ③；加分项 GPU 监控、性能分析）

| 工具/仓库 | ★ | License | pushed_at（API）| 定位 |
| --- | --- | --- | --- | --- |
| [pytorch/pytorch](https://github.com/pytorch/pytorch)（`torch.profiler`） | 105,030 | BSD-3-Clause 系（GitHub 显示 NOASSERTION） | 2026-10-31T14:26:31Z † | Python 层算子/内核耗时，CPU+CUDA 时间线 |
| [benfred/py-spy](https://github.com/benfred/py-spy) | 15,619 | MIT | 2026-10-28T01:09:30Z † | 无侵入采样 Python 栈，查"卡在哪个函数" |
| [torvalds/linux](https://github.com/torvalds/linux)（`tools/perf`） | 253,943 | GPL-2.0（GitHub 显示 NOASSERTION） | 2026-10-31T05:20:24Z † | 系统级 CPU/调度/上下文切换 |
| Nsight Systems / Nsight Compute | — | 专有 | — | **无 GitHub 仓库**，见 §5；GPU 内核级分析的事实标准 |

**学什么**

- 三层分工要能说清：`py-spy`（Python 栈）→ `perf`（OS/调度）→ `nsys/ncu`（CUDA 内核与 warp）。压测里"TPOT 抖动"先看这三层哪一层在抖，而不是先怀疑模型。
- vLLM 的 torch profiler 入口就是 benchmark 的 `--profile`（`serve.py:1787` help 原文：`"Use vLLM Profiling. --profiler-config must be provided on the server."`）—— 也就是说"profiler 接入"是**两端配合**：服务端起 profiler 配置，客户端打标。
- ttftkit 的边界：只做客户端侧时序，不做内核剖析；这一节的价值是"面试能讲清 GPU 侧该用什么工具"，不是自己实现。

**动手命令**

```bash
py-spy top --pid <server_pid>                     # 实时看热点函数
py-spy record -o flame.svg --pid <server_pid>     # 生成火焰图
perf stat -p <pid>                                # 上下文切换/CPU 迁移
nsys profile -t cuda,nvtx -o ttft_trace python client.py   # 需装 Nsight Systems
ncu --set full -o kernel_profile python -c "import torch; ..."  # 需装 Nsight Compute
```

### 1.5 CI 质量门禁与回归基准（JD 职责 ③⑤；加分项 pytest / Jenkins / GitLab CI）

| 仓库 | ★ | License | pushed_at（API）| 定位 |
| --- | --- | --- | --- | --- |
| [benchmark-action/github-action-benchmark](https://github.com/benchmark-action/github-action-benchmark) | 1,269 | MIT | 2026-09-15T10:20:07Z † | 把 benchmark 结果存 gh-pages 并做趋势；**退化超阈值即告警/失败** |
| [ionelmc/pytest-benchmark](https://github.com/ionelmc/pytest-benchmark) | 1,460 | BSD-2-Clause | 2026-10-30T12:59:52Z † | pytest 内的微基准，可输出 JSON 供 CI 比较 |
| [airspeed-velocity/asv](https://github.com/airspeed-velocity/asv) | 1,023 | BSD-3-Clause | 2026-10-31T14:28:21Z † | Python 项目长期性能跑分与网页报告 |
| [mlcommons/inference](https://github.com/mlcommons/inference) | 1,656 | Apache-2.0 | 2026-09-16T13:09:34Z † | MLPerf Inference 参考实现（规则在 `mlcommons/inference_policies`）|

**学什么（这是 ttftkit 最该对标的一组）**

- `github-action-benchmark` 的三件套就是"门禁"的通用形态：`fail-on-alert: true` + `alert-threshold: '150%'` + `tool: customSmallerIsBetter`（README 第 171–222 行实测）。它证明了**"相对基线退化百分比 + 阈值 + 让 CI 变红"**是被广泛接受的准入范式。
- `pytest-benchmark` 证明"性能测试可以是 pytest 的一个 fixture"——对你写 Python 门禁工具，测试组织方式可直接沿用。
- MLPerf 的关键概念是 **latency constraint**：提交必须声明在什么延迟约束下取得该性能（`inference_rules.adoc:139` 的表格列就是 `Latency Constraint | Tail Latency | Performance Metric`），Server 场景用 99th percentile 约束。**"性能数字必须带延迟约束才有意义"**，这正是 ttftkit 报告必须绑定 SLO 的行业依据。

**动手命令/配置**

```yaml
# .github/workflows/bench.yml（github-action-benchmark）
- uses: benchmark-action/github-action-benchmark@v1
  with:
    tool: 'customSmallerIsBetter'     # 延迟类指标越小越好
    output-file-path: bench.json
    fail-on-alert: true
    alert-threshold: '110%'           # 比基线慢 10% 就红
    comment-on-alert: true
```

```bash
pytest --benchmark-only --benchmark-json=bench.json
asv run && asv continuous main HEAD    # 分支间回归对比
```

### 1.6 推理服务指标定义的权威来源（JD 职责 ②）

| 来源 | 位置（可点） | 它定义了什么 |
| --- | --- | --- |
| vLLM benchmark 源码 | `vllm/benchmarks/serve.py:655-657` | TPOT = (latency − ttft) / (output_len − 1) |
| SGLang benchmark 源码 | `python/sglang/benchmark/serving.py:109-111, 1172` | ttft / tpot / itl 字段与同一 TPOT 公式 |
| NVIDIA genai-perf | `genai-perf/README.md:489-496`（在 perf_analyzer 仓库） | TTFT、ITL、Output Token Throughput 的官方定义 |
| MLPerf | `mlcommons/inference_policies` `inference_rules.adoc:139, 228` | Latency Constraint / Tail Latency |
| goodput | DistServe 论文 https://arxiv.org/pdf/2401.09670（vLLM `--goodput` help 指名引用） | SLO 约束下的有效吞吐 |

---

## 2. 指标定义对照表

| 指标 | vLLM | SGLang | genai-perf / aiperf | MLPerf | 口径差异要点 |
| --- | --- | --- | --- | --- | --- |
| TTFT | 客户端发请求 → 收到首个输出 token 的时间（`serve.py` 记录 `outputs[i].ttft`，含连接/排队/prefill） | 同义：`ttft`（首 token 时间） | TTFT："Time between when a request is sent and when its first response is received"，每请求一值（README:489） | 不单列，融进 latency constraint | **是否含 HTTP 建连与队列等待**是各家最大分歧；服务端指标（如 vLLM `time_to_first_token` histogram）会剔掉客户端网络耗时 |
| TPOT | (latency − ttft) / (output_len − 1)（`serve.py:656-657`） | 同公式（`serving.py:1172`） | genai-perf **不直接给 TPOT**，用 ITL 替代；aiperf 给 `Output Token Throughput Per User` | 不定义 | TPOT 是"每输出 token 平均耗时"，分母是 tokens−1；单 token 请求无定义（vLLM 记 0 仅供 goodput） |
| ITL | `outputs[i].itl` 逐 token 间隔列表 | `output.itl` 列表 | "Time between intermediate responses ... divided by the number of generated tokens of the latter response"（README:491）| 不定义 | **SSE 一个 chunk 可能含多个 token**，块间隔 ≠ ITL，必须除以该块的 token 数 |
| 吞吐 | `request_throughput` / `output_throughput` / `total_token_throughput` 三分 | 同类分离 | `Output Token Throughput` = 总输出 token / 压测时长（README:496） | Samples/second（按场景） | 分母是"压测墙钟"还是"各请求生成阶段之和"，数值可差数倍 |
| goodput | `--goodput ttft:200 tpot:50`（ms，仅 ttft/tpot/e2el） | 无原生 goodput | aiperf 有 goodput 教程（SLO 驱动） | 用 latency constraint 表达同一思想 | goodput = 满足 SLO 的请求的吞吐；**必须先声明 SLO**，否则无意义 |

> 结论一句话：**TTFT 在哪一秒开始计时、TPOT 分母是多少、ITL 怎么处理"一块多 token"、吞吐的分母是什么**——这四件事决定了不同工具的数字不可直接横比。写报告时必须把这四条写死。

---

## 3. 学习路线（4 周，每周 3 个可交付动作）

标记：🖥 本机可跑（无 GPU 也行）｜🟡 需要一台 GPU 机器（本地或云端）｜☁️ 建议云端（长时压测，按工作区纪律不上本地长跑）

**第 1 周：把指标体系钉死（不做压测，先做口径）**

1. 🖥 读 `vllm/benchmarks/serve.py` 的 `BenchmarkMetrics`（342-370）与 TPOT 计算（655-657），手写一份"TTFT/TPOT/ITL/吞吐/goodput"定义卡，每个指标标注"客户端侧/服务端侧、含不含网络、分母是什么"。交付：`docs/metric-definitions.md`。
2. 🖥 用 ttftkit 现有 SSE 测试客户端对本地 mock 服务跑通，验证"首内容块延迟"与"块不是 token"两个断言。交付：一次 RED→GREEN 的测试记录。
3. 🖥 读 genai-perf README 489-496 与 MLPerf `inference_rules.adoc:139`，把三方口径差异写进同一张对照表（即本文 §2 的自制版）。

**第 2 周：跑通真实端点的压测（客户端侧）**

1. ☁️ 起一个 OpenAI 兼容服务（vLLM 或 SGLang，云端单卡），用 `vllm bench serve --backend openai` 打 3 组 `--request-rate`，观察 TTFT/TPOT 随并发变化。交付：3 份 `--save-result` JSON。
2. ☁️ 同一端点用 `aiperf profile --streaming` 打一遍，对比 aiperf 与 vllm bench 的 TTFT 是否同量级，解释差异（网络/是否含建连）。交付：对比表 + 一句归因。
3. 🟡 `dcgm-exporter` 起在 GPU 机上，压测同时抓 `DCGM_FI_DEV_FB_USED` 时间序列，交付"显存峰值 + 是否 OOM/XID"的截图与数值。

**第 3 周：把门禁接进 CI（ttftkit 的主场）**

1. 🖥 以一次压测 JSON 为基线，用 ttftkit 的 `limits`/`regressions` 规则跑 `gate`，构造"合规→PASS、超阈值→FAIL、缺指标→阻断"三种输入。交付：三份退出码证据（0/1/2）。
2. 🖥 用 `github-action-benchmark` 的 `customSmallerIsBetter + fail-on-alert + alert-threshold` 做一个最小 workflow，故意提交一份变慢的结果，证明 CI 会红。交付：一次红色 run 链接。
3. 🖥 `pytest --benchmark-only --benchmark-json` 跑一个纯 Python 微基准（如 JSON 解析），接上同样的阈值判定，证明"性能测试可以 pytest 化"。

**第 4 周：排障与知识库（JD 职责 ④）**

1. 🟡 `py-spy top` + `perf stat` 在压测抖动时各抓一次，写一份"TPOT 抖动三层定位"的排障笔记（Python 栈/OS/CUDA 三层各看什么）。
2. 🟡 在 GPU 机上跑一次 `nsys profile -t cuda,nvtx`（需装 Nsight Systems），交付 timeline 截图 + 一句结论（是 prefill 还是 decode 受限）。
3. 🖥 把 1-2 周的真实失败（断流、usage 缺失、空 content）整理成缺陷分级模板：现象/复现/日志/根因/归属组件/回归用例。交付：`docs/defect-template.md` + 3 条真实样例。

---

## 4. 本项目 ttftkit 与它们的差异定位（不重复造轮子）

ttftkit 的自我定位来自 `ttftkit/docs/design.md` 的"测量契约"，不是本文的想象：

- TTFT = 单调时钟记录"发送 HTTP 请求前 → 首个**非空 content** SSE 事件"，**含连接、网络、排队与 prefill**。
- **SSE chunk 不等于 token**；只记录块间隔，不把块间隔称作逐 token ITL。
- TPOT = 首末内容事件时间差 / (服务端 `completion_tokens` − 1)；少于两个 token、缺 usage、不足两个内容事件 → **null**（缺失不是零）。
- token 吞吐 = 所有成功请求的 `completion_tokens` 之和 / 测量墙钟；任一成功请求缺计数 → 总体 null，另报覆盖率。
- GPU 显存与 profiler：v0.1 只提供**接入实践文档**，不宣称已实现 GPU 采样（见 §1.3/§1.4 的接入步骤）。

**取舍表（照抄会重复造轮子的，明确不抄）**

| 能力 | 生态里谁做得最好 | ttftkit 的取舍 |
| --- | --- | --- |
| 负载生成（并发/到达率/数据集） | vLLM bench serve、aiperf、guidellm | **不重造**。ttftkit 只保证"固定 worker 闭环 + warmup 隔离"够用于回归对比，复杂流量交给上面三家 |
| 全量指标与分布 | aiperf / vLLM | **部分重造但更窄**：只保留 TTFT/TPOT/吞吐/错误率，且每个都写死口径（这是差异，不是缺陷） |
| GPU 显存/利用率 | DCGM + dcgm-exporter | **不自己采**，文档指向 dcgm-exporter；显存不作为 v0.1 已实现能力 |
| profiler | nsys/ncu/torch profiler | **不做**，只给接入步骤 |
| **回归门禁与准入判定** | github-action-benchmark（通用）、MLPerf（规则） | **这是 ttftkit 的主场**：把"指标 + 基线 + 阈值"变成可判定的门禁（`limits` / `regressions`，退出码 0/1/2）|

**最该抄的一条设计**：`vllm --goodput` 的 SLO 语义 + `github-action-benchmark` 的 `fail-on-alert` 合成一件事——**门禁规则里必须能写"SLO 约束"（如 `ttft_ms.p95 ≤ 200`），并把它直接映射成 CI 的三态退出码（0 通过 / 1 违规或缺指标 / 2 规则无效或不可比）**。这比"再多测几个指标"重要得多：它把"性能测试"从"看图表"变成"能否发布"的判定。

---

## 5. 已核实为「不存在或已改名」的条目（避免以后误引用）

| 你可能会引用成 | 实测结果 | 正确处理 |
| --- | --- | --- |
| `NVIDIA/nsight-systems` | **HTTP 404**，该路径不存在 | Nsight Systems 是 NVIDIA **专有工具**，无 GitHub 仓库；引用官网 https://docs.nvidia.com/nsight-systems/ |
| `NVIDIA/nsight-compute` | **HTTP 404**，该路径不存在 | Nsight Compute 同样专有，官网 https://docs.nvidia.com/nsight-compute/ |
| `ggerganov/llama.cpp` | 301 重定向到 **`ggml-org/llama.cpp`** | 写 `ggml-org/llama.cpp`（★132,952, MIT）|
| `neuralmagic/guidellm` | 301 重定向到 **`vllm-project/guidellm`** | 写 `vllm-project/guidellm`（★1,735, Apache-2.0）|
| `huggingface/text-generation-inference`（当作在用项目） | 仓库存在但 **`isArchived: true`** | 只能当"历史/概念"引用，别写进技术选型 |
| `ray-project/llmperf`（当作在用项目） | 仓库存在但 **`isArchived: true`**，最后提交 2024-12-09 | 用 aiperf / guidellm 替代 |

> 边界说明：**404 只说明"这个 owner/repo 路径在 GitHub 上不存在"，不等于"这个软件不存在"**（如 Nsight 是商业软件）。同理，`isArchived` 说明维护方已冻结该仓库，不代表代码不能用。

---

### 附：本次核实统计

- 实际发起请求的 `owner/repo` 路径：**32** 个（含 2 个用于验证重定向的旧路径）。
- 真实存在且可取数：**30** 个；其中独立项目 **28** 个，旧路径重定向 **2** 个。
- **404 并已剔除**：**2** 个（`NVIDIA/nsight-systems`、`NVIDIA/nsight-compute`）。
- 归档（archived）并在文中降级为"只读不选型"：**2** 个（TGI、llmperf）。
- 另核实为"非 GitHub 项目"但写入学习路线：DCGM 之外的 Nsight 系列（专有）。

