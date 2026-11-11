# 变更记录

格式按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号按语义化版本。

## [0.3.0] - 2026-11-01

从「测一次」扩到「在约束下能撑多少」和「这一轮数字有多可信」。文献依据见 `docs/papers.md`。

### 新增

- **`ttftkit scan`**：多级并发逐级判 SLO（TTFT p95 / TPOT p95 / 错误率），输出
  「满足 SLO 的最高负载」（SLO-qualified goodput）。违反的那一级保留在报告里（`stopped_at`、`violations`），
  命中即停；缺 p95 记为不满足（`null` 不当通过）。`kind: "scan"` 报告不可过门禁 → 退出码 `2`。
- **客户端事件循环 lag**（`src/ttftkit/lag.py`）：测量窗口内每 10 ms 采一次唤醒延迟，
  报告新增「客户端（事件循环延迟）」段与 `summary.client_lag_p95_ms` 门禁标量；
  `lag_p95 > 5% × TTFT p95` 时提示「这一轮延迟是测量上界」。样本为 0 时是 `null`。
- **分位数 bootstrap 置信区间**：`distribution().ci` 给 p50/p95 的 95% 区间（种子固定 0、
  迭代数入库），样本 < 20 条为 `null`。
- **batch 不变性门禁**（`invariance` 规则）：策略加 `"invariance": {"against_baseline": true}` 并带
  `--baseline`，要求 candidate 与 baseline 的捕获输出逐字节一致（同 prompt 跑 `concurrency=1`
  与目标 batch 各一轮，`capture_outputs=True` 抓证据）。可比性只看 `protocol / model / max_tokens /
  prompt_sha256`；任一侧缺 `outputs` 是「没测」→ 退出码 `2`，不是通过。
- **采样解码分布稳定性门禁**（`stability` 规则）：`"stability": {"against_baseline": true,
  "max_divergence_rate": 0.2}`。temperature>0 时连续批处理的浮点噪声被采样放大，逐字节一致
  （invariance）是错误预期；stability 改判分歧率（输出不一致占比 ≤ 上限，0.0 退化为硬一致）。
  可比性键 = invariance 四键 + 采样参数（`temperature / top_p / seed`）。实测依据：贪心 8/8
  逐字节一致 vs 采样 0.7+seed 7/8 一致（`results/invariance-2026-11-10/verdict-sampled.md`）。
- `run` 采样参数：`--temperature / --top-p / --seed`（默认 temperature=0 贪心，历史行为不变）；
  采样参数进报告 `config`，不同采样路径的报告被判不可比。
- **多 prompt 独立样本口径**：`run_benchmark(prompts=[...])` 每请求独立 prompt + `seed+i` 独立种子，
  修掉「单 prompt × 单 seed 的 N 个请求是同一 query 的 N 份拷贝」导致的 divergence_rate 分母失真
  （verdict-matrix 补充发现 2）；`prompt_sha256` 变为逐请求哈希串联。
- **p99_basis**：count<20 的分布节点带 `"p99_basis": "max"`（p99 实际是最大值，verdict-matrix
  补充发现 3）；gate 对 `*.p99` 上限规则自动附 basis 见证检查（不改判决）。
- 门禁新增 `_check_client_evidence`：`client_lag_p95_ms` 必须能追溯到 `client` 块；
  报告 `kind` 不是 `run` 直接判配置错误。

### 变更

- `run` 报告新增 `kind: "run"`、`client` 段与 `config.client_timing`。
- 变异反证 32 → **41** 条（M31–M39，覆盖 lag/scan/CI/门禁来源校验，以及 client 块形状）。

### 修复

- **client 块形状的静默缺陷**：runner 一度把 `lag_stats()` 的平铺结果直接塞进 `report["client"]`，
  而 metrics/gate/report 都按 `client["lag_ms"]["p95_ms"]` 读，导致 `summary.client_lag_p95_ms`
  恒为 `null`、报告永远写「未取到 lag 样本」——整套客户端污染观测在真实运行里是死的，
  单测却因「null 分支」全绿。现在 runner 统一产出容器形状，并由端到端用例 + M39 变异钉住。
- `docs/papers.md` 的 A1 从「计划」改为「已实现（客户端 lag）」，A3 明确「`utilization.gpu` 只作负载指示」。

## [0.2.1] - 2026-11-01

第二轮独立审阅（N4–N8）后的门禁加固。全部是 **fail-open** 方向的问题：报告能被改得比真实情况更好看。

