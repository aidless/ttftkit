# -*- coding: utf-8 -*-
"""查询级矩阵跨跑对照：从落盘 JSON 重算全部数字，不依赖会话记忆。

三次独立跑（runB / runC / runD）用同一组 8 条 prompt、同一 seed 基值：
- 每跑内部：c=1 baseline 对 c=4/c=16 的分歧槽位与 rate；
- 跨跑：c=1 baseline 自身是否可复现、哪些 query 反复翻、哪些只在某跑翻；
- 判决：同一 stability 上限在不同跑次下给出什么结论，能否当阈值证据。
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ttftkit.gate import _wilson_upper, _min_n_zero_divergence

OUT = Path(__file__).resolve().parent.parent / "results" / "invariance-2026-11-10"
TAGS = ["runB", "runC", "runD"]
CAP = 0.2


def wilson_lower(k, n, z=1.96):
    """二项比例 Wilson 95% 下界（与 gate._wilson_upper 同公式的下侧）。"""
    p = k / n
    z2 = z * z
    return (p + z2 / (2 * n) - z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


def load(tag, kind):
    return json.loads((OUT / f"{kind}-queries-{tag}.json").read_text(encoding="utf-8"))


def outputs(report):
    return {o["index"]: o["text"] for o in report.get("outputs", [])}


def main():
    runs = {}
    for tag in TAGS:
        base = load(tag, "baseline-c1")
        cands = {c: load(tag, f"candidate-c{c}") for c in (4, 16)}
        bo = outputs(base)
        runs[tag] = {"base": base, "bo": bo, "cands": cands,
                     "diff": {c: sorted(i for i in bo if bo[i] != outputs(cands[c]).get(i))
                              for c in (4, 16)}}

    lines = [
        "# 查询级分歧率跨跑对照（2026-11-10）",
        "",
        "- 数据：三次独立跑（runB / runC / runD），同一组 8 条独立 prompt、seed 基值 1234、",
        "  temperature=0.7、max_tokens=128；全部数字由本脚本从 "
        "`*-queries-run*.json` 现算。",
        f"- stability 上限 {CAP:g}（与 verdict-matrix 单 prompt 拷贝口径同档）。",
        "",
        "## 逐跑读数",
        "",
        "| 跑次 | c=4 分歧槽位 | c=4 rate | c=16 分歧槽位 | c=16 rate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for tag in TAGS:
        d = runs[tag]["diff"]
        lines.append(f"| {tag} | {d[4]} | {len(d[4])/8:.3f} | {d[16]} | {len(d[16])/8:.3f} |")

    # c=1 baseline 自身跨跑复现性
    pairs = [(TAGS[i], TAGS[j]) for i in range(len(TAGS)) for j in range(i + 1, len(TAGS))]
    base_same = {}
    for a, b in pairs:
        ao, bo_ = runs[a]["bo"], runs[b]["bo"]
        same = [i for i in sorted(ao) if ao[i] == bo_.get(i)]
        base_same[f"{a}/{b}"] = same
    lines += [
        "",
        "## c=1 baseline 自身跨跑复现性",
        "",
        "- 同一 prompt+seed 在 c=1（无并发批处理）下逐字节一致数：",
    ]
    for k, v in base_same.items():
        lines.append(f"  - {k}: {len(v)}/8 一致" + (f"，不一致 index={sorted(set(range(8))-set(v))}" if len(v) < 8 else ""))

    # 每个 query 的翻转频次
    lines += ["", "## 逐 query 翻转频次（同一条 prompt 在 3 跑里被 batch 翻了几次）", ""]
    freq = {}
    for c in (4, 16):
        cnt = {i: 0 for i in range(8)}
        for tag in TAGS:
            for i in runs[tag]["diff"][c]:
                cnt[i] += 1
        freq[c] = cnt
        lines.append(f"- c={c}: " + ", ".join(f"q{i}={cnt[i]}/3" for i in range(8)))
        always = [i for i, n in cnt.items() if n == 3]
        never = [i for i, n in cnt.items() if n == 0]
        lines.append(f"  - 每跑必翻 index={always}；三跑都不翻 index={never}")

    # 判定口径结论
    upper0 = _wilson_upper(0, 8)
    need = _min_n_zero_divergence(CAP)
    rates = [len(runs[t]["diff"][c]) / 8 for t in TAGS for c in (4, 16)]
    lines += [
        "",
        "## 判决：这一档上限能不能用 8 条 query 判",
        "",
        f"- 六格 rate 范围 {min(rates):.3f}–{max(rates):.3f}；"
        f"按 {CAP:g} 上限，三跑的 c=4/c=16 全部 FAIL 的有 "
        f"{sum(1 for r in rates if r > CAP)}/{len(rates)} 格（点估计口径）。",
        f"- 但三次跑的是**同一组 8 条 query**，不是 24 条独立 query：样本量按 query 计仍是 8，",
        "  重复跑只量到单轮 batch 时序的抖动，不能把分母乘 3。",
        f"- n=8、0/8 的 Wilson 95% 上界 = {upper0:.3f} > {CAP:g}；"
        f"零分歧下要压到 {CAP:g} 以下需 n≥{need} 条**不同** query（gate 的 power 见证同源）。",
        f"- 对称地，3/8 的 Wilson 95% 下界 = {wilson_lower(3, 8):.3f}"
        f"（{'≤' if wilson_lower(3, 8) <= CAP else '>'} {CAP:g}），"
        f"4/8 的下界 = {wilson_lower(4, 8):.3f}"
        f"（{'≤' if wilson_lower(4, 8) <= CAP else '>'} {CAP:g}）：",
        "  c=4 的 FAIL 不是「总体 rate 必然超限」的证明，c=16 的 FAIL 才勉强越线。",
        "  gate 的 power 见证只挂 PASS，是 fail-closed 取舍：PASS 放行部署（危险方向）必须带不确定性，",
        "  FAIL 拦截部署只是保守；两种方向的置信界都应由报告自行算出。",
        "- 结论：stability 闸的 PASS/FAIL 是「本轮这 8 条 query 的点估计」，",
        "  不是「总体分歧率 ≤ 上限」的判决；要下后一种结论，先扩到足够多的独立 query 并声明口径。",
        "",
        "## 转引层：首次跑（未打 tag）",
        "",
        "- 首次查询级跑的控制台读数为 c=4 分歧 0/8、c=16 分歧 1/8（当时 stability 判 PASS）；",
        "  其原始 JSON 在打 tag 之前被后续跑覆盖，只剩控制台转引，**不可复算，不进上表、不用于判决**。",
        "- 该跑的 c=1 TTFT p50=123.4 / p99=385.6（尾部有 399ms 离群），晚三轮 p50=109–116 / p99=124–167，",
        "  提示服务冷启动状态可能影响读数；这只是提示，未做受控验证。防止证据覆盖的修法已落地：",
        "  `invariance_queries_run.py <tag>` 每跑写独立文件名。",
        "",
        "## 与单 prompt 拷贝口径的关系",
        "",
        "- verdict-matrix 的 2/8(c=4)、1/8(c=16) 是同一 query 的槽位翻与不翻，",
        "  本表的 8 个槽位是 8 条不同 query；两者量纲不同，不可直接相减或换算。",
    ]
    (OUT / "verdict-queries-multirun.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "verdict-queries-multirun.json").write_text(json.dumps({
        "tags": TAGS, "cap": CAP,
        "diff": {t: {str(c): runs[t]["diff"][c] for c in (4, 16)} for t in TAGS},
        "baseline_same": base_same,
        "flip_freq": {str(c): freq[c] for c in (4, 16)},
        "rates": rates,
        "wilson_upper_0_of_8": upper0,
        "wilson_lower_3_of_8": wilson_lower(3, 8),
        "wilson_lower_4_of_8": wilson_lower(4, 8),
        "min_n_zero_divergence": need,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
