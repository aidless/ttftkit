"""RED 反证：把每处修复「回退」到修复前的写法，证明对应新测试确实会变红。

在临时副本上操作，绝不改动真实仓库。每条：把某处修复回退 → 跑对应测试 → 必须失败。
如果回退后测试仍通过，说明这条测试测的是装饰而不是机制。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (名称, [(相对文件, 修复后的片段, 回退后的片段), ...], 测试选择器)
CASES = [
    (
        "M2 坏 choices 不再崩溃（整批不中断）",
        [
            ("src/ttftkit/client.py",
             '                    if not isinstance(choices, list):\n'
             '                        return _fail(index, t0, "invalid event: choices is not a list")\n'
             '                    if not choices:\n'
             '                        continue  # 只带 usage 的收尾块\n'
             '                    choice = choices[0]\n',
             '                    choice = (event.get("choices") or [{}])[0]\n'),
            ("src/ttftkit/client.py",
             '                except (AttributeError, TypeError, KeyError, IndexError) as exc:',
             '                except (AttributeError, TypeError) as exc:'),
        ],
        "tests/test_client_hardening.py::test_choices_as_object_is_failure_not_crash "
        "tests/test_client_hardening.py::test_batch_is_not_aborted_by_malformed_choice",
    ),
    (
        "M4 错误串不回显服务端取值",
        [
            ("src/ttftkit/client.py",
             'raise ValueError(f"invalid usage field: {what}")',
             'raise ValueError(f"invalid usage: {what}={value!r}")'),
            ("src/ttftkit/client.py",
             'return _fail(index, t0, "unsuccessful finish_reason", latency_ms, output_text=captured)',
             'return _fail(index, t0, f"unsuccessful finish_reason: {finish_reason}", latency_ms)'),
        ],
        "tests/test_client_hardening.py::test_errors_do_not_echo_server_values",
    ),
    (
        "M5 非字符串 content 判失败",
        [
            ("src/ttftkit/client.py",
             '                    if content is not None and not isinstance(content, str):\n'
             '                        # 多模态数组、数字等都不是「文本内容块」，判失败而不是当一个 chunk\n'
             '                        return _fail(index, t0, "invalid event: content is not a string")\n',
             ''),
        ],
        "tests/test_client_hardening.py::test_non_string_content_is_failure",
    ),
    (
        "M6 残缺报告抛 ValueError（不是 KeyError）",
        [
            ("src/ttftkit/report.py",
             '    missing = [f for f in _DIST_FIELDS if f not in dist]\n'
             '    if missing:\n'
             '        raise ValueError(f"{name} distribution missing {\', \'.join(missing)}")\n',
             ''),
        ],
        "tests/test_report.py::test_partial_distribution_raises_value_error",
    ),
    (
        "m2 warmup 基础设施失败=退出 1（不是 2）",
        [
            ("src/ttftkit/runner.py",
             "raise BenchmarkFailed(f'warmup failed: {rec.error}')",
             "raise ValueError(f'warmup failed: {rec.error}')"),
        ],
        "tests/test_cli.py::test_warmup_infra_failure_exits_1",
    ),
    (
        "M1b 相对门禁校验 baseline 证据（缺 successful/0 请求/浮点）",
        [
            ("src/ttftkit/gate.py",
             "    if baseline is not None:\n"
             "        base = baseline.get(\"summary\") if isinstance(baseline, dict) else None\n"
             "        if not isinstance(base, dict):\n"
             "            return {\"exit_code\": 2, \"checks\": [], \"reasons\": [\"baseline report has no summary block\"]}\n"  # noqa: E501
             "        try:\n"
             "            _validate_summary(base)\n"
             "            _check_hardware_evidence(baseline, \"baseline\")\n"
             "            _check_client_evidence(baseline, \"baseline\")\n"
             "        except _ConfigError as exc:\n"
             "            return {\"exit_code\": 2, \"checks\": [], \"reasons\": [f\"baseline: {exc}\"]}\n",
             ""),
        ],
        "tests/test_gate_failclosed.py::test_baseline_missing_successful_requests_is_configuration_error "
        "tests/test_gate_failclosed.py::test_baseline_with_zero_requests_is_configuration_error "
        "tests/test_gate_failclosed.py::test_baseline_non_integer_successful_requests_is_configuration_error",
    ),
    (
        "M3 失败预算生效（不是硬编码有失败即挂）",
        [
            ("src/ttftkit/gate.py",
             "    if error_rate > failure_budget + 1e-12:",
             "    if failed > 0 or error_rate > 0:"),
        ],
        "tests/test_gate_failclosed.py::test_explicit_failure_budget_allows_rare_failure",
    ),
    (
        "M2b 无换行慢滴流由总预算拦住",
        [
            ("src/ttftkit/client.py",
             "    try:\n"
             "        return await asyncio.wait_for(\n"
             "            _measure_once(client, payload, index=index, t0=t0, timeout_s=timeout_s,\n"
             "                          path=path, capture_text=capture_text),\n"  # noqa: E501
             "            timeout=timeout_s,\n"
             "        )\n"
             "    except asyncio.TimeoutError:\n"
             "        return _fail(index, t0, \"timeout\")\n",
             "    return await _measure_once(client, payload, index=index, t0=t0, timeout_s=timeout_s, path=path, capture_text=capture_text)\n"),  # noqa: E501
        ],
        "tests/test_client_hardening.py::test_total_deadline_stops_slow_trickle "
        "tests/test_client_hardening.py::test_no_newline_trickle_hits_total_deadline",
    ),
    (
        "M7 协议错误文本不回显服务端字节",
        [
            ("src/ttftkit/client.py",
             '        # 协议层异常文本里可能夹带服务端字节（如非法的 chunk 头），只保留异常类名\n'
             '        return _fail(index, t0, f"http error: {type(exc).__name__}")\n',
             '        return _fail(index, t0, f"http error: {type(exc).__name__}: {exc}")\n'),
        ],
        "tests/test_client_hardening.py::test_protocol_error_does_not_echo_server_bytes",
    ),
    (
        "M8 count=0 却带分位数的基线视为无证据",
        [
            ("src/ttftkit/gate.py",
             'if isinstance(count, bool) or not isinstance(count, int) or count < 1:',
             'if isinstance(count, bool) or not isinstance(count, int) or count < 0:'),
        ],
        "tests/test_gate_failclosed.py::test_baseline_zero_count_with_quantile_is_configuration_error",
    ),
    (
        "M9 未采样必须在报告里显式声明（不是静默省略）",
        [
            ("src/ttftkit/report.py",
             """    hardware = result.get("hardware")
    if hardware is not None and not isinstance(hardware, dict):
        raise ValueError("report field 'hardware' must be an object or null")
