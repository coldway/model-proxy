# Created by model-proxy on 2026/05/27
# Copyright © 2026

"""Anthropic Messages API 兼容层

提供 `/v1/messages` 端点，兼容 Anthropic SDK。
将 Anthropic Messages API 格式转换为 OpenAI 格式，调用现有的 dispatcher，
然后将响应转换回 Anthropic 格式。

Anthropic Messages API 文档：
https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import time
import uuid
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import json
import logging

from src.api.routes_pkg.deps import _deps
from src.models.schemas import ChatCompletionRequest, ChatMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/messages", tags=["anthropic"])


# ===========================
# Anthropic Messages API 模型
# ===========================

class AnthropicContentBlock(BaseModel):
    """Anthropic 内容块（文本、工具使用或工具结果）"""
    type: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[dict[str, Any]] = None
    tool_use_id: Optional[str] = None
    content: Optional[str | list[dict[str, Any]]] = None

    model_config = {"extra": "allow"}


class AnthropicMessage(BaseModel):
    """Anthropic 用户/助手消息"""
    role: str
    content: str | list[dict[str, Any]] | list[AnthropicContentBlock]

    model_config = {"extra": "allow"}


class AnthropicMessagesRequest(BaseModel):
    """Anthropic Messages API 请求"""
    model: str
    messages: list[AnthropicMessage]
    max_tokens: int = Field(4096, ge=1)
    metadata: Optional[dict[str, Any]] = None
    stop_sequences: Optional[list[str]] = None
    stream: bool = False
    system: Optional[str | list[dict[str, Any]]] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    top_p: Optional[float] = Field(None, ge=0, le=1)
    top_k: Optional[int] = Field(None, ge=0)
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[dict[str, Any]] = None

    model_config = {"extra": "allow"}


class AnthropicUsage(BaseModel):
    """Anthropic 使用统计"""
    input_tokens: int
    output_tokens: int


class AnthropicResponse(BaseModel):
    """Anthropic Messages API 响应"""
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[AnthropicContentBlock]
    model: str
    stop_reason: Optional[Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]] = None
    stop_sequence: Optional[str] = None
    usage: AnthropicUsage

    model_config = {"json_schema_extra": {"exclude_none": True}}


class AnthropicStreamEvent(BaseModel):
    """Anthropic 流式事件"""
    type: str
    # 各种事件字段根据 type 动态填充


# ===========================
# 格式转换函数
# ===========================

def _anthropic_to_openai_messages(messages: list[AnthropicMessage]) -> list[ChatMessage]:
    """将 Anthropic Messages 转换为 OpenAI ChatMessage

    转换规则：
    - text block → 合并为 content 字符串
    - tool_use block (assistant) → OpenAI tool_calls
    - tool_result block (user) → OpenAI role=tool 消息
    """
    from src.models.schemas import ToolCall, FunctionCall

    result = []
    for msg in messages:
        if isinstance(msg.content, str):
            result.append(ChatMessage(role=msg.role, content=msg.content))
            continue

        # content 是 list（可能是 ContentBlock 对象或 dict）
        blocks = msg.content
        text_parts = []
        tool_calls_list = []
        tool_results = []

        for block in blocks:
            b = block if isinstance(block, dict) else block.model_dump(exclude_none=True)
            block_type = b.get("type", "")

            if block_type == "text":
                text_parts.append(b.get("text", ""))
            elif block_type == "tool_use":
                tool_calls_list.append(ToolCall(
                    id=b.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                    type="function",
                    function=FunctionCall(
                        name=b.get("name", ""),
                        arguments=json.dumps(b.get("input", {}), ensure_ascii=False),
                    ),
                ))
            elif block_type == "tool_result":
                # tool_result 在 Anthropic 中嵌在 user 消息里
                tool_content = b.get("content", "")
                if isinstance(tool_content, list):
                    tool_content = "\n".join(
                        p.get("text", "") for p in tool_content if p.get("type") == "text"
                    )
                tool_results.append({
                    "tool_call_id": b.get("tool_use_id", ""),
                    "content": str(tool_content),
                })
            elif block_type == "thinking":
                pass

        if msg.role == "assistant":
            content = "\n".join(text_parts) if text_parts else None
            if tool_calls_list:
                result.append(ChatMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls_list,
                ))
            elif content:
                result.append(ChatMessage(role="assistant", content=content))
        elif msg.role == "user":
            if tool_results:
                # 先输出可能的文本部分
                if text_parts:
                    result.append(ChatMessage(role="user", content="\n".join(text_parts)))
                # 每个 tool_result 转为独立的 role=tool 消息
                for tr in tool_results:
                    result.append(ChatMessage(
                        role="tool",
                        content=tr["content"],
                        tool_call_id=tr["tool_call_id"],
                    ))
            else:
                result.append(ChatMessage(role="user", content="\n".join(text_parts) if text_parts else ""))

    return result


def _openai_to_anthropic_content(text: str) -> list[AnthropicContentBlock]:
    """将 OpenAI 文本响应转换为 Anthropic ContentBlock 列表"""
    return [AnthropicContentBlock(type="text", text=text)]


def _convert_anthropic_tools_to_openai(tools: list[dict[str, Any]] | None) -> list | None:
    """将 Anthropic 格式的 tools 转换为 OpenAI 格式的 ToolDefinition

    Anthropic: {"name": "X", "description": "...", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "X", "description": "...", "parameters": {...}}}
    """
    if not tools:
        return None

    from src.models.schemas import ToolDefinition, ToolFunction

    result = []
    for tool in tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        parameters = tool.get("input_schema", {})
        result.append(ToolDefinition(
            type="function",
            function=ToolFunction(
                name=name,
                description=description,
                parameters=parameters,
            ),
        ))
    return result


async def _stream_anthropic_response(
    original_stream: AsyncIterator[str],
    model: str,
    message_id: str,
    created: int,
    trace_id: str = "",
) -> AsyncIterator[str]:
    """将 OpenAI SSE 流转换为 Anthropic SSE 流

    Anthropic 流式事件序列：
    1. message_start
    2. content_block_start (text / tool_use)
    3. content_block_delta (text_delta / input_json_delta) (多次)
    4. content_block_stop
    5. message_delta (包含 stop_reason 和 usage)
    6. message_stop

    支持 tool_calls：当 OpenAI 流返回 tool_calls 时，转换为 Anthropic tool_use content blocks。
    """
    import json as _json

    # 1. message_start
    msg_start = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    }
    yield f"event: message_start\ndata: {_json.dumps(msg_start)}\n\n"

    input_tokens = 0
    output_tokens = 0
    finish_reason = None
    collected_text: list[str] = []
    actual_model = ""
    content_block_index = 0
    text_block_started = False
    thinking_block_started = False
    
    # tool_calls 累积器：{index: {"id": str, "name": str, "arguments": str}}
    tc_accumulator: dict[int, dict] = {}
    tc_block_started: dict[int, int] = {}  # {tc_index: content_block_index}

    def _start_text_block():
        nonlocal text_block_started, content_block_index
        if not text_block_started:
            # 先关闭 thinking block
            events = ""
            nonlocal thinking_block_started
            if thinking_block_started:
                cb_stop = {"type": "content_block_stop", "index": content_block_index}
                events += f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
                thinking_block_started = False
                content_block_index += 1
            text_block_started = True
            cb_start = {
                "type": "content_block_start",
                "index": content_block_index,
                "content_block": {"type": "text", "text": ""}
            }
            events += f"event: content_block_start\ndata: {_json.dumps(cb_start)}\n\n"
            return events
        return ""

    def _process_tool_call_chunk(tc_chunk: dict) -> str:
        """处理单个 tool_call 增量 chunk，返回 Anthropic SSE 事件"""
        nonlocal content_block_index
        idx = tc_chunk.get("index", 0)
        fn = tc_chunk.get("function", {})

        events = ""

        if idx not in tc_accumulator:
            tc_accumulator[idx] = {
                "id": tc_chunk.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": fn.get("name", ""),
                "arguments": "",
            }

        if fn.get("name"):
            tc_accumulator[idx]["name"] = fn["name"]
        if tc_chunk.get("id"):
            tc_accumulator[idx]["id"] = tc_chunk["id"]

        # 当 name 出现时启动新的 tool_use content block
        if idx not in tc_block_started and tc_accumulator[idx]["name"]:
            tc_block_started[idx] = content_block_index
            cb_start = {
                "type": "content_block_start",
                "index": content_block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tc_accumulator[idx]["id"],
                    "name": tc_accumulator[idx]["name"],
                    "input": {}
                }
            }
            events += f"event: content_block_start\ndata: {_json.dumps(cb_start)}\n\n"

        # arguments 增量
        args_delta = fn.get("arguments", "")
        if args_delta:
            tc_accumulator[idx]["arguments"] += args_delta
            block_idx = tc_block_started.get(idx, content_block_index)
            delta_evt = {
                "type": "content_block_delta",
                "index": block_idx,
                "delta": {"type": "input_json_delta", "partial_json": args_delta}
            }
            events += f"event: content_block_delta\ndata: {_json.dumps(delta_evt)}\n\n"

        return events

    async for chunk in original_stream:
        if isinstance(chunk, dict):
            content = chunk.get("content", "")
            if content:
                start_evt = _start_text_block()
                if start_evt:
                    yield start_evt
                collected_text.append(content)
                delta_evt = {
                    "type": "content_block_delta",
                    "index": content_block_index,
                    "delta": {"type": "text_delta", "text": content}
                }
                yield f"event: content_block_delta\ndata: {_json.dumps(delta_evt)}\n\n"
                output_tokens += len(content.split())

            reasoning = chunk.get("reasoning_content") or chunk.get("reasoning") or ""
            if reasoning:
                if not thinking_block_started:
                    thinking_block_started = True
                    cb_start = {
                        "type": "content_block_start",
                        "index": content_block_index,
                        "content_block": {"type": "thinking", "thinking": ""}
                    }
                    yield f"event: content_block_start\ndata: {_json.dumps(cb_start)}\n\n"
                delta_evt = {
                    "type": "content_block_delta",
                    "index": content_block_index,
                    "delta": {"type": "thinking_delta", "thinking": reasoning}
                }
                yield f"event: content_block_delta\ndata: {_json.dumps(delta_evt)}\n\n"

            # 处理 dict 格式的 tool_calls
            for tc in chunk.get("tool_calls", []):
                # 先关闭 thinking block（如果有）
                if thinking_block_started:
                    cb_stop = {"type": "content_block_stop", "index": content_block_index}
                    yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
                    thinking_block_started = False
                    content_block_index += 1
                # 先关闭 text block（如果有）
                if text_block_started:
                    cb_stop = {"type": "content_block_stop", "index": content_block_index}
                    yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
                    text_block_started = False
                    content_block_index += 1
                events = _process_tool_call_chunk(tc)
                if events:
                    yield events

            if chunk.get("finish_reason"):
                finish_reason = chunk["finish_reason"]
        elif isinstance(chunk, str):
            if not chunk.strip() or chunk.strip() == "data: [DONE]":
                continue
            if chunk.startswith("data: "):
                try:
                    data = _json.loads(chunk[6:])
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            start_evt = _start_text_block()
                            if start_evt:
                                yield start_evt
                            collected_text.append(content)
                            delta_evt = {
                                "type": "content_block_delta",
                                "index": content_block_index,
                                "delta": {"type": "text_delta", "text": content}
                            }
                            yield f"event: content_block_delta\ndata: {_json.dumps(delta_evt)}\n\n"
                            output_tokens += len(content.split())

                        # 处理 SSE 格式的 tool_calls
                        for tc in delta.get("tool_calls", []):
                            if thinking_block_started:
                                cb_stop = {"type": "content_block_stop", "index": content_block_index}
                                yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
                                thinking_block_started = False
                                content_block_index += 1
                            if text_block_started:
                                cb_stop = {"type": "content_block_stop", "index": content_block_index}
                                yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
                                text_block_started = False
                                content_block_index += 1
                            events = _process_tool_call_chunk(tc)
                            if events:
                                yield events

                        if choices[0].get("finish_reason"):
                            finish_reason = choices[0]["finish_reason"]

                    if "usage" in data:
                        input_tokens = data["usage"].get("prompt_tokens", 0)
                        output_tokens = data["usage"].get("completion_tokens", 0)

                    if not actual_model and data.get("model"):
                        actual_model = data["model"]
                except Exception:
                    pass

    # 关闭最后一个 content block（thinking / text / tool_use）
    if thinking_block_started:
        cb_stop = {"type": "content_block_stop", "index": content_block_index}
        yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
        content_block_index += 1

    if text_block_started:
        cb_stop = {"type": "content_block_stop", "index": content_block_index}
        yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"
        content_block_index += 1
    
    # 关闭所有 tool_use blocks
    for idx, block_idx in sorted(tc_block_started.items()):
        cb_stop = {"type": "content_block_stop", "index": block_idx}
        yield f"event: content_block_stop\ndata: {_json.dumps(cb_stop)}\n\n"

    # message_delta
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        None: "end_turn"
    }
    if tc_accumulator and finish_reason not in ("tool_calls",):
        anthropic_stop = "tool_use"
    else:
        anthropic_stop = stop_reason_map.get(finish_reason, "end_turn")

    msg_delta = {
        "type": "message_delta",
        "delta": {"stop_reason": anthropic_stop, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens}
    }
    yield f"event: message_delta\ndata: {_json.dumps(msg_delta)}\n\n"

    # message_stop
    yield f"event: message_stop\ndata: {{\"type\": \"message_stop\"}}\n\n"

    # 流结束后打印完整响应日志
    full_response = "".join(collected_text)
    tc_summary = ""
    if tc_accumulator:
        tc_names = [tc["name"] for tc in tc_accumulator.values()]
        tc_summary = f" tool_calls=[{','.join(tc_names)}]"
    resp_preview = full_response
    logger.info(
        f"[Anthropic STREAM RESP] trace={trace_id} model={actual_model or model} "
        f"total_chars={len(full_response)} input_tokens={input_tokens} output_tokens={output_tokens} "
        f"stop={anthropic_stop}{tc_summary}\n{resp_preview}"
    )


# ===========================
# API 路由
# ===========================

@router.post("", response_model=AnthropicResponse, response_model_exclude_none=True)
async def create_message(request: AnthropicMessagesRequest, req: Request):
    """Anthropic Messages API 入口
    
    兼容 Anthropic SDK 的 `/v1/messages` 端点。
    将请求转换为 OpenAI 格式，调用 dispatcher，转换响应。
    """
    trace_id = uuid.uuid4().hex[:12]
    raw_body = request.model_dump(exclude_none=True)
    
    # 原封不动打印完整请求体
    logger.info(f"[Anthropic REQ] trace={trace_id}\n{json.dumps(raw_body, ensure_ascii=False)}")
    
    # 1. 转换请求格式
    openai_messages = _anthropic_to_openai_messages(request.messages)
    
    # 如果有 system prompt，插入为第一条消息
    if request.system:
        if isinstance(request.system, str):
            system_content = request.system
        else:
            # list[dict] 格式：合并所有 text 段
            system_parts = []
            for block in request.system:
                if isinstance(block, dict) and block.get("text"):
                    system_parts.append(block["text"])
            system_content = "\n\n".join(system_parts)
        if system_content:
            openai_messages.insert(0, ChatMessage(role="system", content=system_content))
    
    # 从 metadata 中提取 Cursor 特定参数
    metadata = request.metadata or {}
    mode = metadata.get("mode")
    force = metadata.get("force", False)
    sandbox = metadata.get("sandbox")
    workspace_path = metadata.get("workspace_path")
    cursor_session_id = metadata.get("cursor_session_id")
    cursor_continue = metadata.get("cursor_continue", False)
    worktree_name = metadata.get("worktree_name")
    worktree_base = metadata.get("worktree_base")
    skip_worktree_setup = metadata.get("skip_worktree_setup", False)
    approve_mcps = metadata.get("approve_mcps", False)
    
    # 构建 OpenAI 格式请求
    openai_tools = _convert_anthropic_tools_to_openai(request.tools)
    openai_request = ChatCompletionRequest(
        model=request.model,
        messages=openai_messages,
        max_tokens=request.max_tokens,
        temperature=request.temperature or 1.0,
        top_p=request.top_p,
        stream=request.stream,
        stop=request.stop_sequences,
        tools=openai_tools,
        # Cursor 特定参数
        mode=mode,
        force=force,
        sandbox=sandbox,
        workspace_path=workspace_path,
        cursor_session_id=cursor_session_id,
        cursor_continue=cursor_continue,
        worktree_name=worktree_name,
        worktree_base=worktree_base,
        skip_worktree_setup=skip_worktree_setup,
        approve_mcps=approve_mcps
    )
    
    # 2. 获取可用模型
    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型，请检查配置")
    
    # 3. 调用 dispatcher
    if not _deps.dispatcher:
        raise HTTPException(status_code=500, detail="Dispatcher not initialized")
    
    # model=auto 时强制使用流式（auto 路由主要针对流式场景优化，避免 Claude Code fallback 重试）
    force_stream = (request.model == "auto" and not request.stream)
    if force_stream:
        logger.info(f"[Anthropic] trace={trace_id} model=auto + stream=false → 强制走流式路径收集完整响应")
        openai_request.stream = True

    try:
        if request.stream or force_stream:
            if request.stream and not force_stream:
                # 正常流式响应：直接 SSE 返回
                message_id = f"msg_{uuid.uuid4().hex[:24]}"
                created = int(time.time())
                
                async def generate_sse():
                    provider_name, model_name, stream = await _deps.dispatcher.dispatch_stream(
                        request=openai_request,
                        enabled_models=enabled_models,
                        trace_id=trace_id
                    )
                    async for chunk in _stream_anthropic_response(
                        stream, request.model, message_id, created, trace_id=trace_id
                    ):
                        yield chunk
                
                return StreamingResponse(
                    generate_sse(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no"
                    }
                )
            else:
                # force_stream: 内部走流式，收集完整结果后组装为非流式响应
                provider_name, model_name, stream = await _deps.dispatcher.dispatch_stream(
                    request=openai_request,
                    enabled_models=enabled_models,
                    trace_id=trace_id
                )
                collected_text: list[str] = []
                collected_tool_calls: dict[int, dict] = {}
                input_tokens = 0
                output_tokens = 0
                actual_model = model_name
                finish_reason = None
                
                async for chunk in stream:
                    if isinstance(chunk, dict):
                        content = chunk.get("content") or ""
                        if content:
                            collected_text.append(content)
                        for tc in chunk.get("tool_calls", []):
                            idx = tc.get("index", 0)
                            fn = tc.get("function", {})
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                            if fn.get("name"):
                                collected_tool_calls[idx]["name"] = fn["name"]
                            if tc.get("id"):
                                collected_tool_calls[idx]["id"] = tc["id"]
                            collected_tool_calls[idx]["arguments"] += fn.get("arguments", "")
                    elif isinstance(chunk, str):
                        if chunk.strip() and chunk.strip() != "data: [DONE]" and chunk.startswith("data: "):
                            try:
                                data = json.loads(chunk[6:])
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    c = delta.get("content", "")
                                    if c:
                                        collected_text.append(c)
                                    for tc in delta.get("tool_calls", []):
                                        idx = tc.get("index", 0)
                                        fn = tc.get("function", {})
                                        if idx not in collected_tool_calls:
                                            collected_tool_calls[idx] = {"id": tc.get("id", ""), "name": "", "arguments": ""}
                                        if fn.get("name"):
                                            collected_tool_calls[idx]["name"] = fn["name"]
                                        if tc.get("id"):
                                            collected_tool_calls[idx]["id"] = tc["id"]
                                        collected_tool_calls[idx]["arguments"] += fn.get("arguments", "")
                                    if choices[0].get("finish_reason"):
                                        finish_reason = choices[0]["finish_reason"]
                                if "usage" in data:
                                    input_tokens = data["usage"].get("prompt_tokens", input_tokens)
                                    output_tokens = data["usage"].get("completion_tokens", output_tokens)
                                if data.get("model"):
                                    actual_model = data["model"]
                            except Exception:
                                pass
                
                # 组装 Anthropic content blocks
                content_blocks: list[AnthropicContentBlock] = []
                assistant_message = "".join(collected_text)
                if assistant_message:
                    content_blocks.append(AnthropicContentBlock(type="text", text=assistant_message))
                for idx in sorted(collected_tool_calls):
                    tc = collected_tool_calls[idx]
                    try:
                        input_obj = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        input_obj = {}
                    content_blocks.append(AnthropicContentBlock(
                        type="tool_use",
                        id=tc["id"] or f"toolu_{uuid.uuid4().hex[:24]}",
                        name=tc["name"],
                        input=input_obj,
                    ))
                
                if not content_blocks:
                    content_blocks = [AnthropicContentBlock(type="text", text="")]
                
                if not output_tokens:
                    output_tokens = len(assistant_message) // 2
                
                stop_reason_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", None: "end_turn"}
                # 如果有 tool_calls 但 finish_reason 未明确设置为 "tool_calls"，根据内容推断
                if collected_tool_calls and finish_reason not in ("tool_calls",):
                    anthropic_stop = "tool_use"
                else:
                    anthropic_stop = stop_reason_map.get(finish_reason, "end_turn")
                
                response = AnthropicResponse(
                    id=f"msg_{uuid.uuid4().hex[:24]}",
                    content=content_blocks,
                    model=actual_model,
                    stop_reason=anthropic_stop,
                    usage=AnthropicUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens
                    )
                )
                
                resp_content = assistant_message
                logger.info(
                    f"[Anthropic RESP (force_stream)] trace={trace_id} provider={provider_name} model={actual_model} "
                    f"input_tokens={input_tokens} output_tokens={output_tokens} stop={anthropic_stop}\n"
                    f"content: {resp_content}"
                )
                return response
        else:
            # 非 auto 模型的正常非流式响应
            provider_name, model_name, result = await _deps.dispatcher.dispatch(
                request=openai_request,
                enabled_models=enabled_models,
                trace_id=trace_id
            )
            
            assistant_message = result.choices[0].message.content or ""
            
            response = AnthropicResponse(
                id=f"msg_{uuid.uuid4().hex[:24]}",
                content=_openai_to_anthropic_content(assistant_message),
                model=model_name,
                stop_reason="end_turn",
                usage=AnthropicUsage(
                    input_tokens=result.usage.prompt_tokens,
                    output_tokens=result.usage.completion_tokens
                )
            )
            
            raw_resp = response.model_dump(exclude_none=True)
            resp_content = assistant_message
            logger.info(
                f"[Anthropic RESP] trace={trace_id} provider={provider_name} model={model_name} "
                f"input_tokens={result.usage.prompt_tokens} output_tokens={result.usage.completion_tokens}\n"
                f"content: {resp_content}"
            )
            logger.debug(f"[Anthropic RESP FULL] trace={trace_id}\n{json.dumps(raw_resp, ensure_ascii=False)}")
            
            return response
    
    except Exception as e:
        logger.error(f"[Anthropic ERR] trace={trace_id} {e}")
        raise HTTPException(status_code=500, detail=f"Dispatch error: {str(e)}")
