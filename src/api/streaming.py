# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

import httpx
from starlette.responses import StreamingResponse

from src.models.schemas import ChatCompletionRequest, ChatMessage


def create_stream_response(model: str, content_iterator: AsyncIterator[str]) -> StreamingResponse:
    """创建 SSE 流式响应"""
    return StreamingResponse(
        _stream_generator(model, content_iterator),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_generator(model: str, content_iterator: AsyncIterator[str]) -> AsyncIterator[str]:
    """生成 SSE 格式的流式数据"""
    import logging
    logger = logging.getLogger(__name__)
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    has_error = False
    chunk_count = 0
    total_chars = 0
    stream_start = time.monotonic()
    first_chunk_ms = None

    try:
        async for chunk in content_iterator:
            chunk_count += 1
            total_chars += len(chunk)
            if first_chunk_ms is None:
                first_chunk_ms = (time.monotonic() - stream_start) * 1000
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
        error_type = "stream_error"
        error_msg = "流式响应中断"
        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            if status == 429:
                error_type = "rate_limit"
                error_msg = "厂商限流 (429)，请稍后重试"
            elif status >= 500:
                error_type = "server_error"
                error_msg = f"厂商服务异常 ({status})"
            else:
                error_type = "client_error"
                error_msg = f"请求错误 ({status})"
        logger.error("流式生成异常 (已发送%d chunks, type=%s): %s", chunk_count, error_type, e)
        error_data = {
            "id": chat_id, "object": "chat.completion.chunk", "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            }],
            "error": {"message": error_msg, "type": error_type},
        }
        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    elapsed_ms = (time.monotonic() - stream_start) * 1000
    if not has_error:
        logger.info(
            "[SSE] id=%s model=%s 流式完成 | chunks=%d 总字符=%d TTFC=%.0fms 总耗时=%.0fms",
            chat_id, model, chunk_count, total_chars,
            first_chunk_ms or 0, elapsed_ms,
        )
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
        yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"
    else:
        logger.warning(
            "[SSE] id=%s model=%s 流式中断 | chunks=%d 总字符=%d 总耗时=%.0fms",
            chat_id, model, chunk_count, total_chars, elapsed_ms,
        )
    yield "data: [DONE]\n\n"
