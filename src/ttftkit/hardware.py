"""GPU 显存 / 利用率采样（v0.2）。

只做三件事，且每件都要求「采不到就说采不到」：
1. 解析 nvidia-smi / rocm-smi 的真实输出——坏输出一律报错，静默跳过等于把故障算成正常；
2. 在测量窗口内按固定间隔采样，聚合出 baseline（窗口起点）、peak、delta；
3. 采样器自己出错时按次数累计并不中断压测，但错误会如实出现在硬件块里。

不做的事：不采集 ECC / 温度 / 功耗（DCGM 的活），也不做 Prometheus 导出（见 docs/roadmap.md）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

NVIDIA_QUERY = [
    "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
    "--format=csv,noheader,nounits",
]
COMMAND_TIMEOUT_S = 10.0
MB_PER_BYTE = 1024.0 * 1024.0


@dataclass(frozen=True, slots=True)
class GpuSample:
    name: str
    driver: str
    mem_total_mb: float
    mem_used_mb: float
    utilization_pct: float | None = None


def _number(text: str, what: str) -> float:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"invalid {what}: {text!r}") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"invalid {what}: {text!r} (not finite)")
    if value < 0:
        raise ValueError(f"invalid {what}: {text!r} (negative)")
    return value


def _optional_number(text: str, what: str) -> float | None:
    raw = str(text).strip()
    if raw in ("", "[N/A]", "N/A"):
        return None
    return _number(raw, what)


def parse_nvidia_smi(text: str) -> GpuSample:
    """解析 `--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu` 的 CSV。"""
    for line in str(text).splitlines():
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) != 5:
            raise ValueError(f"nvidia-smi line has {len(fields)} fields, expected 5: {line!r}")
        name, driver, total, used, util = fields
        return GpuSample(
            name=name,
            driver=driver,
            mem_total_mb=_number(total, "memory.total"),
            mem_used_mb=_number(used, "memory.used"),
            utilization_pct=_optional_number(util, "utilization.gpu"),
        )
    raise ValueError("nvidia-smi produced no usable line")


def parse_rocm_smi(text: str) -> GpuSample:
    """解析 `rocm-smi --showmeminfo vram --showuse --json` 的输出（字节转 MB）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"rocm-smi output is not JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise ValueError("rocm-smi output must be a JSON object")
    for key, card in data.items():
        if not (isinstance(key, str) and key.startswith("card") and isinstance(card, dict)):
            continue
        total_b = card.get("VRAM Total Memory (B)")
        used_b = card.get("VRAM Total Used Memory (B)")
        if total_b is None or used_b is None:
            raise ValueError(f"{key}: missing VRAM Total Memory / Used Memory fields")
        return GpuSample(
            name=str(card.get("Card Series") or card.get("Card series") or "unknown"),
            driver=str(card.get("Driver version") or "unknown"),
            mem_total_mb=_number(total_b, "VRAM Total Memory (B)") / MB_PER_BYTE,
            mem_used_mb=_number(used_b, "VRAM Total Used Memory (B)") / MB_PER_BYTE,
            utilization_pct=_optional_number(card.get("GPU use (%)", ""), "GPU use (%)"),
        )
    raise ValueError("rocm-smi output has no 'card*' entry")


class CommandSampler:
    """跑一条外部命令并解析成 GpuSample（默认按 nvidia-smi CSV 解析）。"""

    def __init__(self, argv, *, source="command", parser=parse_nvidia_smi):
        if not argv:
            raise ValueError("sampler command must not be empty")
        self.argv = list(argv)
        self.source = source
        self._parser = parser

    def sample(self) -> GpuSample:
        proc = subprocess.run(
            self.argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S, check=False
        )
        if proc.returncode != 0:
            raise OSError(f"{self.source} exited with {proc.returncode}")
        return self._parser(proc.stdout)


class RocmSampler(CommandSampler):
    def __init__(self, argv):
        super().__init__(argv, source="rocm-smi", parser=parse_rocm_smi)


def command_sampler(argv) -> CommandSampler:
    return CommandSampler(argv)


def detect_sampler() -> CommandSampler | RocmSampler | None:
    """探测本机可用的采样器；都没有就返回 None（调用方据此声明「未采样」）。"""
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        return CommandSampler([nvidia, *NVIDIA_QUERY], source="nvidia-smi")
    rocm = shutil.which("rocm-smi")
    if rocm:
        return RocmSampler([rocm, "--showmeminfo", "vram", "--showuse", "--json"])
    return None


class Collector:
    """在测量窗口内按间隔采样；线程只做采样，聚合在 block() 里一次算清。"""

    def __init__(self, sampler, *, interval_s: float = 0.25, max_series: int = 120):
        if interval_s <= 0:
            raise ValueError("sample interval must be positive")
        self.sampler = sampler
        self.interval_s = interval_s
        self.max_series = max_series
        self._values: list[float] = []
        self._util: list[float] = []
        self._total: float | None = None
        self._name: str | None = None
        self._driver: str | None = None
        self._errors = 0
        self._first_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> bool:
        try:
            sample = self.sampler.sample()
        except Exception as exc:  # 采样失败不能拖垮压测，但必须被记下来
            self._errors += 1
            if self._first_error is None:
                self._first_error = type(exc).__name__
            return False
        self._values.append(float(sample.mem_used_mb))
        self._total = float(sample.mem_total_mb)
        self._name = sample.name
        self._driver = sample.driver
        if sample.utilization_pct is not None:
            self._util.append(float(sample.utilization_pct))
        return True

    def start(self) -> None:
        if self.sampler is None:
            return
        self.sample_once()  # 先同步采一次，作为窗口起点 baseline
        self._thread = threading.Thread(target=self._loop, daemon=True)

        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.sample_once()

    def stop(self) -> dict | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s * 4 + 1.0)
            self._thread = None
        return self.block()

    def _series(self) -> list[float]:
        if len(self._values) <= self.max_series:
            return list(self._values)
        stride = (len(self._values) - 1) / (self.max_series - 1)
        picked = [self._values[round(i * stride)] for i in range(self.max_series)]
        picked[-1] = self._values[-1]
        return picked

    def block(self) -> dict | None:
        if not self._values:
            return None
        baseline = self._values[0]
        peak = max(self._values)
        return {
            "source": self.sampler.source,
            "name": self._name,
            "driver": self._driver,
            "mem_total_mb": self._total,
            "mem_used_baseline_mb": baseline,
            "mem_used_peak_mb": peak,
            "mem_used_delta_mb": round(peak - baseline, 3),
            "utilization_peak_pct": max(self._util) if self._util else None,
            "samples": len(self._values),
            "interval_s": self.interval_s,
            "sampler_errors": self._errors,
            "error": self._first_error,
            "mem_used_series_mb": self._series(),
        }


def fingerprint(block: dict | None) -> str:
    """硬件指纹：显存总量不同的两块卡，性能数字不可直接比较。"""
    if not block or not block.get("name"):
        return "none"
    total = block.get("mem_total_mb")
    return f"{block['name']}/{total:g}MB" if isinstance(total, (int, float)) else str(block["name"])


def is_monotonic(series) -> bool:
    """严格单调上升（验收「注入已知泄漏」用；平票不算上升）。"""
    return all(b > a for a, b in zip(series, series[1:], strict=False))


def now() -> float:  # 便于测试替换
    return time.perf_counter()
