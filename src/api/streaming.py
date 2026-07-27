# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator, Callable

from starlette.responses import StreamingResponse

from src.models.schemas import ProxyInfo

logger = logging.getLogger(__name__)


def create_stream_response(
    model: str,
    content_iterator: AsyncIterator[dict | str],
    proxy_info: ProxyInfo | None = None,
    on_complete: Callable[[dict | None], None] | None = None,
) -> StreamingResponse:
    """创建 SSE 流式响应。on_complete 在流正常结束后被调用，参数为 usage dict 或 None。"""
    return StreamingResponse(
        _stream_generator(model, content_iterator, proxy_info, on_complete),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_generator(
    model: str,
    content_iterator: AsyncIterator[dict | str],
    proxy_info: ProxyInfo | None = None,
    on_complete: Callable[[dict | None], None] | None = None,
) -> AsyncIterator[str]:
    """生成 SSE 格式的流式数据。支持 dict delta（保留 tool_calls 等字段）和纯 str 兼容。"""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    trace_id = proxy_info.trace_id if proxy_info else "unknown"

    has_error = False
    collected_text: list[str] = []
    final_usage: dict | None = None
    usage_is_estimated = False
    try:
        async for chunk in content_iterator:
            if isinstance(chunk, dict) and "__usage__" in chunk:
                final_usage = chunk["__usage__"]
                usage_is_estimated = chunk.get("__estimated__", False)
                continue
            delta = chunk if isinstance(chunk, dict) else {"content": chunk}
            if isinstance(delta, dict) and "content" in delta and delta["content"]:
                collected_text.append(delta["content"])
            data = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    except Exception as e:
        has_error = True
        logger.error("流式生成异常: %s", e, exc_info=True)
        error_data = {
            "error": {
                "message": "流式响应中断，请稍后重试",
                "type": "server_error",
                "code": None,
            },
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            }],
        }
        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    if not has_error:
        end_data: dict = {
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
        if final_usage and not usage_is_estimated:
            end_data["usage"] = final_usage
        if proxy_info:
            end_data["proxy_info"] = proxy_info.model_dump()
        yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"

    full_response = "".join(collected_text)
    resp_preview = full_response[:2000] + ("..." if len(full_response) > 2000 else "")
    usage_log = ""
    if final_usage:
        usage_log = f" tokens=input:{final_usage.get('prompt_tokens',0)}+output:{final_usage.get('completion_tokens',0)}={final_usage.get('total_tokens',0)}"
    logger.info(
        "[STREAM RESP] trace=%s model=%s total_chars=%d%s\ncontent_preview: %s",
        trace_id, model, len(full_response), usage_log, resp_preview,
    )

    if on_complete and not has_error:
        try:
            on_complete(final_usage)
        except Exception:
            logger.warning("[STREAM] trace=%s on_complete 回调异常", trace_id, exc_info=True)

    yield "data: [DONE]\n\n"
