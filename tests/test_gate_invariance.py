"""invariance 闸：同 prompt 不同 batch 下输出必须逐字节一致（batch 不变性验收）。

RED→GREEN 纪律：本文件先写（此刻 unknown policy key → 全部 exit 2），实现后才绿。
fail-closed 口径：缺 outputs 证据 = 没测 = exit 2，绝不当通过。
"""
import copy

from ttftkit.gate import evaluate


def _report(outputs=None, **config_overrides):
    """构造一份最小 run 报告。outputs=None 表示「没开捕获」（缺证据形态）。"""
    config = {
        "protocol": "openai-chat-stream", "model": "tiny", "concurrency": 1,
        "max_tokens": 8, "requests": 4,
        "prompt_sha256": "a" * 64, "profile": "test",
    }
    config.update(config_overrides)
    summary = {
        "requests": 4, "successful_requests": 4, "error_rate": 0.0,
    }
    report = {"kind": "run", "config": config, "summary": summary}
    if outputs is not None:
        report["outputs"] = outputs
    return report


def _outputs(texts):
    return [{"index": i, "text": t} for i, t in enumerate(texts)]


GOOD = ["答案A", "答案A", "答案A", "答案A"]
DIFF = ["答案A", "答案A", "答案B", "答案A"]  # batch 下有一个请求输出不同
POLICY = {"invariance": {"against_baseline": True}}


class Testinvariance闸:
    def test输出一致_通过(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=_outputs(GOOD), concurrency=16)  # 只有 batch 不同
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 0, r["reasons"]
        check = [c for c in r["checks"] if c.get("rule") == "invariance"]
        assert check and check[0]["status"] == "PASS"

    def test输出不一致_失败(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=_outputs(DIFF), concurrency=16)
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 1
        check = [c for c in r["checks"] if c.get("rule") == "invariance"]
        assert check and check[0]["status"] == "FAIL"
        assert any("output" in str(reason).lower() or "输出" in str(reason)
                   for reason in r["reasons"])

    def test缺outputs_是没测不是通过(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(concurrency=16)  # candidate 没开捕获
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2
        assert any("outputs" in reason for reason in r["reasons"])

    def test基线缺outputs_同样没测(self):
        base = _report()  # 基线没开捕获
        cand = _report(outputs=_outputs(GOOD), concurrency=16)
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2
        assert any("baseline" in reason.lower() for reason in r["reasons"])

    def test协议不可比_model或prompt不同_配置错误(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=_outputs(GOOD), concurrency=16,
                       prompt_sha256="b" * 64)  # 换了 prompt，不变性无从谈起
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2
        assert any("compar" in reason.lower() or "不可比" in reason
                   for reason in r["reasons"])

    def test策略畸形_配置错误(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=_outputs(GOOD), concurrency=16)
        for bad in [{"invariance": {"against_baseline": False}},
                    {"invariance": {"unknown_key": True}},
                    {"invariance": True}]:
            r = evaluate(copy.deepcopy(cand), bad, baseline=copy.deepcopy(base))
            assert r["exit_code"] == 2, bad

    def test无基线_配置错误(self):
        cand = _report(outputs=_outputs(GOOD))
        r = evaluate(cand, POLICY, baseline=None)
        assert r["exit_code"] == 2

    def test空outputs_没测(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=[], concurrency=16)
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2

    def test文本非字符串_配置错误(self):
        base = _report(outputs=_outputs(GOOD))
        cand = _report(outputs=[{"index": 0, "text": 123}], concurrency=16)
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2
