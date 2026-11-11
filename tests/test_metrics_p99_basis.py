"""p99_basis 标记（n<20 时 p99 是 max，不是分位数）的 RED→GREEN 测试。

实测依据（verdict-matrix.md 补充发现 3）：8 样本的 p99 由末位样本主导
（greedy-c4: median 312.2 vs p99/max 583.3），跨会话把 c=4 判得比 c=16 还慢
——非单调是 max 伪影。报告必须如实标注 p99 的计算基础。
"""
from __future__ import annotations

from ttftkit.metrics import distribution


def test样本8_p99就是max_必须标注():
    data = [100.0, 200.0, 150.0, 180.0, 120.0, 160.0, 140.0, 583.3]
    node = distribution(data)
    assert node["count"] == 8
    assert node["p99_basis"] == "max"
    # 插值分位数下 p99 落在末两位样本之间（≈max），绝不是「第 1% 位」
    assert node["p99"] >= sorted(data)[-2]


def test样本19仍是max_样本20起算分位数():
    small = [float(100 + i) for i in range(19)]
    assert distribution(small)["p99_basis"] == "max"
    big = [float(100 + i) for i in range(20)]
    assert distribution(big)["p99_basis"] == "quantile"


def test样本25_分位数_且小于max():
    data = [float(100 + i * 10) for i in range(25)]
    node = distribution(data)
    assert node["p99_basis"] == "quantile"
    assert node["p99"] < node["max"]