""",
             "    hardware = None\n"),
        ],
        "tests/test_report.py::test_hardware_block_renders_baseline_peak_and_delta "
        "tests/test_report.py::test_sampler_errors_are_disclosed_not_hidden",
    ),
    (
        "M10 硬件指标缺席必须是 null（不是 0）",
        [
            ("src/ttftkit/metrics.py",
             """    if not isinstance(hardware, dict):
        return None
    value = hardware.get(key)""",
             """    if not isinstance(hardware, dict):
        return 0.0
    value = hardware.get(key)"""),
        ],
        "tests/test_runner_hardware.py::test_run_without_sampler_has_no_hardware_evidence",
    ),
    (
        "M11 不同硬件的显存回归不可比（exit 2）",
        [
            ("src/ttftkit/gate.py",
             """        # 显存/利用率的比较只对同一块卡有意义（绝对上限同理：512 MB 的增长上限
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
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_different_hardware_is_not_comparable "
        "tests/test_gate_hardware.py::test_report_without_hardware_fingerprint_is_not_comparable",
    ),
    (
        "M12 硬件读数必须是有限非负数（坏报告 exit 2）",
        [
            ("src/ttftkit/gate.py",
             """        for value in (expected, observed):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _ConfigError(
                    f"{label} {metric} does not match hardware.{key}: {observed!r} vs {expected!r}"
                )
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_invalid_hardware_scalar_is_configuration_error",
    ),
    (
        "M13 影子门禁不阻断（判得出问题但不返回非零）",
        [
            ("scripts/shadow_gate.py",
             '    print("shadow-gate: 影子模式，不阻断（--strict 才会返回非零）")\n    return 0\n',
             "    return code\n"),
        ],
        "tests/test_shadow_gate.py::test_failing_report_does_not_block_in_shadow_mode",
    ),
    (
        "M14 不合法输入必须报「不可比」而不是 PASS",
        [
            ("scripts/shadow_gate.py",
             'label = {0: "PASS", 1: "FAIL", 2: "不合法/不可比"}.get(code, str(code))',
             'label = "PASS"'),
        ],
        "tests/test_shadow_gate.py::test_invalid_report_is_reported_as_not_comparable_not_silently_ok",
    ),
    (
        "M15 利用率方向（limits 是下限，不是上限）",
        [
            ("src/ttftkit/gate.py",
             '     "token_usage_coverage", "goodput_per_s", "gpu_util_peak_pct"}',
             '     "token_usage_coverage", "goodput_per_s"}'),
        ],
        "tests/test_gate_hardware.py::test_utilization_limit_is_a_floor_not_a_ceiling",
    ),
    (
        "M16 stop() 超时必须留下截断痕迹（不能装作线程已经停了）",
        [
            ("src/ttftkit/hardware.py",
             """            if self._thread.is_alive():
                # 线程卡在采样器里：切不断，就如实记一笔——否则峰值会被低报而报告看不出来
                with self._lock:
                    self._errors += 1
                    self._truncated = True
                    if self._first_error is None:
                        self._first_error = "SamplerBusyAtStop"
""",
             """            if self._thread.is_alive():
                pass
"""),
        ],
        "tests/test_hardware.py::test_stop_timeout_is_recorded_as_truncated_not_silently_dropped",
    ),
    (
        "M17 单样本的 delta 必须是 null（不能是 0）",
        [
            ("src/ttftkit/hardware.py",
             '"mem_used_delta_mb": round(peak - baseline, 3) if len(values) >= 2 else None,',
             '"mem_used_delta_mb": round(peak - baseline, 3) if len(values) >= 2 else 0.0,'),
        ],
        "tests/test_hardware.py::test_single_sample_has_no_delta "
        "tests/test_gate_hardware.py::test_single_sample_cannot_satisfy_a_delta_limit",
    ),
    (
        "M18 一次都没采到也要返回块（坏掉的监控不能静默）",
        [
            ("src/ttftkit/hardware.py",
             """        with self._lock:
            values = list(self._values)
            utils = list(self._util)""",
             """        if not self._values:
            return None
        with self._lock:
            values = list(self._values)
            utils = list(self._util)"""),
        ],
        "tests/test_hardware.py::test_collector_with_no_samples_still_reports_the_failure "
        "tests/test_report.py::test_dead_sampler_is_reported_as_sampling_failure_not_unsampled",
    ),
    (
        "M19 指纹缺失必须返回 None（字符串 none 是 truthy，会绕过可比性）",
        [
            ("src/ttftkit/hardware.py",
             """    if not isinstance(block, dict):
        return None
    name, total = block.get("name"), block.get("mem_total_mb")
    if not isinstance(name, str) or not name.strip():
        return None""",
             """    if not isinstance(block, dict):
        return "none"
    name, total = block.get("name"), block.get("mem_total_mb")
    if not isinstance(name, str) or not name.strip():
        return "none"
"""),
        ],
        "tests/test_hardware.py::test_fingerprint_is_none_without_a_device "
        "tests/test_gate_hardware.py::test_placeholder_fingerprint_is_not_a_comparable_hardware",
    ),
    (
        "M20 summary 与 hardware 块不一致必须 exit 2",
        [
            ("src/ttftkit/gate.py",
             '        _check_hardware_evidence(candidate, "candidate")\n',
             ''),
            ("src/ttftkit/gate.py",
             '            _check_hardware_evidence(baseline, "baseline")\n',
             ''),
        ],
        "tests/test_gate_hardware.py::test_summary_disagreeing_with_hardware_block_is_configuration_error "
        "tests/test_gate_hardware.py::test_summary_without_hardware_block_is_configuration_error",
    ),
    (
        "M21 影子门禁读不到输入必须是 exit 2（不能伪装成规则不通过）",
        [
            ("scripts/shadow_gate.py",
             """    try:
        report = _load(args.report)
        policy = _load(args.policy)
        baseline = _load(args.baseline) if args.baseline else None
    except (OSError, ValueError) as exc:
        # 文件缺失 / 坏 JSON 不是「规则不通过」：两者都用 1 的话，CI 会把坏文件当成性能回归。
        print(f"shadow-gate: 无法读取输入（{type(exc).__name__}）：{exc}")
        return 2
""",
             """    report = _load(args.report)
    policy = _load(args.policy)
    baseline = _load(args.baseline) if args.baseline else None
"""),
        ],
        "tests/test_shadow_gate.py::test_missing_or_broken_inputs_are_not_reported_as_rule_failure",
    ),
    (
        "M22 块内三数必须自洽（delta == peak - baseline）",
        [
            ("src/ttftkit/gate.py",
             """        if abs(float(delta) - (float(peak) - float(baseline))) > 1e-3:
            raise _ConfigError(
                f"{label} hardware.mem_used_delta_mb ({delta!r}) != peak - baseline "
                f"({float(peak) - float(baseline):.3f})"
            )
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_internally_inconsistent_hardware_block_is_configuration_error",
    ),
    (
        "M23 delta 需要至少两个样本",
        [
            ("src/ttftkit/gate.py",
             """    if samples < 2 and delta is not None:
        raise _ConfigError(
            f"{label} hardware.samples={samples} cannot have a mem_used_delta_mb "
            "(needs a baseline and a second sample)"
        )
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_delta_requires_at_least_two_samples",
    ),
    (
        "M24 零样本块不能携带读数",
        [
            ("src/ttftkit/gate.py",
             """    if samples == 0 and any(value is not None for value in (baseline, peak, delta)):
        raise _ConfigError(f"{label} hardware block has 0 samples but carries readings")
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_samples_zero_cannot_carry_any_reading",
    ),
    (
        "M25 版本号只有一个真相源（pyproject == __init__）",
        [
            # 锚点不带具体版本号：每次发版都改锚点，迟早会漂成 SETUP-FAIL
            ("src/ttftkit/__init__.py",
             '__version__ = "',
             '__version__ = "0.0.0-'),
        ],
        "tests/test_package.py::test_version_matches_pyproject",
    ),
    (
        "M27 config.hardware 必须与块重算的指纹一致（否则改块就能绕过可比性）",
        [
            ("src/ttftkit/gate.py",
             """    declared = (report.get("config") or {}).get("hardware")
    recomputed = _block_fingerprint(block)
""",
             """    declared = None
    recomputed = None
"""),
        ],
        "tests/test_gate_hardware.py::test_fingerprint_must_match_the_hardware_block "
        "tests/test_gate_hardware.py::test_block_without_device_cannot_claim_a_fingerprint",
    ),
    (
        "M28 截断采样的读数不能充当硬件规则证据",
        [
            ("src/ttftkit/gate.py",
             """    if block.get("truncated") is True:
        return "sampling was truncated at stop (peak may be under-reported)"
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_truncated_sampling_cannot_satisfy_a_hardware_limit",
    ),
    (
        "M29 读数必须物理合理（非负、不超过设备总量）",
        [
            ("src/ttftkit/gate.py",
             """    for key in ("mem_used_baseline_mb", "mem_used_peak_mb"):
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
""",
             ""),
        ],
        "tests/test_gate_hardware.py::test_negative_peak_is_configuration_error "
        "tests/test_gate_hardware.py::test_peak_cannot_exceed_device_total",
    ),
    (
        "M30 Collector 必须拒绝无采样器与不可降采样的 max_series",
        [
            ("src/ttftkit/hardware.py",
             """        if sampler is None:
            # 没有采样器就没有窗口起点，block() 也无从写 source：这种 Collector 是误用，
            # 建的时候就说清楚（「不采样」的正确写法是根本不建 Collector）
            raise ValueError("Collector needs a sampler; pass sampler=None by not creating one")
""",
             ""),
            ("src/ttftkit/hardware.py",
             """        if isinstance(max_series, bool) or not isinstance(max_series, int) or max_series < 2:
            # 降采样公式用 max_series-1 做步长：0 → IndexError，1 → ZeroDivisionError
            raise ValueError("max_series must be an integer >= 2")
""",
             ""),
        ],
        "tests/test_hardware.py::test_collector_without_sampler_is_a_programming_error "
        "tests/test_hardware.py::test_max_series_must_be_usable_for_downsampling",
    ),
    (
        "M26 CLI 必须报得出自己的版本号",
        [
            ("src/ttftkit/cli.py",
             '    parser.add_argument("--version", action="version", version=f"ttftkit {__version__}")\n',
             ''),
        ],
        "tests/test_cli.py::test_version_flag_prints_the_installed_version",
    ),
    (
        "M31 空样本的 lag 统计必须是 null（不是 0）",
        [
            ("src/ttftkit/lag.py",
             """    values = [float(v) for v in samples]
    if not values:
        return None
""",
             """    values = [float(v) for v in samples]
    if not values:
        return {"samples": 0, "interval_s": interval_s, "p50_ms": 0.0,
                "p95_ms": 0.0, "max_ms": 0.0}
"""),
        ],
        "tests/test_lag.py::test_lag_stats_empty_is_none_not_zero",
    ),
    (
        "M32 lag 监视器必须真的测到事件循环被堵住",
        [
            ("src/ttftkit/lag.py",
             '            lag_ms = (loop.time() - t0 - self.interval_s) * 1000.0\n',
             '            lag_ms = 0.0\n'),
        ],
        "tests/test_lag.py::test_monitor_detects_a_blocked_event_loop",
    ),
    (
        "M33 一旦违反 SLO 就停（不许继续往上扫）",
        [
            ("src/ttftkit/scan.py",
             """        if bad:
            stopped_at = level
            break
""",
             """        if bad:
            stopped_at = level
"""),
        ],
        "tests/test_scan.py::test_scan_stops_at_first_violation_and_keeps_the_bad_level",
    ),
    (
        "M34 缺证据（p95 为 null）不算满足 SLO",
        [
            ("src/ttftkit/scan.py",
             """        if value is None:
            found.append(f"{label} missing (no evidence)")
        elif value > bound + 1e-9:
            found.append(f"{label} {value:.1f} > {bound:.1f}")
""",
             """        if value is not None and value > bound + 1e-9:
            found.append(f"{label} {value:.1f} > {bound:.1f}")
"""),
        ],
        "tests/test_scan.py::test_missing_ttft_evidence_is_not_satisfied",
    ),
    (
        "M35 scan 报告不能当单次测量过门禁",
        [
            ("src/ttftkit/gate.py",
             """        kind = candidate.get("kind", "run")
        if kind not in GATEABLE_KINDS:
            raise _ConfigError(
                f"report kind {kind!r} is not gateable: scan reports aggregate several load "
                "levels — pick one level (levels[i].summary) and gate it as a run report"
            )
""",
             ""),
        ],
        "tests/test_scan.py::test_gate_refuses_a_scan_report",
    ),
    (
        "M36 summary 不能凭空声称客户端 lag（必须有 client 块）",
        [
            ("src/ttftkit/gate.py",
             """    if block is None:
        if claimed is not None:
            raise _ConfigError(
                f"{label} summary claims client_lag_p95_ms without a 'client' block"
            )
        return
""",
             """    if block is None:
        return
"""),
        ],
        "tests/test_client_lag_gate.py::test_summary_claiming_lag_without_a_client_block_is_configuration_error",
    ),
    (
        "M37 bootstrap 必须用固定种子（否则每次 CI 判定不可复现）",
        [
            ("src/ttftkit/metrics.py",
             "    rng = random.Random(seed)\n",
             "    rng = random.Random()\n"),
        ],
        "tests/test_metrics_ci.py::test_bootstrap_threads_the_seed_into_the_rng "
        "tests/test_metrics_ci.py::test_interval_is_deterministic",
    ),
    (
        "M38 样本太少时不发置信区间",
        [
            ("src/ttftkit/metrics.py",
             "               if len(data) >= MIN_CI_SAMPLES else None),\n",
             "               if True else None),\n"),
        ],
        "tests/test_metrics_ci.py::test_small_sample_has_no_interval",
    ),
    (
        "M39 client 块必须是容器（lag 统计放在 client.lag_ms 下）",
        [
            ("src/ttftkit/runner.py",
             """        client_block = ({"timing": "asyncio-event-loop", "lag_ms": lag_block}
                        if lag_block is not None else None)
""",
             "        client_block = lag_block\n"),
        ],
        "tests/test_lag.py::test_run_reports_client_lag_in_the_canonical_shape",
    ),
    (
        "M40 invariance 必须真的逐字节比较输出（不是永远 PASS）",
        [
            ("src/ttftkit/gate.py",
             "    mismatched = sorted(i for i in cand_out if cand_out[i] != base_out[i])",
             "    mismatched: list[int] = []  # mutation: 不比较"),
        ],
        "tests/test_gate_invariance.py::test输出不一致_失败",
    ),
    (
        "M41 缺 outputs 是配置错误（不是静默放行）",
        [
            ("src/ttftkit/gate.py",
             '        outputs = report.get("outputs")\n'
             "        if not isinstance(outputs, list) or not outputs:\n",
             '        outputs = report.get("outputs")\n'
             "        if False:\n"),
        ],
        "tests/test_gate_invariance.py::test缺outputs_是没测不是通过 "
        "tests/test_gate_invariance.py::test空outputs_没测",
    ),
]


# CI 里 ttftkit 是 `pip install -e .` 装的，editable finder 挂在 sys.meta_path 上，
# 优先级高于 PYTHONPATH：不摘掉它，临时副本里被改坏的代码根本不会被 import，
# 每条变异都会「测试通过」→ 变异检查变成装饰（本地 uv/temp 目录跑不出来，只有 CI 暴露）。
_BOOTSTRAP = """
import pathlib, sys

sys.meta_path = [f for f in sys.meta_path if "editable" not in type(f).__module__.lower()]
import ttftkit  # noqa: E402

here = pathlib.Path.cwd().resolve()
if not pathlib.Path(ttftkit.__file__).resolve().is_relative_to(here):
    sys.exit(f"SETUP-FAIL: imported {ttftkit.__file__}, not the copy under {here}")

import pytest  # noqa: E402

sys.exit(pytest.main(sys.argv[1:]))
"""


def run_case(name, mutations, selector):
    tmp = Path(tempfile.mkdtemp(prefix="ttftkit-red-"))
    try:
        dst = tmp / "pkg"
        shutil.copytree(ROOT / "src", dst / "src")
        shutil.copytree(ROOT / "tests", dst / "tests")
        shutil.copytree(ROOT / "scripts", dst / "scripts")
        shutil.copy(ROOT / "pyproject.toml", dst / "pyproject.toml")
        for rel, old, new in mutations:
            p = dst / rel
            text = p.read_text(encoding="utf-8")
            if old not in text:
                return name, "SETUP-FAIL", f"锚点未找到于 {rel}"
            p.write_text(text.replace(old, new, 1), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", _BOOTSTRAP, "-q", *selector.split()],
            cwd=dst, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        status = "RED(预期)" if proc.returncode != 0 else "GREEN(失败:测试没抓到)"
        tail = (proc.stdout or proc.stderr).strip().splitlines()[-1:]
        return name, status, tail[0] if tail else ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    bad = 0
    for name, mutations, selector in CASES:
        n, status, detail = run_case(name, mutations, selector)
        print(f"[{status:14}] {n}  {detail}")
        if not status.startswith("RED"):
            bad += 1
    print(f"\n{len(CASES) - bad}/{len(CASES)} 条测试成功变红")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
