"""stability PASS 的统计功效见证（RED→GREEN）。

查询级重跑（verdict-queries）发现：n=8 时 0/8 分歧判 PASS，但 Wilson 95%
上界 = 0.324 > 上限 0.2 —— 「PASS」并不是「分歧率 ≤ 0.2」的证据。
gate 不改 PASS/FAIL（判决本身仍按点估计），但必须附一条 power 见证，
把样本不足这件事写进判决书。
"""
from __future__ import annotations

from ttftkit.gate import evaluate


def _report(n, *, diverged=()):
    """构造 n 条输出的报告：baseline 全 A，candidate 的 diverged 槽位改 B。"""
    def build(is_candidate):
        outputs = [{"index": i,
                    "text": f"text-{i}" if not is_candidate else
                            (f"DIFF-{i}" if i in diverged else f"text-{i}")}
                   for i in range(n)]
        return {
            "schema_version": 1, "kind": "run",
            "config": {
                "protocol": "openai-sse-v1", "model": "m", "concurrency": 1,
                "max_tokens": 32, "requests": n, "warmup": 0,
                "prompt_sha256": "ab" * 32, "profile": "p", "temperature": 0.7,
                "timeout_s": 60.0, "top_p": None, "seed": 1234,
                "hardware": None, "client_timing": "asyncio-event-loop",
                "capture_outputs": True,
            },
            "summary": {
                "requests": n, "successful_requests": n, "error_rate": 0.0,
                "ttft_ms": {"count": n, "p50": 50.0, "p99": 100.0},
                "tpot_ms": {"count": n, "p50": 10.0, "p99": 20.0},
            },
            "outputs": outputs,
        }
    return build(True), build(False)


def _power(v):
    return [c for c in v["checks"] if c.get("kind") == "power"]


def test样本少_PASS带power见证():
    cand, base = _report(8)
    v = evaluate(cand, {"stability": {"against_baseline": True,
                                     "max_divergence_rate": 0.2}}, baseline=base)
    assert v["exit_code"] == 0  # 见证不改判决
    p = _power(v)
    assert len(p) == 1
    assert p[0]["status"] == "PASS"
    assert "0.324" in p[0]["observed"]   # Wilson 95% upper for 0/8
    assert "n>=16" in p[0]["observed"]   # 零分歧下把上界压到 0.2 以下所需样本


def test样本足_PASS无power见证():
    cand, base = _report(40)
    v = evaluate(cand, {"stability": {"against_baseline": True,
                                     "max_divergence_rate": 0.2}}, baseline=base)
    assert v["exit_code"] == 0
    assert _power(v) == []  # 40 个零分歧的 Wilson 上界 0.088 < 0.2


def testFAIL判决不带power见证():
    cand, base = _report(8, diverged={0, 1, 2, 3})
    v = evaluate(cand, {"stability": {"against_baseline": True,
                                     "max_divergence_rate": 0.2}}, baseline=base)
    assert v["exit_code"] == 1
    assert _power(v) == []  # FAIL 已经是决定性判决，不需要功效见证


def test上限更宽_见证按上界与上限比较():
    cand, base = _report(8)
    v = evaluate(cand, {"stability": {"against_baseline": True,
                                     "max_divergence_rate": 0.5}}, baseline=base)
    assert v["exit_code"] == 0
    assert _power(v) == []  # 上界 0.324 < 0.5，样本量够判这一档


def test上限为零_见证如实说无有限样本可解():
    cand, base = _report(3)
    v = evaluate(cand, {"stability": {"against_baseline": True,
                                     "max_divergence_rate": 0.0}}, baseline=base)
    assert v["exit_code"] == 0  # 退化成硬一致，判决本身不受影响
    p = _power(v)
    assert len(p) == 1
    assert "no finite n" in p[0]["observed"]
