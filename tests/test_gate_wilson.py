"""Wilson 区间与判词（PASS/FAIL 是否决定性）的测试。

来由：40 条 query 的实弹要回答「0.2 这一档到底判不判得开」，
判据必须是可复算的区间，而不是「看着差不多」。这两个函数原先埋在
invariance_queries40_run.py 里，无法被成套测试覆盖、也会和 gate 里的
_wilson_upper 各写一份漂移，所以上提到 gate 并在这里钉住边界。
"""
import math

import pytest

from ttftkit.gate import decisiveness, wilson_interval


class TestWilsonInterval:
    def test_零分歧时上界随样本量收紧(self):
        # n=8 的 0/8 上界 0.324 > 0.2（判不了）；n=40 的 0/40 上界 0.088 < 0.2（判得开）
        lo8, hi8 = wilson_interval(0, 8)
        lo40, hi40 = wilson_interval(0, 40)
        assert lo8 == pytest.approx(0.0, abs=1e-9)
        assert hi8 == pytest.approx(0.324, abs=0.002)
        assert hi40 == pytest.approx(0.088, abs=0.002)
        assert hi40 < hi8

    def test_全分歧时下界随样本量上升(self):
        lo40, hi40 = wilson_interval(40, 40)
        assert hi40 == pytest.approx(1.0, abs=1e-9)
        assert lo40 == pytest.approx(0.912, abs=0.002)

    def test_区间必须包含点估计(self):
        for k, n in [(0, 40), (3, 40), (15, 40), (4, 8), (40, 40)]:
            lo, hi = wilson_interval(k, n)
            assert lo <= k / n <= hi

    def test_样本为零时返回全区间而不是零宽(self):
        # 无证据 ≠ 零分歧：给 [0,1] 让调用方必须自己降级，不能读到「上界 0」
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_非法计数抛ValueError(self):
        for k, n in [(-1, 8), (9, 8), (1, -3)]:
            with pytest.raises(ValueError):
                wilson_interval(k, n)

    def test_端点必须精确夹在零到一之间(self):
        # 浮点会让未夹的区间越界：k=0,n=5 的原始下界是 -3e-17，k=5,n=5 的原始上界略大于 1。
        # 用 >=0 / <=1 的精确断言（不是 approx）钉住夹取，否则「区间」会报出负概率。
        for n in (1, 5, 10, 15, 18, 19, 20, 22, 23, 40):
            lo, _ = wilson_interval(0, n)
            _, hi = wilson_interval(n, n)
            assert lo >= 0.0, f"n={n} 下界为负: {lo!r}"
            assert hi <= 1.0, f"n={n} 上界超过 1: {hi!r}"

    def test_三点四十分的上界贴着零点二(self):
        # 这条是 40 条实弹的可判性前提：3/40 的上界 0.199 刚好低于 0.2
        lo, hi = wilson_interval(3, 40)
        assert lo == pytest.approx(0.026, abs=0.002)
        assert hi == pytest.approx(0.199, abs=0.002)
        assert hi < 0.2


class TestDecisiveness:
    def test_上界低于上限判决定性通过(self):
        assert decisiveness(0, 40, 0.2) == "PASS_DECISIVE"
        assert decisiveness(0, 16, 0.2) == "PASS_DECISIVE"

    def test_下界高于上限判决定性失败(self):
        assert decisiveness(15, 40, 0.2) == "FAIL_DECISIVE"
        assert decisiveness(4, 8, 0.2) == "FAIL_DECISIVE"

    def test_区间跨过上限判不了(self):
        # 8 样本的 0/8（上界 0.324）与 3/8（下界 0.137）都跨过 0.2
        assert decisiveness(0, 8, 0.2) == "NOT_DECISIVE"
        assert decisiveness(3, 8, 0.2) == "NOT_DECISIVE"

    def test_四点四十分仍判不了(self):
        # 4/40：下界 0.040 < 0.2 < 上界 0.231 —— 样本够大也可能卡在阈值两侧
        assert decisiveness(4, 40, 0.2) == "NOT_DECISIVE"

    def test_换上限判词跟着换(self):
        # 同一份读数 15/40（点估计 0.375，区间 [0.242, 0.531]）：
        # cap=0.2 判决定性失败，cap=0.6 判决定性通过 —— 判词是 cap 的函数，不是读数的属性。
        assert decisiveness(15, 40, 0.2) == "FAIL_DECISIVE"
        assert decisiveness(15, 40, 0.6) == "PASS_DECISIVE"

    def test_上限为零时连零分歧也判不了(self):
        # cap=0 是个不可达上限：0/40 的 Wilson 上界是 0.088，仍 > 0。
        # 这条钉住「零分歧 ≠ 证明分歧率为 0」，避免把 cap 写成 0 后误读成已证明。
        assert decisiveness(0, 40, 0.0) == "NOT_DECISIVE"
        assert decisiveness(1, 40, 0.0) == "FAIL_DECISIVE"

    def test_非法上限抛ValueError(self):
        for cap in (-0.1, 1.5, math.nan):
            with pytest.raises(ValueError):
                decisiveness(1, 40, cap)
