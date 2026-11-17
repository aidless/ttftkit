"""服务端配置声明（server_env）必须进可比性键。

为什么需要：`VLLM_BATCH_INVARIANT`（vLLM 文档 docs/features/batch_invariance.md）
直接把「输出是否与 batch 大小/顺序无关」这个语义翻过来——flag=0 时 batch 改变输出是
**预期**，flag=1 时才是 bug。若闸不把这项写进可比性键：

    baseline（flag=1，逐字节一致） vs candidate（flag=0，分歧）

会被判成「服务违反了不变性」，而真实结论是「两份读数不是同一个配置」。这是
误判方向最坏的一类：它把配置差异伪装成实现缺陷。

缺省 {}=未声明。老报告没有这个键，两侧都缺 → 仍可比（向后兼容）。
"""
import inspect

from ttftkit.gate import evaluate
from ttftkit.runner import run_benchmark


def _report(outputs=None, **config_overrides):
    config = {
        "protocol": "openai-chat-stream", "model": "tiny", "concurrency": 1,
        "max_tokens": 8, "requests": 4,
        "prompt_sha256": "a" * 64, "profile": "test",
    }
    config.update(config_overrides)
    summary = {"requests": 4, "successful_requests": 4, "error_rate": 0.0}
    report = {"kind": "run", "config": config, "summary": summary}
    if outputs is not None:
        report["outputs"] = outputs
    return report


def _outputs(texts):
    return [{"index": i, "text": t} for i, t in enumerate(texts)]


SAME = ["答案A", "答案A", "答案A", "答案A"]
POLICY = {"invariance": {"against_baseline": True}}
STAB_POLICY = {"stability": {"against_baseline": True, "max_divergence_rate": 0.2}}


class Testinvariance闸的server_env:
    def test_flag不同_判不可比而不是判失败(self):
        """最坏误判：flag=1 的 baseline 比 flag=0 的 candidate。"""
        base = _report(outputs=_outputs(SAME),
                       server_env={"VLLM_BATCH_INVARIANT": "1"})
        cand = _report(outputs=_outputs(SAME), concurrency=16,
                       server_env={"VLLM_BATCH_INVARIANT": "0"})
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2, r
        assert any("server_env" in why for why in r["reasons"]), r["reasons"]

    def test_flag相同_照常比较(self):
        base = _report(outputs=_outputs(SAME),
                       server_env={"VLLM_BATCH_INVARIANT": "0"})
        cand = _report(outputs=_outputs(SAME), concurrency=16,
                       server_env={"VLLM_BATCH_INVARIANT": "0"})
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 0, r["reasons"]

    def test_老报告都没有该键_仍可比(self):
        """向后兼容：2026-11-10 那批实测报告没有 server_env 键。"""
        base = _report(outputs=_outputs(SAME))
        cand = _report(outputs=_outputs(SAME), concurrency=16)
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 0, r["reasons"]

    def test_显式空声明与缺键等价(self):
        """`server_env: {}` 与缺键都是「未声明」——但必须由缺省回落实现，
        否则两侧会退化成「不可比」，把合法读数判成配置冲突。"""
        base = _report(outputs=_outputs(SAME))                    # 缺键
        cand = _report(outputs=_outputs(SAME), concurrency=16,
                       server_env={})                             # 显式空
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 0, r["reasons"]

    def test_声明与未声明不可混用(self):
        """「未声明」不等于「默认就是 0」——服务端配置未知时不许假设。"""
        base = _report(outputs=_outputs(SAME))
        cand = _report(outputs=_outputs(SAME), concurrency=16,
                       server_env={"VLLM_BATCH_INVARIANT": "0"})
        r = evaluate(cand, POLICY, baseline=base)
        assert r["exit_code"] == 2, r
        assert any("server_env" in why for why in r["reasons"]), r["reasons"]


class Teststability闸的server_env:
    def test_flag不同_判不可比(self):
        base = _report(outputs=_outputs(SAME),
                       server_env={"VLLM_BATCH_INVARIANT": "1"},
                       temperature=0.7, seed=1234)
        cand = _report(outputs=_outputs(SAME), concurrency=4,
                       server_env={"VLLM_BATCH_INVARIANT": "0"},
                       temperature=0.7, seed=1234)
        r = evaluate(cand, STAB_POLICY, baseline=base)
        assert r["exit_code"] == 2, r
        assert any("server_env" in why for why in r["reasons"]), r["reasons"]

    def test_两个键都相同_照常比较(self):
        base = _report(outputs=_outputs(SAME),
                       server_env={"VLLM_BATCH_INVARIANT": "1",
                                   "VLLM_USE_FLASHINFER_SAMPLER": "0"},
                       temperature=0.7, seed=1234)
        cand = _report(outputs=_outputs(SAME), concurrency=4,
                       server_env={"VLLM_USE_FLASHINFER_SAMPLER": "0",
                                   "VLLM_BATCH_INVARIANT": "1"},
                       temperature=0.7, seed=1234)
        r = evaluate(cand, STAB_POLICY, baseline=base)
        assert r["exit_code"] == 0, r["reasons"]   # dict 比较与键序无关


class Testrunner侧记录:
    def test_server_env写进config(self):
        src = inspect.getsource(run_benchmark)
        assert "server_env" in src, "run_benchmark 必须能收下服务端配置声明"
        assert "server_env" in src and ("config['server_env']" in src
                                        or 'config["server_env"]' in src), \
            "声明必须落到报告的 config 里，否则下游无从归因"
