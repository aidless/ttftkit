"""回归门禁：把两份报告与一套规则变成退出码。

退出码语义（fail-closed）：
- 0 通过（可能带 WARN）
- 1 有规则不通过，或有失败请求，或缺指标
- 2 规则/报告无效，或两次测量不可比
"""
from __future__ import annotations

import math

from .hardware import fingerprint as _block_fingerprint

# 已知指标路径：规则里写别的路径视为配置错误（防止拼错后静默不检查）
DIST_FIELDS = ("count", "mean", "min", "max", "p50", "p90", "p95", "p99", "stdev", "cv")
DIST_METRICS = ("ttft_ms", "tpot_ms", "latency_ms", "decode_ms", "chunk_gap_ms")
SCALAR_METRICS = (
    "requests",
    "successful_requests",
    "error_rate",
    "requests_per_s",
    "output_tokens_per_s",
    "token_usage_coverage",
    "goodput_per_s",
    "degradation_ratio",
    "mem_used_peak_mb",
    "mem_used_delta_mb",
    "gpu_util_peak_pct",
    "client_lag_p95_ms",
)
# 硬件指标的相对比较只在同一块卡上有意义：显存总量不同 = 不可比
HARDWARE_METRICS = frozenset({"mem_used_peak_mb", "mem_used_delta_mb", "gpu_util_peak_pct"})
# summary 标量与 hardware 块字段的一一对应：两者必须一致，否则报告是拼出来的
_HW_FIELDS = {
    "mem_used_peak_mb": "mem_used_peak_mb",
    "mem_used_delta_mb": "mem_used_delta_mb",
    "gpu_util_peak_pct": "utilization_peak_pct",
}
MISSING_FINGERPRINTS = frozenset({"", "none", "None", "null"})
# 报告类型：只有单次测量（run）可以过门禁；scan 是多级聚合，必须挑一级出来再判
GATEABLE_KINDS = frozenset({"run"})
KNOWN_METRICS = frozenset(
    [f"{m}.{f}" for m in DIST_METRICS for f in DIST_FIELDS] + list(SCALAR_METRICS)
)

# 这些分布的 count 是「参与统计的请求数」，不能超过成功请求数。
# chunk_gap_ms 的 count 是块间隔个数（可多于请求数），故不在其列。
PER_REQUEST_DISTS = ("ttft_ms", "tpot_ms", "latency_ms", "decode_ms")

HIGHER_IS_BETTER = frozenset(
    {"requests", "successful_requests", "requests_per_s", "output_tokens_per_s",
     "token_usage_coverage", "goodput_per_s", "gpu_util_peak_pct"}
)
# 注：gpu_util_peak_pct 方向是「越高越好」——策略里的 limits 是下限（要求真的跑满了卡），
# 而不是上限。写成上限会把「利用率 99% 的正常压测」判失败、把空转判通过。

CONFIG_KEYS = ("protocol", "model", "concurrency", "max_tokens", "requests", "prompt_sha256", "profile")


