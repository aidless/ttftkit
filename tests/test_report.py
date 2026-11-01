"""报告渲染的输入校验：残缺/非法报告必须报 ValueError（CLI 映射为退出码 2），不能崩成 1。"""
import pytest

from ttftkit.report import markdown


def _report(**over):
    base = {
        "config": {"model": "tiny", "concurrency": 1, "requests": 2, "max_tokens": 8,
                   "warmup": 0, "protocol": "openai-sse-v1", "profile": "p",
                   "prompt_sha256": "a" * 64},
        "summary": {"requests": 2, "successful_requests": 2, "error_rate": 0.0,
                    "ttft_ms": {"count": 2, "mean": 10.0, "p50": 10.0, "p95": 12.0, "p99": 12.0},
                    "tpot_ms": None, "latency_ms": None, "decode_ms": None,
                    "chunk_gap_ms": None, "errors": {}},
        "records": [],
        "duration_s": 1.0,
    }
    base.update(over)
    return base


def test_valid_report_renders():
    text = markdown(_report())
    assert "ttftkit 性能报告" in text
    assert "TTFT" in text


def test_partial_distribution_raises_value_error():
    # 修复前：dist['mean'] 抛 KeyError；修复后：明确的 ValueError
    bad = _report()
    bad["summary"]["ttft_ms"] = {"p95": 100}
    with pytest.raises(ValueError):
        markdown(bad)


def test_missing_prompt_sha_does_not_crash():
    r = _report()
    r["config"]["prompt_sha256"] = None
    text = markdown(r)  # 修复前：None[:16] → TypeError
    assert "prompt sha256" in text


def test_goodput_row_only_when_slo_provided():
    # 没给 SLO 时 goodput 无定义（null），不应展示该行，否则读者会以为 0；
    # 给了 SLO（goodput_per_s 为数值）才展示。
    text = markdown(_report())
    assert "goodput" not in text
    summary = dict(_report()["summary"], goodput_per_s=1.25)
    assert "| goodput | 1.250 req/s |" in markdown(_report(summary=summary))


def test_non_object_summary_raises_value_error():
    with pytest.raises(ValueError):
        markdown({"config": {}, "summary": [1, 2, 3], "records": []})


def _hw(**over):
    block = {
        "source": "nvidia-smi", "name": "NVIDIA RTX 4090", "driver": "550.54",
        "mem_total_mb": 24564.0, "mem_used_baseline_mb": 1024.0,
        "mem_used_peak_mb": 3072.0, "mem_used_delta_mb": 2048.0,
        "utilization_peak_pct": 91.0, "samples": 40, "interval_s": 0.25,
        "sampler_errors": 0, "error": None, "mem_used_series_mb": [1024.0, 3072.0],
    }
    block.update(over)
    return block


def test_hardware_block_renders_baseline_peak_and_delta():
    text = markdown(_report(hardware=_hw()))
    assert "## 硬件（GPU 采样）" in text
    assert "NVIDIA RTX 4090" in text
    assert "1,024.0 MB / 峰值 3,072.0 MB" in text
    assert "窗口内增长：2,048.0 MB" in text


def test_sampler_errors_are_disclosed_not_hidden():
    text = markdown(_report(hardware=_hw(sampler_errors=3, error="OSError")))
    assert "采样器曾失败 3 次" in text
    assert "不能当作全程证据" in text


def test_unsampled_run_says_so_instead_of_zeros():
    # 未采样 → 明确写「未采样」，绝不渲染成 0 MB（0 会被读成「测到没占显存」）
    text = markdown(_report(hardware=None))
    assert "未采样：本报告不含显存" in text
    assert "mem_used_peak_mb" not in text


def test_invalid_hardware_block_raises_value_error():
    with pytest.raises(ValueError):
        markdown(_report(hardware=[1, 2, 3]))
