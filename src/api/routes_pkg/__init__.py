# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""模块化路由包 — 聚合所有子路由并保持向后兼容的导出接口

外部代码继续使用:
    from src.api.routes import init_routes, router, _deps
"""

from fastapi import APIRouter

from src.api.routes_pkg import (
    anthropic_messages,
    audio,
    chat_completions,
    chat_sessions,
    config,
    cursor,
    health,
    history,
    images,
    logs,
    models,
    rapid_mlx,
    testing,
    videos,
)
from src.api.routes_pkg.deps import _deps, init_routes, map_dispatch_error, record_failure  # noqa: F401

router = APIRouter()

router.include_router(health.router)
router.include_router(chat_completions.router)
router.include_router(models.router)
router.include_router(config.router)
router.include_router(history.router)
router.include_router(chat_sessions.router)
router.include_router(logs.router)
router.include_router(testing.router)
router.include_router(cursor.router)
router.include_router(anthropic_messages.router)
router.include_router(images.router)
router.include_router(videos.router)
router.include_router(audio.router)
router.include_router(rapid_mlx.router)

# 向后兼容导出（internal_tools.py 等直接 import 这些）
StructuredTestRequest = testing.StructuredTestRequest
test_structured_output = testing.test_structured_output

# 保持旧名称兼容
_record_failure = record_failure
_map_dispatch_error = map_dispatch_error
