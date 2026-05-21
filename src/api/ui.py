# Created by model-proxy on 2026/05/11
# Copyright © 2026

"""UI 面板 - HTML 从独立静态文件加载，开发模式下始终读取最新文件"""

from pathlib import Path

_UI_FILE = Path(__file__).parent / "static" / "ui.html"

_cache: str | None = None


def get_ui_html(*, use_cache: bool = False) -> str:
    """获取 UI HTML 内容。use_cache=True 时缓存至进程生命周期结束。"""
    global _cache
    if use_cache and _cache is not None:
        return _cache
    html = _UI_FILE.read_text(encoding="utf-8")
    if use_cache:
        _cache = html
    return html


UI_HTML: str = get_ui_html()
