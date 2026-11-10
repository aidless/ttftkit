# 发布准入门禁

## 1. 退出码

| 退出码 | 含义 | CI 处置 |
| --- | --- | --- |
| `0` | 全部规则通过（可能带 WARN 贴边提示） | 放行 |
| `1` | 有规则不通过 / 有失败请求 / 缺指标 | 阻断 |
| `2` | 策略或报告无效，或两次测量不可比 | 阻断并修配置，不要重试 |

`2` 与 `1` 分开很重要：`1` 是「模型/服务变差了」，`2` 是「你的门禁本身坏了」。混在一起会让团队习惯性重跑，直到某次碰巧通过。

## 2. 两类规则

```json
{
  "limits": {
    "ttft_ms.p95": 800,
    "tpot_ms.p95": 60,
    "error_rate": 0.0
  },
  "regressions": {
    "ttft_ms.p95": 10,
    "output_tokens_per_s": 5
  }
}
```

**`limits`（绝对）**：延迟/错误率是上限，吞吐类（`output_tokens_per_s`、`requests_per_s`、`goodput_per_s`）与 `gpu_util_peak_pct` 是下限，方向内置。

可用的标量指标：`requests`、`successful_requests`、`error_rate`、`requests_per_s`、`output_tokens_per_s`、`token_usage_coverage`、`goodput_per_s`、`degradation_ratio`、`mem_used_peak_mb`、`mem_used_delta_mb`、`gpu_util_peak_pct`、`client_lag_p95_ms`（v0.3）。分布指标可写 `ttft_ms.p95` 这类路径（字段见 `docs/metrics.md`）。

**`regressions`（相对）**：允许相对基线退化的百分比。延迟类 `observed ≤ baseline × (1 + pct/100)`；吞吐类 `observed ≥ baseline × (1 − pct/100)`。基线为 0 时（例如 `error_rate`），任何正值都算退化。

**贴边 WARN**：合规但距离阈值不足容差的一半时标 `WARN`（放行但提示）。WARN 不改变退出码。

## 3. 阈值怎么定（别拍脑袋）

1. 同一环境连跑 5 次基线，取每次的 `ttft_ms.p95`，看**自身波动**（`max/min`）。
2. 绝对阈值 = 业务可接受上限（如「首字 1 秒内」），而不是「当前值 × 1.1」。
3. 相对容差 = 至少 2 倍自身波动，否则门禁会被噪声随机打红。

自身波动 8% 却设 5% 容差的门禁，等于掷硬币。**门禁的误报率本身要可测**：连续跑 20 次基线对基线，红了几次就是它的假阳性率。

## 4. 可比性要求

相对比较前，两份报告必须同协议指纹：

```
protocol / model / concurrency / max_tokens / requests / prompt_sha256 / profile
```

任一不一致 → 退出码 `2`。`profile` 是使用者自己填的部署标识（如 `rtx4090-vllm-0.6.3`），它**不验证**硬件是否真的一致——那需要外部证据（`nvidia-smi` 输出、镜像 digest）。

**硬件指标另有两条可比性**（`mem_used_peak_mb`、`mem_used_delta_mb`、`gpu_util_peak_pct` 出现在 `regressions` 或 `limits` 里时）：

1. `config.hardware` 必须在两侧都存在，且**不**等于占位值（`""`/`"none"`/`"null"`）——「两侧都没采样」不是「同一块卡」。
2. `config.hardware` 必须等于门禁**用 `hardware` 块自己重算**出来的指纹（`型号/总显存MB`）。只信 config 的自称等于「指纹写对就拦、写错（或陈旧）反而不拦」：把块改成另一块卡而 config 不动，过去会直接放行。

显存总量不同 = 不可比；这包括**绝对上限**：512 MB 的增长上限在 6 GB 卡与 24 GB 卡上不是同一件事。硬件读数本身也要物理合理（非负、峰值不超过设备总量），否则按配置错误处理。

