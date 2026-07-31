# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable

from src.models.schemas import ChatCompletionRequest, ChatCompletionResponse


class BaseProvider(ABC):
    """所有厂商适配器的基类"""

    def __init__(self, api_key: str | Callable[[], str]):
        self._key_source = api_key

    @property
    def _api_key(self) -> str:
        """每次请求时动态获取 key，支持 round-robin 轮转"""
        if callable(self._key_source):
            return self._key_source()
        return self._key_source

    @_api_key.setter
    def _api_key(self, value: str | Callable[[], str]):
        self._key_source = value

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
    ) -> AsyncIterator[dict]:
        """流式聊天补全，逐块 yield delta dict（保留 content/tool_calls/reasoning 等完整字段）。
        默认回退到非流式调用后一次性 yield 完整内容。
        """
        result = await self.chat_completion(model, request)
        msg = result.choices[0].message if result.choices else None
        delta: dict = {}
        if msg:
            if msg.content:
                delta["content"] = msg.content
            if getattr(msg, "tool_calls", None):
                delta["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
        yield delta or {"content": ""}

    async def image_generation(self, model: str, **kwargs) -> dict:
        """图像生成（子类按需覆盖）"""
        raise NotImplementedError(f"{self.__class__.__name__} 不支持图像生成")

    async def create_video(self, model: str, **kwargs) -> dict:
        """创建视频任务（子类按需覆盖）"""
        raise NotImplementedError(f"{self.__class__.__name__} 不支持视频生成")

    async def poll_video(self, task_id: str, video_id: str | None = None) -> dict:
        """查询视频任务状态（子类按需覆盖）"""
        raise NotImplementedError(f"{self.__class__.__name__} 不支持视频状态查询")

    async def embeddings(self, model: str, input: str | list[str], **kwargs) -> dict:
        """向量化文本（子类按需覆盖）"""
        raise NotImplementedError(f"{self.__class__.__name__} 不支持 Embedding")

    async def rerank(self, model: str, query: str, documents: list[str], **kwargs) -> dict:
        """重排序（子类按需覆盖）"""
        raise NotImplementedError(f"{self.__class__.__name__} 不支持 Rerank")

    @abstractmethod
    async def list_models(self) -> list[str]:
        """列出该厂商支持的模型"""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        ...
