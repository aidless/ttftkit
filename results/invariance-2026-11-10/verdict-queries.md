# 查询级分歧率判定（2026-11-10，多 prompt 独立样本口径）

- 口径：8 条同域改写 prompt（每请求独立 query，seed+i 独立种子），
  temperature=0.7，max_tokens=128——分歧率分母从此是独立 query 数。
- 判据：stability 闸，max_divergence_rate=0.2（与单 prompt 版同一上限）。

## c=1 vs c=4
- 分歧 3/8（rate=0.375）→ stability exit_code=1（FAIL）
- c=1  TTFT p50=109.0ms（n<20 的 p99=123.7 由末位样本主导，实测未必等于 max=123.9，只引 p50）
- c=4 TTFT p50=113.3ms（p99=142.1，max=142.1）
- 分歧槽位 index=[0, 2, 3]（每槽一条独立 query）
## c=1 vs c=16
- 分歧 4/8（rate=0.500）→ stability exit_code=1（FAIL）
- c=1  TTFT p50=109.0ms（n<20 的 p99=123.7 由末位样本主导，实测未必等于 max=123.9，只引 p50）
- c=16 TTFT p50=339.9ms（p99=341.7，max=341.8）
- 分歧槽位 index=[0, 2, 3, 4]（每槽一条独立 query）

## 与单 prompt 版对照
- 单 prompt 拷贝口径（verdict-matrix）：c=4 分歧 2/8=0.250 / c=16 分歧 1/8=0.125，
  分母是同槽拷贝的翻与不翻；
- 本轮查询级口径：分歧槽位即独立 query 翻转数，rate 直接可读为 query 级分歧率。
- 本轮最差档 rate=0.500（上限 0.2）。

## 统计功效：小样本的点估计不是达标证据
- n=8 时 0/8 的 Wilson 95% 上界 = 0.324 > 上限 0.2；1/8 的上界 = 0.471；本轮最差档 4/8 的上界 = 0.785。
  PASS 只说明点估计在限内，不足以声称 rate ≤ 上限；FAIL 同理只是本轮点估计越线。
- 零分歧下把 Wilson 上界压到 0.2 以下需 n≥16 条独立 query（gate 已自动附 power 见证，
  不改判决但写进 checks）。要判定这一档，实验应扩到该样本量以上的不同 prompt。
- 分歧率对 query 组合和单轮 batch 时序都敏感（同一组 prompt 重跑读数会变），
  报告必须声明口径与跑次；跨跑对照见 verdict-queries-multirun.md。

## p99 口径修正（本轮实测反例）
- 旧表述「n<20 时 p99 就是 max」过强：本轮 c=1 的 TTFT p99=123.7 而 max=123.9，两者并不相等。准确说法是 p99 由末位
  1–2 个样本主导、不是可分位估计；p99_basis="max" 是伪影标记，延迟结论只引 p50。