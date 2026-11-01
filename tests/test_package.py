"""包元数据：版本号只能有一个真相源，别让 pyproject 与 __init__ 悄悄漂开。

不用 `tomllib`：它 3.11 才有，而 CI 要在 3.10 上跑（v0.2.0 那次 3.10 就挂在
`ModuleNotFoundError: No module named 'tomllib'`）。这里只读 `[project]` 段的
`version =` 一行 —— 判据不变（仍然逐字比较两个版本号），只是不借 TOML 解析器。
"""
import re
from pathlib import Path

import ttftkit

ROOT = Path(__file__).resolve().parent.parent


def _project_version(text: str) -> str:
    """取 `[project]` 段里的 version；切段而不是全文搜，避免命中别的表。"""
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        if section != "project":
            continue
        match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml has no [project] version")


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert ttftkit.__version__ == _project_version(text)
