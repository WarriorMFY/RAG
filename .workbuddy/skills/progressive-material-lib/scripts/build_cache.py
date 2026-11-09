"""建库辅助：只做目录落盘，不做分类/命中判断。

被 `<站点>/tools/fetch.py` 引用，调用约定：ensure_tree(out, site) -> 站点目录路径。
"""
from __future__ import annotations

from pathlib import Path


def ensure_tree(out: str, site: str) -> str:
    """确保 `<out>/<site>/` 存在并返回其路径。"""
    root = Path(out) / site
    root.mkdir(parents=True, exist_ok=True)
    return str(root)
