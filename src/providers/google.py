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

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleProvider(BaseProvider):
    """Google AI Studio (Gemini) 适配器"""

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{GOOGLE_API_BASE}/models/{model}:generateContent"
        params = {"key": self._api_key}

        contents = self._convert_messages(request.messages)
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens

        resp = await self._client.post(url, params=params, json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = self._extract_text(data)
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(
                prompt_tokens=data.get("usageMetadata", {}).get("promptTokenCount", 0),
                completion_tokens=data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
                total_tokens=data.get("usageMetadata", {}).get("totalTokenCount", 0),
            ),
        )

    async def list_models(self) -> list[str]:
        url = f"{GOOGLE_API_BASE}/models"
        params = {"key": self._api_key}
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return [
                m["name"].replace("models/", "")
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
        except Exception as e:
            logger.error(f"获取 Google 模型列表失败: {e}")
            return []

    async def health_check(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False

    def _convert_messages(self, messages: list[ChatMessage]) -> list[dict]:
        """将 OpenAI 格式消息转换为 Gemini 格式"""
        contents = []
        for msg in messages:
            role = "user" if msg.role in ("user", "system") else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg.content}],
            })
        return contents

    def _extract_text(self, data: dict) -> str:
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)
