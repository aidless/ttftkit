"""阈值校准的成对证据：既要放行真实跨跑噪声，也要抓住人为回归。

单有「全都 PASS」不算证明——那可能只是阈值太宽。所以每个接受性断言都配一条
注入违规的拒绝性断言（RED 在这里是「造一份退化报告，门禁必须 exit 1」）。
"""
import json
from pathlib import Path

from ttftkit.gate import evaluate

ROOT = Path(__file__).resolve().parent.parent
BASELINE = json.loads((ROOT / "baselines" / "main.json").read_text(encoding="utf-8"))
POLICY = json.loads((ROOT / "configs" / "policy.baseline.json").read_text(encoding="utf-8"))
SELF_RUNS = [json.loads((ROOT / "baselines" / f"run{i}.json").read_text(encoding="utf-8"))
             for i in range(1, 6)]


def test_policy_accepts_every_self_variation_run():
    # 5 次同机同配置的真实跨跑，全部必须 PASS：否则门禁在拿噪声当回归
    codes = [evaluate(run, POLICY, BASELINE)["exit_code"] for run in SELF_RUNS]
    assert codes == [0, 0, 0, 0, 0]


def test_policy_rejects_simulated_ttft_regression():
    bad = json.loads(json.dumps(BASELINE))
    for key in ("mean", "p50", "p95", "p99"):
        bad["summary"]["ttft_ms"][key] *= 3.0
    verdict = evaluate(bad, POLICY, BASELINE)
    assert verdict["exit_code"] == 1
    assert any("ttft_ms.p50" in r for r in verdict["reasons"])


def test_policy_rejects_throughput_collapse():
    bad = json.loads(json.dumps(BASELINE))
    bad["summary"]["output_tokens_per_s"] = 8.0  # 39.2 → 8.0，约 -80%
    verdict = evaluate(bad, POLICY, BASELINE)
    assert verdict["exit_code"] == 1


def test_policy_rejects_any_failed_request():
    bad = json.loads(json.dumps(BASELINE))
    bad["summary"]["successful_requests"] = bad["summary"]["requests"] - 1
    bad["summary"]["error_rate"] = 1 / bad["summary"]["requests"]
    # 分布 count 必须同步降到 7，否则是「报告自相矛盾」（exit 2），测不到失败预算
    for name in ("ttft_ms", "tpot_ms", "latency_ms", "decode_ms"):
        dist = bad["summary"].get(name)
        if isinstance(dist, dict):
            dist["count"] = bad["summary"]["successful_requests"]
    verdict = evaluate(bad, POLICY, BASELINE)
    assert verdict["exit_code"] == 1
    assert any("error_rate" in r for r in verdict["reasons"])


def test_policy_rejects_missing_vram_evidence_for_hardware_rule():
    # 显存规则一旦写进策略，「没采样」必须是 FAIL 而不是 PASS
    policy = {"limits": {"mem_used_delta_mb": 512}}
    report = json.loads(json.dumps(BASELINE))
    assert report["hardware"] is None  # 这批基线是 --gpu none 采的
    assert evaluate(report, policy)["exit_code"] == 1
