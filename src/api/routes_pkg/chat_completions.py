# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""OpenAI 兼容 /v1/chat/completions 路由"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.api.streaming import create_stream_response
from src.models.schemas import ChatCompletionRequest, ProxyInfo, ImageGenerationRequest, VideoGenerationRequest
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    ModelNotFound,
    PayloadTooLarge,
    ProviderCallError,
    RateLimitExceeded,
)
from src.api.routes_pkg.deps import _deps, record_failure, map_dispatch_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _extract_json_from_response(content: str) -> str:
    """从可能包含思考过程的模型输出中提取 JSON。"""
    if not content:
        return content
    stripped = content.strip()
    if stripped.endswith("```"):
        stripped = stripped[:-3].strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    if "```" in stripped:
        lines = stripped.split("\n")
        in_block = False
        json_lines = []
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            elif line.strip() == "```" and in_block:
                break
            elif in_block:
                json_lines.append(line)
        if json_lines:
            candidate = "\n".join(json_lines).strip()
            try:
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass
    for start_char in ("{", "["):
        start = stripped.find(start_char)
        if start >= 0:
            try:
                obj = json.loads(stripped[start:])
                return json.dumps(obj, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
    return content


async def _handle_model_type_routing(request: ChatCompletionRequest):
    """根据 model_type 路由到 image/video 生成接口"""
    from src.api.routes_pkg.images import image_generations
    from src.api.routes_pkg.videos import create_video

    last_user_msg = ""
    for msg in reversed(request.messages):
        if msg.role == "user":
            last_user_msg = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    if not last_user_msg:
        raise HTTPException(status_code=400, detail="model_type 路由需要至少一条 user 消息作为 prompt")

    if request.model_type == "image":
        img_req = ImageGenerationRequest(model=request.model, prompt=last_user_msg)
        return await image_generations(img_req)
    elif request.model_type == "video":
        vid_req = VideoGenerationRequest(model=request.model, prompt=last_user_msg)
        return await create_video(vid_req)

    raise HTTPException(status_code=400, detail=f"不支持的 model_type: {request.model_type}")


# 已知的图像/视频模型名称模式（用于自动检测）
_IMAGE_MODEL_PATTERNS = ("image", "imagen", "dall-e", "flux", "stable-diffusion")
_VIDEO_MODEL_PATTERNS = ("video", "runway", "kling", "pika", "sora")


def _infer_model_type(model: str) -> str | None:
    """根据模型名称自动推断 model_type：image / video / None（文字）"""
    lower = model.lower()
    for pat in _IMAGE_MODEL_PATTERNS:
        if pat in lower:
            return "image"
    for pat in _VIDEO_MODEL_PATTERNS:
        if pat in lower:
            return "video"
    return None


@router.post("/v1/chat/completions", summary="聊天补全", description="兼容 OpenAI SDK 的聊天补全接口。支持 model=auto 自动调度、流式/非流式输出、Cursor Agent 扩展参数。\n\n**多模态路由（图像/视频生成）**：\n- 自动检测：模型名含 image/imagen/dall-e/flux → 走图像生成；含 video/runway/kling/pika/sora → 走视频生成\n- 显式覆盖：传入 model_type=image/video 强制路由；model_type=text 强制走文字\n- 优先级：model_type 显式指定 > 模型名自动检测 > 默认走文字补全")
async def chat_completions(request: ChatCompletionRequest):
    """聊天补全接口（支持流式和非流式）"""
    # 优先级：model_type 显式指定 > 模型名自动检测 > 默认走文字
    effective_type = request.model_type or _infer_model_type(request.model)
    if effective_type in ("image", "video"):
        request.model_type = effective_type
        return await _handle_model_type_routing(request)

    trace_id = uuid.uuid4().hex[:12]
    sid_tag = f" session={request.session_id}" if request.session_id else ""
    
    # 原封不动打印完整请求体
    raw_body = request.model_dump(exclude_none=True)
    logger.info(
        "[API] trace=%s%s /v1/chat/completions\n%s",
        trace_id, sid_tag, json.dumps(raw_body, ensure_ascii=False),
    )

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型，请检查配置")

    if request.stream:
        return await _handle_stream(request, enabled_models, trace_id)

    start_time = time.time()
    try:
        provider_name, model_name, result = await _deps.dispatcher.dispatch(request, enabled_models, trace_id=trace_id)
        latency = (time.time() - start_time) * 1000
        route_strategy = _deps.dispatcher.last_route_strategy or ""
        if _deps.history:
            _deps.history.record(
                provider=provider_name,
                model=model_name,
                success=True,
                latency_ms=latency,
                prompt_tokens=result.usage.prompt_tokens if result.usage else 0,
                completion_tokens=result.usage.completion_tokens if result.usage else 0,
                route_strategy=route_strategy,
            )
        if _deps.cost_tracker and result.usage:
            _deps.cost_tracker.record(
                model_name,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
            )
        bound = _deps.dispatcher.get_session_binding(request.session_id) if request.session_id else None
        info = ProxyInfo(
            provider=provider_name,
            trace_id=trace_id,
            latency_ms=round(latency, 1),
            route_strategy=route_strategy,
            session_id=request.session_id,
            bound_model=f"{bound[0]}:{bound[1]}" if bound else None,
        )
        if request.response_format and request.response_format.type in ("json_object", "json_schema"):
            msg = result.choices[0].message if result.choices else None
            if msg and msg.content:
                msg.content = _extract_json_from_response(msg.content)

        logger.info("[API] trace=%s%s 完成 | provider=%s model=%s 耗时=%.0fms", trace_id, sid_tag, provider_name, model_name, latency)
        # 响应内容日志
        resp_content = result.choices[0].message.content if result.choices and result.choices[0].message else ""
        tokens_info = f"input={result.usage.prompt_tokens} output={result.usage.completion_tokens}" if result.usage else "usage=N/A"
        logger.info("[API RESP] trace=%s %s\ncontent: %s", trace_id, tokens_info, resp_content)
        out = result.model_dump()
        out["proxy_info"] = info.model_dump(exclude_none=True)
        return JSONResponse(content=out)
    except PayloadTooLarge as e:
        record_failure(start_time, str(e))
        logger.warning("[API] trace=%s payload 过大: %s", trace_id, e)
        raise HTTPException(status_code=413, detail=str(e))
    except RateLimitExceeded as e:
        record_failure(start_time, str(e))
        logger.warning("[API] trace=%s 限流: %s", trace_id, e)
        raise HTTPException(status_code=429, detail=str(e))
    except ModelNotFound as e:
        record_failure(start_time, str(e))
        logger.warning("[API] trace=%s 模型未找到: %s", trace_id, e)
        raise HTTPException(status_code=404, detail=str(e))
    except AllModelsUnavailable as e:
        record_failure(start_time, str(e))
        logger.error("[API] trace=%s 所有模型不可用: %s", trace_id, e)
        raise HTTPException(status_code=503, detail="所有模型均不可用，请稍后重试")
    except ProviderCallError as e:
        record_failure(start_time, str(e))
        logger.warning("[API] trace=%s 厂商调用失败（可恢复）: %s", trace_id, e)
        raise HTTPException(status_code=503, detail="模型服务暂时不可用，请稍后重试")
    except Exception as e:
        record_failure(start_time, str(e))
        logger.error("[API] trace=%s 推理请求异常: %s", trace_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")


async def _handle_stream(request: ChatCompletionRequest, enabled_models, trace_id: str = ""):
    """处理流式请求，返回 SSE StreamingResponse"""
    sid_tag = f" session={request.session_id}" if request.session_id else ""
    start_time = time.time()
    try:
        provider_name, model_name, content_iter = await _deps.dispatcher.dispatch_stream(request, enabled_models, trace_id=trace_id)
    except Exception as e:
        record_failure(start_time, str(e))
        raise map_dispatch_error(e, trace_id)

    latency = (time.time() - start_time) * 1000
    route_strategy = _deps.dispatcher.last_route_strategy or ""
    logger.info("[API] trace=%s%s 流式连接建立 | provider=%s model=%s 耗时=%.0fms", trace_id, sid_tag, provider_name, model_name, latency)
    bound = _deps.dispatcher.get_session_binding(request.session_id) if request.session_id else None
    info = ProxyInfo(
        provider=provider_name,
        trace_id=trace_id,
        latency_ms=round(latency, 1),
        route_strategy=route_strategy,
        session_id=request.session_id,
        bound_model=f"{bound[0]}:{bound[1]}" if bound else None,
    )

    def _on_stream_complete(usage: dict | None) -> None:
        total_latency = (time.time() - start_time) * 1000
        prompt_tokens = usage.get("prompt_tokens", 0) if usage else 0
        completion_tokens = usage.get("completion_tokens", 0) if usage else 0
        if _deps.history:
            _deps.history.record(
                provider=provider_name,
                model=model_name,
                success=True,
                latency_ms=total_latency,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                route_strategy=route_strategy,
            )
        if _deps.cost_tracker and usage:
            _deps.cost_tracker.record(model_name, prompt_tokens, completion_tokens)
        logger.info(
            "[API] trace=%s%s 流式完成 | provider=%s model=%s 总耗时=%.0fms tokens=input:%d+output:%d",
            trace_id, sid_tag, provider_name, model_name,
            total_latency, prompt_tokens, completion_tokens,
        )

    return create_stream_response(model_name, content_iter, proxy_info=info, on_complete=_on_stream_complete)
