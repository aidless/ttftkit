# ttftkit v0.1 设计与执行计划

> 本文是 v0.1 的执行计划，保留作历史记录。当前行为以 README、`docs/metrics.md`、`docs/gate.md`、`docs/FAILURE_MODES.md` 和 CHANGELOG 为准。文中「此版本不宣称 GPU 采样已经实现」说的是 v0.1 的范围；v0.2 起有采样代码，但仓库里仍然没有真卡读数。

目标：为自有 OpenAI 兼容推理服务建立可复算的性能回归门禁，作为推理测试岗位的学习项目。技术栈为 Python、asyncio、httpx、pytest、GitHub Actions。原始请求记录与配置保存在 JSON，Markdown 报告由同一记录生成。

## 测量契约

- TTFT：单调时钟记录从发送 HTTP 请求前到首个非空 content SSE 事件的时间，包含连接、网络、排队与 prefill。
- SSE chunk 不等于 token。记录块间隔，不将块间隔称为逐 token ITL。
- TPOT：首末内容事件的时间差 / (服务端 completion_tokens − 1)。少于两个 token、缺 usage 或不足两个内容事件均为 null。结果属于客户端近似，不能分解同一块内部的 token 时间。
- token 吞吐：所有成功请求的服务端 completion_tokens 之和 / 测量墙钟时间。任一成功请求缺计数则总体 token 吞吐为 null，覆盖率另报。
- 请求吞吐：成功请求数 / 墙钟时间。错误率使用全部测量请求作分母。
- warmup 在测量窗口前串行执行，不进入统计。并发模型为固定 worker、闭环负载；不代表固定到达率的线上流量。
- 输出缺失不是零。所有数值必须有限，非法 JSON、断流、空内容、非成功 finish_reason、HTTP 错误归为失败。
- GPU 显存与 profiler v0.1 提供接入实践文档；此版本不宣称 GPU 采样已经实现。

## 架构与验证顺序

| 步骤 | 产物 | 验证方式 |
| --- | --- | --- |
| 1 | `models.py`、`metrics.py`：请求记录、分布、聚合 | 先运行缺模块的失败测试；手算分位数、墙钟吞吐、缺计数边界后实现 |
| 2 | `gate.py`：绝对 SLO 与相对退化阈值 | 违规输入必 FAIL；合规与等于边界必 PASS；缺值、NaN、基线不兼容必阻断 |
| 3 | `client.py`：SSE 请求与并发编排 | 本地真实 HTTP SSE 服务测试正常、断流、坏 JSON、HTTP 500、usage 尾块；观察 RED 后实现 |
| 4 | `cli.py`：run、gate、report | 子进程测试退出码与报告可回读；无 key/endpoint query 落入报告 |
| 5 | README、CI、学习路线、故障模板 | 全套 pytest、ruff、wheel 打包、CLI smoke；独立审阅完整新仓库 diff |
| 6 | 独立 git 仓库与发布 | staged diff / 敏感信息检查；验证远端认证能力，公开发布须使用授权的 GitHub 通道 |

## 门禁契约

规则 JSON 使用 `limits`（指标最大值）与 `regressions`（相对基线允许退化百分比）。指标使用点路径，如 `ttft_ms.p95`、`error_rate`、`output_tokens_per_s`。延迟和错误率越低越好，吞吐越高越好。退出码 0=全部通过、1=违反规则或缺指标、2=规则/输入无效或不可比较。规则集为空必须拒绝；报告带失败请求时默认拒绝。相对比较要求测量协议、模型、并发、输出上限、请求数、输入 prompt 摘要与部署 profile 完全一致，部署 profile 由使用者标识硬件及后端配置，不能代替独立验证。

## 执行约束

只修改独立目录 `ttftkit/`，不修改现有 C++ 项目和工作区共享文件。所有实现先有失败测试；本地只进行亚分钟级 smoke；长时间模型/GPU 压测在云端完成。初版报告不包含真实性能优劣结论。学习路线、协议测试、真实模型结果分别描述，不能混报。
