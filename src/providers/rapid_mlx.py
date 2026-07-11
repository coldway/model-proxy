# Created by model-proxy on 2026/07/03
# Copyright © 2026

"""Rapid-MLX 本地模型 Provider — 通过 OpenAI 兼容 API 接入本地 Rapid-MLX 推理服务器。

特性：
- 无需 API Key
- 实时查询当前加载的模型（/v1/models），带短时缓存
- 支持流式 / 非流式 / tool calling / reasoning
- base_url 可通过 config.yaml 的 api_key 字段自定义，默认 http://localhost:8001
- 集成 CircuitBreaker：Rapid-MLX 不可用时快速失败
- 流式响应传递 usage 信息
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

_DEFAULT_BASE_URL = "http://localhost:8001"
_CONNECT_TIMEOUT = 3
_REQUEST_TIMEOUT = 120
_MODELS_CACHE_TTL = 60


from src.providers import register_provider


@register_provider("rapid_mlx", requires_key=False)
class RapidMLXProvider(BaseProvider):
    """Rapid-MLX 本地推理服务器 Provider

    ``api_key`` 参数用于传递自定义 base_url（Rapid-MLX 不需要密钥）。
    为空时使用 ``http://localhost:8001``。
    """

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        raw = api_key.strip()
        self._base_url = (raw if raw.startswith("http") else _DEFAULT_BASE_URL).rstrip("/")
        from src.providers.utils import create_http_client
        self._client = create_http_client(
            timeout=_REQUEST_TIMEOUT, connect_timeout=_CONNECT_TIMEOUT,
        )
        self._models_cache: list[dict[str, Any]] | None = None
        self._models_cache_time: float = 0
        self._breaker_ref: Any = None
        self._provider_id: str = "rapid_mlx"

    def set_breaker(self, breaker: Any, provider_id: str = "rapid_mlx") -> None:
        """注入 CircuitBreaker 引用，使内部方法能感知熔断状态。"""
        self._breaker_ref = breaker
        self._provider_id = provider_id

    def _is_broken(self) -> bool:
        if self._breaker_ref is None:
            return False
        return self._breaker_ref.is_open(self._provider_id)

    def _record_failure(self) -> None:
        if self._breaker_ref:
            self._breaker_ref.record_failure(self._provider_id)

    def _record_success(self) -> None:
        if self._breaker_ref:
            self._breaker_ref.record_success(self._provider_id)

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
        if stream:
            payload["stream_options"] = {"include_usage": True}
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
        if self._is_broken():
            raise httpx.ConnectError("Rapid-MLX 处于熔断状态，暂时不可用")

        url = f"{self._base_url}/v1/chat/completions"
        try:
            resp = await self._client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=self._build_payload(model, request),
            )
            resp.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._record_failure()
            raise httpx.ConnectError(f"Rapid-MLX 连接失败: {e}") from e

        self._record_success()
        data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise httpx.HTTPStatusError(
                f"Rapid-MLX 返回空 choices: {str(data)[:200]}",
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
        if self._is_broken():
            raise httpx.ConnectError("Rapid-MLX 处于熔断状态，暂时不可用")

        url = f"{self._base_url}/v1/chat/completions"
        try:
            async with self._client.stream(
                "POST",
                url,
                headers={"Content-Type": "application/json"},
                json=self._build_payload(model, request, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    self._record_failure()
                    resp.raise_for_status()
                self._record_success()
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
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._record_failure()
            raise httpx.ConnectError(f"Rapid-MLX 连接失败: {e}") from e

    # ------------------------------------------------------------------
    # 模型列表：带 TTL 缓存的 /v1/models
    # ------------------------------------------------------------------

    async def _fetch_models(self, force: bool = False) -> list[dict[str, Any]]:
        """获取模型列表，默认使用缓存（TTL=60s）"""
        now = time.time()
        if not force and self._models_cache is not None and (now - self._models_cache_time) < _MODELS_CACHE_TTL:
            return self._models_cache

        if self._is_broken():
            return self._models_cache or []

        url = f"{self._base_url}/v1/models"
        try:
            resp = await self._client.get(url, timeout=_CONNECT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._models_cache = data.get("data", [])
            self._models_cache_time = now
            self._record_success()
            return self._models_cache
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._record_failure()
            logger.warning("获取 Rapid-MLX 模型列表失败（%s）: %s", self._base_url, e)
            return self._models_cache or []
        except Exception as e:
            logger.warning("获取 Rapid-MLX 模型列表失败（%s）: %s", self._base_url, e)
            return self._models_cache or []

    async def list_models(self) -> list[str]:
        """查询 Rapid-MLX 当前加载的模型列表"""
        models = await self._fetch_models()
        return [m["id"] for m in models if "id" in m]

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """检查 Rapid-MLX 服务是否可达"""
        try:
            resp = await self._client.get(
                f"{self._base_url}/v1/models", timeout=_CONNECT_TIMEOUT,
            )
            ok = resp.status_code == 200
            if ok:
                self._record_success()
            return ok
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 动态模型发现 → 注册到 Catalog
    # ------------------------------------------------------------------

    async def discover_and_register(
        self, catalog: Any, provider_id: str = "rapid_mlx"
    ) -> list[str]:
        """查询 Rapid-MLX 已加载的模型，自动注册到 Catalog，返回新增模型 ID 列表。"""
        models = await self._fetch_models(force=True)
        if not models:
            return []

        if catalog.get_provider(provider_id) is None:
            catalog.add_provider(provider_id, {
                "name": "Rapid-MLX 本地推理",
                "enabled": True,
                "priority": 2,
                "url": "https://rapidmlx.com/",
                "description": "Apple Silicon 高性能本地推理（无需 API Key，模型自动发现）",
                "api_key_guide": "无需 API Key",
                "models": [],
            })

        existing_ids = {m["id"] for m in catalog.get_models(provider_id)}
        added: list[str] = []

        for m in models:
            model_id = m.get("id", "")
            if not model_id or model_id in existing_ids:
                continue

            owned_by = m.get("owned_by", "")
            description = f"Rapid-MLX | {owned_by}" if owned_by else "Rapid-MLX 本地模型"

            catalog.add_model(provider_id, {
                "id": model_id,
                "name": model_id,
                "description": description,
                "category": "本地",
                "tool_calling": _infer_tool_calling(model_id),
                "enabled": True,
                "priority": 10,
            })
            added.append(model_id)

        if added:
            logger.info("Rapid-MLX 模型发现: 新增 %d 个模型 — %s", len(added), ", ".join(added))
        return added


def _infer_tool_calling(model_id: str) -> bool:
    """根据模型名推断是否支持 tool calling"""
    lower = model_id.lower()
    tc_patterns = (
        "qwen2.5", "qwen3", "qwen-2.5", "qwen-3",
        "llama3.1", "llama3.2", "llama3.3", "llama-3.1", "llama-3.2", "llama-3.3",
        "llama4", "llama-4",
        "mistral", "mixtral",
        "command-r", "hermes", "nemotron",
        "deepseek-v2.5", "deepseek-v3", "deepseek-r1",
        "gemma-4", "gemma4",
    )
    return any(p in lower for p in tc_patterns)
