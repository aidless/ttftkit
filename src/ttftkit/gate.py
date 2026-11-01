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
    unknown = set(rules) - {"limits", "regressions"}
    if unknown:
        raise _ConfigError(f"unknown policy keys: {sorted(unknown)}")
    raw_limits = rules.get("limits", {})
    raw_regressions = rules.get("regressions", {})
    for key, value in (("limits", raw_limits), ("regressions", raw_regressions)):
        if value is not None and not isinstance(value, dict):
            raise _ConfigError(f"{key!r} must be a JSON object, got {type(value).__name__}")
    limits = raw_limits or {}
    regressions = raw_regressions or {}
    if not limits and not regressions:
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
    return {"limits": limits, "regressions": regressions}


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
    if peak is not None and isinstance(total, (int, float)) and not isinstance(total, bool):
        if float(peak) > float(total) + 1e-6:
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
        if not isinstance(candidate, dict) or not isinstance(candidate.get("summary"), dict):
            raise _ConfigError("candidate report has no summary block")
        policy = _validate_rules(rules, baseline, candidate)
    except _ConfigError as exc:
        return {"exit_code": 2, "checks": [], "reasons": [str(exc)]}

    summary = candidate["summary"]
    try:
        _validate_summary(summary)
        _check_hardware_evidence(candidate, "candidate")
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
