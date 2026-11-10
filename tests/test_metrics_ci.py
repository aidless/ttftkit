"""分位数不是点估计：样本少到只有 8 条时，p95 的波动可以超过 100%。

依据 docs/papers.md B2：进入 CI 的判定不许依赖未固定种子的随机重采样。
所以这里同时钉住三件事：样本不足时不给区间、给了区间就得有序、同样输入必须同样输出。
"""
from __future__ import annotations

from ttftkit.metrics import MIN_CI_SAMPLES, distribution


def test_small_sample_has_no_interval():
    dist = distribution([float(i) for i in range(MIN_CI_SAMPLES - 1)])
    assert dist is not None
    assert dist.get("ci") is None


def test_enough_samples_get_an_interval_that_brackets_the_point_estimate():
    values = [float(i % 37) + 10.0 for i in range(200)]
    dist = distribution(values)
    ci = dist["ci"]
    assert ci["level"] == 0.95
    assert ci["iterations"] >= 200
    assert ci["seed"] == 0
    for point, key in ((dist["p50"], "p50"), (dist["p95"], "p95")):
        low, high = ci[key]
        assert low <= point <= high, (key, low, point, high)


def test_interval_is_deterministic():
    values = [float(i % 53) for i in range(120)]
    first = distribution(values)["ci"]
    second = distribution(values)["ci"]
    assert first == second


def test_bootstrap_threads_the_seed_into_the_rng(monkeypatch):
    """种子必须是「传进去的」，不是「碰巧两次一样」。

    M37 反证暴露过：只比较两次输出的结果，在 1000 次重采样下可能偶然相等，
    于是「回退成无种子 Random」仍能过——那测的是巧合，不是机制。
    """
    import random

    from ttftkit import metrics

    seeds: list[int | None] = []
    real = random.Random

    class _Spy(real):
        def __init__(self, seed=None):
            seeds.append(seed)
            super().__init__(seed)

    monkeypatch.setattr(metrics.random, "Random", _Spy)
    metrics.bootstrap_ci([float(i % 31) for i in range(40)], iterations=50)
    assert seeds == [metrics.CI_SEED]


def test_narrow_distribution_has_narrow_interval():
    tight = distribution([100.0] * 200)["ci"]
    wide = distribution([float(i) for i in range(200)])["ci"]
    tight_width = tight["p95"][1] - tight["p95"][0]
    wide_width = wide["p95"][1] - wide["p95"][0]
    assert tight_width <= wide_width
