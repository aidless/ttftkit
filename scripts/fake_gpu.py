#!/usr/bin/env python3
"""模拟一块 GPU 的 nvidia-smi CSV 输出，用于在无 GPU 机器上做「显存泄漏必须被看见」的验收。

用法：
    python scripts/fake_gpu.py                # 固定占用 512MB（基线场景）
    python scripts/fake_gpu.py --leak-mb 64   # 每次被调用多占 64MB（注入已知泄漏）

它只是一块「会说话的假硬件」，用来验证采样链路本身；**不是**性能数据来源。
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def _state_path() -> Path:
    return Path(tempfile.gettempdir()) / "ttftkit-fake-gpu.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="假的 nvidia-smi，输出同样格式的 CSV")
    parser.add_argument("--leak-mb", type=float, default=0.0, help="每次调用增加的显存（MB）")
    parser.add_argument("--baseline-mb", type=float, default=512.0)
    parser.add_argument("--total-mb", type=float, default=6144.0)
    parser.add_argument("--util", type=float, default=63.0)
    args = parser.parse_args()

    state = _state_path()
    used = args.baseline_mb
    if args.leak_mb:
        try:
            used = float(json.loads(state.read_text(encoding="utf-8"))["used_mb"])
        except (OSError, ValueError, KeyError):
            used = args.baseline_mb
    print(f"Fake GPU, 0.0, {args.total_mb:g}, {used:g}, {args.util:g}")
    if args.leak_mb:
        state.write_text(json.dumps({"used_mb": used + args.leak_mb}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
