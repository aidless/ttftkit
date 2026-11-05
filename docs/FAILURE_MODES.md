# 失败模式

退出码 0 表示「这份报告满足策略里写下来的规则」。下面三件事会让这个绿灯被读成它没有证明的结论。每条都标了证据从哪来。

- **OBSERVED**：仓库里的代码、测试或 JSON 能复核。
- **inferred**：从机制推出来的后果。本仓库没有一份线上服务「确实这样骗过门禁」的记录，不要把 inferred 写成事故。

锁行为的测试在 `tests/test_failure_modes.py`。补洞时改代码、改本文、改测试，三处一起动。

## 1. 假绿

### 已经关上的（OBSERVED）

这些是以前会静默放行、现在会失败的输入。名字对应 `scripts/red_check.py` 的 `CASES`，回退修复后对应测试变红。

| 输入 | 现在的结果 |
| --- | --- |
| 指标缺失 / `null` / `NaN` / `inf` / `"100"` / `true` | 不通过，不当作 0 |
| 空策略、未知指标名、非法阈值 | 退出码 2 |
| 分布 `count` 为 0 却带分位数，或 `count` 少于成功请求数 | 无证据，不通过 |
| 成功请求里只有一部分带 `completion_tokens` | token 吞吐为 `null`，不拿子集冒充整体 |
| 显存没采样却写成 0、单样本 `delta = 0`、零样本却带读数 | `null` 或配置错误 |
| `summary` 里的显存数字对不上 `hardware` 块，或块内 `delta ≠ peak − baseline` | 退出码 2 |
| 采样在 `stop()` 被截断 | 硬件规则 FAIL（峰可能被低报） |
| 指纹是 `"none"`、或 `config.hardware` 与块重算结果不一致 | 不可比，退出码 2 |
| `usage` 不是非负整数、`content` 不是字符串、`choices` 不是数组 | 该请求失败，不把坏事件算进延迟 |

### 仍开着的

**门禁不重算 `records`（OBSERVED）。** `evaluate()` 读 `summary`（以及硬件规则下的 `hardware` 块）。`records` 可以是空的，也可以写出和 summary 相反的成败，退出码仍可以是 0。`test_gate_ignores_records_and_wall_clock`、`test_inflated_usage_shrinks_tpot_and_gate_still_passes`。

入库的 `baselines/*.json` 和 `results/smoke-ollama.json` 用当前公式重算是对齐的（`tests/test_doc_drift.py`）。这只说明这几份文件没有被改坏，不说明门禁会拒绝下一份手改的 summary。

**只有 `limits` 时，基线的数字不参与比较（OBSERVED）。** 相对比较在 `regressions` 那一段。`configs/policy.json` 没有 `regressions`。命令行就算带了 `--baseline`，候选的 TTFT 也不会去减基线的 TTFT；模型名不同也可以退出码 0，只要绝对上限还盖得住。`test_limits_only_policy_does_not_compare_baseline_numbers`。硬件指标是例外：策略里出现硬件规则且提供了基线时，会比对 `config.hardware` 指纹。

**影子模式的退出码是 0（OBSERVED）。** `scripts/shadow_gate.py` 默认打印逐条 PASS/FAIL/WARN，然后返回 0。`--strict` 才把 `evaluate()` 的退出码透传出去。CI 的 `shadow-gate` 作业不带 `--strict`。作业本身会失败的情况只有两种：一条读数都没打印，或脚本报告无法读取输入。策略 FAIL 不会让这个作业变红。

**WARN 不改变退出码（OBSERVED）。** 贴着阈值但没越界是 WARN，进程仍是 0。

**`profile` 是调用者自己写的字符串（OBSERVED，见 `docs/gate.md`）。** 两次报告写了同一个 profile，门禁就当它们是同一套部署。它不核对机器。

**闭环并发不是到达率（OBSERVED，测量模型就是固定 worker）。** 排队只发生在这 N 个 worker 里面。线上若请求到达比服务快，TTFT 会更差；这份绿灯看不见那种排队。inferred：把闭环结果当成容量规划会偏乐观。

**小样本上的 p95（OBSERVED）。** `baselines/` 只有 8 个请求。TTFT p95 在这 5 次里的极差是 143.3%（`baselines/README.md`，由 JSON 重算）。`policy.baseline.json` 把相对容差放到 50%–60%，就是为了不把这份噪声判成回归。通过这份策略，意思是「没有超出当时测到的抖动」，不是「快」。

