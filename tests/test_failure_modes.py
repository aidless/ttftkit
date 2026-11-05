"""已经写明、但代码还没堵上的洞。

这些测试锁定的是今天的行为，不是期望的终态。补洞时要同时改
docs/FAILURE_MODES.md 和这里的断言。
"""
import json
from pathlib import Path

import pytest
from test_client import run
from test_gate import report

from ttftkit.gate import evaluate

ROOT = Path(__file__).resolve().parent.parent
# 2001-09-09 的 Unix 时间。入库报告里的 started_at 远小于它，不是墙钟。
_UNIX_2001 = 1_000_000_000


def test_inflated_usage_shrinks_tpot_and_gate_still_passes(sse_base):
    """completion_tokens 报得很大时 TPOT 变小，绝对上限仍然可以通过。

    不能用块数打假：一个 SSE 块可以含多个 token。这是开着的洞，不是漏测。
    """
    rec = run(sse_base, "lying-usage")
    assert rec.ok is True
    assert rec.content_chunks == 2
    assert rec.output_tokens == 10000
    assert rec.tpot_ms == pytest.approx(rec.decode_ms / 9999)
    assert rec.tpot_ms < 1.0

    candidate = report()
    candidate["summary"]["requests"] = 1
    candidate["summary"]["successful_requests"] = 1
    candidate["summary"]["error_rate"] = 0.0
    candidate["summary"]["ttft_ms"] = {"count": 1, "p95": rec.ttft_ms}
    candidate["summary"]["tpot_ms"] = {"count": 1, "p95": rec.tpot_ms}
    candidate["records"] = []
    verdict = evaluate(candidate, {"limits": {"tpot_ms.p95": 50}})
    assert verdict["exit_code"] == 0


def test_gate_ignores_records_and_wall_clock():
    """summary 自洽就能过。records 可以与 summary 矛盾，报告也可以没有墙钟字段。"""
    candidate = report()
    candidate["records"] = [
        {"ok": False, "error": "gate does not read records", "started_at": 0}
    ]
    assert "measured_at" not in candidate
    assert "measured_at_utc" not in candidate
    assert evaluate(candidate, {"limits": {"ttft_ms.p95": 100}})["exit_code"] == 0


def test_limits_only_policy_does_not_compare_baseline_numbers():
    """只有 limits 时，基线的指标不参与比较。模型不同也可以退出码 0。"""
    baseline = report()
    candidate = report()
    candidate["config"]["model"] = "not-the-baseline-model"
    candidate["summary"]["ttft_ms"] = {"count": 8, "p95": 900}
    verdict = evaluate(candidate, {"limits": {"ttft_ms.p95": 1000}}, baseline)
    assert verdict["exit_code"] == 0
    assert any(
        c["status"] == "PASS" and c["metric"] == "ttft_ms.p95" for c in verdict["checks"]
    )


def test_checked_in_runs_have_no_wall_clock():
    paths = [ROOT / "results" / "smoke-ollama.json"]
    paths += [ROOT / "baselines" / f"run{i}.json" for i in range(1, 6)]
    paths.append(ROOT / "baselines" / "main.json")
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "measured_at" not in data
        assert "measured_at_utc" not in data
        for rec in data["records"]:
            assert rec["started_at"] < _UNIX_2001
