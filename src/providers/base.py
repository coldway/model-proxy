# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.schemas import ChatCompletionRequest, ChatCompletionResponse


class BaseProvider(ABC):
    """所有厂商适配器的基类"""

    def __init__(self, api_key: str):
        self._api_key = api_key

    @abstractmethod
    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """发送聊天补全请求"""
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """列出该厂商支持的模型"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        ...
