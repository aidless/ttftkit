"""客户端 lag 进报告与门禁：把「测的是客户端还是服务端」变成可判的事。

见 docs/papers.md A1：单进程 asyncio 客户端的排队会注入 TTFT。报告要显式给出
事件循环延迟，门禁允许把它当上限（客户端撑不住时，这一轮的数字不该被当服务端性能）。
"""
from __future__ import annotations

from ttftkit.gate import evaluate
from ttftkit.report import markdown


def _client(p95=1.5, p50=0.8, samples=200):
    return {"lag_ms": {"p50_ms": p50, "p95_ms": p95, "max_ms": p95 * 2, "samples": samples,
                       "interval_s": 0.01}}


def _report(client_block=None, lag_p95=None):
    return {
        "kind": "run",
        "config": {"model": "tiny", "concurrency": 1, "requests": 8, "max_tokens": 8,
                   "warmup": 0, "protocol": "openai-sse-v1", "profile": "p",
                   "prompt_sha256": "a" * 64},
        "summary": {
            "requests": 8, "successful_requests": 8, "error_rate": 0.0,
            "requests_per_s": 4.0, "output_tokens_per_s": 20.0, "token_usage_coverage": 1.0,
            "ttft_ms": {"count": 8, "mean": 90.0, "p50": 90.0, "p95": 95.0, "p99": 99.0},
            "tpot_ms": {"count": 8, "mean": 20.0, "p50": 20.0, "p95": 22.0, "p99": 23.0},
            "latency_ms": None, "decode_ms": None, "chunk_gap_ms": None,
            "degradation_ratio": 1.0,
            "client_lag_p95_ms": lag_p95,
            "errors": {},
        },
        "records": [],
        "duration_s": 2.0,
        "client": client_block,
    }


def test_client_lag_limit_is_a_ceiling():
    verdict = evaluate(_report(_client(p95=35.0), lag_p95=35.0),
                       {"limits": {"client_lag_p95_ms": 20.0}}, None)
    assert verdict["exit_code"] == 1
    assert any("client_lag" in c["metric"] and c["status"] == "FAIL" for c in verdict["checks"])


def test_client_lag_under_the_ceiling_passes():
    verdict = evaluate(_report(_client(p95=2.0), lag_p95=2.0),
                       {"limits": {"client_lag_p95_ms": 20.0}}, None)
    assert verdict["exit_code"] == 0


def test_summary_claiming_lag_without_a_client_block_is_configuration_error():
    """没有 client 块却声称 lag 读数 → 伪造证据，必须 exit 2。"""
    verdict = evaluate(_report(None, lag_p95=5.0), {"limits": {"client_lag_p95_ms": 20.0}}, None)
    assert verdict["exit_code"] == 2


def test_client_block_and_summary_must_agree():
    verdict = evaluate(_report(_client(p95=2.0), lag_p95=9.0),
                       {"limits": {"client_lag_p95_ms": 20.0}}, None)
    assert verdict["exit_code"] == 2


def test_report_renders_client_section():
    text = markdown(_report(_client(p95=1.5), lag_p95=1.5))
    assert "客户端" in text
    assert "1.5" in text


def test_report_warns_when_client_lag_dominates_ttft():
    text = markdown(_report(_client(p95=40.0), lag_p95=40.0))
    assert "污染" in text or "上界" in text


def test_report_without_client_block_says_not_measured():
    text = markdown(_report(None, lag_p95=None))
    assert "未记录" in text or "n/a" in text
