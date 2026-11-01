"""RED 反证：把每处修复「回退」到修复前的写法，证明对应新测试确实会变红。

在临时副本上操作，绝不改动真实仓库。每条：把某处修复回退 → 跑对应测试 → 必须失败。
如果回退后测试仍通过，说明这条测试测的是装饰而不是机制。
"""
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
             'return _fail(index, t0, "unsuccessful finish_reason", latency_ms)',
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
             "            _measure_once(client, payload, index=index, t0=t0, timeout_s=timeout_s, path=path),\n"  # noqa: E501
             "            timeout=timeout_s,\n"
             "        )\n"
             "    except asyncio.TimeoutError:\n"
             "        return _fail(index, t0, \"timeout\")\n",
             "    return await _measure_once(client, payload, index=index, t0=t0, timeout_s=timeout_s, path=path)\n"),  # noqa: E501
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
             """        # 显存/利用率的相对比较只对同一块卡有意义：指纹缺失或不同 → 不可比
        if any(name in HARDWARE_METRICS for name in policy["regressions"]):
            cand_hw = (candidate.get("config") or {}).get("hardware")
            base_hw = (baseline.get("config") or {}).get("hardware")
            if not cand_hw or not base_hw:
                return {"exit_code": 2, "checks": [], "reasons": [
                    "hardware regression needs 'config.hardware' in both candidate and baseline; "
                    f"got candidate={cand_hw!r} baseline={base_hw!r}"]}
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
        "M12 硬件标量类型/符号校验（坏报告 exit 2）",
        [
            ("src/ttftkit/gate.py",
             """    for metric in HARDWARE_METRICS:
        node = summary.get(metric)
        if node is None:
            continue  # 未采样（schema 允许）——规则用到它时由上层判「无可用值」
        if isinstance(node, bool) or not isinstance(node, (int, float)):
            raise _ConfigError(f"'{metric}' must be a number or null, got {node!r}")
        if not math.isfinite(node) or node < 0:
            raise _ConfigError(f"'{metric}' must be finite and non-negative, got {node!r}")
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
]


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
            [sys.executable, "-m", "pytest", "-q", *selector.split()],
            cwd=dst, capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
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
