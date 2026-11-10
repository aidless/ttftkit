"""run_benchmark 的 capture_outputs：invariance 闸的证据面，报告要带逐请求输出文本。"""
import asyncio

from ttftkit.runner import run_benchmark


def _run(base, requests=3, **kwargs):
    return asyncio.run(run_benchmark(
        endpoint=f"{base}/normal/v1/chat/completions",
        model="tiny", prompt="hi", requests=requests, concurrency=1, max_tokens=8, timeout_s=5,
        **kwargs,
    ))


def test默认不捕获_报告无outputs键(sse_base):
    report = _run(sse_base)
    assert "outputs" not in report  # 没开捕获就没有 outputs 键（不是空列表）


def test开启捕获_报告带逐请求输出(sse_base):
    report = _run(sse_base, capture_outputs=True)
    outs = report["outputs"]
    assert len(outs) == 3  # normal 场景 3 个请求全部成功
    for i, entry in enumerate(sorted(outs, key=lambda e: e["index"])):
        assert entry["index"] == i
        assert entry["text"] == "abc"  # 'normal' 场景固定内容 a+b+c
    assert report["config"]["capture_outputs"] is True


def test开启捕获_outputs按请求序号(sse_base):
    report = _run(sse_base, capture_outputs=True)
    assert [e["index"] for e in report["outputs"]] == [0, 1, 2]
