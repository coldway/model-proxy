# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx

from src.models.schemas import ChatCompletionRequest, ChatCompletionResponse

_DEFAULT_POOL_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30,
)


class BaseProvider(ABC):
    """所有厂商适配器的基类"""

    def __init__(self, api_key: str):
        self._api_key = api_key

    @staticmethod
    def _create_client(timeout: float = 120.0, http2: bool = False) -> httpx.AsyncClient:
        """创建共享连接池参数的 httpx 异步客户端"""
        return httpx.AsyncClient(
            timeout=timeout,
            limits=_DEFAULT_POOL_LIMITS,
            http2=http2,
        )

    async def close(self) -> None:
        """释放底层连接资源（子类有 httpx client 时应覆盖）"""
        pass

    @abstractmethod
    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """发送聊天补全请求"""
        ...

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> AsyncIterator[str]:
        """流式聊天补全，逐块 yield 文本内容。
        默认回退到非流式调用后一次性 yield 完整内容。
        """
        result = await self.chat_completion(model, request)
        content = result.choices[0].message.content if result.choices else ""
        yield content

    @abstractmethod
    async def list_models(self) -> list[str]:
        """列出该厂商支持的模型"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        ...
