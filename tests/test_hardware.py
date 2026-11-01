"""硬件采样（v0.2）：解析、聚合与「注入已知泄漏必须看到单调上升」的验收反证。

验收判据来自 docs/roadmap.md：不做注入式反证，采样就是装饰。
本文件用合成采样器（真实实现 Sampler 协议）驱动真实聚合逻辑，
另用真实子进程脚本验证命令采样器的端到端路径。
"""
import time

import pytest

from ttftkit import hardware


class FakeSampler:
    """按序列吐样本的采样器；序列用尽后重复最后一个值。"""

    def __init__(self, values, source="fake", total=6144.0, util=50.0):
        self.source = source
        self._values = list(values)
        self._i = 0
        self.total = total
        self.util = util
        self.calls = 0

    def sample(self):
        self.calls += 1
        used = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return hardware.GpuSample(
            name="Fake GPU", driver="1.0", mem_total_mb=self.total,
            mem_used_mb=used, utilization_pct=self.util,
        )


class BoomSampler:
    source = "boom"

    def __init__(self, fail_on=2):
        self.fail_on = fail_on
        self.calls = 0

    def sample(self):
        self.calls += 1
        if self.calls == self.fail_on:
            raise OSError("nvidia-smi disappeared")
        return hardware.GpuSample(name="Boom", driver="0", mem_total_mb=1000.0,
                                  mem_used_mb=100.0, utilization_pct=1.0)


NVIDIA_CSV = "NVIDIA GeForce RTX 3060 Laptop GPU, 580.95.05, 6144, 512, 37\n"


def test_parse_nvidia_smi_reads_all_fields():
    s = hardware.parse_nvidia_smi(NVIDIA_CSV)
    assert s.name == "NVIDIA GeForce RTX 3060 Laptop GPU"
    assert s.driver == "580.95.05"
    assert s.mem_total_mb == 6144.0
    assert s.mem_used_mb == 512.0
    assert s.utilization_pct == 37.0


def test_parse_nvidia_smi_takes_first_gpu_when_multiple():
    two = NVIDIA_CSV + "NVIDIA A100-SXM4-80GB, 550.54, 81920, 1024, 99\n"
    assert hardware.parse_nvidia_smi(two).name == "NVIDIA GeForce RTX 3060 Laptop GPU"


@pytest.mark.parametrize("bad", [
    "NVIDIA X, 580, notanumber, 512, 37\n",   # 非法数字
    "NVIDIA X, 580, 6144, 512\n",             # 字段数不足
    "NVIDIA X, 580, 6144, -5, 37\n",          # 负显存
    "\n",                                      # 空输出
])
def test_parse_nvidia_smi_rejects_bad_output(bad):
    # 静默跳过坏输出 = 把「采不到」伪装成「没占用」，必须报错
    with pytest.raises(ValueError):
        hardware.parse_nvidia_smi(bad)


ROCM_JSON = """{
  "card0": {
    "Card Series": "Radeon RX 7900 XTX",
    "Driver version": "6.12.3",
    "VRAM Total Memory (B)": "8589934592",
    "VRAM Total Used Memory (B)": "1073741824",
    "GPU use (%)": "42"
  }
}
"""


def test_parse_rocm_smi_converts_bytes_to_mb():
    s = hardware.parse_rocm_smi(ROCM_JSON)
    assert s.name == "Radeon RX 7900 XTX"
    assert s.mem_total_mb == 8192.0
    assert s.mem_used_mb == 1024.0
    assert s.utilization_pct == 42.0


@pytest.mark.parametrize("bad", [
    '{"card0": {"Card Series": "X"}}',            # 缺显存字段
    '{"card0": {"VRAM Total Memory (B)": "abc", "VRAM Total Used Memory (B)": "1"}}',
    "not json",
    '{"ok": true}',                                # 没有 card 节点
])
def test_parse_rocm_smi_rejects_bad_output(bad):
    with pytest.raises(ValueError):
        hardware.parse_rocm_smi(bad)


