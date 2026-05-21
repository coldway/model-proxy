# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""Ollama 本地模型 Provider — 通过 OpenAI 兼容 API 接入本地 Ollama 实例。

特性：
- 无需 API Key（Ollama 忽略认证）
- 实时查询本地已安装模型（/api/tags）
- 支持流式 / 非流式 / tool calling
- base_url 可通过 config.yaml 的 api_key 字段自定义，默认 http://localhost:11434
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

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

_DEFAULT_BASE_URL = "http://localhost:11434"
_CONNECT_TIMEOUT = 5
_REQUEST_TIMEOUT = 120


class OllamaProvider(BaseProvider):
    """Ollama 本地模型 Provider

    ``api_key`` 参数用于传递自定义 base_url（Ollama 不需要密钥）。
    为空时使用 ``http://localhost:11434``。
    """

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        raw = api_key.strip()
        self._base_url = (raw if raw.startswith("http") else _DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=_CONNECT_TIMEOUT),
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # OpenAI 兼容的 chat completion
    # ------------------------------------------------------------------

    def _build_payload(
        self, model: str, request: ChatCompletionRequest, *, stream: bool = False
    ) -> dict:
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
            rf: dict[str, Any] = {"type": request.response_format.type}
            if (
                request.response_format.type == "json_schema"
                and request.response_format.json_schema
            ):
                rf["json_schema"] = request.response_format.json_schema
            payload["response_format"] = rf
        return payload

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{self._base_url}/v1/chat/completions"
        resp = await self._client.post(
            url,
            headers={"Content-Type": "application/json"},
            json=self._build_payload(model, request),
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise httpx.HTTPStatusError(
                f"Ollama 返回空 choices: {str(data)[:200]}",
                request=resp.request,
                response=resp,
            )
        choice = choices[0]
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
        url = f"{self._base_url}/v1/chat/completions"
        async with self._client.stream(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
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
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    if delta:
                        yield delta
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    # ------------------------------------------------------------------
    # 模型列表：通过 Ollama 原生 /api/tags 端点
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """查询本地 Ollama 已安装的模型列表"""
        url = f"{self._base_url}/api/tags"
        try:
            resp = await self._client.get(url, timeout=_CONNECT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.warning("获取 Ollama 本地模型列表失败（%s）: %s", self._base_url, e)
            return []

    async def list_models_detail(self) -> list[dict[str, Any]]:
        """返回模型详情列表（含 size / family / parameter_size 等元数据）"""
        url = f"{self._base_url}/api/tags"
        try:
            resp = await self._client.get(url, timeout=_CONNECT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("models", [])
        except Exception as e:
            logger.warning("获取 Ollama 模型详情失败（%s）: %s", self._base_url, e)
            return []

    async def is_model_installed(self, model: str) -> bool:
        """检查指定模型是否已安装在本地 Ollama"""
        installed = await self.list_models()
        return any(model == m or model == m.split(":")[0] for m in installed)

    async def pull_model(self, model: str) -> AsyncIterator[dict]:
        """触发 Ollama 下载模型，流式返回进度。

        使用独立 httpx client 避免与主 client 的超时/连接冲突。
        """
        url = f"{self._base_url}/api/pull"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(3600, connect=10),
        ) as client:
            async with client.stream(
                "POST", url,
                json={"name": model, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """检查 Ollama 服务是否可达"""
        try:
            resp = await self._client.get(
                f"{self._base_url}/api/tags", timeout=_CONNECT_TIMEOUT,
            )
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 动态模型发现 → 注册到 Catalog
    # ------------------------------------------------------------------

    async def discover_and_register(
        self, catalog: Any, provider_id: str = "ollama"
    ) -> list[str]:
        """查询本地已安装的模型，自动注册到 Catalog，返回新增模型 ID 列表。

        若 Catalog 中不存在该 provider，会先自动创建。
        已存在于 Catalog 中的模型不会重复添加。
        """
        models = await self.list_models_detail()
        if not models:
            return []

        if catalog.get_provider(provider_id) is None:
            catalog.add_provider(provider_id, {
                "name": "Ollama 本地模型",
                "enabled": True,
                "priority": 3,
                "url": "https://ollama.com/",
                "description": "本地运行的开源模型（无需 API Key，模型自动发现）",
                "api_key_guide": "无需 API Key",
                "models": [],
            })

        existing_ids = {m["id"] for m in catalog.get_models(provider_id)}
        added: list[str] = []

        for m in models:
            model_id = m["name"]
            if model_id in existing_ids:
                continue

            details = m.get("details", {})
            family = details.get("family", "")
            param_size = details.get("parameter_size", "")
            quant = details.get("quantization_level", "")
            size_gb = m.get("size", 0) / (1024 ** 3)

            desc_parts = []
            if param_size:
                desc_parts.append(param_size)
            if family:
                desc_parts.append(family)
            if quant:
                desc_parts.append(quant)
            if size_gb >= 0.1:
                desc_parts.append(f"{size_gb:.1f}GB")
            description = " | ".join(desc_parts) if desc_parts else "本地 Ollama 模型"

            catalog.add_model(provider_id, {
                "id": model_id,
                "name": model_id,
                "description": description,
                "category": "本地",
                "tool_calling": _infer_tool_calling(model_id, family),
                "enabled": True,
                "priority": 50,
            })
            added.append(model_id)

        if added:
            logger.info("Ollama 模型发现: 新增 %d 个模型 — %s", len(added), ", ".join(added))
        return added


def _infer_tool_calling(model_id: str, family: str) -> bool:
    """根据模型名 / family 推断是否支持 tool calling。

    Ollama 中支持 tool calling 的模型族主要包括：
    llama3.1+, qwen2.5+, mistral, command-r, hermes, nemotron, firefunction 等。
    """
    no_tc_markers = ("abliterated", "uncensored", "raw")
    lower = model_id.lower()
    if any(m in lower for m in no_tc_markers):
        return False

    tc_patterns = (
        "llama3.1", "llama3.2", "llama3.3", "llama-3.1", "llama-3.2", "llama-3.3",
        "llama4", "llama-4",
        "qwen2.5", "qwen3", "qwen-2.5", "qwen-3",
        "mistral", "mixtral",
        "command-r", "command-r-plus",
        "hermes", "nemotron", "firefunction",
        "granite", "phi4",
        "deepseek-v2.5", "deepseek-v3", "deepseek-r1",
    )
    if any(p in lower for p in tc_patterns):
        return True
    lower_family = family.lower()
    if any(p in lower_family for p in ("llama", "qwen", "mistral", "command")):
        return True
    return False