`client_lag_p95_ms`（v0.3）**不做指纹校验**：它是压测机自己的排队时间，没有设备指纹可言。代价要说清：**跨机器比较这个数没有意义**，它只在同一台压测机、同一并发下回答「这一轮的数字被客户端污染了多少」。用它的正确姿势是设一个绝对上限（如 `"client_lag_p95_ms": 20`）当**测量可信度闸**，而不是当性能指标去比。

`prompt_sha256` 只存哈希：prompt 原文可能含内部业务信息，不进报告。

## 5. fail-closed 清单

| 输入 | 结果 | 理由 |
| --- | --- | --- |
| 指标缺失 / `null` | FAIL | 没测到 ≠ 达标 |
| `NaN` / `inf` / `"100"` / `true` | FAIL（或配置错误） | 类型混淆不能当数值 |
| 空策略 `{}` | 2 | 什么也没检查 |
| 未知指标名（`limtis`、`made_up`） | 2 | 拼错会静默不检查 |
| 负阈值 / `NaN` 阈值 | 2 | 规则本身无效 |
| `requests == 0` | 2 | 无证据 |
| `successful_requests > requests` | 2 | 报告自相矛盾 |
| 分布 `count` < 成功请求数 | FAIL | 指标只覆盖子集 |
| 任一请求失败 | FAIL | 默认不允许带病发布 |
| 两份报告配置不一致 | 2 | 不是同一个实验 |
| 硬件规则 + 缺指纹 / 占位指纹 / 指纹与块不符 | 2 | 跨卡比较无意义，五年前的指纹也不能当今天 |
| 硬件规则 + 采样窗口被截断（`truncated`） | FAIL | 峰可能被低报，「可能漏掉的数」不是证据 |
| `summary` 声称硬件值但报告没有 `hardware` 块 | 2 | 数字没有来源 |
| 块内 `delta` ≠ `peak − baseline`，或零样本带读数 | 2 | 证据自相矛盾 |
| 硬件读数为负 / 峰值 > 设备总量 | 2 | 物理上不可能 |
| `summary` 声称 `client_lag_p95_ms` 但报告没有 `client` 块 | 2 | 数字没有来源 |
| `client.lag_ms` 为 `null` 却声称 lag 值 / 两者不一致 | 2 | 证据自相矛盾 |
| 报告 `kind` 不是 `run`（如 `scan`） | 2 | 多级聚合不是一次测量，请挑一级 `levels[i].summary` 再判 |

失败预算默认是 **0**：不写 `error_rate` 上限时，任一请求失败即阻断。要允许偶发失败，必须显式把预算写进 `limits`（如 `"error_rate": 0.005` 表示 ≤0.5%），门禁按该预算判：`error_rate > budget` 才 FAIL。预算不是「默默放行」——它会作为一条显式的 `implicit` 检查出现在输出里。

基线同样是判据的一部分：`baseline` 缺 `successful_requests`、`requests=0`、或分布 `count` 不可用（例如只来自 1 个样本）时，相对比较直接判配置错误（退出码 `2`），不会拿薄样本当基准。

## 6. 灰度与回滚

1. **影子模式**：先把 `gate` 挂在 CI 里只记录不阻断（`continue-on-error: true`），跑两周，统计它会拦下多少次、其中多少次是误报。
2. **误报率可接受后**再转阻断。
3. 基线文件进仓库（`baselines/main.json`），每次「有意变更性能特征」（换驱动、升级引擎、改量化）时人工更新基线，并在 commit message 里写清原因。
4. 回滚判据：门禁红了且定位到具体规则（报告里 `[FAIL] ttft_ms.p95`），回滚到上一个绿色版本的镜像/配置，而不是先改阈值。

## 7. 反模式

- **用门禁阈值掩盖回归**：红了就调阈值。这会训练团队忽略红灯。
- **只比 p50**：p50 稳定但 p99 爆炸是典型退化（排队、GC、显存碎片）。
- **跨机器比绝对值**：不同 GPU/驱动/CPU 的数字放一起比较，结论不可用。
- **基线永不过期**：三个月前的基线代表的是另一个软件栈。
