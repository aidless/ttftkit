"""采样解码分布稳定性闸（stability 规则）的 RED→GREEN 测试。

设计动机（verdict-sampled.md 判决书第 2 条启示）：temperature>0 时逐字节
一致是错误预期——连续批处理的浮点噪声被采样放大，invariance 闸会常态
FAIL。采样解码需要另一把尺：**分歧率**（divergence_rate = 输出与 baseline
不一致的比例）不超过可接受上限，而不是要求 0 分歧。

可比性比 invariance 更严：采样参数（temperature/top_p/seed）也必须一致——
不同采样路径的报告放一起比分歧率是假比较。
"""
from __future__ import annotations

from ttftkit.gate import evaluate


def _report(outputs, *, temperature=0.7, seed=1234, concurrency=1):
    ok = len(outputs)
    return {
        "schema_version": 1, "kind": "run",
        "config": {
            "protocol": "openai-sse-v1", "model": "m", "concurrency": concurrency,
            "max_tokens": 32, "requests": len(outputs), "warmup": 0,
            "prompt_sha256": "ab" * 32, "profile": "p", "temperature": temperature,
            "timeout_s": 60.0, "top_p": None, "seed": seed,
            "hardware": None, "client_timing": "asyncio-event-loop",
            "capture_outputs": True,
        },
        "summary": {"requests": len(outputs), "successful_requests": ok,
                    "error_rate": 0.0,
                    "ttft_ms": {"count": ok, "p50": 1.0, "p99": 1.0},
                    "tpot_ms": {"count": ok, "p50": 1.0, "p99": 1.0}},
        "outputs": [{"index": i, "text": t} for i, t in enumerate(outputs)],
    }


def _policy(cap):
    return {"stability": {"against_baseline": True, "max_divergence_rate": cap}}


def test分歧率低于上限_通过():
    base = _report(["同" * 50] * 4 + ["甲" * 50] * 2, concurrency=1)   # 6 条，2 条不同
    cand = _report(["同" * 50] * 4 + ["乙" * 50] * 2, concurrency=16)
    v = evaluate(cand, _policy(0.4), baseline=base)
    assert v["exit_code"] == 0
    chk = [c for c in v["checks"] if c["rule"] == "stability"][0]
    assert chk["status"] == "PASS"
    assert chk["observed"].startswith("2/6")


def test分歧率超上限_失败():
    base = _report(["同" * 50] * 4, concurrency=1)
    cand = _report(["异" * 50] * 4, concurrency=16)   # 4/4 全不同
    v = evaluate(cand, _policy(0.25), baseline=base)
    assert v["exit_code"] == 1
    chk = [c for c in v["checks"] if c["rule"] == "stability"][0]
    assert chk["status"] == "FAIL"
    assert "4/4" in chk["observed"]


def test完全一致_分歧率0():
    base = _report(["同" * 50] * 3, concurrency=1)
    cand = _report(["同" * 50] * 3, concurrency=16)
    v = evaluate(cand, _policy(0.0), baseline=base)   # 上限 0：退化为硬一致
    assert v["exit_code"] == 0


def test采样参数不同_不可比():
    base = _report(["同" * 50] * 2, seed=1234)
    cand = _report(["同" * 50] * 2, seed=999)   # seed 不同
    v = evaluate(cand, _policy(0.5), baseline=base)
    assert v["exit_code"] == 2
    assert any("seed" in r or "not comparable" in r for r in v["reasons"])


def testtemperature不同_不可比():
    base = _report(["同" * 50] * 2, temperature=0.7)
    cand = _report(["同" * 50] * 2, temperature=0.0)
    v = evaluate(cand, _policy(0.5), baseline=base)
    assert v["exit_code"] == 2


def test上限缺失_配置错误():
    base = _report(["同" * 50] * 2)
    cand = _report(["同" * 50] * 2, concurrency=16)
    v = evaluate(cand, {"stability": {"against_baseline": True}}, baseline=base)
    assert v["exit_code"] == 2


def test上限越界_配置错误():
    base = _report(["同" * 50] * 2)
    cand = _report(["同" * 50] * 2, concurrency=16)
    v = evaluate(cand, _policy(1.5), baseline=base)
    assert v["exit_code"] == 2


def test缺baseline_配置错误():
    cand = _report(["同" * 50] * 2)
    v = evaluate(cand, _policy(0.5), baseline=None)
    assert v["exit_code"] == 2


def test缺outputs_是没测不是通过():
    base = _report(["同" * 50] * 2)
    cand = _report(["同" * 50] * 2, concurrency=16)
    del cand["outputs"]
    v = evaluate(cand, _policy(0.5), baseline=base)
    assert v["exit_code"] == 2


def testindex集不一致_配置错误():
    base = _report(["同" * 50] * 3)
    cand = _report(["同" * 50] * 2, concurrency=16)
    v = evaluate(cand, _policy(0.5), baseline=base)
    assert v["exit_code"] == 2


def test失败明细带分歧位置():
    base = _report(["A" * 40 + "尾巴甲", "B" * 100], concurrency=1)
    cand = _report(["A" * 40 + "尾巴乙", "B" * 100], concurrency=16)
    v = evaluate(cand, _policy(0.25), baseline=base)
    assert v["exit_code"] == 1
    chk = [c for c in v["checks"] if c["rule"] == "stability"][0]
    # 第 1 条在前 40 字符后才分叉，明细应携带最早分歧位置
    assert "first_diff" in chk or "diverge" in chk.get("reason", "") + chk.get("observed", "")
