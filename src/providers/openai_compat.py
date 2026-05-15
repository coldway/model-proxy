# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator

import httpx

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    UsageInfo,
)
from src.providers.base import BaseProvider
from src.providers.utils import msg_to_dict, parse_tool_calls

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(BaseProvider):
    """
    通用 OpenAI 兼容 Provider（支持 tool calling）。
    适用于所有兼容 OpenAI API 格式的厂商。
    """

    def __init__(self, api_key: str, base_url: str, provider_name: str = "openai_compat"):
        super().__init__(api_key)
        self._base_url = base_url.rstrip("/")
        self._provider_name = provider_name
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, model: str, request: ChatCompletionRequest, stream: bool = False) -> dict:
        payload: dict = {
            "model": model,
            "messages": [msg_to_dict(m) for m in request.messages],
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [t.model_dump() for t in request.tools]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        return payload

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self._base_url}/chat/completions"
        logger.info(
            "[%s] 非流式请求 model=%s url=%s msgs=%d temp=%s max_tokens=%s tools=%d",
            self._provider_name, model, url, len(request.messages),
            request.temperature, request.max_tokens,
            len(request.tools) if request.tools else 0,
        )
        t0 = time.monotonic()
        resp = await self._client.post(
            url, headers=self._build_headers(), json=self._build_payload(model, request),
        )
        http_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[%s] HTTP响应 status=%d 耗时=%.0fms body_len=%d",
            self._provider_name, resp.status_code, http_ms, len(resp.content),
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})
        msg = choice["message"]

        return ChatCompletionResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=data.get("created", int(time.time())),
            model=data.get("model", model),
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role=msg["role"],
                        content=msg.get("content"),
                        tool_calls=parse_tool_calls(msg.get("tool_calls")),
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

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> AsyncIterator[str]:
        url = f"{self._base_url}/chat/completions"
        logger.info(
            "[%s] 流式请求 model=%s url=%s msgs=%d",
            self._provider_name, model, url, len(request.messages),
        )
        async with self._client.stream(
            "POST", url,
            headers=self._build_headers(),
            json=self._build_payload(model, request, stream=True),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    text = choices[0].get("delta", {}).get("content", "")
                    if text:
                        yield text
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

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
