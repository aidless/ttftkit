# 显存采样（v0.2）

v0.1 只测延迟与吞吐：报告里没有显存，也就无法回答「这次跑完，显存有没有涨」。
v0.2 补上 GPU 采样，验收标准不是「能采到数」，而是**注入一个已知泄漏，必须看得见它单调上涨**。

## 用法

```bash
# 默认 auto：探测 nvidia-smi，其次 rocm-smi，都没有就不采样（报告里会明确写「未采样」）
ttftkit run --endpoint http://127.0.0.1:8000/v1/chat/completions --model m --gpu auto

# 明确不采样
ttftkit run ... --gpu none

# 自定义命令：stdout 必须是 nvidia-smi 的 CSV 格式（name, driver, total, used, util）
ttftkit run ... --gpu command --gpu-command "/usr/bin/nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv,noheader,nounits"

# 采样间隔（默认 0.25 s；短跑用小间隔，长跑别低于 0.1 s 以免频繁 fork）
ttftkit run ... --sample-interval 0.1
```

## 采到的三个数

| 字段 | 定义 | 为什么这样定义 |
| --- | --- | --- |
| `mem_used_baseline_mb` | 测量窗口开始前的**第一次**同步采样 | 起点必须落在窗口外/边界，否则「起跑前已占用的部分」会算成增长 |
| `mem_used_peak_mb` | 窗口内所有采样的最大值 | 泄漏常常是阶梯式，峰值比末值更能暴露 |
| `mem_used_delta_mb` | `peak − baseline` | 这是门禁真正要卡的量；单看峰值无法区分「模型本来就占 6 GB」 |

报告同时保留 `mem_used_series_mb`（最多 120 个点，超出按等距抽样并保留末点），
用于人工看「是不是单调涨」。序列只用于诊断，不进门禁——门禁只用标量，避免形状判据的脆弱性。

## 采样器：要么采到，要么说清没采到

| 情况 | 报告里的表现 |
| --- | --- |
| 有 nvidia-smi | `hardware.source = "nvidia-smi"`，`name`/`driver`/`mem_total_mb` 齐全 |
| 有 rocm-smi | `hardware.source = "rocm-smi"`，字节 ÷ 1024² 转 MB |
| `--gpu none` 或无工具 | `hardware = null`，`summary.mem_used_*` 一律 `null`（**不是 0**） |
| 采样器中途报错 | `sampler_errors > 0`、`error` 记首个异常类名；只要有 1 次成功采样仍出块，并在报告里写明「数字只覆盖采样成功的时刻」 |
| 一次都没采到 | `hardware = null`；此时若策略要求看显存 → 门禁判「无可用值」FAIL |

`null` 与 `0` 的区别是这份工最关键的一条：`0 MB` 会被下游读成「测到了，没占显存」，
而 `null` 才能让门禁说「你没测」。这条有专门的反证（`red_check` M10：把 `return None` 改回 `return 0.0`，
`test_run_without_sampler_has_no_hardware_evidence` 立刻失败）。

## 坏输出必须报错，不能静默跳过

`parse_nvidia_smi` / `parse_rocm_smi` 对以下情况一律 `ValueError`：

- 字段数不是 5、`memory.total` 非数字、负数、`nan`/`inf`；
- rocm JSON 不是对象、没有 `card*` 条目、缺 `VRAM Total Memory (B)` / `VRAM Total Used Memory (B)`。

理由：采样器输出格式异常时「跳过这一次」会让报告看起来正常，实际是全程瞎子——
这正是 v0.1 里 `count=0 却带分位数` 那类 fail-open 的同一个坑。

## 怎么验证它真的能看见泄漏（三层，全部可复跑）

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_hardware.py tests/test_runner_hardware.py tests/test_cli_hardware.py
PYTHONPATH=src python3 scripts/red_check.py     # M9–M12 必须 RED
```

| 层 | 做法 | 断言 |
| --- | --- | --- |
| 单元 | `FakeSampler([400, 528, 656, 784, 912])` 模拟每次 +128 MB | `delta = 512`、序列严格单调、`is_monotonic` 为真 |
| 端到端 | 真子进程 `scripts/fake_gpu.py --leak-mb 64`（每次被调用多占 64 MB） | `samples ≥ 2` 且序列严格递增、`delta ≥ 64` |
| CLI | `--gpu command --gpu-command "<python> scripts/fake_gpu.py --leak-mb 32"` | 报告 `hardware.source == "command"`、`delta ≥ 32`，且 JSON 与 Markdown 一致 |

`scripts/fake_gpu.py` 把「已占用量」存在 `/tmp/ttftkit-fake-gpu.json`，所以连续调用会累加——
这就是「已知泄漏」的注入方式，不需要真的弄坏一个推理服务。

本地实测（无 GPU 机器）用 `--gpu none` 采的基线，`hardware` 全程为 `null`；
有卡机器上跑同一条命令即可看到真实数字。**本仓库不发布任何未经实测的显存数字。**

## 门禁怎么用显存

```json
{ "limits": { "mem_used_delta_mb": 512 }, "regressions": { "mem_used_peak_mb": 10 } }
```

- 绝对上限：`mem_used_delta_mb ≤ 512`（默认「越小越好」，与延迟同向）。
- 相对回归：显存类规则要求两侧报告的 `config.hardware` 指纹**相同**（`"name/totalMB"`），
  否则判「不可比」退出 2——24 GB 卡上的 1.8 GB 峰值拿去和 6 GB 卡比是没意义的。
  指纹缺失（旧报告没采样）同样判不可比，而不是默默当作同一块卡。

## 已知边界

- 只采**设备级**显存：同卡还有其他进程（别的服务、别人的任务）时，`delta` 里可能混入它们的变化。
  要排除，用独占机器或 MIG 隔离后重采基线。
- 采样是轮询：间隔内的一次性尖峰可能被漏掉；判断泄漏看趋势，不要只看单个 0.25 s 窗口。
- 不做 ECC / 温度 / 功耗 / PCIe 吞吐（那是 DCGM 的范围，见 `docs/ecosystem.md`）。
- 不做多卡聚合：nvidia-smi 默认返回第一块卡的 CSV 行，多卡场景要自己写 `--gpu-command`
  （例如 `nvidia-smi --id=0,1 ...` 后自行聚合）。
