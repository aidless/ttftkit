"""显存指标进门禁：绝对上限、缺采样即缺证据、硬件指纹变了不可比。

v0.2 修完 R1–R6 后的判据：summary 里的硬件数必须能在那份报告的 hardware 块里对上；
指纹只有真的「型号 + 容量」才算数（"none" 是 truthy，绝不能再被当成一块卡）。
"""
import pytest

from ttftkit.gate import evaluate

CONFIG = {
    "protocol": "openai-sse-v1", "model": "m", "concurrency": 1, "max_tokens": 8,
    "requests": 8, "prompt_sha256": "abc", "profile": "p", "hardware": "Fake GPU/6144MB",
}


def block(samples=8, baseline=500.0, peak=1200.0, util=63.0, errors=0, truncated=False):
    delta = round(peak - baseline, 3) if samples >= 2 else None
    return {
        "source": "fake", "name": "Fake GPU" if samples else None, "driver": "1.0" if samples else None,
        "mem_total_mb": 6144.0 if samples else None,
        "mem_used_baseline_mb": baseline if samples else None,
        "mem_used_peak_mb": peak if samples else None,
        "mem_used_delta_mb": delta,
        "utilization_peak_pct": util if samples else None,
        "samples": samples, "interval_s": 0.25, "sampler_errors": errors,
        "error": "OSError" if errors and not truncated else ("SamplerBusyAtStop" if truncated else None),
        "truncated": truncated, "mem_used_series_mb": [],
    }


def report(peak=1200.0, baseline=500.0, util=63.0, samples=8, hardware="Fake GPU/6144MB",
           errors=0, truncated=False, include_block=True):
    config = dict(CONFIG)
    # 无样本时块里没有设备身份，runner 派生的指纹也就是 None：
    # 报告不可能「没有设备」却声称一个指纹（那正是 N4 要拦的形状）。
    if hardware is None or samples == 0 and hardware == CONFIG["hardware"]:
        del config["hardware"]
    else:
        config["hardware"] = hardware
    hw = block(samples=samples, baseline=baseline, peak=peak, util=util,
               errors=errors, truncated=truncated)
    summary = {
        "requests": 8, "successful_requests": 8, "error_rate": 0.0,
        "ttft_ms": {"count": 8, "mean": 100.0, "p50": 100.0, "p95": 100.0, "p99": 100.0},
        "mem_used_peak_mb": hw["mem_used_peak_mb"],
        "mem_used_delta_mb": hw["mem_used_delta_mb"],
        "gpu_util_peak_pct": hw["utilization_peak_pct"],
    }
    out = {"config": config, "summary": summary, "records": []}
    if include_block:
        out["hardware"] = hw
    return out


