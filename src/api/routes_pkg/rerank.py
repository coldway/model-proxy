# Created by model-proxy on 2026/07/31
# Copyright © 2026

"""重排序路由 /v1/rerank — 兼容 Cohere/Jina Rerank API"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.routes_pkg.deps import _deps, resolve_provider_for_model
from src.models.schemas import ProxyInfo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rerank"])


class RerankRequest(BaseModel):
    model: str
    query: str
    documents: list[str]
    top_n: int | None = Field(default=None, description="返回前 N 个结果")
    return_documents: bool = Field(default=True, description="是否在结果中返回原始文档内容")


@router.post("/v1/rerank", summary="文档重排序", description="兼容 Cohere/Jina Rerank API 的重排序接口。根据 query 对 documents 重新排序。")
async def create_rerank(request: RerankRequest):
    """重排序接口，透传到上游 Provider"""
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()

    logger.info("[RERANK] trace=%s model=%s query=%s docs=%d",
                trace_id, request.model, request.query[:60], len(request.documents))

    prov_id, provider = resolve_provider_for_model(request.model)

    try:
        kwargs = {}
        if request.top_n is not None:
            kwargs["top_n"] = request.top_n
        if request.return_documents is not None:
            kwargs["return_documents"] = request.return_documents
        result = await provider.rerank(request.model, request.query, request.documents, **kwargs)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"厂商 {prov_id} 不支持 Rerank") from exc
    except HTTPException:
        raise
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[RERANK] trace=%s Rerank 失败: %s (%.0fms)", trace_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"上游 Rerank 失败: {e}") from e

    latency = (time.time() - start_time) * 1000
    info = ProxyInfo(provider=prov_id, trace_id=trace_id, latency_ms=round(latency, 1))

    if _deps.history:
        _deps.history.record(provider=prov_id, model=request.model, success=True, latency_ms=latency)

    results_count = len(result.get("results", []))
    logger.info("[RERANK] trace=%s 完成 | provider=%s model=%s 耗时=%.0fms results=%d",
                trace_id, prov_id, request.model, latency, results_count)

    result["proxy_info"] = info.model_dump(exclude_none=True)
    return JSONResponse(content=result)
