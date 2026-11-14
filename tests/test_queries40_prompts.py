"""40 条 query 的 prompt 集合守卫。

为什么值得单测：分歧率的分母是**独立 query 数**。这个集合里只要有一条
重复（复制粘贴改一半），分母就被做假成 39，而读数看起来完全正常——
8 条那轮已经踩过同类坑（单 prompt × 单 seed 的 8 个请求被当成 8 个独立样本）。
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "invariance_queries40_run.py"


def _load_prompts():
    spec = importlib.util.spec_from_file_location("queries40", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.argv = ["invariance_queries40_run.py"]  # 脚本顶层读 argv[1] 当 tag
    spec.loader.exec_module(module)
    return module.PROMPTS, module.N


def test_prompt条数等于声明的样本量():
    prompts, n = _load_prompts()
    assert n == 40
    assert len(prompts) == n


def test_prompt两两不同():
    prompts, _ = _load_prompts()
    assert len(set(prompts)) == len(prompts), "有重复 prompt：分母不再是独立 query 数"


def test_prompt都非空且足够长():
    prompts, _ = _load_prompts()
    assert all(isinstance(p, str) and p.strip() for p in prompts)
    assert min(len(p) for p in prompts) >= 8, "过短的 prompt 容易和别的条目撞成同一问法"


def test_prompt覆盖四类考点():
    # 分母的可解释性：这 40 条是同一考点族的四类问法，外推范围写在判决书里。
    prompts, _ = _load_prompts()
    joined = "".join(prompts)
    for keyword in ("KV cache", "批处理", "TTFT", "显存"):
        assert keyword in joined, f"缺少考点关键词：{keyword}"
