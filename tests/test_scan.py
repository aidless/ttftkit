"""SLO 负载扫描：把「多快」换成「在什么延迟约束下还能多快」。

依据 docs/papers.md C1（OpenDC-Infer-LC）：SLO-qualified goodput = 满足 p95 延迟与
成功率约束的**最高**负载对应的有效吞吐。关键纪律：违反 SLO 的那一级必须留在结果里，
不许只报好看的数字；一级失败即停，不许继续往上扫。
"""
from __future__ import annotations

import json

import pytest

from ttftkit.gate import evaluate
from ttftkit.scan import scan


def _run_report(concurrency: int, rps: float, ttft_p95: float | None, *, error_rate: float = 0.0,
                tokens_per_s: float | None = 20.0) -> dict:
    requests = 8
    return {
        "kind": "run",
        "config": {"concurrency": concurrency, "requests": requests},
        "records": [],
        "summary": {
            "requests": requests,
            "successful_requests": int(round(requests * (1 - error_rate))),
            "error_rate": error_rate,
            "requests_per_s": rps,
            "output_tokens_per_s": tokens_per_s,
            "ttft_ms": None if ttft_p95 is None else {"count": requests, "p95": ttft_p95},
            "tpot_ms": {"count": requests, "p95": 20.0},
        },
    }


def test_scan_stops_at_first_violation_and_keeps_the_bad_level():
    seen: list[int] = []
    latencies = {1: 100.0, 2: 300.0, 4: 900.0}

    def run_level(level: int) -> dict:
        seen.append(level)
        return _run_report(level, 10.0 / level, latencies[level])

    result = scan(run_level, [1, 2, 4], slo_ttft_ms=200.0)
    assert seen == [1, 2], "违反 SLO 之后不许继续扫"
    assert result["kind"] == "scan"
    assert result["stopped_at"] == 2
    assert result["slo_rps"] == 10.0
    assert [row["satisfied"] for row in result["levels"]] == [True, False]
    assert result["levels"][1]["violations"]


def test_scan_all_levels_pass_reports_the_highest():
    def run_level(level: int) -> dict:
        return _run_report(level, float(level) * 2, 50.0)

    result = scan(run_level, [1, 2, 4], slo_ttft_ms=500.0)
    assert result["stopped_at"] is None
    assert result["slo_rps"] == 8.0
    assert [row["satisfied"] for row in result["levels"]] == [True, True, True]


def test_scan_without_any_satisfying_level_has_no_slo_rps():
    def run_level(level: int) -> dict:
        return _run_report(level, 1.0, 9999.0)

    result = scan(run_level, [1, 2], slo_ttft_ms=500.0)
    assert result["slo_rps"] is None
    assert result["slo_output_tokens_per_s"] is None
    assert result["stopped_at"] == 1


def test_error_rate_violation_counts_as_not_satisfied():
    def run_level(level: int) -> dict:
        return _run_report(level, 2.0, 10.0, error_rate=0.5)

    result = scan(run_level, [1], slo_error_rate=0.01)
    assert result["slo_rps"] is None
    assert any("error_rate" in v for v in result["levels"][0]["violations"])


def test_missing_ttft_evidence_is_not_satisfied():
    """没有 p95 证据 ≠ 满足 SLO：null 绝不能被当成通过。"""

    def run_level(level: int) -> dict:
        return _run_report(level, 2.0, None)

    result = scan(run_level, [1], slo_ttft_ms=500.0)
    assert result["slo_rps"] is None
    assert result["levels"][0]["satisfied"] is False


def test_levels_must_be_ascending_positive_integers():
    for bad in ([], [0], [-1], [4, 2], [1, 1], [1, 2.0]):
        with pytest.raises(ValueError):
            scan(lambda level: _run_report(level, 1.0, 10.0), bad, slo_ttft_ms=100.0)


def test_gate_refuses_a_scan_report():
    """扫描结果是多级聚合，不能当单次测量过门禁——必须挑一级出来再判。"""

    def run_level(level: int) -> dict:
        return _run_report(level, 2.0, 10.0)

    result = scan(run_level, [1, 2], slo_ttft_ms=100.0)
    policy = {"limits": {"ttft_ms.p95": 1000}}
    verdict = evaluate(result, policy, None)
    assert verdict["exit_code"] == 2
    assert any("scan" in reason for reason in verdict["reasons"])


def test_cli_scan_stops_when_latency_grows_with_concurrency(sse_base, tmp_path):
    """congest 场景：服务端延迟随在飞请求数增长，SLO 在某个级别必然被打破。"""
    from ttftkit.cli import main

    out = tmp_path / "scan.json"
    code = main([
        "scan", "--endpoint", f"{sse_base}/congest", "--model", "fake",
        "--levels", "1,2,8", "--requests", "8", "--max-tokens", "8",
        "--slo-ttft", "150", "--output", str(out),
    ])
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["kind"] == "scan"
    assert report["slo_rps"] is not None
    assert report["stopped_at"] == 8, report["levels"]
    assert [row["concurrency"] for row in report["levels"]] == [1, 2, 8]
    md = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "SLO" in md
