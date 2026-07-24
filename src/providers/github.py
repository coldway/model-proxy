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

GITHUB_MODELS_BASE = "https://models.inference.ai.azure.com"


from src.providers import register_provider


@register_provider("github")
class GitHubProvider(BaseProvider):
    """GitHub Models 适配器（支持 tool calling）"""

    def __init__(self, api_key: str):
        super().__init__(api_key)
        from src.providers.utils import create_http_client
        self._client = create_http_client()

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
        if request.response_format:
            rf = {"type": request.response_format.type}
            if request.response_format.type == "json_schema" and request.response_format.json_schema:
                rf["json_schema"] = request.response_format.json_schema
            payload["response_format"] = rf
        return payload

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{GITHUB_MODELS_BASE}/chat/completions"
        resp = await self._client.post(
            url, headers=self._build_headers(), json=self._build_payload(model, request),
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        usage = data.get("usage") or {}
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
            ) if usage.get("prompt_tokens") or usage.get("completion_tokens") else None,
        )

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> AsyncIterator[dict]:
        url = f"{GITHUB_MODELS_BASE}/chat/completions"
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
                    delta = choices[0].get("delta", {})
                    if delta:
                        yield delta
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    async def list_models(self) -> list[str]:
        url = f"{GITHUB_MODELS_BASE}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            logger.error("获取 GitHub Models 列表失败: %s", e)
            return []

    async def health_check(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False