def test_collector_records_baseline_peak_and_delta():
    c = hardware.Collector(FakeSampler([500.0, 700.0, 900.0]), interval_s=0.01)
    c.sample_once()
    c.sample_once()
    c.sample_once()
    block = c.block()
    assert block["mem_used_baseline_mb"] == 500.0
    assert block["mem_used_peak_mb"] == 900.0
    assert block["mem_used_delta_mb"] == 400.0
    assert block["samples"] == 3


def test_injected_leak_must_show_monotonic_growth():
    # 验收反证：每次采样固定 +128MB 的「已知泄漏」，必须被如实记录成单调上升
    leak_mb = 128.0
    values = [400.0 + leak_mb * i for i in range(5)]
    c = hardware.Collector(FakeSampler(values), interval_s=0.01)
    for _ in range(5):
        c.sample_once()
    block = c.block()
    series = block["mem_used_series_mb"]
    assert series == values
    assert hardware.is_monotonic(series), "泄漏必须表现为严格上升"
    assert block["mem_used_delta_mb"] == leak_mb * 4


def test_sampler_error_is_counted_not_fatal():
    c = hardware.Collector(BoomSampler(fail_on=2), interval_s=0.01)
    c.sample_once()          # ok
    c.sample_once()          # raises
    c.sample_once()          # ok again
    block = c.block()
    assert block["samples"] == 2
    assert block["sampler_errors"] == 1
    assert block["error"] == "OSError"


def test_collector_with_no_samples_still_reports_the_failure():
    # 采样器全灭 ≠ 没有采样器：块必须留下，错误必须能被报告与门禁看见。
    c = hardware.Collector(BoomSampler(fail_on=1), interval_s=0.01)
    c.sample_once()
    block = c.block()
    assert block["samples"] == 0
    assert block["sampler_errors"] == 1
    assert block["error"] == "OSError"
    assert block["mem_used_baseline_mb"] is None
    assert block["mem_used_peak_mb"] is None
    assert block["mem_used_delta_mb"] is None
    assert block["mem_used_series_mb"] == []


def test_single_sample_has_no_delta():
    # 一个样本算不出增长：必须是 None，不能是 0.0（0.0 会冒充「测到了、没涨」）
    c = hardware.Collector(FakeSampler([800.0]), interval_s=0.01)
    c.sample_once()
    block = c.block()
    assert block["samples"] == 1
    assert block["mem_used_peak_mb"] == 800.0
    assert block["mem_used_delta_mb"] is None


def test_stop_timeout_is_recorded_as_truncated_not_silently_dropped():
    # 采样线程卡在 sampler 里：join 超时后必须留痕（否则峰值被低报而报告说“错误 0 次”）
    class Slow:
        source = "slow"

        def sample(self):
            time.sleep(0.3)
            return hardware.GpuSample(name="Fake GPU", driver="1.0", mem_total_mb=6144.0,
                                      mem_used_mb=512.0)

    c = hardware.Collector(Slow(), interval_s=0.01, stop_timeout_s=0.05)
    c.start()          # start() 里那次同步采样要 0.3s
    time.sleep(0.05)   # 让后台线程真正进到 sample() 里（间隔 0.01s 后它就开始了）
    block = c.stop()
    assert block["truncated"] is True
    assert block["sampler_errors"] >= 1
    assert block["error"] == "SamplerBusyAtStop"


def test_fingerprint_is_none_without_a_device():
    # "none" 是 truthy：门禁若拿它当指纹，两块都没有卡的报告会被判成“同一块卡”
    assert hardware.fingerprint(None) is None
    assert hardware.fingerprint({}) is None
    assert hardware.fingerprint({"name": "Fake GPU", "mem_total_mb": None}) is None
    assert hardware.fingerprint({"name": "Fake GPU", "mem_total_mb": 6144.0}) == "Fake GPU/6144MB"


def test_detect_sampler_returns_none_without_tools(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert hardware.detect_sampler() is None


def test_command_sampler_uses_given_command():
    s = hardware.command_sampler(["python3", "-c", "print('Fake, 1, 100, 20, 5')"])
    sample = s.sample()
    assert sample.name == "Fake"
    assert sample.mem_used_mb == 20.0
    assert s.source == "command"
