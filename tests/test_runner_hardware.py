"""把采样器接进真实测量流程：报告里必须有硬件块，且注入的泄漏必须被看见。"""
import asyncio
import sys
from pathlib import Path

from test_hardware import FakeSampler

from ttftkit import hardware
from ttftkit.runner import run_benchmark

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fake_gpu.py"


def _run(base, scenario="normal", sampler=None, interval=0.005):
    return asyncio.run(run_benchmark(
        endpoint=f"{base}/{scenario}/v1/chat/completions",
        model="tiny", prompt="hi", requests=3, concurrency=1, max_tokens=8, timeout_s=5,
        sampler=sampler, sample_interval_s=interval,
    ))


def test_run_attaches_hardware_block_and_summary_scalars(sse_base):
    result_adict = _run(sse_base, sampler=FakeSampler([500.0, 800.0, 1200.0]))
    hw = result_adict["hardware"]
    assert hw["name"] == "Fake GPU"
    assert hw["mem_used_baseline_mb"] == 500.0
    assert hw["mem_used_peak_mb"] == 1200.0
    assert result_adict["summary"]["mem_used_peak_mb"] == 1200.0
    assert result_adict["summary"]["mem_used_delta_mb"] == 700.0
    assert "Fake GPU" in result_adict["config"]["hardware"]


def test_run_without_sampler_has_no_hardware_evidence(sse_base):
    result_adict = _run(sse_base)
    assert result_adict["hardware"] is None
    assert result_adict["summary"]["mem_used_peak_mb"] is None
    assert result_adict["summary"]["mem_used_delta_mb"] is None
    assert result_adict["config"]["hardware"] == "none"


def test_leaking_gpu_command_is_detected_end_to_end(sse_base):
    # 真实子进程脚本，每次被调用多分配 64MB（模拟已知显存泄漏），走完整命令采样路径
    sampler = hardware.command_sampler([sys.executable, str(SCRIPT), "--leak-mb", "64"])
    result_adict = _run(sse_base, sampler=sampler, interval=0.02)
    hw = result_adict["hardware"]
    series = hw["mem_used_series_mb"]
    assert hw["samples"] >= 2, f"采样次数不足：{series}"
    assert all(b > a for a, b in zip(series, series[1:], strict=False)), f"泄漏未表现为单调上升：{series}"
    assert hw["mem_used_delta_mb"] >= 64.0
    assert result_adict["summary"]["mem_used_delta_mb"] == hw["mem_used_delta_mb"]
