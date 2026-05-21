# Created by model-proxy on 2026/05/14
# Copyright © 2026

"""OpenAI 兼容格式 Provider 公共工具函数"""

from __future__ import annotations

import httpx

from src.models.schemas import ChatMessage, FunctionCall, ToolCall

_DEFAULT_LIMITS = httpx.Limits(
    max_connections=200,
    max_keepalive_connections=50,
    keepalive_expiry=30,
)


def create_http_client(timeout: float = 120.0, **kwargs) -> httpx.AsyncClient:
    """创建标准化的 httpx 客户端（统一连接池配置）"""
    return httpx.AsyncClient(
        timeout=timeout,
        limits=kwargs.pop("limits", _DEFAULT_LIMITS),
        http2=kwargs.pop("http2", False),
        **kwargs,
    )


def msg_to_dict(m: ChatMessage) -> dict:
    """将 ChatMessage 转为 OpenAI API 格式的 dict（含 tool_calls/tool_call_id）"""
    d: dict = {"role": m.role}
    if m.content is not None:
        d["content"] = m.content
    if m.tool_calls:
        d["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
    if m.tool_call_id:
        d["tool_call_id"] = m.tool_call_id
    if m.name:
        d["name"] = m.name
    return d


def parse_tool_calls(raw_tcs: list[dict] | None) -> list[ToolCall] | None:
    """从 OpenAI 兼容 API 响应解析 tool_calls，跳过格式异常的条目"""
    if not raw_tcs:
        return None
    result = []
    for tc in raw_tcs:
        try:
            func = tc.get("function") or {}
            result.append(ToolCall(
                id=tc.get("id", ""),
                type=tc.get("type", "function"),
                function=FunctionCall(
                    name=func.get("name", "unknown"),
                    arguments=func.get("arguments", "{}"),
                ),
            ))
        except Exception:
            continue
    return result or None
