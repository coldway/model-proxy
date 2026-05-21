# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""向后兼容的 routes 模块

将所有导出转发到 src.api.routes_pkg 包，
使 `from src.api.routes import init_routes, router, _deps` 等原有导入继续工作。
"""

from src.api.routes_pkg import (  # noqa: F401
    router,
    init_routes,
    _deps,
    _record_failure,
    _map_dispatch_error,
    StructuredTestRequest,
    test_structured_output,
)
