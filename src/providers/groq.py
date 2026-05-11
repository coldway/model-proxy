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

logger = logging.getLogger(__name__)

GROQ_API_BASE = "https://api.groq.com/openai/v1"


class GroqProvider(BaseProvider):
    """Groq 适配器（兼容 OpenAI 格式）"""

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client = httpx.AsyncClient(timeout=60.0)

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, model: str, request: ChatCompletionRequest, stream: bool = False) -> dict:
        payload: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        return payload

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{GROQ_API_BASE}/chat/completions"
        resp = await self._client.post(
            url, headers=self._build_headers(), json=self._build_payload(model, request),
        )
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

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> AsyncIterator[str]:
        url = f"{GROQ_API_BASE}/chat/completions"
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
        url = f"{GROQ_API_BASE}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.error(f"获取 Groq 模型列表失败: {e}")
            return []

    async def health_check(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False
