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


@pytest.mark.parametrize("bad", [True, "8", float("nan"), float("inf"), -1, 0, 8.0])
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


# --- 第二轮独立审阅发现的 baseline 侧绕过 ---
def test_baseline_missing_successful_requests_is_configuration_error():
    baseline = realistic()
    del baseline["summary"]["successful_requests"]
    baseline["summary"]["ttft_ms"]["count"] = 1
    candidate = realistic()
    candidate["summary"]["ttft_ms"]["p95"] = 110.0
    result = evaluate(candidate, {"regressions": {"ttft_ms.p95": 20}}, baseline)
    assert result["exit_code"] == 2


def test_baseline_with_zero_requests_is_configuration_error():
    baseline = realistic()
    baseline["summary"].update({"requests": 0, "successful_requests": 0, "error_rate": 0.0,
                                "ttft_ms": None, "tpot_ms": None, "latency_ms": None,
                                "decode_ms": None, "chunk_gap_ms": None})
    candidate = realistic()
    result = evaluate(candidate, {"regressions": {"ttft_ms.p95": 20}}, baseline)
    assert result["exit_code"] == 2


def test_baseline_non_integer_successful_requests_is_configuration_error():
    baseline = realistic()
    baseline["summary"]["successful_requests"] = 8.0
    candidate = realistic()
    result = evaluate(candidate, {"regressions": {"ttft_ms.p95": 20}}, baseline)
    assert result["exit_code"] == 2


def test_baseline_zero_count_with_quantile_is_configuration_error():
    # 独立复核发现的残余绕过：count=0 却带 p95=100 的手写 baseline，
    # 经 _lookup 取到数值后相对规则可返回 exit 0 —— 自相矛盾即无证据，必须判 2
    baseline = realistic()
    baseline["summary"].update({
        "requests": 8, "successful_requests": 0, "error_rate": 1.0,
        "tpot_ms": None, "latency_ms": None, "decode_ms": None, "chunk_gap_ms": None,
    })
    baseline["summary"]["ttft_ms"] = {"count": 0, "p95": 100.0}
    candidate = realistic()
    result = evaluate(candidate, {"regressions": {"ttft_ms.p95": 20}}, baseline)
    assert result["exit_code"] == 2


# --- 失败预算：显式 limits.error_rate 应当生效，而不是被硬编码的「有失败即挂」覆盖 ---
def _report_with_failures(total, failed):
    r = realistic()
    r["summary"].update({
        "requests": total,
        "successful_requests": total - failed,
        "error_rate": failed / total,
    })
    return r


def test_explicit_failure_budget_allows_rare_failure():
    # 1000 请求里 1 个失败（0.1%），预算 0.5% → 放行
    r = _report_with_failures(1000, 1)
    result = evaluate(r, {"limits": {"error_rate": 0.005}})
    assert result["exit_code"] == 0
    assert not any(c["metric"] == "error_rate" and c["status"] == "FAIL" for c in result["checks"])


def test_failure_exceeding_budget_fails():
    r = _report_with_failures(1000, 1)
    result = evaluate(r, {"limits": {"error_rate": 0.0005}})
    assert result["exit_code"] == 1


def test_no_budget_means_any_failure_blocks():
    r = _report_with_failures(1000, 1)
    result = evaluate(r, {"limits": {"ttft_ms.p95": 500}})
    assert result["exit_code"] == 1
