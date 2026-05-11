# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import time
import uuid

import httpx

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    UsageInfo,
)
from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    """
    通用 OpenAI 兼容 Provider。
    适用于所有兼容 OpenAI API 格式的厂商：
    - Cerebras (https://api.cerebras.ai/v1)
    - SambaNova (https://api.sambanova.ai/v1)
    - OpenRouter (https://openrouter.ai/api/v1)
    - Mistral (https://api.mistral.ai/v1)
    - 以及任何兼容 /chat/completions 的服务
    """

    def __init__(self, api_key: str, base_url: str, provider_name: str = "openai_compat"):
        super().__init__(api_key)
        self._base_url = base_url.rstrip("/")
        self._provider_name = provider_name
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return ChatCompletionResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=data.get("created", int(time.time())),
            model=data.get("model", model),
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role=choice["message"]["role"],
                        content=choice["message"]["content"],
                    ),
                    finish_reason=choice.get("finish_reason", "stop"),
                )
            ],
            usage=UsageInfo(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
        )

    async def list_models(self) -> list[str]:
        url = f"{self._base_url}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.error(f"获取 {self._provider_name} 模型列表失败: {e}")
            return []

    async def health_check(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False


# 预定义各厂商的 base_url
PROVIDER_BASE_URLS = {
    "cerebras": "https://api.cerebras.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
}


def create_openai_provider(provider_name: str, api_key: str) -> OpenAICompatibleProvider:
    """工厂方法：根据厂商名创建对应的 OpenAI 兼容 Provider"""
    base_url = PROVIDER_BASE_URLS.get(provider_name)
    if not base_url:
        raise ValueError(f"未知的 OpenAI 兼容厂商: {provider_name}")
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, provider_name=provider_name)
