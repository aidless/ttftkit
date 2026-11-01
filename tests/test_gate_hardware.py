"""显存指标进门禁：绝对上限、缺采样即缺证据、硬件指纹变了不可比。"""
import pytest

from ttftkit.gate import evaluate

CONFIG = {
    "protocol": "openai-sse-v1", "model": "m", "concurrency": 1, "max_tokens": 8,
    "requests": 8, "prompt_sha256": "abc", "profile": "p", "hardware": "Fake GPU/6144MB",
}


def report(delta=700.0, peak=1200.0, hardware="Fake GPU/6144MB"):
    config = dict(CONFIG)
    if hardware is None:
        del config["hardware"]
    else:
        config["hardware"] = hardware
    summary = {
        "requests": 8, "successful_requests": 8, "error_rate": 0.0,
        "ttft_ms": {"count": 8, "mean": 100.0, "p50": 100.0, "p95": 100.0, "p99": 100.0},
        "mem_used_peak_mb": peak, "mem_used_delta_mb": delta, "gpu_util_peak_pct": 63.0,
    }
    return {"config": config, "summary": summary, "records": []}


def test_mem_delta_over_limit_fails():
    verdict = evaluate(report(delta=700.0), {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("mem_used_delta_mb" in r for r in verdict["reasons"])


def test_mem_delta_within_limit_passes():
    assert evaluate(report(delta=120.0), {"limits": {"mem_used_delta_mb": 512}})["exit_code"] == 0


def test_mem_limit_without_sampling_fails_as_missing_evidence():
    # 策略要求看显存，但这次根本没采样 → 不是「通过」，而是「没测」
    verdict = evaluate(report(delta=None, peak=None), {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("no usable value" in r for r in verdict["reasons"])


def test_hardware_regression_compares_when_fingerprints_match():
    baseline = report(delta=700.0, peak=1200.0)
    candidate = report(delta=900.0, peak=1400.0)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 1


def test_different_hardware_is_not_comparable():
    baseline = report(peak=1200.0)
    candidate = report(peak=1300.0, hardware="Other GPU/24576MB")
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 2
    assert any("hardware" in r for r in verdict["reasons"])


def test_report_without_hardware_fingerprint_is_not_comparable():
    # 旧报告没有 hardware 字段：不能默默当作「同一块卡」
    baseline = report(peak=1200.0, hardware=None)
    candidate = report(peak=1300.0)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 2


@pytest.mark.parametrize("bad", ["700", -1.0, True, float("nan")])
def test_invalid_hardware_scalar_is_configuration_error(bad):
    r = report()
    r["summary"]["mem_used_delta_mb"] = bad
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2