- **指纹溯源**：`config.hardware` 必须等于门禁用 `hardware` 块自己重算的指纹。此前只信 config 的自称，
  把块改成另一块卡而 config 不动，可比性门禁会被直接绕过（「写对就拦、写错反而不拦」）。
- **截断采样不能当证据**：`stop()` 卡在采样器里时峰值可能被低报，硬件规则遇到 `truncated` 判 FAIL。
- **物理合理性**：显存读数非负、峰值不得超过设备总量；`delta` 仍必须等于 `peak − baseline`。
- **Collector 参数**：无采样器（`Collector(None)`）与 `max_series < 2` 直接报错，不再在 `block()` /
  降采样里崩成 `AttributeError` / `IndexError` / `ZeroDivisionError`。
- **CI 3.10**：`tests/test_package.py` 不再 `import tomllib`（3.11+ 才有）——v0.2.0 的 3.10 job 就是挂在
  这里；版本判据不变（仍逐字比较 `pyproject` 的 `[project] version` 与 `ttftkit.__version__`）。
- **文档**：`docs/gate.md` 补硬件可比性两条与 fail-closed 清单新增行。

变异检查从 28 条增至 32 条（M27–M30 覆盖上述四项），全部实测变红。
- **变异检查本身**：M25 的锚点不再写死版本号（发版 bump 会让锚点漂成 `SETUP-FAIL`），
  子进程改为先摘掉 editable finder 再断言 import 到的 `ttftkit` 就在临时副本里——
  否则「变异没生效」会表现为「测试通过」这种假绿。plain 环境与 `pip install -e .` 环境都实测 32/32 全红。

## [0.2.0] — 2026-11-01

### 新增

- **GPU 显存 / 利用率采样**：`ttftkit run --gpu auto|none|command`。`auto` 依次探测 `nvidia-smi`、`rocm-smi`；
  `command` 允许任何输出同样 CSV 格式的命令。报告顶层新增 `hardware` 段，summary 新增
  `mem_used_peak_mb` / `mem_used_delta_mb` / `gpu_util_peak_pct` 三个标量，可直接用现有门禁规则判。
- **采集窗口三数**：`baseline`（窗口起点）、`peak`（峰值）、`delta = peak − baseline`。
  `delta` 抓的是阶梯式泄漏，比只看末值更稳。
- **影子门禁** `scripts/shadow_gate.py`：完整判定并打印逐条读数，默认不阻断；`--strict` 才透传退出码。
- **实测基线** `baselines/`：同机连跑 5 次 + 自变异统计（TTFT p95 极差 143.3% 等），`main.json` 取中位那一次。
- **判据**：门禁对硬件指标做证据链校验——summary 必须能追溯到 `hardware` 块、块内三数自洽、
  两侧 `config.hardware` 指纹相同才可比。
- **学习手册** `docs/labs.md`（8 个可复跑实验，含 JD 映射）与 `docs/ecosystem.md`（28 个已核实的上游项目）。

### 修复

- `stop()` 的 join 超时不再静默丢在途样本：记 `truncated=true` + `SamplerBusyAtStop`，报告声明峰值可能低报。
- 单样本时 `mem_used_delta_mb` 为 `null`（此前是 `0.0`，会冒充「测到没涨」并骗过绝对上限）。
- 采样器一次都没采到时仍返回 `hardware` 块（`samples=0`、错误可见），不再与「未采样」混为一谈。
- 硬件指纹拿不到设备时返回 `null`（此前返回字符串 `"none"`，truthy，会让两侧都被判成同一块卡）。
- `gpu_util_peak_pct` 方向修正：`limits` 是下限（要求真跑满卡），此前当成上限，99% 负载会被判失败。
- 影子门禁读不到输入（缺文件 / 坏 JSON）返回 2，与「规则不通过」区分。
- 报告三态渲染：未采样 / 采样失败 / 有读数；空 hardware 对象报 `ValueError`。

## [0.1.0] — 2026-10-31

### 新增

- OpenAI 兼容端点的闭环并发测量：TTFT（发出 → 首个非空 `delta.content`）、
  TPOT（解码段 ÷ `completion_tokens − 1`）、token 吞吐（分母为墙钟窗口）、错误率、
  `latency_ms.cv` 与 `degradation_ratio`。
- 门禁 `ttftkit gate`：绝对上限/下限 + 相对回归（对基线），退出码 0 / 1 / 2。
- Markdown + JSON 报告；SSE 异常隔离到单个请求；总时限（含服务器无换行滴流）。
- 变异反证脚本 `scripts/red_check.py`：每处修复回退后对应用例必须变红。
