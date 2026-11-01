# 学习实验室（每条都能在本机或指定云端跑出可验证结果）

目标不是「读过文档」，而是每条都能留下**可复跑的读数**。下面 8 个实验按 JD 的五条职责排：

- 职责 1/2（benchmark 设计 + TTFT/TPOT/吞吐/显存/稳定性指标）
- 职责 3（profiler、CI 自动化、质量门禁）
- 职责 4（缺陷分级、根因归档、问题知识库）
- 职责 5（发布准入标准）

> 纪律：本机只做亚分钟级 smoke、日志解析、改代码（见仓库 `AGENTS.md` 与 `docs/roadmap.md`），
> 需要 GPU 的实验一律上云端机器跑，不在本机长跑。

## Lab 1 — 端到端测一次，读懂四个数

| 项 | 内容 |
| --- | --- |
| 目标 | 亲手产出一份报告，能解释 TTFT / TPOT / 吞吐 / 错误率各自的分子分母 |
| JD 映射 | 职责 1、2；加分项 vLLM/Transformers |
| 本机可行 | ✅（Ollama + CPU 小模型） |

```bash
export PYTHONPATH=src
python3 -m ttftkit run --endpoint http://127.0.0.1:11434/v1/chat/completions \
  --model qwen2.5-coder:0.5b --requests 8 --concurrency 1 --max-tokens 16 --warmup 1 \
  --profile local-cpu --gpu none --output results/lab1.json
```

**通过判据**：能回答并给出报告里的字段证据——

- TTFT 从「请求发出」算到「第一个非空 `delta.content`」，包含 DNS/TCP/TLS/排队/预填充；
- TPOT = 解码段 ÷ (`usage.completion_tokens` − 1)，分母 <2 或没有 usage 时为 `null`；
- token 吞吐分母是**墙钟窗口**，不是延迟之和（后者会低估）；
- SSE 块 ≠ token：报告里的 `chunk_gap_ms` 只是传输层间隔，**不能**叫 ITL。

对照阅读：`docs/metrics.md`（口径逐条定义）。

## Lab 2 — 给假服务端注入 12 种坏行为，看客户端会不会静静算错

| 项 | 内容 |
| --- | --- |
| 目标 | 训练「异常输入必须 fail-closed」的直觉，而不是只看 happy path |
| JD 映射 | 职责 3、4；加分项 pytest |
| 本机可行 | ✅ |

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_client_hardening.py -v
```

场景都在 `tests/conftest.py` 的 `SCENARIOS` 里：
`bad-json`（块本身不是 JSON）、`bad-chunk`（非法分块头）、`cut`（流中断）、`empty`（无 content 块）、
`no-usage`（缺 usage）、`dict-choices`（`choices` 是对象不是数组）、`bad-content`（content 是数字）、
`secret-usage` / `secret-finish`（服务端把秘密塞进 usage/finish_reason）、`trickle` / `trickle-nonl`（慢滴流）、
`http500`（服务端直接 500）。

**通过判据**：能说出每种坏输入被映射成什么——失败请求（`ok=false` + 固定文案）、还是整体不可信
（如缺 usage → 吞吐置 `null` 并报 `token_usage_coverage < 1`），以及为什么**不能**把服务端原始字节
写进 `error`（日志注入 / 泄密）。

## Lab 3 — 阈值是量出来的，不是拍的

| 项 | 内容 |
| --- | --- |
| 目标 | 用同机多次运行估出噪声地板，再据此定阈值 |
| JD 映射 | 职责 5；加分项 CI |
| 本机可行 | ✅ |

```bash
cat baselines/README.md          # 5 次实测表：TTFT p95 抖 143%，p50 抖 37.8%
PYTHONPATH=src python3 -m pytest -q tests/test_baseline_policy.py
PYTHONPATH=src python3 scripts/shadow_gate.py --report baselines/run3.json \
  --baseline baselines/main.json --policy configs/policy.baseline.json --strict; echo "exit=$?"
```

**通过判据**：解释为什么「相对基线退化 10% 就拦」在这台机器上会把噪声当回归；
自己算出某一列的极差/中位与 CV（表里已有，但要能复算），并说清绝对上限与相对容差各自的来源。

## Lab 4 — 影子门禁：先收集读数，再决定要不要卡人

| 项 | 内容 |
| --- | --- |
| 目标 | 体会「门禁上线路径」：影子模式 → 观察误报 → 收紧为强制 |
| JD 映射 | 职责 3、5 |
| 本机可行 | ✅ |

```bash
# 影子：无论 PASS/FAIL 都返回 0，只在日志里留读数
PYTHONPATH=src python3 scripts/shadow_gate.py --report baselines/run1.json \
  --baseline baselines/main.json --policy configs/policy.baseline.json; echo "exit=$?"
# 强制：发布线用 --strict，把 gate 退出码原样透传
PYTHONPATH=src python3 scripts/shadow_gate.py --report baselines/run1.json \
  --baseline baselines/main.json --policy configs/policy.baseline.json --strict; echo "exit=$?"
