"""CLI 接线：--gpu none/auto/command 三态，必须能在报告里分辨「测了」与「没测」。"""
import json
import shlex
import sys
from pathlib import Path

from test_cli import cli

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fake_gpu.py"


def _run(base, out, *extra):
    return cli("run", "--endpoint", f"{base}/normal/v1/chat/completions", "--model", "tiny",
               "--prompt", "hi", "--requests", 3, "--max-tokens", 8, "--timeout", 5,
               "--output", out, *extra)


def test_gpu_none_writes_explicitly_unsampled_report(sse_base, tmp_path):
    out = tmp_path / "run.json"
    proc = _run(sse_base, out, "--gpu", "none")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["hardware"] is None
    assert data["summary"]["mem_used_peak_mb"] is None
    assert "未采样：本报告不含显存" in (tmp_path / "run.md").read_text(encoding="utf-8")


def test_gpu_command_samples_a_real_subprocess(sse_base, tmp_path):
    out = tmp_path / "run.json"
    # 路径里有字面反斜杠（F:\deepseek），必须按 shell 规则引用，否则 shlex 会吃掉反斜杠
    cmd = shlex.join([sys.executable, str(SCRIPT), "--leak-mb", "32"])
    proc = _run(sse_base, out, "--gpu", "command", "--gpu-command", cmd,
                "--sample-interval", "0.02")
    assert proc.returncode == 0, proc.stderr
    hw = json.loads(out.read_text(encoding="utf-8"))["hardware"]
    assert hw["source"] == "command"
    assert hw["samples"] >= 2
    assert hw["mem_used_delta_mb"] >= 32.0


def test_gpu_command_without_command_is_usage_error(sse_base, tmp_path):
    proc = _run(sse_base, tmp_path / "run.json", "--gpu", "command")
    assert proc.returncode == 2
    assert "gpu-command" in proc.stderr


def test_sample_interval_must_be_positive(sse_base, tmp_path):
    proc = _run(sse_base, tmp_path / "run.json", "--gpu", "none", "--sample-interval", "0")
    assert proc.returncode == 2
    assert "sample-interval" in proc.stderr
