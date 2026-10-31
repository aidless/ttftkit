# results/

存放 `ttftkit run` 的输出（`*.json` 报告 + 同名 `*.md` 渲染）。默认只保留一份**链路演示样本**，其余本地结果不入库（见 `.gitignore` 的 `results/*` 规则）。

| 文件 | 说明 |
| --- | --- |
| `smoke-ollama.json` / `.md` | 本机 CPU + ollama `qwen2.5-coder:0.5b`，3 请求、并发 1、`max_tokens=16` 的真实测量输出 |

## 复现

```bash
ttftkit run --endpoint http://127.0.0.1:11434/v1/chat/completions \
    --model qwen2.5-coder:0.5b --requests 3 --concurrency 1 \
    --max-tokens 16 --warmup 1 --profile local-ollama-cpu \
    --output results/smoke-ollama.json
```

这是**链路可用性演示**，不是任何模型的性能结论：样本仅 3 个、CPU 推理、无 GPU 参与；`degradation_ratio` 为 `null`（需要 ≥4 条成功记录）。
