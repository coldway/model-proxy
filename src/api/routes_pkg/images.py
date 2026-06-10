# Created by model-proxy on 2026/06/10
# Copyright © 2026

"""图像生成路由 /v1/images/generations — 轻量透传到上游 Provider"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.api.routes_pkg.deps import _deps, resolve_provider_for_model
from src.models.schemas import ImageGenerationRequest, ProxyInfo

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/images/generations")
async def image_generations(request: ImageGenerationRequest):
    """图像生成接口，透传到上游 Provider"""
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()

    logger.info("[IMAGE] trace=%s model=%s prompt=%s", trace_id, request.model, request.prompt[:100])

    prov_id, provider = resolve_provider_for_model(request.model)

    try:
        payload = request.model_dump(exclude_none=True)
        model = payload.pop("model")
        result = await provider.image_generation(model, **payload)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"厂商 {prov_id} 不支持图像生成") from exc
    except HTTPException:
        raise
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[IMAGE] trace=%s 图像生成失败: %s (%.0fms)", trace_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"上游图像生成失败: {e}") from e

    latency = (time.time() - start_time) * 1000
    info = ProxyInfo(provider=prov_id, trace_id=trace_id, latency_ms=round(latency, 1))

    if _deps.history:
        _deps.history.record(provider=prov_id, model=request.model, success=True, latency_ms=latency)

    data_count = len(result.get("data", []))
    logger.info("[IMAGE] trace=%s 完成 | provider=%s model=%s 耗时=%.0fms 图片数=%d",
                trace_id, prov_id, request.model, latency, data_count)

    result["proxy_info"] = info.model_dump(exclude_none=True)
    return JSONResponse(content=result)
