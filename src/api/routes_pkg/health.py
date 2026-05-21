# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""健康探针路由"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.api.routes_pkg.deps import _deps

router = APIRouter()


@router.get("/health")
async def health_probe():
    """进程存活探针（负载均衡 / k8s liveness）"""
    return {"status": "ok"}


@router.get("/ready")
async def ready_probe():
    """就绪探针：配置中至少有一个 provider 填写了 API Key"""
    if not _deps.config_manager:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "配置未初始化"})
    configured = any(
        bool(str(getattr(p, "api_key", "")).strip())
        for p in _deps.config_manager.config.providers.values()
    )
    if not configured:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "未配置任何厂商 API Key"},
        )
    return {"ready": True}
