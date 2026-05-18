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
from src.providers.utils import msg_to_dict

logger = logging.getLogger(__name__)


class CloudflareProvider(BaseProvider):
    """
    Cloudflare Workers AI 适配器。
    API 格式与 OpenAI 不同，需要 account_id。
    api_key 格式："{account_id}:{api_token}"
    """

    def __init__(self, api_key: str):
        super().__init__(api_key)
        parts = api_key.split(":", 1)
        if len(parts) == 2:
            self._account_id = parts[0]
            self._token = parts[1]
        else:
            self._account_id = ""
            self._token = api_key
        self._client = self._create_client(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _base_url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai"

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self._base_url()}/run/{model}"
        headers = {"Authorization": f"Bearer {self._token}"}

        payload = {
            "messages": [msg_to_dict(m) for m in request.messages],
        }

        logger.info("[cloudflare] 非流式请求 model=%s msgs=%d", model, len(request.messages))
        t0 = time.monotonic()
        resp = await self._client.post(url, headers=headers, json=payload)
        http_ms = (time.monotonic() - t0) * 1000
        logger.info("[cloudflare] HTTP响应 status=%d 耗时=%.0fms body_len=%d", resp.status_code, http_ms, len(resp.content))
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", {})
        content = result.get("response", "")

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(),
        )

    async def list_models(self) -> list[str]:
        url = f"{self._base_url()}/models/search"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = await self._client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("result", [])
            return [m.get("name", m.get("id", "")) for m in models if "chat" in m.get("task", {}).get("name", "").lower() or "text-generation" in m.get("task", {}).get("name", "").lower()]
        except Exception as e:
            logger.error(f"获取 Cloudflare 模型列表失败: {e}")
            return [
                "@cf/meta/llama-3.1-8b-instruct",
                "@cf/mistral/mistral-7b-instruct-v0.2-lora",
                "@cf/google/gemma-7b-it-lora",
                "@cf/qwen/qwen1.5-14b-chat-awq",
            ]

    async def health_check(self) -> bool:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/models/search"
            headers = {"Authorization": f"Bearer {self._token}"}
            resp = await self._client.get(url, headers=headers)
            return resp.status_code == 200
        except Exception:
            return False