class _ConfigError(Exception):
    """规则或输入无效 → 退出码 2。"""


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ConfigError(f"{what} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise _ConfigError(f"{what} must be finite, got {value!r}")
    return float(value)


def _validate_rules(rules, baseline, candidate) -> dict:
    if not isinstance(rules, dict):
        raise _ConfigError("policy must be a JSON object")
    unknown = set(rules) - {"limits", "regressions", "invariance", "stability"}
    if unknown:
        raise _ConfigError(f"unknown policy keys: {sorted(unknown)}")
    raw_limits = rules.get("limits", {})
    raw_regressions = rules.get("regressions", {})
    for key, value in (("limits", raw_limits), ("regressions", raw_regressions)):
        if value is not None and not isinstance(value, dict):
            raise _ConfigError(f"{key!r} must be a JSON object, got {type(value).__name__}")
    limits = raw_limits or {}
    regressions = raw_regressions or {}
    # invariance（batch 不变性）：同 prompt 不同 batch 下输出逐字节一致。
    # 目前只支持 {"against_baseline": true}——candidate 输出必须与 baseline
    # 逐字节一致；畸形策略是配置错误，不是「检查通过」。
    raw_invariance = rules.get("invariance")
    invariance = None
    if raw_invariance is not None:
        if not isinstance(raw_invariance, dict):
            raise _ConfigError(f"'invariance' must be a JSON object, got {type(raw_invariance).__name__}")
        unknown_inv = set(raw_invariance) - {"against_baseline"}
        if unknown_inv:
            raise _ConfigError(f"unknown invariance keys: {sorted(unknown_inv)}")
        if raw_invariance.get("against_baseline") is not True:
            raise _ConfigError("'invariance.against_baseline' must be true (only supported mode)")
        if baseline is None:
            raise _ConfigError("invariance policy requires a baseline report")
        invariance = {"against_baseline": True}
    # stability（采样解码分布稳定性）：temperature>0 时逐字节一致是错误预期
    # （浮点噪声被采样放大），改判分歧率 = 与 baseline 不一致的输出占比 ≤ 上限。
    raw_stability = rules.get("stability")
    stability = None
    if raw_stability is not None:
        if not isinstance(raw_stability, dict):
            raise _ConfigError(f"'stability' must be a JSON object, got {type(raw_stability).__name__}")
        unknown_st = set(raw_stability) - {"against_baseline", "max_divergence_rate"}
        if unknown_st:
            raise _ConfigError(f"unknown stability keys: {sorted(unknown_st)}")
        if raw_stability.get("against_baseline") is not True:
            raise _ConfigError("'stability.against_baseline' must be true (only supported mode)")
        cap = raw_stability.get("max_divergence_rate")
        if not isinstance(cap, (int, float)) or isinstance(cap, bool) \
                or not math.isfinite(cap) or not 0 <= cap <= 1:
            raise _ConfigError("'stability.max_divergence_rate' must be a number in [0, 1]")
        if baseline is None:
            raise _ConfigError("stability policy requires a baseline report")
        stability = {"against_baseline": True, "max_divergence_rate": float(cap)}
    if not limits and not regressions and invariance is None and stability is None:
        raise _ConfigError("policy is empty: no rule would ever run")

    for name, value in limits.items():
        if name not in KNOWN_METRICS:
            raise _ConfigError(f"unknown metric in limits: {name!r}")
        if _number(value, f"limit for {name}") < 0:
            raise _ConfigError(f"limit for {name} must be >= 0")

    for name, value in regressions.items():
        if name not in KNOWN_METRICS:
            raise _ConfigError(f"unknown metric in regressions: {name!r}")
        if _number(value, f"regression tolerance for {name}") < 0:
            raise _ConfigError(f"regression tolerance for {name} must be >= 0")
        if baseline is None:
            raise _ConfigError("relative policy requires a baseline report")
        _check_comparable(candidate, baseline, name)
    return {"limits": limits, "regressions": regressions,
            "invariance": invariance, "stability": stability}


# 不变性比较只要求「问的是同一个问题、用同一个模型、同一生成上限」；
# concurrency/requests 是 batch 形态本身，允许（也应当）不同。
INVARIANCE_CONFIG_KEYS = ("protocol", "model", "max_tokens", "prompt_sha256")


def _check_invariance(candidate: dict, baseline: dict) -> dict | None:
    """batch 不变性检查：两份报告的捕获输出必须逐字节一致。

    fail-closed：任一侧缺 outputs、空 outputs、条目畸形、index 集不一致
    都是「没测」→ _ConfigError（exit 2），绝不折成通过或 FAIL。
    返回 PASS/FAIL 检查条目；证据问题直接抛 _ConfigError。
    """
    differing = [k for k in INVARIANCE_CONFIG_KEYS
                 if candidate["config"][k] != baseline["config"][k]]
    if differing:
        raise _ConfigError(
            f"measurements are not comparable for invariance: {differing} differ"
        )
    labeled = (("candidate", candidate), ("baseline", baseline))
    for label, report in labeled:
        outputs = report.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise _ConfigError(
                f"{label} report has no captured outputs — run the benchmark with "
                "capture_outputs=True; missing evidence is not a pass"
            )
        for entry in outputs:
            if not isinstance(entry, dict):
                raise _ConfigError(f"{label} outputs entry must be an object: {entry!r}")
            idx, text = entry.get("index"), entry.get("text")
            if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
                raise _ConfigError(f"{label} outputs entry has invalid index: {idx!r}")
            if not isinstance(text, str):
                raise _ConfigError(f"{label} outputs[{idx}] text must be a string, got {type(text).__name__}")
    cand_out = {e["index"]: e["text"] for e in candidate["outputs"]}
    base_out = {e["index"]: e["text"] for e in baseline["outputs"]}
    if set(cand_out) != set(base_out):
        raise _ConfigError(
            f"output index sets differ (candidate {sorted(cand_out)} vs baseline {sorted(base_out)}); "
            "invariance needs the same prompts measured on both sides"
        )
    mismatched = sorted(i for i in cand_out if cand_out[i] != base_out[i])
    if mismatched:
        first = mismatched[0]
        return {
            "rule": "invariance", "kind": "invariance", "status": "FAIL",
            "observed": f"{len(mismatched)}/{len(cand_out)} outputs differ",
            "threshold": "0 differing outputs",
            "reason": f"output[{first}] differs from baseline (batch changed the output)",
        }
    return {
        "rule": "invariance", "kind": "invariance", "status": "PASS",
        "observed": f"{len(cand_out)} outputs byte-identical to baseline",
        "threshold": "0 differing outputs", "reason": "",
    }


# stability 的可比性键：invariance 四键之外，采样参数也必须一致——
# 不同采样路径的报告放一起比分歧率是假比较。
STABILITY_CONFIG_KEYS = INVARIANCE_CONFIG_KEYS + ("temperature", "top_p", "seed")


def _wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    """二项比例 Wilson 95% 上界。n 小时点估计 k/n 的 PASS 不等于置信结论。"""
    p = k / n
    z2 = z * z
    return (p + z2 / (2 * n) + z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


def _min_n_zero_divergence(cap: float) -> int | None:
    """零分歧时把 Wilson 上界压到 cap 以下所需的最小样本数；cap<=0 无解。"""
    if cap <= 0:
        return None
    for n in range(1, 10000):
        if _wilson_upper(0, n) < cap:
            return n
    return None


def _power_witness(k: int, n: int, cap: float) -> dict | None:
    """stability PASS 的功效见证：上界仍超过上限时，把「PASS 不是证据」写进判决书。

    与 basis 见证同款：不改 PASS/FAIL（判决按点估计），只禁止把小样本 PASS
    读成「分歧率 ≤ 上限」。实测依据见 results/invariance-2026-11-10/verdict-queries-multirun.md。
    """
    upper = _wilson_upper(k, n)
    if upper <= cap:
        return None
    need = _min_n_zero_divergence(cap)
    need_str = f"n>={need} zero-divergence queries needed" if need is not None else \
               "no finite n resolves a zero cap; quote the upper bound"
    return {
        "rule": "stability", "kind": "power", "status": "PASS",
        "observed": (f"PASS on {k}/{n} divergences is not evidence rate<={cap:g}: "
                     f"Wilson 95% upper={upper:.3f}; {need_str}"),
        "threshold": cap, "reason": "",
    }


def _check_stability(candidate: dict, baseline: dict, max_rate: float) -> tuple[dict, int, int]:
    """采样解码分布稳定性检查：分歧率 = 与 baseline 不一致的输出占比 ≤ max_rate。

    fail-closed 与 invariance 同款：缺 outputs/畸形条目/index 集不一致都是
    「没测」→ _ConfigError（exit 2），绝不折成通过。
    与 invariance 的区别：不要求逐字节一致，只约束分歧占比（temperature>0
    时浮点噪声被采样放大，0 分歧是错误预期——见 results/invariance 判决书）。
    """
    differing = [k for k in STABILITY_CONFIG_KEYS
                 if candidate["config"][k] != baseline["config"][k]]
    if differing:
        raise _ConfigError(
            f"measurements are not comparable for stability: {differing} differ"
        )
    labeled = (("candidate", candidate), ("baseline", baseline))
    for label, report in labeled:
        outputs = report.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise _ConfigError(
                f"{label} report has no captured outputs — run the benchmark with "
                "capture_outputs=True; missing evidence is not a pass"
            )
        for entry in outputs:
            if not isinstance(entry, dict):
                raise _ConfigError(f"{label} outputs entry must be an object: {entry!r}")
            idx, text = entry.get("index"), entry.get("text")
            if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
                raise _ConfigError(f"{label} outputs entry has invalid index: {idx!r}")
            if not isinstance(text, str):
                raise _ConfigError(f"{label} outputs[{idx}] text must be a string, got {type(text).__name__}")
    cand_out = {e["index"]: e["text"] for e in candidate["outputs"]}
    base_out = {e["index"]: e["text"] for e in baseline["outputs"]}
    if set(cand_out) != set(base_out):
        raise _ConfigError(
            f"output index sets differ (candidate {sorted(cand_out)} vs baseline {sorted(base_out)}); "
            "stability needs the same prompts measured on both sides"
        )
    diverged = sorted(i for i in cand_out if cand_out[i] != base_out[i])
    rate = len(diverged) / len(cand_out)
    observed = f"{len(diverged)}/{len(cand_out)} outputs diverge (rate {rate:.3f})"
    if rate > max_rate + 1e-12:
        detail = ""
        if diverged:
            first = diverged[0]
            pos = next(
                (k for k, (a, b) in enumerate(
                    zip(base_out[first], cand_out[first], strict=False))
                 if a != b),
                None,
            )
            len_detail = (f"; output[{first}] length differs "
                          f"({len(base_out[first])} vs {len(cand_out[first])})")
            detail = (f"; output[{first}] first differs at char {pos}"
                      if pos is not None else len_detail)
        return {
            "rule": "stability", "kind": "stability", "status": "FAIL",
            "observed": observed, "threshold": f"divergence_rate <= {max_rate:g}",
            "reason": f"divergence_rate {rate:.3f} exceeds cap {max_rate:g}{detail}",
        }, len(diverged), len(cand_out)
    return {
        "rule": "stability", "kind": "stability", "status": "PASS",
        "observed": observed, "threshold": f"divergence_rate <= {max_rate:g}",
        "reason": "",
    }, len(diverged), len(cand_out)


def _check_comparable(candidate: dict, baseline: dict, metric: str) -> None:
    """相对比较要求两次测量的协议指纹完全一致，否则不是同一个实验。"""
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        if not isinstance(report, dict) or not isinstance(report.get("config"), dict):
            raise _ConfigError(f"{label} report has no config block")
        for key in CONFIG_KEYS:
            if key not in report["config"]:
                raise _ConfigError(f"{label} config missing {key!r} (cannot compare for {metric})")
    differing = [k for k in CONFIG_KEYS if candidate["config"][k] != baseline["config"][k]]
    if differing:
        raise _ConfigError(f"measurements are not comparable for {metric}: {differing} differ")


def _lookup(summary: dict, path: str):
    """按点路径取值；缺键或非数值返回 None（由调用方判 FAIL）。"""
    node = summary
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return None
    if not math.isfinite(node):
        return None
    return float(node)


def _validate_summary(summary: dict) -> None:
    """报告必须携带可判定的证据量，否则不是「通过」而是「没测」。"""
    requests = summary.get("requests")
    successful = summary.get("successful_requests")
    if isinstance(requests, bool) or not isinstance(requests, int) or requests <= 0:
        raise _ConfigError("report has no positive integer 'requests' — no evidence to judge")
    if isinstance(successful, bool) or not isinstance(successful, int) or not 0 <= successful <= requests:
        raise _ConfigError("'successful_requests' must be an integer within [0, requests]")
    if "error_rate" not in summary:
        raise _ConfigError("report has no 'error_rate' — cannot tell whether any request failed")
    error_rate = summary["error_rate"]
    if (
        isinstance(error_rate, bool)
        or not isinstance(error_rate, (int, float))
        or not math.isfinite(error_rate)
        or not 0.0 <= error_rate <= 1.0
    ):
        raise _ConfigError(f"'error_rate' must be a finite number in [0, 1], got {error_rate!r}")
    expected_error_rate = (requests - successful) / requests
    if abs(float(error_rate) - expected_error_rate) > 1e-3:
        raise _ConfigError(
            f"'error_rate' ({error_rate!r}) is inconsistent with requests/successful_requests "
            f"(expected {expected_error_rate!r})"
        )
    for metric in DIST_METRICS:
        node = summary.get(metric)
        if node is None:
            continue
        if not isinstance(node, dict):
            raise _ConfigError(f"'{metric}' must be an object or null, got {type(node).__name__}")
        count = node.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            # count=0 却带分位数是自相矛盾（真实 summarize 无样本时整节点为 null），
            # 会经 _lookup 取到数值骗过相对门禁，故一律视为无证据。
            raise _ConfigError(f"'{metric}.count' must be a positive integer, got {count!r}")
        if metric in PER_REQUEST_DISTS and count > successful:
            raise _ConfigError(
                f"'{metric}.count' ({count}) exceeds successful_requests ({successful})"
            )


def _check_hardware_evidence(report: dict, label: str) -> None:
    """summary 里的硬件标量必须能追溯到 hardware 块，且块本身自洽。

    没有这一步，手改过的报告或「采样半途卡死」的半程数据都能当完整证据通过：
    gate 对 error_rate ↔ requests/successful 有一致性校验，硬件指标必须有同等待遇。
    """
    summary = report["summary"]
    block = report.get("hardware")
    claimed = {k: v for k, v in ((k, summary.get(k)) for k in _HW_FIELDS) if v is not None}
    if block is None:
        if claimed:
            raise _ConfigError(
                f"{label} summary claims hardware metrics {sorted(claimed)} without a 'hardware' block"
            )
        return
    if not isinstance(block, dict):
        raise _ConfigError(f"{label} 'hardware' must be an object or null")
    samples = block.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
        raise _ConfigError(f"{label} hardware.samples must be a non-negative integer, got {samples!r}")
    for metric, key in _HW_FIELDS.items():
        expected = block.get(key)
        observed = summary.get(metric)
        if expected is None and observed is None:
            continue
        for value in (expected, observed):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _ConfigError(
                    f"{label} {metric} does not match hardware.{key}: {observed!r} vs {expected!r}"
                )
        if abs(float(observed) - float(expected)) > 1e-6:
            raise _ConfigError(
                f"{label} {metric} ({observed!r}) disagrees with hardware.{key} ({expected!r})"
            )
    # 块自身也要自洽：手拼的报告可以让 summary 与块「一致」却与实际采样形状矛盾
    baseline = block.get("mem_used_baseline_mb")
    peak = block.get("mem_used_peak_mb")
    delta = block.get("mem_used_delta_mb")
    if samples == 0 and any(value is not None for value in (baseline, peak, delta)):
        raise _ConfigError(f"{label} hardware block has 0 samples but carries readings")
    if samples < 2 and delta is not None:
        raise _ConfigError(
            f"{label} hardware.samples={samples} cannot have a mem_used_delta_mb "
            "(needs a baseline and a second sample)"
        )
    if delta is not None:
        for value in (baseline, peak):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _ConfigError(
                    f"{label} hardware.mem_used_delta_mb needs finite baseline/peak, "
                    f"got {baseline!r}/{peak!r}"
                )
        if abs(float(delta) - (float(peak) - float(baseline))) > 1e-3:
            raise _ConfigError(
                f"{label} hardware.mem_used_delta_mb ({delta!r}) != peak - baseline "
                f"({float(peak) - float(baseline):.3f})"
            )
    # 物理合理性：显存占用不可能是负数，也不该超过设备的显存总量。
    # 少了这两条，「-5 MB 峰值」或「9999 MB 峰值 / 6144 MB 设备」这种数字能被当成完整证据。
    for key in ("mem_used_baseline_mb", "mem_used_peak_mb"):
        value = block.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise _ConfigError(f"{label} hardware.{key} must be a finite number or null, got {value!r}")
        if float(value) < 0:
            raise _ConfigError(f"{label} hardware.{key} is negative ({value!r}): not a possible reading")
    total = block.get("mem_total_mb")
    if (peak is not None and isinstance(total, (int, float)) and not isinstance(total, bool)
            and float(peak) > float(total) + 1e-6):
        raise _ConfigError(
            f"{label} hardware.mem_used_peak_mb ({peak!r}) exceeds device total ({total!r})"
        )
    # 指纹溯源：config.hardware 必须等于「用块自己重算出来的指纹」。
    # 否则改一份报告的块（换卡/换容量）就能绕过可比性门禁 —— 只信自称等于「写对就拦、写错不拦」。
    declared = (report.get("config") or {}).get("hardware")
    recomputed = _block_fingerprint(block)
    declared_missing = declared is None or (
        isinstance(declared, str) and declared.strip() in MISSING_FINGERPRINTS
    )
    if recomputed is None:
        if not declared_missing:
            raise _ConfigError(
                f"{label} config.hardware claims {declared!r} but the hardware block has no usable device"
            )
    elif declared_missing:
        raise _ConfigError(
            f"{label} config.hardware is missing but the hardware block identifies {recomputed!r}"
        )
    elif declared != recomputed:
        raise _ConfigError(
            f"{label} config.hardware ({declared!r}) disagrees with the hardware block "
            f"({recomputed!r}): the fingerprint was not derived from this block"
        )


def _check_client_evidence(report: dict, label: str) -> None:
    """客户端 lag 读数必须能追溯到 client 块（与硬件证据同等待遇）。

    没有这一步，报告可以只手写一个 `client_lag_p95_ms` 就宣称「客户端没问题」。
    """
    summary = report["summary"]
    block = report.get("client")
    claimed = summary.get("client_lag_p95_ms")
    if block is None:
        if claimed is not None:
            raise _ConfigError(
                f"{label} summary claims client_lag_p95_ms without a 'client' block"
            )
        return
    if not isinstance(block, dict):
        raise _ConfigError(f"{label} 'client' must be an object or null")
    lag = block.get("lag_ms")
    if lag is None:
        if claimed is not None:
            raise _ConfigError(
                f"{label} summary claims client_lag_p95_ms but client.lag_ms is null"
            )
        return
    if not isinstance(lag, dict):
        raise _ConfigError(f"{label} client.lag_ms must be an object")
    samples = lag.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
        raise _ConfigError(
            f"{label} client.lag_ms.samples must be a non-negative integer, got {samples!r}"
        )
    expected = lag.get("p95_ms")
    if expected is None and claimed is None:
        return
    for value in (expected, claimed):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise _ConfigError(
                f"{label} client_lag_p95_ms does not match client.lag_ms.p95_ms: "
                f"{claimed!r} vs {expected!r}"
            )
    if abs(float(claimed) - float(expected)) > 1e-6:
        raise _ConfigError(
            f"{label} client_lag_p95_ms ({claimed!r}) disagrees with client.lag_ms.p95_ms "
            f"({expected!r})"
        )


def _hardware_fingerprint(report: dict, label: str) -> str:
    """取可比较的硬件指纹；拿不到就 fail-closed（而不是当成同一块卡）。"""
    config = report.get("config")
    if not isinstance(config, dict):
        raise _ConfigError(f"{label} report has no config block")
    value = config.get("hardware")
    if not isinstance(value, str) or value.strip() in MISSING_FINGERPRINTS:
        raise _ConfigError(
            f"{label} config.hardware is missing ({value!r}): "
            "hardware metrics are only comparable on the same device"
        )
    return value


def _insufficient_samples(summary: dict, path: str) -> str | None:
    """分布指标的 count 明显少于成功请求数 → 该指标只覆盖了子集，不能代表整体。"""
    parts = path.split(".")
    if len(parts) != 2 or parts[0] not in PER_REQUEST_DISTS:
        return None
    dist = summary.get(parts[0])
    successful = summary.get("successful_requests")
    if not isinstance(dist, dict):
        return None
    if isinstance(successful, bool) or not isinstance(successful, int):
        # 靠 successful_requests 判「覆盖度」，它不可用就不能当作「样本充足」
        return "'successful_requests' is not an integer, cannot verify sample coverage"
    count = dist.get("count")
    if isinstance(count, bool) or not isinstance(count, int):
        return f"'{parts[0]}.count' is not an integer ({count!r})"
    if count < successful:
        return f"computed from {count} of {successful} successful requests"
    return None


def _hardware_unusable(candidate: dict, path: str) -> str | None:
    """硬件规则 + 截断采样 = 没证据。

    `stop()` 卡在采样器里意味着窗口末尾的数没采到，峰值可能被低报；
    拿一个「可能漏掉峰值」的读数去对照上限，等于把噪声当合规。
    只有本侧（被审的那份报告）如此才判，基线侧截断只会让候选显得更差（保守）。
    """
    if path not in HARDWARE_METRICS:
        return None
    block = candidate.get("hardware")
    if not isinstance(block, dict):
        return None
    if block.get("truncated") is True:
        return "sampling was truncated at stop (peak may be under-reported)"
    return None


def _status(ok: bool, comfortable: bool) -> str:
    """ok=是否合规；comfortable=是否离阈值还有余量。

    合规但贴边 → WARN（放行但提示）；不合规 → FAIL。
    """
    if not ok:
        return "FAIL"
    return "PASS" if comfortable else "WARN"


def evaluate(candidate: dict, rules: dict, baseline: dict | None = None) -> dict:
    """执行门禁。返回 {exit_code, checks, reasons}。"""
    checks: list[dict] = []
    reasons: list[str] = []
    try:
        if not isinstance(candidate, dict):
            raise _ConfigError("candidate report must be a JSON object")
        # 先判报告类型再判内容：scan 报告本来就没有 summary 段，
        # 若先查 summary，报错会变成「缺 summary」而掩盖真正原因。
        kind = candidate.get("kind", "run")
        if kind not in GATEABLE_KINDS:
            raise _ConfigError(
                f"report kind {kind!r} is not gateable: scan reports aggregate several load "
                "levels — pick one level (levels[i].summary) and gate it as a run report"
            )
        if not isinstance(candidate.get("summary"), dict):
            raise _ConfigError("candidate report has no summary block")
        policy = _validate_rules(rules, baseline, candidate)
    except _ConfigError as exc:
        return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}

    summary = candidate["summary"]
    try:
        _validate_summary(summary)
        _check_hardware_evidence(candidate, "candidate")
        _check_client_evidence(candidate, "candidate")
    except _ConfigError as exc:
        return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}

    # baseline 也是判据的一部分：基线缺证据（薄样本、0 请求、非法分布 count）
    # 时相对比较就没有意义，必须 fail-closed，而不是拿 1 个样本当基准。
    if baseline is not None:
        base = baseline.get("summary") if isinstance(baseline, dict) else None
        if not isinstance(base, dict):
            return {"exit_code": 2, "checks": [], "reasons": ["baseline report has no summary block"]}
        try:
            _validate_summary(base)
            _check_hardware_evidence(baseline, "baseline")
            _check_client_evidence(baseline, "baseline")
        except _ConfigError as exc:
            return {"exit_code": 2, "checks": [], "reasons": [f"baseline: {exc}"]}
        # 显存/利用率的比较只对同一块卡有意义（绝对上限同理：512 MB 的增长上限
        # 在 6 GB 卡与 24 GB 卡上不是同一件事）。指纹缺失/非法/不同 → 不可比。
        hw_rules = [n for n in list(policy["regressions"]) + list(policy["limits"])
                    if n in HARDWARE_METRICS]
        if hw_rules:
            try:
                cand_hw = _hardware_fingerprint(candidate, "candidate")
                base_hw = _hardware_fingerprint(baseline, "baseline")
            except _ConfigError as exc:
                return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}
            if cand_hw != base_hw:
                return {"exit_code": 2, "checks": [], "reasons": [
                    f"hardware differs ({cand_hw!r} vs {base_hw!r}): not comparable"]}

    # batch 不变性：独立于延迟/吞吐类规则运行（即便两者都不在策略里也要执行）
    if policy.get("invariance"):
        try:
            inv_check = _check_invariance(candidate, baseline)
        except _ConfigError as exc:
            return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}
        if inv_check is not None:
            checks.append(inv_check)
            if inv_check["status"] == "FAIL":
                reasons.append(inv_check["reason"])

    # 采样解码分布稳定性：与 invariance 互斥使用（逐字节一致 vs 分歧率上限）
    if policy.get("stability"):
        try:
            st_check, st_k, st_n = _check_stability(
                candidate, baseline, policy["stability"]["max_divergence_rate"])
        except _ConfigError as exc:
            return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}
        checks.append(st_check)
        if st_check["status"] == "FAIL":
            reasons.append(st_check["reason"])
        else:
            power = _power_witness(st_k, st_n, policy["stability"]["max_divergence_rate"])
            if power is not None:
                checks.append(power)

    failed = summary["requests"] - summary["successful_requests"]
    error_rate = summary["error_rate"]
    # 失败预算：默认 0（任何失败都拦）。若策略显式给了 error_rate 上限，
    # 就按该预算判，避免「文档说可配置、代码却硬编码必挂」的自相矛盾。
    failure_budget = float(policy["limits"].get("error_rate", 0.0))
    if error_rate > failure_budget + 1e-12:
        checks.append({
            "metric": "error_rate", "kind": "implicit", "status": "FAIL",
            "observed": error_rate, "threshold": failure_budget,
            "reason": f"{failed} failed request(s), error_rate {error_rate:g} "
                      f"exceeds budget {failure_budget:g}",
        })
        reasons.append(f"{failed} failed request(s) / error_rate {error_rate:g} > {failure_budget:g}")

    for name, limit in policy["limits"].items():
        observed = _lookup(summary, name)
        if observed is None:
            checks.append({"metric": name, "kind": "limit", "status": "FAIL",
                           "observed": None, "threshold": limit,
                           "reason": "metric missing or not a finite number"})
            reasons.append(f"{name}: no usable value")
            continue
        # p99_basis 见证：n<20 时 p99 由末位 1–2 个样本主导、不是可分位估计
        #（verdict-matrix 补充发现 3；canonical runD 实测 p99 123.7 vs max 123.9）。
        # 不改 PASS/FAIL（上限判定本身仍有效），但判决书必须带着这条出处，
        # 防止「8 个样本的 p99」被当真分位数引用。
        if name.endswith(".p99"):
            prefix = name[:-len(".p99")]
            node = summary.get(prefix)
            if isinstance(node, dict) and node.get("p99_basis") == "max":
                checks.append({
                    "metric": name, "kind": "basis", "status": "PASS",
                    "observed": f"p99 is dominated by the extreme sample(s) of {node.get('count')} samples "
                                "(n<20, max-dominated, not a quantile estimate); quote p50 for latency conclusions",
                    "threshold": limit, "reason": "",
                })
        short = _insufficient_samples(summary, name)
        if short:
            checks.append({"metric": name, "kind": "limit", "status": "FAIL",
                           "observed": observed, "threshold": limit, "reason": short})
            reasons.append(f"{name}: {short}")
            continue
        truncated = _hardware_unusable(candidate, name)
        if truncated:
            checks.append({"metric": name, "kind": "limit", "status": "FAIL",
                           "observed": observed, "threshold": limit, "reason": truncated})
            reasons.append(f"{name}: {truncated}")
            continue
        higher = name in HIGHER_IS_BETTER
        if higher:
            ok = observed >= limit
            comfortable = observed >= limit * 1.1  # 高于下限 10% 才算有余量
        else:
            ok = observed <= limit
            comfortable = observed <= limit * 0.9  # 低于上限 10% 才算有余量
        status = _status(ok, comfortable)
        checks.append({"metric": name, "kind": "limit", "status": status,
                       "observed": observed, "threshold": limit, "reason": ""})
        if status == "FAIL":
            reasons.append(f"{name}={observed:g} violates limit {limit:g}")

    base_summary = (baseline or {}).get("summary", {}) if baseline else {}
    for name, tolerance_pct in policy["regressions"].items():
        observed = _lookup(summary, name)
        reference = _lookup(base_summary, name) if baseline else None
        if observed is None or reference is None:
            checks.append({"metric": name, "kind": "regression", "status": "FAIL",
                           "observed": observed, "baseline": reference,
                           "threshold": tolerance_pct,
                           "reason": "metric missing in candidate or baseline"})
            reasons.append(f"{name}: missing value for relative comparison")
            continue
        short = _insufficient_samples(summary, name)
        if short:
            checks.append({"metric": name, "kind": "regression", "status": "FAIL",
                           "observed": observed, "baseline": reference,
                           "threshold": tolerance_pct, "reason": short})
            reasons.append(f"{name}: {short}")
            continue
        truncated = _hardware_unusable(candidate, name)
        if truncated:
            checks.append({"metric": name, "kind": "regression", "status": "FAIL",
                           "observed": observed, "baseline": reference,
                           "threshold": tolerance_pct, "reason": truncated})
            reasons.append(f"{name}: {truncated}")
            continue
        short_base = _insufficient_samples(base_summary, name)
        if short_base:
            reason = f"baseline {short_base}"
            checks.append({"metric": name, "kind": "regression", "status": "FAIL",
                           "observed": observed, "baseline": reference,
                           "threshold": tolerance_pct, "reason": reason})
            reasons.append(f"{name}: {reason}")
            continue
        higher = name in HIGHER_IS_BETTER
        if higher:
            allowed = reference * (1 - tolerance_pct / 100)
            ok = observed >= allowed
            comfortable = observed >= reference * (1 - tolerance_pct / 200)  # 退化 < 半个容差
        else:
            if reference == 0:
                ok = observed <= 0  # 基线为 0 时任何正值都是退化
                comfortable = ok
            else:
                allowed = reference * (1 + tolerance_pct / 100)
                ok = observed <= allowed
                comfortable = observed <= reference * (1 + tolerance_pct / 200)
        status = _status(ok, comfortable)
        checks.append({"metric": name, "kind": "regression", "status": status,
                       "observed": observed, "baseline": reference,
                       "threshold": tolerance_pct, "reason": ""})
        if status == "FAIL":
            reasons.append(
                f"{name}={observed:g} vs baseline {reference:g} "
                f"exceeds {tolerance_pct:g}% tolerance"
            )

    if not checks:
        return {"exit_code": 2, "checks": [], "reasons": ["no check was executed"]}
    exit_code = 1 if any(c["status"] == "FAIL" for c in checks) else 0
    return {"exit_code": exit_code, "checks": checks, "reasons": reasons}
