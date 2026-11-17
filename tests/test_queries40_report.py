"""40 条 query 实弹的「判决组装」离线测试。

为什么要测这一支
----------------
实弹本身要 GPU（vLLM + 4B 模型）。脚本里把「两次 baseline 是否可复现」「逐 query 翻转槽位」
「Wilson 区间」「判词」全写在 `main()` 里，意味着这些逻辑在拿到 GPU 之前从没被执行过——
真跑一轮 15 分钟，最后在组装报告时崩掉或把分母算错，等于白跑。

所以把组装段抽成纯函数 `build_report()`，用合成报告（形状与 run_benchmark 输出一致）测它。
合成数据在这里是**夹具**，不是读数；任何对外数字仍只能来自实弹 JSON。
"""
import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "invariance_queries40_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("queries40", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def q40():
    return _load()


def _cfg(concurrency: int) -> dict:
    """gate 的可比性键：除 concurrency 外全同，否则会被判不可比。"""
    return {
        "protocol": "openai-completions",
        "model": "Qwen3-4B-AWQ",
        "max_tokens": 128,
        "prompt_sha256": "deadbeef",
        "concurrency": concurrency,
        "temperature": 0.7,
        "seed": 1234,
        "capture_outputs": True,
    }


def _report(concurrency: int, texts: list[str]) -> dict:
    return {
        "config": _cfg(concurrency),
        "outputs": [{"index": i, "text": t} for i, t in enumerate(texts)],
        "summary": {
            "ttft_ms": {"p50": 120.0, "p99": 400.0},
            "tpot_ms": {"p50": 40.0, "p99": 45.0},
        },
    }


def _same(n: int) -> list[str]:
    return [f"答案 {i}" for i in range(n)]


def test_基线两次一致时可复现(q40):
    base = _same(40)
    report, rows = q40.build_report(
        _report(1, base), _report(1, base), {"c4": _report(4, base)}, q40.DEFAULT_POLICY)
    assert report["baseline_reproducible"] is True
    assert report["baseline_drift_indices"] == []
    assert rows[0]["diverged"] == 0


def test_基线两次不一致必须判作废(q40):
    a = _same(40)
    b = list(a)
    b[7] = "漂移"
    report, _ = q40.build_report(
        _report(1, a), _report(1, b), {"c4": _report(4, a)}, q40.DEFAULT_POLICY)
    assert report["baseline_reproducible"] is False
    assert report["baseline_drift_indices"] == [7]


def test_零分歧是决定性PASS(q40):
    base = _same(40)
    _, rows = q40.build_report(
        _report(1, base), _report(1, base), {"c4": _report(4, base)}, q40.DEFAULT_POLICY)
    assert rows[0]["decisiveness"] == "PASS_DECISIVE"
    assert rows[0]["wilson_high"] < q40.CAP


def test_三个翻转仍是决定性PASS(q40):
    base = _same(40)
    cand = list(base)
    for i in (0, 5, 9):
        cand[i] = "翻了"
    _, rows = q40.build_report(
        _report(1, base), _report(1, base), {"c4": _report(4, cand)}, q40.DEFAULT_POLICY)
    assert rows[0]["diverged"] == 3
    assert rows[0]["flipped_indices"] == [0, 5, 9]
    assert rows[0]["decisiveness"] == "PASS_DECISIVE"


def test_十五个翻转是决定性FAIL(q40):
    base = _same(40)
    cand = list(base)
    for i in range(15):
        cand[i] = "翻了"
    _, rows = q40.build_report(
        _report(1, base), _report(1, base), {"c4": _report(4, cand)}, q40.DEFAULT_POLICY)
    assert rows[0]["decisiveness"] == "FAIL_DECISIVE"
    assert rows[0]["wilson_low"] > q40.CAP
    assert rows[0]["gate_exit"] != 0


def test_build_report_只读真实报告字段(q40):
    """夹具曾经自造一个 run_benchmark 从不产出的 "metrics" 键，于是单测全绿、真跑 KeyError。

    真报告的延迟在 summary 里（results/**/*.json 的顶层键为
    schema_version/kind/config/duration_s/records/hardware/client/summary/outputs）。
    这条断言把「读哪个键」钉在代码上，别再让夹具替生产代码发明字段。
    """
    import inspect

    src = inspect.getsource(q40.build_report)
    assert '["metrics"]' not in src and "['metrics']" not in src, \
        "build_report 读了不存在的 metrics 键：run_benchmark 把延迟放在 summary 里"
