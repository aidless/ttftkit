"""文档里的计数、基线表和 CI 形状必须跟仓库里的东西一致。

不生成第二份数字。断言失败时，改文档去贴源码，或者改源码后改文档。
"""
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

from conftest import SCENARIOS

from ttftkit.metrics import summarize
from ttftkit.models import RequestRecord

ROOT = Path(__file__).resolve().parent.parent


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _table(readme: str, key: str) -> str:
    prefix = f"| {key} |"
    for line in readme.splitlines():
        if line.startswith(prefix):
            return line.split("|")[2].strip()
    raise AssertionError(f"README 计数表缺少 {key}")


def _red_check_cases() -> int:
    import importlib.util

    path = ROOT / "scripts" / "red_check.py"
    spec = importlib.util.spec_from_file_location("red_check_cases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return len(module.CASES)


def _runs():
    runs = []
    for i in range(1, 6):
        runs.append(json.loads((ROOT / "baselines" / f"run{i}.json").read_text(encoding="utf-8")))
    return runs


def test_red_check_and_lab_counts():
    n = _red_check_cases()
    readme = _readme()
    labs = (ROOT / "docs" / "labs.md").read_text(encoding="utf-8")
    assert _table(readme, "red_check 用例") == str(n)
    match = re.search(r"red_check\.py\s+#\s+(\d+)/\1 条必须 RED", labs)
    assert match is not None
    assert int(match.group(1)) == n
    assert len(re.findall(r"^## Lab \d+", labs, re.M)) == 8
    assert "8 个实验" in readme


def test_pytest_collected_matches_readme():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--override-ini", "addopts="],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    nodeids = [line for line in proc.stdout.splitlines() if "::" in line]
    assert proc.returncode == 0, proc.stderr
    assert _table(_readme(), "pytest 收集") == str(len(nodeids))


def test_ci_matches_readme():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    readme = _readme()
    jobs = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", ci, re.M)
    assert ", ".join(jobs) == _table(readme, "CI jobs")
    versions = re.search(r"python:\s*\[([^\]]+)\]", ci)
    assert versions is not None
    parsed = ", ".join(part.strip().strip("'\"") for part in versions.group(1).split(","))
    assert parsed == _table(readme, "CI Python")
    red = re.search(
        r"if:\s*matrix\.python == '([^']+)'\s*\n\s*run:\s*python scripts/red_check\.py",
        ci,
    )
    assert red is not None
    assert red.group(1) == _table(readme, "red_check 的 CI Python")
    assert "pypa/gh-action-pypi-publish" not in ci
    assert "twine" not in ci
    for name in ("run1.json", "run2.json", "run3.json", "run4.json", "run5.json"):
        assert name in ci


def test_baseline_table_matches_json():
    specs = [
        ("TTFT mean (ms)", lambda s: s["ttft_ms"]["mean"]),
        ("TTFT p50 (ms)", lambda s: s["ttft_ms"]["p50"]),
        ("TTFT p95 (ms)", lambda s: s["ttft_ms"]["p95"]),
        ("TTFT cv（单次内）", lambda s: s["ttft_ms"]["cv"]),
        ("TPOT mean (ms)", lambda s: s["tpot_ms"]["mean"]),
        ("TPOT p95 (ms)", lambda s: s["tpot_ms"]["p95"]),
        ("请求总延迟 p95 (ms)", lambda s: s["latency_ms"]["p95"]),
        ("请求吞吐 (req/s)", lambda s: s["requests_per_s"]),
        ("token 吞吐 (tok/s)", lambda s: s["output_tokens_per_s"]),
        ("退化比（后1/4÷前1/4）", lambda s: s["degradation_ratio"]),
    ]
    text = (ROOT / "baselines" / "README.md").read_text(encoding="utf-8")
    summaries = [run["summary"] for run in _runs()]
    readme = _readme()
    for label, pick in specs:
        values = [pick(summary) for summary in summaries]
        median = statistics.median(values)
        span = (max(values) - min(values)) / median * 100
        cv = statistics.stdev(values) / statistics.mean(values) * 100
        cells = [f"{v:.2f}" for v in values]
        cells += [f"{median:.2f}", f"{span:.1f}%", f"{cv:.1f}%"]
        row = next(line for line in text.splitlines() if line.startswith(f"| {label} |"))
        assert [part.strip() for part in row.strip("|").split("|")[1:]] == cells
        if label == "TTFT p95 (ms)":
            assert f"{span:.1f}%" in readme
        if label == "TTFT p50 (ms)":
            assert f"{span:.1f}%" in readme
        if label == "token 吞吐 (tok/s)":
            assert f"{span:.1f}%" in readme


def test_main_json_is_the_median_p50_run():
    runs = _runs()
    main = json.loads((ROOT / "baselines" / "main.json").read_text(encoding="utf-8"))
    p50s = [run["summary"]["ttft_ms"]["p50"] for run in runs]
    median = statistics.median(p50s)
    closest = min(runs, key=lambda run: abs(run["summary"]["ttft_ms"]["p50"] - median))
    assert main == closest
    assert main == json.loads((ROOT / "baselines" / "run4.json").read_text(encoding="utf-8"))


def test_smoke_figures_and_schema_gap_are_stated():
    smoke = json.loads((ROOT / "results" / "smoke-ollama.json").read_text(encoding="utf-8"))
    summary = smoke["summary"]
    readme = _readme()
    for value in (
        summary["ttft_ms"]["p50"],
        summary["tpot_ms"]["mean"],
        summary["output_tokens_per_s"],
    ):
        assert f"{value:.2f}" in readme
    assert "hardware" not in smoke
    assert "无 `hardware` 键" in readme
    omitted = {"gpu_util_peak_pct", "mem_used_delta_mb", "mem_used_peak_mb"}
    assert omitted.isdisjoint(summary)


def test_bad_scenario_count_matches_labs():
    labs = (ROOT / "docs" / "labs.md").read_text(encoding="utf-8")
    bad = sorted(name for name in SCENARIOS if name != "normal")
    match = re.search(r"注入 (\d+) 种坏行为", labs)
    assert match is not None
    assert int(match.group(1)) == len(bad)
    for name in bad:
        assert f"`{name}`" in labs


def test_failure_mode_doc_names_the_locking_tests():
    text = (ROOT / "docs" / "FAILURE_MODES.md").read_text(encoding="utf-8")
    for name in (
        "test_inflated_usage_shrinks_tpot_and_gate_still_passes",
        "test_gate_ignores_records_and_wall_clock",
        "test_limits_only_policy_does_not_compare_baseline_numbers",
        "test_checked_in_runs_have_no_wall_clock",
    ):
        assert name in text
    readme = _readme()
    assert "docs/FAILURE_MODES.md" in readme
    assert "OBSERVED" in readme
    assert "inferred" in readme


def test_no_author_home_paths_or_publish_badges():
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # 拼起来再查，避免本文件的源码里出现这些连续字面量，把自己判红。
    needles = (
        "/Users" + "/",
        "/home" + "/",
        "C:" + "\\Users",
        "C:/" + "Users/",
        "shields" + ".io",
        "pypi" + ".org",
    )
    for rel in listed.stdout.splitlines():
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            assert needle not in text, f"{rel} contains {needle}"


def _as_records(data: dict) -> list[RequestRecord]:
    records = []
    for raw in data["records"]:
        records.append(
            RequestRecord(
                index=raw["index"],
                ok=raw["ok"],
                latency_ms=raw["latency_ms"],
                ttft_ms=raw["ttft_ms"],
                decode_ms=raw["decode_ms"],
                content_chunks=raw["content_chunks"],
                output_tokens=raw["output_tokens"],
                prompt_tokens=raw["prompt_tokens"],
                chunk_intervals_ms=tuple(raw["chunk_intervals_ms"]),
                error=raw["error"],
                started_at=raw["started_at"],
            )
        )
    return records


def _close(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_close(left[k], right[k]) for k in left)
    if isinstance(left, float) or isinstance(right, float):
        if left is None or right is None:
            return left is right
        return abs(float(left) - float(right)) <= 1e-6 * max(1.0, abs(float(left)))
    return left == right


def test_checked_in_summaries_match_records():
    paths = [ROOT / "baselines" / f"run{i}.json" for i in range(1, 6)]
    paths.append(ROOT / "baselines" / "main.json")
    paths.append(ROOT / "results" / "smoke-ollama.json")
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        got = summarize(_as_records(data), data["duration_s"], hardware=data.get("hardware"))
        for key, value in data["summary"].items():
            assert _close(value, got[key]), f"{path.name} {key}"
        for key, value in got.items():
            if key not in data["summary"]:
                assert value is None, f"{path.name} dropped non-null {key}"
