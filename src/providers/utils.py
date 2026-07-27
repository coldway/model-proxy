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


_global_read_timeout: float = 600.0
_global_connect_timeout: float = 15.0


def configure_timeouts(read_timeout: float, connect_timeout: float) -> None:
    """由启动流程调用，从 AppSettings 注入全局超时默认值"""
    global _global_read_timeout, _global_connect_timeout
    _global_read_timeout = read_timeout
    _global_connect_timeout = connect_timeout


def create_http_client(
    timeout: float | None = None,
    connect_timeout: float | None = None,
    **kwargs,
) -> httpx.AsyncClient:
    """创建标准化的 httpx 客户端（统一连接池配置，连接超时与读写超时分离）

    不传 timeout/connect_timeout 时使用 configure_timeouts 注入的全局值。
    """
    t = timeout if timeout is not None else _global_read_timeout
    ct = connect_timeout if connect_timeout is not None else _global_connect_timeout
    return httpx.AsyncClient(
        timeout=httpx.Timeout(t, connect=ct),
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
