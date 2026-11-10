# 论文地图：这个工具凭什么这么测

这份文档回答一个问题：**ttftkit 的每条测量纪律，背后有没有文献依据？**

每条记录都写明验证方式。验证分三档：
- **abs 页验号**：`curl https://arxiv.org/abs/<id>`，标题与编号一致（AGENTS.md 铁律：绝不信任记忆里的 arXiv 号）。
- **DOI 验真**：`curl https://api.crossref.org/works/<doi>`，题名/会议/年份与描述一致。
- **官方页面**：会议官方页面存在该题目（用于无 DOI 的经典论文）。

> 检索环境声明（2026-11-01）：本次检索中 arXiv 与 Semantic Scholar 的 HTTP 接口两次返回 **429 Too Many Requests**，`paper_search` 只有 OpenAlex / Crossref 应答。因此「本次没搜到某篇」不构成「不存在该文献」的证据——下文标注为「未验证」的条目是**我没验成**，不是**它不存在**。

## A. 测量方法学：客户端会污染读数

### A1. 单进程 asyncio 客户端在高并发下系统性抬高 TTFT/TPOT

- **文献**：*Identifying and Mitigating Systemic Measurement Bias in Production LLM Inference Benchmarks*，arXiv [2605.24217](https://arxiv.org/abs/2605.24217)（2026）。
- **验证**：abs 页验号 ✅。
- **主张（摘要原文范围）**：单进程 asyncio 负载客户端在排队层面构成 M/G/1 + GIL 瓶颈，高并发时把客户端自身的排队时间算进 TTFT/TPOT；主张多进程客户端或把客户端开销显式记账。
- **对 ttftkit 的启示（可执行）**：ttftkit 正是**单进程 asyncio 闭环**客户端——这是必须承认的测量上界。v0.3 起在报告里显式记录**客户端事件循环延迟**（`client.lag_ms`），并在 lag p95 超过 TTFT p95 的 5% 时打印污染警告；门禁可对 `client_lag_p95_ms` 设上限。
- **状态**：v0.3 已实现（`client.lag_ms` + 报告警告 + 门禁规则）。

### A2. 闭环与开环压测给出的结论可以不同

- **文献**：*Open Versus Closed: A Cautionary Tale*，Bianca Schroeder, Adam Wierman, Mor Harchol-Balter，NSDI 2006。
- **验证**：USENIX 官方会议页面存在该题目 ✅（[链接](https://www.usenix.org/conference/nsdi-06/open-versus-closed-cautionary-tale)）；无 DOI，故不做 DOI 验真。
- **主张**：闭环（closed-loop）负载下，系统的响应时间反馈会影响请求发起节奏，测得的「吞吐-延迟」关系与开环/半开环的到达过程不同；用闭环结论去推断生产（近似开环）行为会偏。
- **对 ttftkit 的启示**：ttftkit 只做**闭环并发**，所以它的数字只能回答「在 K 个并发客户端下」，不能回答「在 R req/s 到达率下」。v0.3 的 `scan` 子命令用**多级并发扫描 + SLO 判定**逼近「最大 SLO 满足负载」，但仍是闭环——这点必须写在报告与 README，不许滑成「生产容量」。
- **状态**：口径已写入 `docs/metrics.md` 与 `scan` 输出说明；开环模式列为 roadmap（未实现）。

### A3. 单个 GPU 利用率数字藏住了机制

- **文献**：*Dissecting GPU Utilization for LLM Inference on Nvidia Hopper*，arXiv [2609.12923](https://arxiv.org/abs/2609.12923)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：单个 SM 利用率百分比会把多种机制压成一个数；decode 阶段每请求只出一个 token，小 batch 的 dense projection GEMM 变成小行矩阵乘，在 Hopper 上只能填满固定 64 行 fragment 的一小部分；作者用 Nsight Compute 的八种计数器视图替代单一利用率。
- **对 ttftkit 的启示**：ttftkit 的 `gpu_util_peak_pct` 是**负载指示**，不是效率指标——利用率高不等于干活多，利用率低也不等于没瓶颈。文档与报告必须这么标注；真要判效率得上 profiler（`docs/labs.md` Lab 7 的方向）。
- **状态**：v0.3 文档口径已改（`docs/hardware.md`、`docs/metrics.md`）；profiler 采集列为 roadmap。

## B. 统计与回归检测：阈值不是判据

### B1. 变点检测优于固定阈值

- **文献**：*The Use of Change Point Detection to Identify Software Performance Regressions in a Continuous Integration System*，ICPE 2020，DOI [10.1145/3358960.3375791](https://doi.org/10.1145/3358960.3375791)。
- **验证**：Crossref DOI 验真 ✅（题名/会议/2020-04-20 一致）。
- **主张**：在噪声中识别「哪个提交造成了性能回退」，人工看时序图和固定阈值都不够及时；用 E-Divisive means 变点检测给出显著变化点列表，再人工复核。
- **对 ttftkit 的启示**：`gate` 的绝对阈值（limits）只能挡「明显坏了」，挡不住「慢慢变差」。v0.4 计划 `ttftkit trend`：对历史 run 序列跑变点检测，输出「哪一次开始变了」。
- **状态**：计划（roadmap v0.4），`baselines/run1..5.json` 已够做最小样例。

### B2. 变点检测内部的显著性检验要确定性

- **文献**：*Hunter: Using Change Point Detection to Hunt for Performance Regressions*，ICPE 2023，DOI [10.1145/3578244.3583719](https://doi.org/10.1145/3578244.3583719)。
- **验证**：Crossref DOI 验真 ✅（题名/会议/2023-04-15 一致）。
- **主张**：Hunter 把 E-divisive means 里基于**随机置换**的显著性检验换成 **Student t 检验**，因为随机置换在合理迭代次数下得不到确定性结果。
- **对 ttftkit 的启示**：这是个**可复现性**教训，不是统计口味问题。任何进入 CI 的判定（含未来 `trend`、含 `scripts/red_check.py` 的变异判定）都不许依赖随机种子未固定的重采样；必须固定种子或改用确定性检验，并在输出里写明。
- **状态**：已实现——分位数 bootstrap 置信区间固定种子（`metrics.CI_SEED = 0`，迭代数与种子写入 `ci` 字段），
  变异反证 M37 钉住「种子必须传进去」；`scripts/red_check.py` 全程确定性；`trend` 实现时沿用同一纪律。

### B3. 回归测试可以只跑一部分提交，但要按风险分配

- **文献**：*Risk-Aware Batch Testing for Performance Regression Detection*，arXiv [2604.00222](https://arxiv.org/abs/2604.00222)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：全量性能套件每提交都跑太贵；把「提交级回归风险模型」和「自适应批测」合起来，可减少跑的次数而不漏关键回退（Mozilla Firefox 数据集，CodeBERT 风险模型 ROC-AUC 0.694）。
- **对 ttftkit 的启示**：接入 CI 时不必每个 PR 都跑长基线段；可按「改了推理路径/依赖/容器」触发全量、文档改动只跑 smoke。这条属于**接入策略**，写在 `docs/labs.md` Lab 5 的扩展阅读里。
- **状态**：文档（Lab 5 扩展），不实现。

## C. SLO 与 goodput：把「多快」换成「在什么约束下多快」

### C1. SLO-qualified goodput + 低负载参考跑做质量护栏

- **文献**：*OpenDC-Infer-LC: Benchmarking Production-Valid Long-Context and RAG LLM Serving*，EMNLP 2026 Industry Track，DOI [10.18653/2026.emnlp-industry.64](https://doi.org/10.18653/2026.emnlp-industry.64)。
- **验证**：Crossref DOI 验真 ✅（题名/会议/2026 一致）。
- **主张**：只看吞吐会奖励「牺牲延迟换吞吐」的配置；他们用 **SLO-qualified goodput**——在满足 p95 TTFT/TPOT/E2E 与成功率约束的前提下，系统能承受的最高负载对应的有效输出 token 吞吐——并用**低负载参考跑**做相对质量护栏，防止靠降质刷分。
- **对 ttftkit 的启示**：这正是 v0.3 `scan` 的设计依据：多级负载 → 逐级判 SLO（error_rate / TTFT p95 / TPOT p95）→ 取「最高满足级的吞吐」作为 `slo_rps`，把违反 SLO 的那一级也记进结果（`stopped_at`），不许只报好看的那个数。
- **状态**：v0.3 已实现（`ttftkit scan`）。

### C2. SLO 约束下的自动调参要「预算一致 + 崩溃感知」

- **文献**：*SLO-Guard: Crash-Aware, Budget-Consistent Autotuning for SLO-Constrained LLM Serving*，arXiv [2604.17627](https://arxiv.org/abs/2604.17627)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：SLO 约束下的自动调参要处理试错崩溃与预算一致性——否则调参过程本身会破坏可复现性与停机预算。
- **对 ttftkit 的启示**：`scan` 需要「一级失败即停」而不是继续往上扫（否则崩溃/超时会把整轮拖长且污染后续级别）；每次 scan 都要能从中断处继续（roadmap：`scan --resume`）。当前实现：任一级出现请求失败即停，并把失败级别落盘。
- **状态**：v0.3 实现「一级失败即停 + 记录 stopped_at」；`--resume` 计划中。

## D. 可复现与确定性：这决定了「两次跑出的差异算不算数」

### D1. 动态批处理是输出不确定性的系统级来源

- **文献**：*LLM-42: Enabling Determinism in LLM Inference with Verified Speculation*，SOSP 2026，DOI [10.1145/3830418.3843901](https://doi.org/10.1145/3830418.3843901)。
- **验证**：Crossref DOI 验真 ✅（题名/SOSP 2026 一致）。
- **主张**：同一 prompt 多次请求输出不同，源于浮点非结合性与动态批处理/核选择；关掉动态批处理能确定但吞吐崩；batch-invariant 核把确定性与核实现绑死；LLM-42 用逐 token 校验在不大改吞吐的前提下恢复确定性。
- **对 ttftkit 的启示**：**性能基准不该声称输出确定**。ttftkit 只测延迟/吞吐，不比对输出；若未来加「质量护栏」（如 C1 的相对质量检查），必须显式声明服务端是否 deterministic 模式，并把该设置写进 `config` 参与可比性判定。
- **状态**：口径写入 `docs/metrics.md`（不可比项清单）；未实现输出比对。

### D2. 批内翻转是稀疏的，但确实存在

- **文献**：*MarginGate: Sparse Margin-Triggered Verification for Batch-Invariant LLM Inference*，arXiv [2605.30218](https://arxiv.org/abs/2605.30218)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：temperature=0 的 BF16 推理常被当作可复现，但同一请求单独解码与批内解码会吐不同 token；翻转稀疏（MATH500 上 Llama-3.1-8B 占 0.48% 的解码步，五个模型在 MATH500/GSM8K/HumanEval 上落在 0.3%–1.3%），低 top-1/top-2 logit margin 暴露大部分风险。
- **对 ttftkit 的启示**：若做「同 prompt 重放一致性」检查，预期**低比例翻转是正常的**，不能把 1 次翻转当 bug；要按比例阈值判定，并记录 top-1 margin（若服务端暴露 logprobs）。
- **状态**：口径已写入 `docs/metrics.md`（不可比项清单）；重放一致性检查列为 roadmap。

### D3. 跨架构复现要固定归约顺序

- **文献**：*Accelerating the Mitigation of LLM Inference Nondeterminism Across GPU Architectures*，arXiv [2609.25624](https://arxiv.org/abs/2609.25624)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：不同 GPU 上框架选不同 GEMM 核、归约顺序不同，舍入差异会翻转输出 token；作者用固定配置的 fused-upcast GEMM 核（16-bit 权重大到寄存器升 FP32、按概率的纯函数归约顺序累加）换跨架构一致。
- **对 ttftkit 的启示**：跨机型比较性能时，「同一模型同一框架」不等于「同一计算」；`config` 里的 `profile` 必须把机型/驱动/框架版本/量化一起写进去，否则跨机对比无意义——这条已是 ttftkit 的可比性键之一。
- **状态**：已实现（`config` 可比性键 + 硬件指纹）。

## E. 工作负载与系统刻画：测什么形状的流量

### E1. 两阶段异质性与扩展原则

- **文献**：*A Systematic Characterization of LLM Inference on GPUs*，arXiv [2512.01644](https://arxiv.org/abs/2512.01644)（2025）。
- **验证**：abs 页验号 ✅。
- **主张**：prefill 与 decode 的微架构瓶颈不同（两阶段异质性），据此给出系统扩展原则与范式边界。
- **对 ttftkit 的启示**：`max_tokens` 小、prompt 短时测出来的多半是 prefill 主导；把 `max_tokens`/prompt 长度当成**可比性键**（已做）之外，报告应同时给 TTFT（prefill 侧）与 TPOT（decode 侧），不要合成一个「平均延迟」。
- **状态**：已实现（TTFT/TPOT 分列，绝不合成）。

### E2. CPU 侧可能是真正的瓶颈，且指标要说清

- **文献**：*Characterizing and Optimizing LLM Inference Workloads on CPU-GPU Coupled Architectures*，ISPASS 2025，DOI [10.1109/ispass64960.2025.00015](https://doi.org/10.1109/ispass64960.2025.00015)。
- **验证**：Crossref DOI 验真 ✅（题名/会议/2025-05-11 一致）。
- **主张**：在紧耦合（GH200）与松耦合（PCIe A100/H100）系统上做算子到核的细粒度 trace，提出 SKIP profiler 与 TKLQT（Total Kernel Launch and Queuing Time）指标；GH200 在 batch 上仍比松耦合系统多撑 4 倍才转为 CPU-bound。
- **对 ttftkit 的启示**：**CPU/host 侧会先饱和**，这与 A1 的客户端偏差同源。ttftkit 用 `client.lag_ms` 把 host 侧饱和暴露出来，而不是让它在 TTFT 里冒充 GPU 慢。
- **状态**：v0.3 已实现（客户端 lag 观测）。

### E3. 大 batch 仍是显存带宽受限

- **文献**：*Mind the Memory Gap: Unveiling GPU Bottlenecks in Large-Batch LLM Inference*，IEEE CLOUD 2025，DOI [10.1109/cloud67622.2025.00036](https://doi.org/10.1109/cloud67622.2025.00036)。
- **验证**：Crossref DOI 验真 ✅（题名/IEEE CLOUD 2025/2025-07-07 一致）。
- **主张**：大 batch 推理仍受 DRAM 带宽饱和限制，多数算力闲置；提出 Batching Configuration Advisor 降低显存占用而不损吞吐。
- **对 ttftkit 的启示**：显存增长（`mem_used_delta_mb`）不是「越小越好」的唯一判据，它和 batch/序列长度耦合；门禁的显存规则必须与 `concurrency`/`max_tokens` 一同纳入可比性（已做），且阈值要按 profile 定。
- **状态**：已实现（可比性 + 指纹）；显存阈值的推导见 `baselines/README.md`。

### E4. Agent 负载是多轮、上下文增长的

- **文献**：*AgentPerfBench: A Benchmarking and Evaluation Suite for Inference Performance of Agentic LLMs*，arXiv [2609.34683](https://arxiv.org/abs/2609.34683)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：现有基准多测单轮聊天；agent 负载是多轮、带工具调用、上下文不断增长，输入长度应从经验分布采样；他们用 SWE-Bench/TerminalBench 的真实 trace 构造负载。
- **对 ttftkit 的启示**：ttftkit 目前每请求一条固定 prompt、固定 `max_tokens`，**不代表 agent 负载**。roadmap：`--workload` 支持多轮递进上下文（每轮把上一轮回答拼进 prompt），并把上下文增长曲线记录进 config 参与可比性。
- **状态**：计划（roadmap v0.4/v0.5）。

### E5. 先算下限再网格搜索

- **文献**：*Think Before You Grid-Search: Floor-First Triage for LLM Serving*，arXiv [2607.05876](https://arxiv.org/abs/2607.05876)（2026）。
- **验证**：abs 页验号 ✅。
- **主张**：调参前先用五维资源向量（HBM / FLOPs / 网络 / KV）算解析下限，得到 floor 区间 `[max, sum]`，用区间宽度读「重叠质量」，据此决定要不要真跑网格。
- **对 ttftkit 的启示**：`scan` 的级别选择不该拍脑袋——先按模型/KV 大小估一个「物理上不可能的并发上限」，超过就不要扫（省时间也避免把 OOM 当性能数据）。roadmap：`scan --max-feasible` 估算并给出建议级别。
- **状态**：计划（roadmap）；当前 `scan` 要求显式给 `--levels`。

## F. 未通过验证的条目（留痕，不引用）

| 条目 | 问题 | 处置 |
| --- | --- | --- |
| arXiv `2606.00000` | abs 页返回 "Article identifier not recognized" | 用作**验号流程的对照组**：说明验号能识别不存在的编号，不引用 |
| *A Goodput-Centric Benchmark … INFERBENCH*（IJISAE 2024，DOI 10.5281/zenodo.21157800） | 0 引用、期刊口碑不明、与已核验文献的 goodput 定义难以互证 | **不引用**；若需 goodput 定义只用 C1 |
| arXiv / Semantic Scholar HTTP 接口 | 本次两次 429 | 记录在本文开头；结论中不得写「无相关文献」 |

## G. 这张地图怎么用

1. **写报告时**：任何一条「我们这么测」的口径，都能在 A–E 里找到出处或明说没有出处。
2. **有人质疑数字**：先看 A1/A2——闭环 + 单进程客户端是**已声明的上界**，不是隐藏缺陷。
3. **决定要不要加功能**：按「文献主张 → 我们缺什么 → 能不能用测试钉住」三步；钉不住的（如 profiler 自动化）先写进 roadmap，不半做。