def test_mem_delta_over_limit_fails():
    # 1200 - 500 = 700 > 512
    verdict = evaluate(report(peak=1200.0, baseline=500.0), {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("mem_used_delta_mb" in r for r in verdict["reasons"])


def test_mem_delta_within_limit_passes():
    r = report(peak=1200.0, baseline=1080.0)   # 增长 120MB
    assert evaluate(r, {"limits": {"mem_used_delta_mb": 512}})["exit_code"] == 0


def test_single_sample_cannot_satisfy_a_delta_limit():
    # R2：一个样本算不出增长。以前 delta=0.0 直接过 512 的上限 → 假 PASS。
    r = report(samples=1, peak=800.0, baseline=800.0)
    verdict = evaluate(r, {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("no usable value" in x for x in verdict["reasons"])


def test_zero_samples_is_missing_evidence_not_a_pass():
    verdict = evaluate(report(samples=0), {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("no usable value" in x for x in verdict["reasons"])


def test_mem_limit_without_sampling_fails_as_missing_evidence():
    # 策略要求看显存，但这次根本没采样 → 不是「通过」，而是「没测」
    verdict = evaluate(report(samples=0), {"limits": {"mem_used_delta_mb": 512}})
    assert verdict["exit_code"] == 1
    assert any("no usable value" in x for x in verdict["reasons"])


def test_summary_without_hardware_block_is_configuration_error():
    # R3/R6：summary 说有显存读数，报告里却没有采样块 —— 证据链断了，不能默默采信 summary。
    r = report()
    r.pop("hardware")
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_summary_disagreeing_with_hardware_block_is_configuration_error():
    r = report(peak=1200.0, baseline=500.0)
    r["summary"]["mem_used_delta_mb"] = 12.0     # 与块里的 700 对不上
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_hardware_regression_compares_when_fingerprints_match():
    baseline = report(peak=1200.0, baseline=500.0)
    candidate = report(peak=1400.0, baseline=500.0)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 1


def _with_device(r, name, total):
    """把报告换成「块与指纹自洽」的另一台机器。

    可比性用例必须这样造：块写 A 卡、指纹也写 A 卡，两边只差「是不是同一块卡」。
    若只改 config 不改块，拦下它的是指纹溯源（N4），可比性规则就成了摆设。
    """
    r["hardware"]["name"] = name
    r["hardware"]["mem_total_mb"] = float(total)
    r["config"]["hardware"] = f"{name}/{total:g}MB"
    return r


def test_different_hardware_is_not_comparable():
    baseline = report(peak=1200.0)
    candidate = _with_device(report(peak=1300.0), "Other GPU", 24576)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 2
    assert any("hardware" in r for r in verdict["reasons"])


def test_absolute_mem_limit_is_not_comparable_across_hardware():
    # R7：绝对上限也是按型号定的；给定了基线就必须同卡，否则 6GB 的上限会被拿去卡 24GB 的卡
    baseline = report(peak=1200.0)
    candidate = _with_device(report(peak=1300.0), "Other GPU", 24576)
    verdict = evaluate(candidate, {"limits": {"mem_used_peak_mb": 1500}}, baseline)
    assert verdict["exit_code"] == 2


@pytest.mark.parametrize("missing", ["none", "None", "null", "", "  "])
def test_placeholder_fingerprint_is_not_a_comparable_hardware(missing):
    # R4：两侧都写 "none" 时曾经判「同一块卡」
    baseline = report(peak=1200.0, hardware=missing)
    candidate = report(peak=1300.0, hardware=missing)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 2


def test_report_without_hardware_fingerprint_is_not_comparable():
    # 旧报告没有 hardware 字段：不能默默当作「同一块卡」
    baseline = report(peak=1200.0, hardware=None)
    baseline["hardware"]["name"] = None
    baseline["hardware"]["mem_total_mb"] = None
    candidate = report(peak=1300.0)
    verdict = evaluate(candidate, {"regressions": {"mem_used_peak_mb": 10}}, baseline)
    assert verdict["exit_code"] == 2


@pytest.mark.parametrize("bad", ["700", -1.0, True, float("nan")])
def test_invalid_hardware_scalar_is_configuration_error(bad):
    r = report()
    r["summary"]["mem_used_delta_mb"] = bad
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


@pytest.mark.parametrize("bad", [True, -1, 1.5, "8"])
def test_invalid_samples_count_is_configuration_error(bad):
    r = report()
    r["hardware"]["samples"] = bad
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_utilization_limit_is_a_floor_not_a_ceiling():
    # 峰值利用率越高越好：limits 写 90 表示「至少要跑到 90%」
    busy = report(util=99.0)
    assert evaluate(busy, {"limits": {"gpu_util_peak_pct": 90}})["exit_code"] == 0
    idle = report(util=40.0)
    assert evaluate(idle, {"limits": {"gpu_util_peak_pct": 90}})["exit_code"] == 1


def test_truncated_sampling_does_not_fail_the_run_by_itself():
    # 截断只影响“读数可能低报”，报告已披露；除非策略要 delta 而这种情形采不到足够样本
    r = report(samples=8, peak=1200.0, baseline=500.0, truncated=True, errors=1)
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 0


def test_internally_inconsistent_hardware_block_is_configuration_error():
    # 手拼的报告：块里 baseline/peak 说涨了 700 MB，delta 却写 5 —— 三数必须自洽
    r = report(peak=1200.0, baseline=500.0)
    r["hardware"]["mem_used_delta_mb"] = 5.0
    r["summary"]["mem_used_delta_mb"] = 5.0
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_delta_requires_at_least_two_samples():
    r = report(samples=1, peak=800.0, baseline=800.0)
    r["hardware"]["mem_used_delta_mb"] = 0.0
    r["summary"]["mem_used_delta_mb"] = 0.0
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_samples_zero_cannot_carry_any_reading():
    r = report(samples=0)
    r["hardware"]["mem_used_peak_mb"] = 512.0     # 零样本却说采到了
    r["summary"]["mem_used_peak_mb"] = 512.0
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_zero_samples_with_error_detail_is_still_valid():
    r = report(samples=0)
    r["hardware"]["sampler_errors"] = 3
    r["hardware"]["error"] = "OSError"
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 0


def test_fingerprint_must_match_the_hardware_block():
    # N4：指纹与块各说各话时，只信 config 就等于「写对就拦、写错反而不拦」
    r = report(peak=1200.0)
    r["hardware"]["name"] = "Other GPU"
    r["hardware"]["mem_total_mb"] = 24576.0
    assert evaluate(r, {"regressions": {"mem_used_peak_mb": 10}}, report(peak=1000.0))["exit_code"] == 2


def test_block_without_device_cannot_claim_a_fingerprint():
    r = report(samples=0)
    r["config"]["hardware"] = "Fake GPU/6144MB"
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_negative_peak_is_configuration_error():
    r = report(peak=-5.0, baseline=-5.0)
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_peak_cannot_exceed_device_total():
    r = report(peak=9999.0, baseline=500.0)
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 2


def test_truncated_sampling_cannot_satisfy_a_hardware_limit():
    # N5：stop() 卡死意味着峰值可能低报 —— 拿它判上限就是拿「可能漏掉的数」当证据
    r = report(samples=8, peak=800.0, baseline=500.0, truncated=True, errors=1)
    verdict = evaluate(r, {"limits": {"mem_used_peak_mb": 5000}})
    assert verdict["exit_code"] == 1
    assert any("truncated" in x for x in verdict["reasons"])


def test_truncated_sampling_does_not_affect_non_hardware_rules():
    r = report(samples=8, peak=800.0, baseline=500.0, truncated=True, errors=1)
    assert evaluate(r, {"limits": {"ttft_ms.p95": 500}})["exit_code"] == 0
