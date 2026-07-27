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

# v1 路由：保持在根路径，符合 OpenAI/Anthropic 协议标准
v1_router = APIRouter()
v1_router.include_router(chat_completions.router)
v1_router.include_router(models.router)
v1_router.include_router(anthropic_messages.router)
v1_router.include_router(images.router)
v1_router.include_router(videos.router)
v1_router.include_router(audio.router)

# 管理路由：统一在 /mp 前缀下，简化反向代理配置
mp_router = APIRouter(prefix="/mp")
mp_router.include_router(health.router)
mp_router.include_router(config.router)
mp_router.include_router(history.router)
mp_router.include_router(chat_sessions.router)
mp_router.include_router(logs.router)
mp_router.include_router(testing.router)
mp_router.include_router(cursor.router)
mp_router.include_router(rapid_mlx.router)

# 兼容旧接口：保留 router 变量供 main.py 使用
router = APIRouter()
router.include_router(v1_router)
router.include_router(mp_router)

# 向后兼容导出（internal_tools.py 等直接 import 这些）
StructuredTestRequest = testing.StructuredTestRequest
test_structured_output = testing.test_structured_output

# 保持旧名称兼容
_record_failure = record_failure
_map_dispatch_error = map_dispatch_error
