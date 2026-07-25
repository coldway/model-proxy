# Created by model-proxy on 2026/07/25
# Copyright © 2026

"""音频生成路由 /v1/audio/speech — 透传到本地 Rapid-MLX Audio 服务

通过 RapidMLXManager 动态获取音频模型实例的 base_url，
不再硬编码端口号。支持 kokoro 等 TTS 模型在任意端口运行。
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audio"])

_TIMEOUT = httpx.Timeout(timeout=120.0, connect=10.0)
_FALLBACK_BASE = "http://127.0.0.1:8002"

_manager = None


def set_audio_manager(manager) -> None:
    """由 main.py 注入 RapidMLXManager 实例"""
    global _manager
    _manager = manager


def _resolve_audio_base() -> str:
    """从 manager 获取音频模型（kokoro）的 base_url，未找到时回退默认值"""
    if _manager:
        url_map = _manager.get_model_url_map()
        for model_name, url in url_map.items():
            if "kokoro" in model_name.lower():
                return url
    return _FALLBACK_BASE


@router.post(
    "/v1/audio/speech",
    summary="文本转语音",
    description="兼容 OpenAI TTS API 的语音合成接口。转发到本地 Rapid-MLX Audio 服务。",
)
async def audio_speech(request: Request):
    """TTS 接口，透传到 Rapid-MLX Audio 后端"""
    trace_id = uuid.uuid4().hex[:12]
    start_time = time.time()

    body = await request.body()
    base_url = _resolve_audio_base()
    logger.info("[TTS] trace=%s 收到请求 body_len=%d target=%s", trace_id, len(body), base_url)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base_url}/v1/audio/speech",
                content=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.ConnectError as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[TTS] trace=%s 无法连接 Rapid-MLX Audio (%s): %s (%.0fms)", trace_id, base_url, e, latency)
        raise HTTPException(
            status_code=503,
            detail=f"TTS 后端服务未启动。请通过 /api/rapid-mlx/instances/start 启动音频模型。",
        ) from e
    except httpx.TimeoutException as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[TTS] trace=%s TTS 请求超时: %s (%.0fms)", trace_id, e, latency)
        raise HTTPException(status_code=504, detail="TTS 请求超时") from e
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        logger.error("[TTS] trace=%s TTS 请求失败: %s (%.0fms)", trace_id, e, latency, exc_info=True)
        raise HTTPException(status_code=502, detail=f"TTS 上游错误: {e}") from e

    latency = (time.time() - start_time) * 1000

    if resp.status_code != 200:
        logger.warning("[TTS] trace=%s 上游返回 %d: %s (%.0fms)", trace_id, resp.status_code, resp.text[:200], latency)
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])

    logger.info("[TTS] trace=%s 完成 | content_type=%s 耗时=%.0fms bytes=%d",
                trace_id, resp.headers.get("content-type", "?"), latency, len(resp.content))

    if _manager:
        _manager.record_request("kokoro", latency)

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "audio/wav"),
        headers={"X-Trace-Id": trace_id, "X-Latency-Ms": str(round(latency, 1))},
    )


@router.get(
    "/v1/audio/voices",
    summary="列出可用 TTS 语音",
    description="返回 Rapid-MLX Audio 后端支持的所有语音列表。",
)
async def list_voices():
    """获取 TTS 可用的 voice 列表"""
    base_url = _resolve_audio_base()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(f"{base_url}/v1/audio/voices")
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=503,
            detail=f"TTS 后端服务未启动。请通过 /api/rapid-mlx/instances/start 启动音频模型。",
        ) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"查询 voices 失败: {e}") from e

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])

    return resp.json()
