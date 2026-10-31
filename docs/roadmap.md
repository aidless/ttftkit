# 路线图与 GPU 实践接入

v0.1 只做「客户端测量 + 回归门禁」，并且诚实地不包含 GPU 采样。下面是补上它需要的东西，以及每项的验收标准。

## v0.2：显存与 GPU 指标

目标字段（报告新增 `hardware` 段）：

```json
{
  "gpu": {"name": "RTX 4090", "driver": "550.54", "mem_total_mb": 24564,
          "mem_used_baseline_mb": 21100, "mem_used_peak_mb": 23890,
          "utilization_peak_pct": 98},
  "samples": 42
}
```

接入顺序（从轻到重）：

1. **`nvidia-smi --query-gpu=...`**：零依赖，1Hz 采样即可抓「显存随时间上涨」。用 `--query-gpu=memory.used,utilization.gpu,temperature.gpu --format=csv,noheader,nounits --loop=1`。
2. **pynvml / nvitop**：进程内采样，精度高，但需要 GPU 与驱动；本机（无 NVIDIA 用户态库）无法验证，必须在云 GPU 上跑通再合并。
3. **DCGM exporter + Prometheus + Grafana**：集群级监控，压测时同时抓 `DCGM_FI_DEV_FB_USED`、`DCGM_FI_DEV_GPU_UTIL`。这是 JD 加分项里的 Prometheus/Grafana 组合。

验收标准：注入一个已知的显存泄漏（例如故意不释放 KV block），`mem_used_peak_mb − mem_used_baseline_mb` 必须随时间单调上升，且报告能抓到。**没有这个反证，采样就是装饰。**

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