# 变更记录

格式按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号按语义化版本。

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