```

**通过判据**：能说清三种退出码的含义（0 通过 / 1 规则不通过或存在失败请求 / 2 策略非法或不可比），
并解释为什么「不可比」也必须是 2 —— 拿 24 GB 卡的读数和 6 GB 卡比是假比较。

## Lab 5 — CI 里跑测试、lint、反证、装机

| 项 | 内容 |
| --- | --- |
| 目标 | 读一遍 `.github/workflows/ci.yml`，知道每一道门在防什么 |
| JD 映射 | 职责 3；加分项 Jenkins/GitLab CI（概念同构）、pytest |
| 本机可行 | ✅ |

```bash
cat .github/workflows/ci.yml
PYTHONPATH=src python3 scripts/red_check.py    # 27/27 条必须 RED
python3 -m ruff check src tests scripts
python3 -m pytest -q
```

**通过判据**：能解释 `red_check.py` 为什么存在——它把每个修复「回退」到修复前，跑对应测试，
测试必须变红。只有这样才能证明测试测的是机制而不是装饰；它同时是「门必须能失败」的反证。
参考项目：`github-action-benchmark`（1,269★）——把 benchmark 结果写进 CI 并做阈值比较。

## Lab 6 — 服务端指标 vs 客户端指标：同一件事的两个数

| 项 | 内容 |
| --- | --- |
| 目标 | 分清「客户端测得慢」和「服务端算得慢」 |
| JD 映射 | 职责 2、3；加分项 Prometheus/Grafana、vLLM |
| 本机可行 | ⚠️ 需要一台能起 vLLM 的 GPU 机器（本机无 GPU） |

做法：在 GPU 机器上起 vLLM（开 `--metrics`），用 `curl :8000/metrics` 看服务端直方图
（vLLM 暴露 `vllm:time_to_first_token_seconds` 这类指标），同时用本仓库的客户端测量同一条请求。

**通过判据**：写出两张数表并解释差值来源——客户端 TTFT 含 DNS/TCP/TLS/排队/网络往返，
服务端直方图只含引擎内部时间；因此**同一次请求客户端一定 ≥ 服务端**（除非时钟/口径不同）。
参考项目：`vllm-project/vllm`（95,971★）的 benchmark/ 目录、`aiperf`（838★）、
`neuralmagic/guidellm`→`vllm-project/guidellm`（1,735★，注意已迁移）。

> ⚠️ 指标名会随版本变，必须以目标版本 `/metrics` 的实际输出为准，不要照抄别处的名字。

## Lab 7 — 显存泄漏：注入已知量，必须看得见

| 项 | 内容 |
| --- | --- |
| 目标 | 验证「显存监控」本身有效，而不是只验证它能出数 |
| JD 映射 | 职责 2；加分项 GPU 监控、DCGM |
| 本机可行 | ✅（用假采样器；真卡读数需 GPU 机器） |

```bash
# 假采样器：每次被调用多占 64 MB，模拟泄漏
# --session 换了才从 baseline 重新开始，否则会接着上一轮的占用量涨
PYTHONPATH=src python3 scripts/fake_gpu.py --leak-mb 64 --session lab7
PYTHONPATH=src python3 scripts/fake_gpu.py --leak-mb 64 --session lab7
PYTHONPATH=src python3 -m pytest -q tests/test_hardware.py tests/test_runner_hardware.py tests/test_cli_hardware.py
```

**通过判据**：能解释 `delta = peak − baseline` 为什么比「看末值」更能抓住阶梯式泄漏；
能说出 `null`（没采样）与 `0`（采到 0）为什么必须区分。
真卡路径：`--gpu auto` 走 nvidia-smi；对照项目 `NVIDIA/DCGM` + `dcgm-exporter`（1,920★，
字段名以它的 README 为准，常见形如 `DCGM_FI_DEV_FB_*`）。

## Lab 8 — 缺陷档案：从一次「静默 fail-open」写出 RCA

| 项 | 内容 |
| --- | --- |
| 目标 | 练「缺陷分级 + 根因 + 回归」的完整链路，产出可检索的条目 |
| JD 映射 | 职责 4 |
| 本机可行 | ✅ |

仓库里有一份真实的缺陷分级模板 `docs/defect-triage.md`。练习：用 `git log` 找一个已修的 fail-open，
按模板填一条 RCA：现象 → 复现命令 → 根因（哪一行判据不严）→ 影响面 → 修复 → 回归测试名。
两个现成样本，一个比一个刁：

```bash
git log --oneline | head -20
git show 87b0475   # 样本 A：分布 count=0 却带分位数，被当成「有证据」
git show b7b9ad6   # 样本 B：单样本时 delta=0.0 冒充 measured + 采样器全灭静默 + 指纹 "none" 绕门
PYTHONPATH=src python3 -m pytest -q tests/test_gate_failclosed.py -v
```

样本 B 的教训值得单独记住：三条缺陷同源——**「没有数据」被写成「数据是 0」**。
修法不是补一个 if，而是让 `null` 一路传到底（块 → summary → 门禁 → 报告），
再给每一环配一条「回退后必须变红」的变异反证（`scripts/red_check.py` M16–M20）。

**通过判据**：RCA 里每个数字与每句根因都能被命令复现（`git show` + 测试名），并且写清
「同类缺陷还可能出现在哪」——例如 `null` vs `0`、`count` 与实际样本数不一致、报告缺字段被默认补成合法值。

## 实验顺序建议

1 → 2 → 3 → 4 → 5（本机全可跑）→ 8（本机）→ 7（假采样器本机，真卡上云）→ 6（上云）。

做完 1–5 与 8，JD 的五条职责都有可展示的产物；6、7 是加分项里最能拉开差距的两块。
