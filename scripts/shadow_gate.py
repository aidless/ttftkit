"""影子门禁（shadow gate）：完整执行判定、打印逐条读数，但默认不阻断合并。

为什么需要影子模式：阈值刚从 5 次实测的自变异里定出来，直接拿来卡 PR 会把噪声当回归。
影子期只收集「真跑一遍的读数」，等样本够了再收紧成强制门。
`--strict` 用于本地/发布线：把 gate 的退出码原样透传。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ttftkit.gate import evaluate


def _load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def render(verdict: dict) -> list[str]:
    lines = []
    for check in verdict.get("checks", []):
        observed = check.get("observed")
        observed_text = f"{observed:g}" if isinstance(observed, (int, float)) else "n/a"
        threshold = check.get("threshold")
        lines.append(
            f"  [{check.get('status'):4}] {check.get('kind'):9} {check.get('metric'):22} "
            f"observed={observed_text} threshold={threshold} {check.get('reason') or ''}".rstrip()
        )
    for reason in verdict.get("reasons", []):
        lines.append(f"  reason: {reason}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="影子门禁：判定并打印，但不阻断（除非 --strict）")
    parser.add_argument("--report", required=True, help="候选报告 JSON")
    parser.add_argument("--policy", required=True, help="策略 JSON")
    parser.add_argument("--baseline", default=None, help="基线报告 JSON（相对门禁用）")
    parser.add_argument("--strict", action="store_true", help="把 gate 退出码原样返回（发布线用）")
    args = parser.parse_args(argv)

    report = _load(args.report)
    policy = _load(args.policy)
    baseline = _load(args.baseline) if args.baseline else None
    verdict = evaluate(report, policy, baseline)
    code = verdict["exit_code"]
    label = {0: "PASS", 1: "FAIL", 2: "不合法/不可比"}.get(code, str(code))
    print(f"shadow-gate: {args.report} vs {args.baseline or '(无基线)'} → {label}")
    for line in render(verdict):
        print(line)
    if args.strict:
        return code
    print("shadow-gate: 影子模式，不阻断（--strict 才会返回非零）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
