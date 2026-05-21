# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""Ollama 本地模型 Provider — 通过 OpenAI 兼容 API 接入本地 Ollama 实例。

特性：
- 无需 API Key（Ollama 忽略认证）
- 实时查询本地已安装模型（/api/tags），带短时缓存
- 支持流式 / 非流式 / tool calling
- base_url 可通过 config.yaml 的 api_key 字段自定义，默认 http://localhost:11434
- 集成 CircuitBreaker：Ollama 不可用时快速失败
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

_DEFAULT_BASE_URL = "http://localhost:11434"
_CONNECT_TIMEOUT = 3
_REQUEST_TIMEOUT = 120
_TAGS_CACHE_TTL = 30
_SHOW_TIMEOUT = 5


from src.providers import register_provider


@register_provider("ollama", requires_key=False)
class OllamaProvider(BaseProvider):
    """Ollama 本地模型 Provider

    ``api_key`` 参数用于传递自定义 base_url（Ollama 不需要密钥）。
    为空时使用 ``http://localhost:11434``。
    """

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        raw = api_key.strip()
        self._base_url = (raw if raw.startswith("http") else _DEFAULT_BASE_URL).rstrip("/")
        from src.providers.utils import create_http_client
        self._client = create_http_client(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=_CONNECT_TIMEOUT),
        )
        self._tags_cache: list[dict[str, Any]] | None = None
        self._tags_cache_time: float = 0
        self._breaker_ref: Any = None
        self._provider_id: str = "ollama"

    def set_breaker(self, breaker: Any, provider_id: str = "ollama") -> None:
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
            raise httpx.ConnectError("Ollama 处于熔断状态，暂时不可用")

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
            raise httpx.ConnectError(f"Ollama 连接失败: {e}") from e

        self._record_success()
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
        if self._is_broken():
            raise httpx.ConnectError("Ollama 处于熔断状态，暂时不可用")

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
                        choices = chunk.get("choices", [])
                        if not choices:
                            usage = chunk.get("usage")
                            if usage:
                                yield {"usage": usage}
                            continue
                        delta = choices[0].get("delta", {})
                        chunk_usage = chunk.get("usage")
                        if chunk_usage:
                            delta["usage"] = chunk_usage
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._record_failure()
            raise httpx.ConnectError(f"Ollama 连接失败: {e}") from e

    # ------------------------------------------------------------------
    # 模型列表：带 TTL 缓存的 /api/tags
    # ------------------------------------------------------------------

    async def _fetch_tags(self, force: bool = False) -> list[dict[str, Any]]:
        """获取模型列表，默认使用缓存（TTL=30s）"""
        now = time.time()
        if not force and self._tags_cache is not None and (now - self._tags_cache_time) < _TAGS_CACHE_TTL:
            return self._tags_cache

        if self._is_broken():
            return self._tags_cache or []

        url = f"{self._base_url}/api/tags"
        try:
            resp = await self._client.get(url, timeout=_CONNECT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._tags_cache = data.get("models", [])
            self._tags_cache_time = now
            self._record_success()
            return self._tags_cache
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            self._record_failure()
            logger.warning("获取 Ollama 模型列表失败（%s）: %s", self._base_url, e)
            return self._tags_cache or []
        except Exception as e:
            logger.warning("获取 Ollama 模型列表失败（%s）: %s", self._base_url, e)
            return self._tags_cache or []

    async def list_models(self) -> list[str]:
        """查询本地 Ollama 已安装的模型列表"""
        models = await self._fetch_tags()
        return [m["name"] for m in models]

    async def list_models_detail(self) -> list[dict[str, Any]]:
        """返回模型详情列表（含 size / family / parameter_size 等元数据）"""
        return await self._fetch_tags()

    async def is_model_installed(self, model: str) -> bool:
        """通过 /api/show 精确查询单个模型是否已安装（O(1)，不拉全量列表）"""
        if self._is_broken():
            return False
        url = f"{self._base_url}/api/show"
        try:
            resp = await self._client.post(
                url, json={"name": model}, timeout=_SHOW_TIMEOUT,
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout):
            self._record_failure()
            return False
        except Exception:
            return False

    async def pull_model(self, model: str) -> AsyncIterator[dict]:
        """触发 Ollama 下载模型，流式返回进度。"""
        if self._is_broken():
            yield {"status": "error", "error": "Ollama 处于熔断状态，暂时不可用"}
            return

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

    async def delete_model(self, model: str) -> bool:
        """删除本地模型"""
        if self._is_broken():
            return False
        url = f"{self._base_url}/api/delete"
        try:
            resp = await self._client.request("DELETE", url, json={"name": model}, timeout=30)
            if resp.status_code == 200:
                self._invalidate_cache()
                return True
            return False
        except Exception as e:
            logger.warning("删除 Ollama 模型 %s 失败: %s", model, e)
            return False

    async def list_running(self) -> list[dict[str, Any]]:
        """查询当前加载在 VRAM 中的模型（/api/ps）"""
        if self._is_broken():
            return []
        url = f"{self._base_url}/api/ps"
        try:
            resp = await self._client.get(url, timeout=_CONNECT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("models", [])
        except Exception as e:
            logger.warning("获取 Ollama 运行中模型失败: %s", e)
            return []

    def _invalidate_cache(self) -> None:
        self._tags_cache = None
        self._tags_cache_time = 0

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """检查 Ollama 服务是否可达"""
        try:
            resp = await self._client.get(
                f"{self._base_url}/api/tags", timeout=_CONNECT_TIMEOUT,
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
        self, catalog: Any, provider_id: str = "ollama"
    ) -> list[str]:
        """查询本地已安装的模型，自动注册到 Catalog，返回新增模型 ID 列表。"""
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
        seen_digests: set[str] = set()
        added: list[str] = []

        for m in models:
            model_id = m["name"]
            if model_id in existing_ids:
                continue

            digest = m.get("digest", "")
            if digest and digest in seen_digests:
                continue
            if digest:
                seen_digests.add(digest)

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

    async def test_tool_calling(self, model: str) -> bool:
        """动态测试模型是否支持 tool calling（发送简单 tool call 请求）"""
        if self._is_broken():
            return False

        test_request_data = {
            "model": model,
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "A simple calculator",
                    "parameters": {
                        "type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"],
                    },
                },
            }],
            "temperature": 0,
            "stream": False,
        }
        url = f"{self._base_url}/v1/chat/completions"
        try:
            resp = await self._client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=test_request_data,
                timeout=30,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return False
            msg = choices[0].get("message", {})
            return bool(msg.get("tool_calls"))
        except Exception:
            return False


def _infer_tool_calling(model_id: str, family: str) -> bool:
    """根据模型名 / family 推断是否支持 tool calling（快速启发式判断）。

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
