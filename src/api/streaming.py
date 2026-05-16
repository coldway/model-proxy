# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

from starlette.responses import StreamingResponse

from src.models.schemas import ChatCompletionRequest, ChatMessage, ProxyInfo


def create_stream_response(
    model: str,
    content_iterator: AsyncIterator[str],
    proxy_info: ProxyInfo | None = None,
) -> StreamingResponse:
    """创建 SSE 流式响应"""
    return StreamingResponse(
        _stream_generator(model, content_iterator, proxy_info),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_generator(
    model: str,
    content_iterator: AsyncIterator[str],
    proxy_info: ProxyInfo | None = None,
) -> AsyncIterator[str]:
    """生成 SSE 格式的流式数据"""
    import logging
    logger = logging.getLogger(__name__)
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    has_error = False
    try:
        async for chunk in content_iterator:
            data = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    except Exception as e:
        has_error = True
        logger.error(f"流式生成异常: {e}")
        error_data = {
            "id": chat_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            }],
            "error": {"message": "流式响应中断"},
        }
        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    if not has_error:
        end_data = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        if proxy_info:
            end_data["proxy_info"] = proxy_info.model_dump()
        yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
