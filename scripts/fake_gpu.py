#!/usr/bin/env python3
"""模拟一块 GPU 的 nvidia-smi CSV 输出，用于在无 GPU 机器上做「显存泄漏必须被看见」的验收。

用法：
    python scripts/fake_gpu.py                # 固定占用 512MB（基线场景）
    python scripts/fake_gpu.py --leak-mb 64   # 每次被调用多占 64MB（注入已知泄漏）
    python scripts/fake_gpu.py --leak-mb 64 --session my-run   # 换 session 就从 baseline 重新开始

--session 是「一次实验」的标识：状态文件按 session 归属，session 变了才归零。
不这么设计就没有可复现性 —— 每次调用都带同一个 --reset 参数的话，泄漏永远涨不起来。

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
    parser.add_argument("--state", default=None, help="状态文件路径（默认系统临时目录）")
    parser.add_argument("--session", default="default",
                        help="本次实验的标识；换了 session 才从 --baseline-mb 重新开始")
    args = parser.parse_args()

    state = Path(args.state) if args.state else _state_path()
    used = args.baseline_mb
    # 状态落在临时目录且默认不清理：不带 session 会从上一轮的占用量接着涨。
    if args.leak_mb:
        try:
            saved = json.loads(state.read_text(encoding="utf-8"))
            if saved.get("session") == args.session:
                used = float(saved["used_mb"])
        except (OSError, ValueError, KeyError, TypeError):
            used = args.baseline_mb
    print(f"Fake GPU, 0.0, {args.total_mb:g}, {used:g}, {args.util:g}")
    if args.leak_mb:
        state.write_text(json.dumps({"session": args.session, "used_mb": used + args.leak_mb}),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
