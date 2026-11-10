# deploy-route 验收判决（2026-11-10）：vLLM LoRA 挂载部署 PASS

## 判决：PASS —— QLoRA adapter 不经合并、以 vLLM LoRA 模块挂载直接服务，三闸全过

- 服务：vLLM 0.33.0 / Qwen3-4B-AWQ（AWQ 4-bit 底模）+ `--enable-lora --lora-modules p1v2=…/tmlr-qwen3-4b-p1v2 --max-lora-rank 64` @ 127.0.0.1:8124
- 先行验证：`agent` adapter（tmlr-qwen3-4b-agent）同样挂载成功（deploy-lora-agent.json，4/4 成功，gate PASS），证明 LoRA+AWQ 路线可复制，非单 adapter 特例
- 验收 bench：p1v2 adapter，8 请求 / c=2 / max_tokens 128 / warmup 2（deploy-p1v2-bench.json）
- 闸策略：limits（ttft_ms.p99<=3000, tpot_ms.p99<=200, error_rate=0），deploy-p1v2-bench.json 过闸 exit=0

## 实测数字

| 指标 | agent adapter（先行） | p1v2 adapter（验收） |
|---|---|---|
| 成功率 | 4/4 | 8/8 |
| TTFT p99 (ms) | 376.8 | 684.4 |
| TPOT p99 (ms) | 103.3 | 177.1 |
| 输出吞吐 (tok/s) | 19.63 | 16.26 |
| 电费 (¥/1M out tok) | 0.506* | 0.345 |

\* 电费口径：1 Hz nvidia-smi power.draw 采样（power-lora.csv / power-p1v2.csv），截去采样器启动/收尾各 15 个样本取均值，电价 ¥0.6/kWh（对齐 vllm-serving-card card.json）。1024 输出 token 单轮，方差大，只作量级参考。

## 质量侧实测（paper_agent/reports/eval_3way_v2.json）

- base（无 adapter）：evd 证据引用率 0.125（1/8），平均生成长度 540 tok
- p1v2 adapter：evd 1.0（8/8），平均生成长度 418 tok
- 注意：评测是离线 transformers 路径做的（不是本次 vLLM 服务端点），同一 adapter 权重；把「离线质量数字」直接安到「在线服务路线」头上是本判决的已知近似，已在 cost-per-correct 真实案例里声明

## 对 playbook 的回填

playbooks/cloud-qlora-finetune.yaml 的 deploy-route 步骤由 done:false → done:true，证据=evidence_cite: ttftkit results/invariance-2026-11-10/（本目录）@ 2026-11-10。
