# Created by model-proxy on 2026/06/10
# Copyright © 2026

"""视频生成路由 /v1/videos — 异步任务模式，客户端自行轮询"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src.api.routes_pkg.deps import _deps, resolve_provider_by_id, resolve_provider_for_model
from src.models.schemas import ProxyInfo, VideoGenerationRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["videos"])


@router.post("/v1/videos", summary="创建视频生成任务", description="提交异步视频生成任务，返回 task_id 用于轮询进度。支持 kling、runway、pika 等厂商。")
async def create_video(request: VideoGenerationRequest):
    """创建视频生成任务（异步），返回 task_id 供客户端轮询"""
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()

    logger.info("[VIDEO] trace=%s model=%s prompt=%s", trace_id, request.model, request.prompt[:100])

    prov_id, provider = resolve_provider_for_model(request.model)

    try:
        payload = request.model_dump(exclude_none=True)
        model = payload.pop("model")
        result = await provider.create_video(model, **payload)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"厂商 {prov_id} 不支持视频生成") from exc
    except HTTPException:
        raise
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[VIDEO] trace=%s 视频创建失败: %s (%.0fms)", trace_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"上游视频创建失败: {e}") from e

    latency = (time.time() - start_time) * 1000
    info = ProxyInfo(provider=prov_id, trace_id=trace_id, latency_ms=round(latency, 1))

    if _deps.history:
        _deps.history.record(provider=prov_id, model=request.model, success=True, latency_ms=latency)

    task_id = result.get("id", "unknown")
    status = result.get("status", "unknown")
    logger.info("[VIDEO] trace=%s 任务已创建 | provider=%s model=%s task=%s status=%s 耗时=%.0fms",
                trace_id, prov_id, request.model, task_id, status, latency)

    result["proxy_info"] = info.model_dump(exclude_none=True)
    # 注入 provider_id 供后续 poll 时使用
    result["provider_id"] = prov_id
    return JSONResponse(content=result)


@router.get("/v1/videos/{task_id}", summary="查询视频任务状态", description="根据 task_id 轮询视频生成任务进度，完成后返回视频 URL。")
async def poll_video(task_id: str, provider_id: str = Query(description="厂商 ID，从创建响应的 provider_id 字段获取"), video_id: str | None = Query(default=None, description="视频 ID（Agnes 推荐使用此字段轮询）")):
    """查询视频生成任务状态"""
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()

    provider = resolve_provider_by_id(provider_id)

    try:
        result = await provider.poll_video(task_id, video_id=video_id)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"厂商 {provider_id} 不支持视频状态查询") from exc
    except HTTPException:
        raise
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[VIDEO] trace=%s poll 失败: task=%s %s (%.0fms)", trace_id, task_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"上游视频状态查询失败: {e}") from e

    latency = (time.time() - start_time) * 1000
    status = result.get("status", "unknown")
    logger.info("[VIDEO] trace=%s poll | provider=%s task=%s status=%s 耗时=%.0fms",
                trace_id, provider_id, task_id, status, latency)

    info = ProxyInfo(provider=provider_id, trace_id=trace_id, latency_ms=round(latency, 1))
    result["proxy_info"] = info.model_dump(exclude_none=True)
    return JSONResponse(content=result)
