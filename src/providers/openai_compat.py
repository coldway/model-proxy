# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

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

    def __init__(
        self, api_key: str, base_url: str, provider_name: str = "openai_compat",
        read_timeout: float | None = None, connect_timeout: float | None = None,
    ):
        super().__init__(api_key)
        self._base_url = base_url.rstrip("/")
        self._provider_name = provider_name
        from src.providers.utils import create_http_client
        self._client = create_http_client(timeout=read_timeout, connect_timeout=connect_timeout)

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
        if stream:
            payload["stream_options"] = {"include_usage": True}
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
        resp = await self._client.post(
            url, headers=self._build_headers(), json=self._build_payload(model, request),
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
    ) -> AsyncIterator[dict]:
        url = f"{self._base_url}/chat/completions"
        async with self._client.stream(
            "POST", url,
            headers=self._build_headers(),
            json=self._build_payload(model, request, stream=True),
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    usage = chunk.get("usage")
                    if usage:
                        yield {"__usage__": usage}
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    if delta:
                        yield delta
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    async def image_generation(self, model: str, **kwargs) -> dict:
        url = f"{self._base_url}/images/generations"
        payload = {"model": model, **kwargs}
        resp = await self._client.post(url, headers=self._build_headers(), json=payload, timeout=120.0)
        resp.raise_for_status()
        return resp.json()

    async def create_video(self, model: str, **kwargs) -> dict:
        url = f"{self._base_url}/videos"
        payload = {"model": model, **kwargs}
        resp = await self._client.post(url, headers=self._build_headers(), json=payload, timeout=300.0)
        resp.raise_for_status()
        return resp.json()

    async def poll_video(self, task_id: str) -> dict:
        url = f"{self._base_url}/videos/{task_id}"
        resp = await self._client.get(url, headers=self._build_headers(), timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    async def list_models(self) -> list[str]:
        """拉取厂商模型列表。网络或认证错误时抛出异常（不再静默返回空列表）。"""
        url = f"{self._base_url}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]

    async def health_check(self) -> bool:
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False


# 跳过能力测试的厂商（付费厂商，避免无端消耗 tokens）
SKIP_CAPABILITY_TEST_PROVIDERS = {"dashscope", "spark"}

# CatalogManager 单例缓存，避免每次 create 都重新加载 YAML
_catalog_instance = None


def _get_catalog():
    global _catalog_instance
    if _catalog_instance is None:
        from src.config.catalog import CatalogManager
        _catalog_instance = CatalogManager()
    return _catalog_instance


def create_openai_provider(
    provider_name: str, api_key: str,
    read_timeout: float | None = None, connect_timeout: float | None = None,
    base_url_override: str | None = None,
) -> OpenAICompatibleProvider:
    """工厂方法：根据厂商名创建对应的 OpenAI 兼容 Provider

    base_url 来源：
    1. base_url_override 参数（显式传入）
    2. providers_catalog.yaml 中的 base_url 字段
    """
    base_url = base_url_override
    if not base_url:
        try:
            prov = _get_catalog().get_provider(provider_name)
            if prov:
                base_url = prov.get("base_url")
        except Exception:
            pass
    if not base_url:
        raise ValueError(
            f"未知的 OpenAI 兼容厂商: {provider_name}"
            f"（请在 providers_catalog.yaml 中配置 type: openai_compat 和 base_url）"
        )
    provider = OpenAICompatibleProvider(
        api_key=api_key, base_url=base_url, provider_name=provider_name,
        read_timeout=read_timeout, connect_timeout=connect_timeout,
    )
    if provider_name in SKIP_CAPABILITY_TEST_PROVIDERS:
        provider.skip_bulk_capability_test = True
    return provider
