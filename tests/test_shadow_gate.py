"""影子门禁必须「判得出问题，但不阻断」——两件事都要有反证。"""
import json
import sys
from pathlib import Path

# scripts/ 不在 pytest 的 pythonpath 里，显式加入后再导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from shadow_gate import main as shadow_main  # noqa: E402

GOOD = {
    "config": {"protocol": "openai-sse-v1", "model": "m", "concurrency": 1, "max_tokens": 8,
               "requests": 8, "prompt_sha256": "abc", "profile": "p", "hardware": "none"},
    "summary": {"requests": 8, "successful_requests": 8, "error_rate": 0.0,
                "requests_per_s": 2.0, "output_tokens_per_s": 30.0,
                "ttft_ms": {"count": 8, "mean": 40.0, "p50": 40.0, "p95": 50.0, "p99": 60.0},
                "tpot_ms": {"count": 8, "mean": 20.0, "p50": 20.0, "p95": 25.0, "p99": 30.0}},
    "records": [],
}


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _policy(tmp_path, limit):
    return _write(tmp_path, "policy.json", {"limits": {"ttft_ms.p95": limit}})


def test_failing_report_does_not_block_in_shadow_mode(tmp_path, capsys):
    report = _write(tmp_path, "report.json", GOOD)
    code = shadow_main(["--report", report, "--policy", _policy(tmp_path, 10.0)])
    out = capsys.readouterr().out
    assert code == 0, "影子模式绝不能返回非零"
    assert "FAIL" in out
    assert "ttft_ms.p95" in out


def test_passing_report_passes_and_shows_readings(tmp_path, capsys):
    report = _write(tmp_path, "report.json", GOOD)
    code = shadow_main(["--report", report, "--policy", _policy(tmp_path, 500.0)])
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS" in out
    assert "observed=50" in out


def test_strict_mode_propagates_gate_exit_code(tmp_path):
    report = _write(tmp_path, "report.json", GOOD)
    assert shadow_main(["--report", report, "--policy", _policy(tmp_path, 10.0), "--strict"]) == 1
    assert shadow_main(["--report", report, "--policy", _policy(tmp_path, 500.0), "--strict"]) == 0


def test_invalid_report_is_reported_as_not_comparable_not_silently_ok(tmp_path, capsys):
    report = _write(tmp_path, "report.json", {"config": {}, "summary": {"requests": 0}})
    code = shadow_main(["--report", report, "--policy", _policy(tmp_path, 500.0)])
    out = capsys.readouterr().out
    assert code == 0  # 影子模式仍不阻断…
    assert "不合法/不可比" in out  # …但必须说清是「输入不对」而不是「通过」
