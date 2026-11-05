# results/

存放 `ttftkit run` 的输出（`*.json` 报告 + 同名 `*.md` 渲染）。默认只保留一份**链路演示样本**，其余本地结果不入库（见 `.gitignore` 的 `results/*` 规则）。

| 文件 | 说明 |
| --- | --- |
| `smoke-ollama.json` / `.md` | 一次已入库的测量输出。JSON 里的模型名是 `qwen2.5-coder:0.5b`，3 请求、并发 1、`max_tokens=16`。文件无 `hardware` 键 |

## 复现

```bash
ttftkit run --endpoint http://127.0.0.1:11434/v1/chat/completions \
    --model qwen2.5-coder:0.5b --requests 3 --concurrency 1 \
    --max-tokens 16 --warmup 1 --profile local-ollama-cpu \
    --output results/smoke-ollama.json
```

这是**一份历史输出**，不是离线就能重放的演示：命令要本机已经有 Ollama。样本仅 3 个；`degradation_ratio` 为 `null`（需要 ≥4 条成功记录）。「CPU、无 GPU」是作者记录，不在 JSON 里。文件也没有当前 `summarize()` 会写上的 `mem_used_peak_mb` / `mem_used_delta_mb` / `gpu_util_peak_pct`（重算结果是 `null`）。不要把 84.61 ms 当成模型结论。离线路径是 `python3 -m pytest`，见仓库 README。
