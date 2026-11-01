"""包元数据：版本号只能有一个真相源，别让 pyproject 与 __init__ 悄悄漂开。"""
from pathlib import Path

import tomllib

import ttftkit

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert ttftkit.__version__ == data["project"]["version"]
