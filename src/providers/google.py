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
    FunctionCall,
    ToolCall,
    UsageInfo,
)
from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleProvider(BaseProvider):
    """Google AI Studio (Gemini) 适配器（支持 tool calling）"""

    def __init__(self, api_key: str):
        super().__init__(api_key)
        self._client = httpx.AsyncClient(timeout=120.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _build_payload(self, request: ChatCompletionRequest) -> dict:
        contents = self._convert_messages(request.messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
            },
        }
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [self._convert_tools(request.tools)]
        return payload

    def _convert_tools(self, tools) -> dict:
        """将 OpenAI tools 格式转为 Google functionDeclarations"""
        declarations = []
        for tool in tools:
            func = tool.function
            decl = {"name": func.name, "description": func.description}
            if func.parameters:
                decl["parameters"] = self._clean_json_schema(func.parameters)
            declarations.append(decl)
        return {"functionDeclarations": declarations}

    def _clean_json_schema(self, schema: dict) -> dict:
        """清理 JSON Schema 使其兼容 Google API（移除 title 等不支持的字段）"""
        cleaned = {}
        for k, v in schema.items():
            if k in ("title", "default"):
                continue
            if isinstance(v, dict):
                cleaned[k] = self._clean_json_schema(v)
            elif isinstance(v, list):
                cleaned[k] = [self._clean_json_schema(i) if isinstance(i, dict) else i for i in v]
            else:
                cleaned[k] = v
        return cleaned

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        url = f"{GOOGLE_API_BASE}/models/{model}:generateContent"
        params = {"key": self._api_key}

        resp = await self._client.post(url, params=params, json=self._build_payload(request))
        resp.raise_for_status()
        data = resp.json()

        text, tool_calls = self._extract_response(data)
        finish_reason = "tool_calls" if tool_calls else "stop"

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=text,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=UsageInfo(
                prompt_tokens=data.get("usageMetadata", {}).get("promptTokenCount", 0),
                completion_tokens=data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
                total_tokens=data.get("usageMetadata", {}).get("totalTokenCount", 0),
            ),
        )

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> AsyncIterator[dict]:
        url = f"{GOOGLE_API_BASE}/models/{model}:streamGenerateContent"
        params = {"key": self._api_key, "alt": "sse"}

        async with self._client.stream(
            "POST", url, params=params, json=self._build_payload(request),
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                logger.error(f"Google 流式请求失败 ({resp.status_code}): {resp.text[:200]}")
                raise Exception(f"Google API {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                    candidates = chunk.get("candidates", [])
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text = "".join(p.get("text", "") for p in parts)
                    if text:
                        yield {"content": text}
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

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
        """将 OpenAI 格式消息转换为 Gemini 格式。

        对于 tool_call 历史：由于 Gemini 3+ 要求 functionCall 必须携带
        thought_signature（仅 Gemini 自身产生），而跨模型路由时 tool_call
        来自其他厂商无此签名，因此将 tool_call 历史降级为文本描述，
        避免 400 Bad Request。
        """
        contents = []
        for msg in messages:
            if msg.role == "tool":
                tool_name = msg.name or "unknown"
                result_text = msg.content or ""
                contents.append({
                    "role": "user",
                    "parts": [{"text": f"[工具 {tool_name} 返回结果]\n{result_text}"}],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                parts = []
                if msg.content:
                    parts.append({"text": msg.content})
                call_descs = []
                for tc in msg.tool_calls:
                    raw_args = tc.function.arguments
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except (json.JSONDecodeError, TypeError):
                            args = {"raw": raw_args}
                    else:
                        args = raw_args or {}
                    call_descs.append(f"调用工具 {tc.function.name}({json.dumps(args, ensure_ascii=False)})")
                parts.append({"text": "\n".join(call_descs)})
                contents.append({"role": "model", "parts": parts})
            else:
                role = "user" if msg.role in ("user", "system") else "model"
                if isinstance(msg.content, list):
                    parts = []
                    for part in msg.content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                parts.append({"text": part.get("text", "")})
                            elif part.get("type") == "image_url":
                                url = part.get("image_url", {}).get("url", "")
                                if url and url.startswith("data:"):
                                    import re
                                    m = re.match(r"data:([^;]+);base64,(.+)", url, re.DOTALL)
                                    if m:
                                        parts.append({"inlineData": {"mimeType": m.group(1), "data": m.group(2)}})
                                elif url:
                                    parts.append({"inlineData": {"mimeType": "image/png", "data": ""}})
                        else:
                            parts.append({"text": str(part)})
                    if not parts:
                        parts = [{"text": ""}]
                    contents.append({"role": role, "parts": parts})
                else:
                    text = msg.content or ""
                    contents.append({
                        "role": role,
                        "parts": [{"text": text}],
                    })
        return contents

    def _extract_response(self, data: dict) -> tuple[str | None, list[ToolCall] | None]:
        """从 Gemini 响应中提取文本和/或 function calls"""
        candidates = data.get("candidates", [])
        if not candidates:
            return "", None

        parts = candidates[0].get("content", {}).get("parts", [])
        texts = []
        tool_calls = []

        for part in parts:
            if "text" in part:
                texts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(ToolCall(
                    id=f"call_{uuid.uuid4().hex[:12]}",
                    type="function",
                    function=FunctionCall(
                        name=fc["name"],
                        arguments=json.dumps(fc.get("args", {}), ensure_ascii=False),
                    ),
                ))

        text = "".join(texts) if texts else None
        return text, tool_calls if tool_calls else None
