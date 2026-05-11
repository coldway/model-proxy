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

HF_INFERENCE_BASE = "https://api-inference.huggingface.co/models"
HF_CHAT_BASE = "https://api-inference.huggingface.co/v1"


class HuggingFaceProvider(BaseProvider):
    """
    HuggingFace Inference API 适配器。
    支持两种调用方式：
    1. 对于支持 chat 格式的模型，使用 /v1/chat/completions（OpenAI 兼容）
    2. 对于旧模型，使用原始推理接口
    """

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        # 优先使用 OpenAI 兼容的 chat 接口
        url = f"{HF_CHAT_BASE}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        try:
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
        except httpx.HTTPStatusError:
            # 回退到原始推理接口
            return await self._legacy_inference(model, request)

    async def _legacy_inference(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """旧版推理接口（text-generation）"""
        url = f"{HF_INFERENCE_BASE}/{model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        prompt = "\n".join(f"{m.role}: {m.content}" for m in request.messages)
        prompt += "\nassistant:"

        payload = {
            "inputs": prompt,
            "parameters": {"temperature": request.temperature, "return_full_text": False},
        }
        if request.max_tokens:
            payload["parameters"]["max_new_tokens"] = request.max_tokens

        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        text = data[0].get("generated_text", "") if isinstance(data, list) else ""

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text.strip()),
                    finish_reason="stop",
                )
            ],
            usage=UsageInfo(),
        )

    async def list_models(self) -> list[str]:
        # HuggingFace 模型数量太多，返回推荐的免费可用模型
        return [
            "meta-llama/Llama-3.1-8B-Instruct",
            "meta-llama/Llama-3.1-70B-Instruct",
            "mistralai/Mistral-7B-Instruct-v0.3",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct",
            "google/gemma-2-27b-it",
            "google/gemma-2-9b-it",
            "microsoft/Phi-3.5-mini-instruct",
        ]

    async def health_check(self) -> bool:
        try:
            url = f"{HF_INFERENCE_BASE}/meta-llama/Llama-3.1-8B-Instruct"
            headers = {"Authorization": f"Bearer {self._api_key}"}
            resp = await self._client.get(url, headers=headers)
            return resp.status_code in (200, 503)  # 503 = model loading，仍说明可用
        except Exception:
            return False