**显存是设备级轮询（OBSERVED，解析与假采样器；真卡读数不在仓库里）。** 同卡其他进程会进 `delta`。两次采样之间的尖峰可以不出现。inferred：一个只在间隔里出现的泄漏，这份报告可以是绿的。

## 2. 没有墙钟时间戳

**OBSERVED**

- `RequestRecord.started_at` 写入的是 `time.perf_counter()`（`src/ttftkit/client.py`）。单位是秒，起点是单调时钟，不是 Unix 纪元。
- `baselines/run1.json`–`run5.json`、`baselines/main.json`、`results/smoke-ollama.json` 的 `started_at` 都小于 `1e9`（2001-09-09 的 Unix 时间）。2026 年的墙钟大约是 `1.7e9`。这些数字对不上任何日历。
- 上述文件没有 `measured_at` / `measured_at_utc`。`test_checked_in_runs_have_no_wall_clock`。
- `evaluate()` 不读 `started_at`，也不要求报告带日期。没有时间字段的报告可以退出码 0。`test_gate_ignores_records_and_wall_clock`。
- `baselines/README.md` 里的「采集时间：2026-10-31」是作者记录，不是 JSON 字段。

**inferred**

- 不能把这份报告和服务器日志按时间对齐。
- 不能从文件本身判断它是不是上周跑的。一份旧报告，只要 summary 仍满足策略，门禁就当它是新的。
- 给报告加一个客户端墙钟字段，仍然证明不了服务器时钟，也证明不了没有人改过这个字段。在门禁不校验「时间字段存在且不可篡改」之前，本文不把缺时间戳写成已修复。

## 3. 服务端谎报 usage

TPOT 和 token 吞吐都信任 `usage.completion_tokens`。

**OBSERVED：什么会被拒绝**

- 缺 `usage`：该请求的 `output_tokens` 是 `null`，TPOT 是 `null`。不是 0，也不改用块数（`tests/test_client.py` 的 `no-usage`）。
- 成功请求里只要有一个缺计数，整体 `output_tokens_per_s` 就是 `null`（`src/ttftkit/metrics.py`）。
- `completion_tokens` 不是非负整数（字符串、布尔、负数）：该请求失败，错误文案不回显服务端原文。
- 策略要求 `tpot_ms.p95` 而分布是 `null`：FAIL，不是通过。

**OBSERVED：什么会被当成真的**

- 非负整数就会进公式：`tpot_ms = decode_ms / (completion_tokens − 1)`（`src/ttftkit/models.py`）。少于 2 则 TPOT 为 `null`。
- 夹具 `lying-usage`（`tests/conftest.py`）发 2 个内容块，`completion_tokens` 为 10000。请求仍是成功，TPOT 等于 `decode_ms / 9999`，实测小于 1 ms。拿这个数去对 `tpot_ms.p95 ≤ 50` 的绝对上限，退出码是 0。`test_inflated_usage_shrinks_tpot_and_gate_still_passes`。
- 吞吐的分子是同一个整数。报得越大，`output_tokens_per_s` 越高。

**为什么不用「块数 ≈ token 数」来打假（inferred，也是设计选择）**

一个 SSE 块可以含多个 token，尤其在带 `stream_options.include_usage` 或服务端合并增量的时候。块数少于 `completion_tokens` 是正常协议，不是谎言的证明。用块数做上限会把合法服务器判红。ttftkit 把块间隔单独放在 `chunk_gap_ms`，并写明它不是 ITL。

因此：服务端把 token 数报大（仍是整数、且 ≥ 2）时，延迟门禁会看到一个更好看的 TPOT。本仓库没有观察过某个真实推理引擎这样做。上面的证据是本地假 SSE，不是线上抓包。

报小也有方向：`completion_tokens` 仍 ≥ 2 但偏小，TPOT 变差、吞吐变低，绝对上限更可能红（假红，不是假绿）。报成 1 或省略，TPOT 变成 `null`，要求该指标的策略会失败。危险的方向是报大。

## 本文没有做的事

- 没有给报告补墙钟字段。
- 没有让门禁重算 `records`。
- 没有用 tokenizer 核对 `usage`。
- 没有把 `configs/policy.json` 的 1000 ms / 100 ms 说成测出来的阈值。
