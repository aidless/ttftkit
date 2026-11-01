# 路线图与 GPU 实践接入

v0.1 只做「客户端测量 + 回归门禁」，并且诚实地不包含 GPU 采样。v0.2 已补上显存采样（见下），v0.3/v0.4 仍未做。

## v0.2：显存与 GPU 指标（已完成）

实际落地的形状与最初计划有一处不同：不再做成「`gpu` 对象 + 顶层 `samples`」，而是报告顶层一个 `hardware` 段，
summary 里三个标量（`mem_used_peak_mb` / `mem_used_delta_mb` / `gpu_util_peak_pct`），
以便门禁直接按标量规则判——对象内嵌的嵌套字段在策略里既要拼路径又难做可比性检查。

```json
"hardware": {"source": "nvidia-smi", "name": "NVIDIA GeForce RTX 3060 Laptop GPU",
             "driver": "580.95.05", "mem_total_mb": 6144,
             "mem_used_baseline_mb": 512, "mem_used_peak_mb": 1024,
             "mem_used_delta_mb": 512, "utilization_peak_pct": 37,
             "samples": 42, "interval_s": 0.25, "sampler_errors": 0}
```

接入顺序（从轻到重）：

1. **`nvidia-smi --query-gpu=...`**：零依赖，`--sample-interval` 默认 0.25 s。✅ 已实现（`auto` 优先探测它）
2. **rocm-smi**：AMD 卡走 `--showmeminfo vram --showuse --json`，字节 ÷ 1024² 转 MB。✅ 已实现（本机有这个工具但无设备，只有解析层单测覆盖）
3. **自定义命令**：`--gpu command --gpu-command "..."`，stdout 须是 nvidia-smi CSV 格式；多卡/自定义字段的逃生口。✅ 已实现
4. **pynvml / nvitop**：进程内采样，精度高，但需要 GPU 与驱动；本机（无 NVIDIA 用户态库）无法验证，**推迟到能在云 GPU 上跑通再合并**。
5. **DCGM exporter + Prometheus + Grafana**：集群级监控，压测时同时抓 `DCGM_FI_DEV_FB_USED`、`DCGM_FI_DEV_GPU_UTIL`（字段名以 dcgm-exporter README 为准）。这是 JD 加分项里的 Prometheus/Grafana 组合，仍未接。

验收标准（已过）：注入已知显存泄漏，`mem_used_peak_mb − mem_used_baseline_mb` 必须看得见单调上涨。
做法是 `scripts/fake_gpu.py --leak-mb 64`（每次被调用多占 64 MB）+ `tests/test_runner_hardware.py` 的真子进程端到端断言
（`samples ≥ 2`、序列严格递增、`delta ≥ 64`）。三层验证与门禁用法的细节见 [`hardware.md`](hardware.md)。

同时落地：`baselines/` 五次实测 + `configs/policy.baseline.json` + `scripts/shadow_gate.py`（影子模式），
阈值来自实测噪声而不是拍脑袋，见 [`../baselines/README.md`](../baselines/README.md)。

> 诚实边界：真卡读数**尚未**在本项目实测（本机无 NVIDIA 设备），只有假采样器链路被验证过。

## v0.3：profiler 交接

- `--profile-trace` 选项：压测同时用 `torch.profiler` 或 `nsys profile` 抓一段 trace，`*.nsys-rep` / `*.json` 落到 `results/`。
- 报告里记录 trace 的时间窗口与当时并发，避免「trace 是空载时抓的」这类错误归因。
- 分析流程写进 `docs/profiler.md`：先看 kernel 时间占比，再看是否有 GPU idle（说明在等 CPU/网络），最后才怀疑算子。

## v0.4：更真实的负载

- 到达率驱动（poisson）替代固定 worker 闭环，与线上流量形状对齐。
- 数据集驱动的输入长度分布（sharegpt / 内部日志），报告里记录长度分布摘要而不是单条 prompt。
- 多轮对话场景（前缀复用对 KV cache 命中率影响很大）。

## 在云 GPU 上跑（本机没有 NVIDIA 设备）

本机是 Ryzen 5800H + 无 NVIDIA 用户态库，所以 GPU 相关能力**无法在本机验证**。推荐路径：

```bash
# 云 GPU（AMD 实例 / 阿里云 ECS / Kaggle 均可，重点是能起 vLLM）
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-7B-Instruct --max-model-len 4096 --port 8000 &

# 压测（在能访问该端点的机器上）
ttftkit run --endpoint http://127.0.0.1:8000/v1/chat/completions \
    --model Qwen/Qwen2.5-7B-Instruct --requests 64 --concurrency 16 \
    --max-tokens 256 --warmup 4 --profile "a10-vllm-0.6.3" --output results/a10.json
```

跑完把 `results/a10.json` 连同 `nvidia-smi -q` 的输出一起归档，作为「这份报告在什么硬件上取得」的证据。

## 明确不做

- 不做分布式多机压测（用 `aiperf`）。
- 不做模型精度评测（那是 lm-eval-harness 的活）。
- 不内置 LLM 生成的「结论」——报告只给数字与判定，解读由人写。