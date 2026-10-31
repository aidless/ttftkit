"""门禁的 fail-closed 回归测试：报告缺少可判定的证据时，不能「通过」。

这些用例来自一次只读独立审阅：它指出 `_insufficient_samples` 遇到
缺 `count` / 非整型 `count` 会直接跳过，且相对比较只校验候选侧。
每个用例都对应一个真实可发生的报告（手写报告、或旧版本 summarize 产物）。
"""
import pytest

from ttftkit.gate import evaluate

POLICY = {"limits": {"ttft_ms.p95": 500}}


def dist(count, value=100.0):
    return {
        "count": count, "mean": value, "min": value, "max": value,
        "p50": value, "p90": value, "p95": value, "p99": value,
        "stdev": 0.0, "cv": 0.0,
    }


def realistic():
    """与 metrics.summarize 真实输出同形的报告（8 请求全部成功）。"""
    return {
        "schema_version": 1,
        "config": {
            "protocol": "openai-sse-v1", "model": "tiny", "concurrency": 2,
            "max_tokens": 32, "requests": 8, "prompt_sha256": "abc", "profile": "cpu-test",
        },
        "summary": {
            "requests": 8, "successful_requests": 8, "error_rate": 0.0,
            "requests_per_s": 4.0, "output_tokens_per_s": 20.0,
            "token_usage_coverage": 1.0, "goodput_per_s": None,
            "ttft_ms": dist(8, 90.0), "tpot_ms": dist(8, 10.0),
            "latency_ms": dist(8, 100.0), "decode_ms": dist(8, 60.0),
            "chunk_gap_ms": dist(16, 5.0),
            "degradation_ratio": 1.0, "errors": {},
        },
    }


def test_realistic_report_passes():
    assert evaluate(realistic(), POLICY)["exit_code"] == 0


def test_distribution_without_count_cannot_pass():
    r = realistic()
    del r["summary"]["ttft_ms"]["count"]
    assert evaluate(r, POLICY)["exit_code"] != 0


@pytest.mark.parametrize("bad", [True, "8", float("nan"), float("inf"), -1, 8.0])
def test_distribution_with_invalid_count_cannot_pass(bad):
    r = realistic()
    r["summary"]["ttft_ms"]["count"] = bad
    assert evaluate(r, POLICY)["exit_code"] != 0


def test_distribution_covering_fewer_requests_than_reported_fails():
    r = realistic()
    r["summary"]["ttft_ms"]["count"] = 3
    result = evaluate(r, POLICY)
    assert result["exit_code"] == 1
    assert any("3 of 8" in reason for reason in result["reasons"])


def test_baseline_with_thin_samples_blocks_relative_comparison():
    baseline = realistic()
    baseline["summary"]["ttft_ms"]["count"] = 1
    candidate = realistic()
    candidate["summary"]["ttft_ms"]["p95"] = 95.0
    result = evaluate(candidate, {"regressions": {"ttft_ms.p95": 10}}, baseline)
    assert result["exit_code"] != 0


def test_missing_error_rate_is_configuration_error():
    r = realistic()
    del r["summary"]["error_rate"]
    assert evaluate(r, POLICY)["exit_code"] == 2


@pytest.mark.parametrize("bad", [-0.1, 1.5, "0", None, float("nan"), True])
def test_error_rate_out_of_range_or_wrong_type_is_configuration_error(bad):
    r = realistic()
    r["summary"]["error_rate"] = bad
    assert evaluate(r, POLICY)["exit_code"] == 2
