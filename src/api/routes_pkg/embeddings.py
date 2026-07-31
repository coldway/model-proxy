# Created by model-proxy on 2026/07/31
# Copyright © 2026

"""向量化路由 /v1/embeddings — 兼容 OpenAI Embeddings API"""

from __future__ import annotations

import base64
import logging
import struct
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.api.routes_pkg.deps import _deps, resolve_provider_for_model
from src.models.schemas import EmbeddingRequest, ProxyInfo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embeddings"])


def _floats_to_base64(floats: list[float]) -> str:
    """将 float 数组编码为 little-endian base64 字符串（OpenAI SDK 期望的格式）。"""
    return base64.b64encode(struct.pack(f"<{len(floats)}f", *floats)).decode("ascii")


@router.post("/v1/embeddings", summary="文本向量化", description="兼容 OpenAI Embeddings API 的向量化接口。支持 ollama、openrouter、rapid-mlx 等 embedding 模型。")
async def create_embeddings(request: EmbeddingRequest):
    """向量化接口，透传到上游 Provider。

    始终以 float 格式请求上游，避免上游不支持 base64 导致 400。
    若客户端请求 base64 格式，则在本地完成转换后返回。
    """
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()
    client_format = request.encoding_format or "float"

    input_preview = request.input[:80] if isinstance(request.input, str) else f"[{len(request.input)} texts]"
    logger.info("[EMBED] trace=%s model=%s input=%s format=%s", trace_id, request.model, input_preview, client_format)

    prov_id, provider = resolve_provider_for_model(request.model)

    try:
        kwargs = {}
        if request.dimensions:
            kwargs["dimensions"] = request.dimensions
        result = await provider.embeddings(request.model, request.input, **kwargs)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"厂商 {prov_id} 不支持 Embedding") from exc
    except HTTPException:
        raise
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[EMBED] trace=%s Embedding 失败: %s (%.0fms)", trace_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"上游 Embedding 失败: {e}") from e

    if client_format == "base64":
        for item in result.get("data", []):
            embedding = item.get("embedding")
            if isinstance(embedding, list):
                item["embedding"] = _floats_to_base64(embedding)
                item["encoding_format"] = "base64"

    latency = (time.time() - start_time) * 1000
    info = ProxyInfo(provider=prov_id, trace_id=trace_id, latency_ms=round(latency, 1))

    if _deps.history:
        _deps.history.record(provider=prov_id, model=request.model, success=True, latency_ms=latency)

    data_count = len(result.get("data", []))
    logger.info("[EMBED] trace=%s 完成 | provider=%s model=%s 耗时=%.0fms vectors=%d",
                trace_id, prov_id, request.model, latency, data_count)

    result["proxy_info"] = info.model_dump(exclude_none=True)
    return JSONResponse(content=result)
