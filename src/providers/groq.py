# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import copy
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

GROQ_API_BASE = "https://api.groq.com/openai/v1"


class GroqProvider(BaseProvider):
    """Groq 适配器（兼容 OpenAI 格式，支持 tool calling）"""

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _relax_schema(schema: dict) -> dict:
        """递归放宽 JSON Schema 以兼容 Groq 模型的非严格输出：
        1. boolean → anyOf[boolean, string("true"/"false")]
        2. array items: {type: "string"} → items: anyOf[string, object]

        使用 deepcopy 避免污染调用方的原始 schema。
        """
        if not isinstance(schema, dict):
            return schema
        schema = copy.deepcopy(schema)
        if schema.get("type") == "boolean":
            return {"anyOf": [{"type": "boolean"}, {"type": "string", "enum": ["true", "false"]}]}
        if schema.get("type") == "array":
            items = schema.get("items", {})
            if isinstance(items, dict) and items.get("type") == "string":
                schema["items"] = {"anyOf": [{"type": "string"}, {"type": "object"}]}
        if "properties" in schema and isinstance(schema["properties"], dict):
            schema["properties"] = {
                k: GroqProvider._relax_schema(v) for k, v in schema["properties"].items()
            }
        return schema

    @staticmethod
    def _is_tool_call_json(text: str) -> bool:
        """判断文本是否为模型试图生成的工具调用 JSON（非面向用户的文本）。"""
        stripped = text.strip()
        if not stripped:
            return False
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return False
        if isinstance(parsed, list):
            return any(isinstance(item, dict) and "name" in item for item in parsed[:3])
        if isinstance(parsed, dict) and "name" in parsed and "parameters" in parsed:
            return True
        return False

    def _try_recover_tool_use_failed(
        self, resp: httpx.Response, model: str,
    ) -> dict | None:
        """Groq 严格模式下模型想输出文本但被拒绝时，
        从 failed_generation 中恢复有效响应，避免不必要的降级。
        如果 failed_generation 是工具调用 JSON，则不恢复（让降级链处理）。"""
        try:
            body = resp.json()
        except Exception:
            return None
        err = body.get("error", {})
        if err.get("code") != "tool_use_failed":
            return None
        text = err.get("failed_generation", "")
        if not text:
            return None
        if self._is_tool_call_json(text):
            logger.info(
                "Groq tool_use_failed: failed_generation 是工具调用 JSON (%d字)，不恢复",
                len(text),
            )
            return None
        logger.info(
            "Groq tool_use_failed 恢复: 提取 failed_generation (%d字) 作为有效响应",
            len(text),
        )
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
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
            relaxed = []
            for t in request.tools:
                td = t.model_dump()
                if "function" in td and "parameters" in td["function"]:
                    td["function"]["parameters"] = self._relax_schema(td["function"]["parameters"])
                relaxed.append(td)
            payload["tools"] = relaxed
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        return payload

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{GROQ_API_BASE}/chat/completions"
        resp = await self._client.post(
            url, headers=self._build_headers(), json=self._build_payload(model, request),
        )
        if resp.status_code == 400:
            data = self._try_recover_tool_use_failed(resp, model)
            if data is None:
                resp.raise_for_status()
        else:
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
        url = f"{GROQ_API_BASE}/chat/completions"
        async with self._client.stream(
            "POST", url,
            headers=self._build_headers(),
            json=self._build_payload(model, request, stream=True),
        ) as resp:
            if resp.status_code == 400:
                body = await resp.aread()
                try:
                    err_data = json.loads(body)
                    err = err_data.get("error", {})
                    if err.get("code") == "tool_use_failed":
                        text = err.get("failed_generation", "")
                        if text and not self._is_tool_call_json(text):
                            logger.info(
                                "Groq stream tool_use_failed 恢复: 提取 failed_generation (%d字)",
                                len(text),
                            )
                            yield {"content": text}
                            return
                except (json.JSONDecodeError, KeyError):
                    pass
                resp.raise_for_status()
            else:
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
