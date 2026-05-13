# Created by model-proxy on 2026/05/11
# Copyright © 2026

"""UI 面板 - HTML 从独立静态文件加载"""

from pathlib import Path

_UI_FILE = Path(__file__).parent / "static" / "ui.html"

UI_HTML: str = _UI_FILE.read_text(encoding="utf-8")
