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
        "M3 整请求总时限",
        [
            ("src/ttftkit/client.py",
             '                if time.perf_counter() > deadline:\n'
             '                    return _fail(index, t0, "timeout")\n',
             ''),
        ],
        "tests/test_client_hardening.py::test_total_deadline_stops_slow_trickle",
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
]


def run_case(name, mutations, selector):
    tmp = Path(tempfile.mkdtemp(prefix="ttftkit-red-"))
    try:
        dst = tmp / "pkg"
        shutil.copytree(ROOT / "src", dst / "src")
        shutil.copytree(ROOT / "tests", dst / "tests")
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
