# Created by model-proxy on 2026/05/13
# Copyright © 2026

"""模型能力自动检测与缓存

拉取远程模型时，自动测试每个模型的能力（tool_calling 等），
结果持久化到 conf/model_capabilities.yaml，已测试的模型下次跳过。

tool_calling 判定：原生 OpenAI tools 协议 或 prompt 注入 JSON 输出，
满足其一即视为支持 TC。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CAPABILITIES_FILE = Path("conf/model_capabilities.yaml")

# 跳过能力测试的厂商列表（直接标记为 available=True，减少 API 用量消耗）
SKIP_TEST_PROVIDERS: set[str] = {"dashscope"}

# catalog（providers_catalog）中可手动标注的能力字段；与探测缓存合并时以此为准
_CATALOG_CAP_OVERRIDE_KEYS = frozenset({
    "tool_calling", "streaming", "multi_turn_tc", "chinese", "vision",
    "json_mode", "reasoning", "latency_ms",
})


def merge_catalog_capabilities(
    cached: dict[str, Any] | None,
    catalog_model: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并能力缓存与目录：目录中显式声明的字段覆盖自动探测结果。"""
    out = dict(cached or {})
    if not catalog_model:
        return out
    for k in _CATALOG_CAP_OVERRIDE_KEYS:
        if k in catalog_model:
            out[k] = catalog_model[k]
    return out

_TC_PROMPT_INJECT_SYSTEM = """你是 API 助手。用户会通过对话请求你使用工具或直接回答。

当需要调用工具时，你必须只输出一个 JSON 对象（不要 markdown，不要解释）：
{"tool_calls":[{"id":"call_xxx","type":"function","function":{"name":"工具名","arguments":"JSON字符串"}}]}

当可以直接回答、不需要工具时，只输出：
{"content":"你的回答"}

规则：
1. arguments 必须是 JSON 字符串（内部引号需转义）
2. 每次最多调用必要的工具，优先使用已有工具结果
3. 不要输出除 JSON 以外的任何文字
"""

TOOL_CALLING_PROBE = {
    "messages": [{"role": "user", "content": "北京现在几点？"}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取指定城市的当前时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"],
            },
        },
    }],
    "tool_choice": "auto",
    "temperature": 0.1,
    "max_tokens": 200,
}


class CapabilityCache:
    """模型能力缓存管理（线程安全）"""

    def __init__(self, path: Path | None = None):
        self._path = path or CAPABILITIES_FILE
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error("加载能力缓存失败: %s", e)
        return {}

    def save(self) -> None:
        with self._lock:
            snapshot = dict(self._data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(
                snapshot, f,
                allow_unicode=True, default_flow_style=False, sort_keys=False,
            )
        logger.info("能力缓存已保存至 %s", self._path)

    def _key(self, provider: str, model_id: str) -> str:
        return f"{provider}/{model_id}"

    def get(self, provider: str, model_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(self._key(provider, model_id))

    def has(self, provider: str, model_id: str) -> bool:
        with self._lock:
            return self._key(provider, model_id) in self._data

    def set(
        self,
        provider: str,
        model_id: str,
        capabilities: dict[str, Any],
    ) -> None:
        key = self._key(provider, model_id)
        with self._lock:
            self._data[key] = {
                **capabilities,
                "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._data)

    def remove(self, provider: str, model_id: str) -> bool:
        key = self._key(provider, model_id)
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


PROBE_INTERVAL_SECONDS = 1
INTER_MODEL_INTERVAL_SECONDS = 2


class CapabilityTester:
    """模型能力测试器

    通过向模型发送探针请求来检测能力：
    - tool_calling: 原生 tools 或 prompt 注入 JSON 任一通过即视为支持
    - available: 模型是否可正常调用（非 429/503）
    """

    _TEST_IMAGE_B64: str | None = None

    def __init__(
        self,
        cache: CapabilityCache | None = None,
        probe_interval: float = PROBE_INTERVAL_SECONDS,
        inter_model_interval: float = INTER_MODEL_INTERVAL_SECONDS,
    ):
        self._cache = cache or CapabilityCache()
        self._probe_interval = probe_interval
        self._inter_model_interval = inter_model_interval

    @property
    def cache(self) -> CapabilityCache:
        return self._cache

    def _build_tool_defs(self):
        """构建测试用的工具定义"""
        from src.models.schemas import ToolDefinition, ToolFunction
        return [
            ToolDefinition(
                type="function",
                function=ToolFunction(
                    name="get_current_time",
                    description="获取指定城市的当前时间",
                    parameters={
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "城市名"},
                        },
                        "required": ["city"],
                    },
                ),
            )
        ]

    @staticmethod
    def _serialize_tools_for_prompt(tools) -> str:
        payload = []
        for tool in tools:
            fn = tool.function
            payload.append({
                "name": fn.name,
                "description": fn.description or "",
                "parameters": fn.parameters or {"type": "object", "properties": {}},
            })
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        idx = text.find("{")
        if idx >= 0:
            candidates.append(text[idx:])
        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        return None

    @classmethod
    def _validate_get_current_time_probe(cls, tool_calls) -> tuple[bool, str]:
        """校验探针响应是否调用了 get_current_time 且参数合理"""
        if not isinstance(tool_calls, list):
            return False, ""
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") or {}
            name = fn.get("name") or item.get("name")
            if name != "get_current_time":
                continue
            args_raw = fn.get("arguments", item.get("arguments", "{}"))
            if isinstance(args_raw, dict):
                args = args_raw
            else:
                try:
                    args = json.loads(str(args_raw or "{}"))
                except json.JSONDecodeError:
                    args = {}
            city = str(args.get("city", "")).strip()
            if city:
                return True, f"get_current_time({json.dumps(args, ensure_ascii=False)})"
            return True, f"get_current_time({args_raw})"
        return False, ""

    @classmethod
    def _probe_result_from_tool_calls(cls, tool_calls, *, tc_method: str) -> dict[str, Any]:
        ok, probe = cls._validate_get_current_time_probe(tool_calls)
        if ok:
            return {
                "tool_calling": True,
                "tc_method": tc_method,
                "probe_response": probe,
            }
        return {}

    async def _test_tool_calling(
        self, provider, model_id: str, tools,
    ) -> dict[str, Any]:
        """测试单轮 tool calling（原生 OpenAI tools 协议）"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            model=model_id,
            messages=[ChatMessage(role="user", content="北京现在几点？")],
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=200,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None

        if msg and msg.tool_calls:
            native_calls = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in msg.tool_calls
            ]
            native_result = self._probe_result_from_tool_calls(native_calls, tc_method="native")
            if native_result:
                return native_result
            tc = msg.tool_calls[0]
            return {
                "tool_calling": True,
                "tc_method": "native",
                "probe_response": f"{tc.function.name}({tc.function.arguments})",
            }
        elif msg and msg.content:
            return {"tool_calling": False, "probe_response": msg.content[:80]}
        return {"tool_calling": False, "probe_response": "empty response"}

    async def _test_prompt_injected_tool_calling(
        self, provider, model_id: str, tools,
    ) -> dict[str, Any]:
        """测试 prompt 注入式 tool calling（不依赖原生 tools 协议）"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        tools_json = self._serialize_tools_for_prompt(tools)
        system_content = (
            f"{_TC_PROMPT_INJECT_SYSTEM}\n\n"
            f"可用工具定义（JSON）：\n{tools_json}\n\n"
            "请根据用户问题，输出 JSON（tool_calls 或 content）。"
        )
        request = ChatCompletionRequest(
            model=model_id,
            messages=[
                ChatMessage(role="system", content=system_content),
                ChatMessage(role="user", content="北京现在几点？"),
            ],
            temperature=0.1,
            max_tokens=300,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None
        if not msg:
            return {"tool_calling": False, "probe_response": "empty response"}

        if msg.tool_calls:
            prompt_calls = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in msg.tool_calls
            ]
            prompt_result = self._probe_result_from_tool_calls(prompt_calls, tc_method="prompt")
            if prompt_result:
                return prompt_result

        content = (msg.content or "").strip()
        parsed = self._extract_json_object(content)
        if parsed:
            prompt_result = self._probe_result_from_tool_calls(
                parsed.get("tool_calls"), tc_method="prompt",
            )
            if prompt_result:
                return prompt_result
            if parsed.get("content"):
                return {"tool_calling": False, "probe_response": str(parsed["content"])[:80]}

        return {"tool_calling": False, "probe_response": (content or "empty response")[:80]}

    async def _test_multi_turn_tc(
        self, provider, model_id: str, tools,
    ) -> dict[str, Any]:
        """测试多轮 tool calling（模型能否正确消化工具返回并回复用户）"""
        from src.models.schemas import (
            ChatCompletionRequest, ChatMessage, FunctionCall, ToolCall,
        )

        messages = [
            ChatMessage(role="user", content="北京现在几点？"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(
                    id="call_probe_mt",
                    type="function",
                    function=FunctionCall(
                        name="get_current_time",
                        arguments='{"city": "北京"}',
                    ),
                )],
            ),
            ChatMessage(
                role="tool",
                content="2026-05-13 16:00:00 CST",
                tool_call_id="call_probe_mt",
                name="get_current_time",
            ),
        ]

        request = ChatCompletionRequest(
            model=model_id,
            messages=messages,
            tools=tools,
            temperature=0.1,
            max_tokens=200,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None

        if msg and msg.tool_calls:
            return {"multi_turn_tc": False, "mt_issue": "loop_call"}
        elif msg and msg.content and msg.content.strip():
            return {"multi_turn_tc": True, "mt_response": msg.content[:80]}
        return {"multi_turn_tc": False, "mt_issue": "empty_response"}

    async def _test_chinese(
        self, provider, model_id: str,
    ) -> dict[str, Any]:
        """测试中文能力"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            model=model_id,
            messages=[ChatMessage(role="user", content="用中文20字以内回答：什么是和弦？")],
            temperature=0.1,
            max_tokens=100,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None
        content = (msg.content or "") if msg else ""

        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in content)
        return {
            "chinese": has_chinese,
            "chinese_response": content[:60],
        }

    @classmethod
    def _get_test_image_b64(cls) -> str:
        """返回缓存的 20x20 红色方块 PNG（base64），首次调用时生成"""
        if cls._TEST_IMAGE_B64 is not None:
            return cls._TEST_IMAGE_B64

        width, height = 20, 20
        raw_data = b""
        for _ in range(height):
            raw_data += b"\x00"
            for _ in range(width):
                raw_data += b"\xff\x00\x00\xff"

        def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
            chunk = chunk_type + data
            return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        png = b"\x89PNG\r\n\x1a\n"
        png += _png_chunk(b"IHDR", ihdr)
        png += _png_chunk(b"IDAT", zlib.compress(raw_data))
        png += _png_chunk(b"IEND", b"")

        cls._TEST_IMAGE_B64 = base64.b64encode(png).decode("ascii")
        return cls._TEST_IMAGE_B64

    async def _test_vision(
        self, provider, model_id: str,
    ) -> dict[str, Any]:
        """测试视觉/图像理解能力（使用内联 base64 小图片，避免网络依赖）"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        img_b64 = self._get_test_image_b64()
        data_url = f"data:image/png;base64,{img_b64}"

        messages = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "What color is this image? Answer in one word."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            )
        ]

        request = ChatCompletionRequest(
            model=model_id,
            messages=messages,
            temperature=0.1,
            max_tokens=50,
        )

        try:
            response = await provider.chat_completion(model_id, request)
            msg = response.choices[0].message if response.choices else None
            content = (msg.content or "") if msg else ""
            if content.strip():
                return {"vision": True, "vision_response": content[:60]}
            return {"vision": False, "vision_response": "empty"}
        except Exception as e:
            err = str(e)
            if "400" in err or "not support" in err.lower() or "invalid" in err.lower() or "INVALID" in err:
                return {"vision": False, "vision_error": "not_supported"}
            raise

    async def _test_json_mode(
        self, provider, model_id: str,
    ) -> dict[str, Any]:
        """测试结构化 JSON 输出能力"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            model=model_id,
            messages=[
                ChatMessage(role="system", content="你是一个只输出 JSON 的助手，不要输出任何其他内容。"),
                ChatMessage(role="user", content='用 JSON 格式回答：列出 3 种乐器，格式为 {"instruments": [{"name": "...", "type": "..."}]}'),
            ],
            temperature=0.1,
            max_tokens=200,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None
        content = (msg.content or "").strip() if msg else ""

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if len(lines) > 2 else lines[1:])
            content = content.strip()

        try:
            parsed = json.loads(content)
            valid = isinstance(parsed, dict) and "instruments" in parsed
            return {"json_mode": valid, "json_sample": content[:80]}
        except (json.JSONDecodeError, ValueError):
            return {"json_mode": False, "json_sample": content[:80]}

    async def _test_streaming(
        self, provider, model_id: str,
    ) -> dict[str, Any]:
        """测试流式输出能力"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            model=model_id,
            messages=[ChatMessage(role="user", content="用10个字回答：什么是音乐？")],
            stream=True,
            temperature=0.1,
            max_tokens=50,
        )

        chunk_count = 0
        first_chunk_time = None
        start_time = time.time()

        try:
            async for _chunk in provider.stream_chat_completion(model_id, request):
                chunk_count += 1
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                if chunk_count >= 5:
                    break
        except Exception as e:
            return {"streaming": False, "stream_error": str(e)[:60]}

        ttfb = round((first_chunk_time - start_time) * 1000) if first_chunk_time else 0
        return {
            "streaming": chunk_count >= 2,
            "stream_chunks": chunk_count,
            "stream_ttfb_ms": ttfb,
        }

    async def _test_reasoning(
        self, provider, model_id: str,
    ) -> dict[str, Any]:
        """测试逻辑推理能力（一个简单但需要推理的问题）"""
        from src.models.schemas import ChatCompletionRequest, ChatMessage

        request = ChatCompletionRequest(
            model=model_id,
            messages=[ChatMessage(
                role="user",
                content="一个房间有3盏灯，门外有3个开关分别控制它们。你只能进房间一次。如何确定每个开关对应哪盏灯？只回答方法，30字以内。",
            )],
            temperature=0.1,
            max_tokens=150,
        )

        response = await provider.chat_completion(model_id, request)
        msg = response.choices[0].message if response.choices else None
        content = (msg.content or "") if msg else ""

        has_key_concept = any(kw in content for kw in ["热", "温", "摸", "烫", "warm", "heat", "touch"])
        return {
            "reasoning": has_key_concept,
            "reasoning_response": content[:80],
        }

    async def test_model_via_provider(
        self,
        provider: "BaseProvider",
        provider_name: str,
        model_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """通过已注册的 Provider 实例测试单个模型

        返回 {available, tool_calling, multi_turn_tc, chinese,
              vision, json_mode, streaming, reasoning, latency_ms, error, ...}
        """
        if not force and self._cache.has(provider_name, model_id):
            cached = self._cache.get(provider_name, model_id)
            err = cached.get("error", "")
            if err and ("429" in err or "rate_limit" in err):
                logger.info("模型 %s/%s 上次因限流跳过，重新测试", provider_name, model_id)
            else:
                logger.debug("跳过已测试模型 %s/%s", provider_name, model_id)
                return {**cached, "cached": True}

        result: dict[str, Any] = {
            "provider": provider_name,
            "model": model_id,
            "available": False,
            "tool_calling": False,
            "tc_method": None,
            "multi_turn_tc": False,
            "chinese": False,
            "vision": False,
            "json_mode": False,
            "streaming": False,
            "reasoning": False,
            "latency_ms": 0,
            "error": None,
        }

        tools = self._build_tool_defs()
        tc_result: dict[str, Any] = {"tool_calling": False, "probe_response": ""}

        # ── 阶段 1：单轮 tool calling（原生协议，同时测延迟） ──
        t0 = time.time()
        tc_400 = False
        try:
            tc_result = await self._test_tool_calling(provider, model_id, tools)
            result["latency_ms"] = round((time.time() - t0) * 1000)
            result["available"] = True
            result.update(tc_result)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                result["error"] = "rate_limited (429)"
                self._cache.set(provider_name, model_id, result)
                logger.info("测试 %s/%s: 阶段1失败 - %s", provider_name, model_id, result["error"])
                return {**result, "cached": False}
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                result["error"] = "unavailable (503)"
                self._cache.set(provider_name, model_id, result)
                logger.info("测试 %s/%s: 阶段1失败 - %s", provider_name, model_id, result["error"])
                return {**result, "cached": False}
            elif "400" in err_str:
                result["available"] = True
                result["tool_calling"] = False
                result["tc_note"] = f"bad_request (400): {err_str[:80]}"
                tc_400 = True
                logger.info("测试 %s/%s: 原生 tool_calling 不支持 (400)，尝试 prompt 注入探测", provider_name, model_id)
            else:
                result["error"] = err_str[:120]
                self._cache.set(provider_name, model_id, result)
                logger.info("测试 %s/%s: 阶段1失败 - %s", provider_name, model_id, result["error"])
                return {**result, "cached": False}

        # ── 阶段 1a：原生 TC 未通过时，尝试 prompt 注入式 TC ──
        if not result["tool_calling"] and result.get("available"):
            try:
                await asyncio.sleep(self._probe_interval)
                prompt_tc_result = await self._test_prompt_injected_tool_calling(
                    provider, model_id, tools,
                )
                if prompt_tc_result.get("tool_calling"):
                    result.update(prompt_tc_result)
                    logger.info(
                        "测试 %s/%s: prompt 注入 TC 通过 - %s",
                        provider_name, model_id, prompt_tc_result.get("probe_response"),
                    )
                elif not tc_400:
                    result["probe_response"] = prompt_tc_result.get(
                        "probe_response", result.get("probe_response"),
                    )
            except Exception as e:
                result["tc_prompt_error"] = str(e)[:80]
                logger.info(
                    "测试 %s/%s: prompt 注入 TC 探测失败 - %s",
                    provider_name, model_id, result["tc_prompt_error"],
                )

        if not tc_400 and tc_result.get("probe_response") == "empty response" and not result["tool_calling"]:
            result["error"] = "empty_response (模型返回 200 但无内容，可能是软限流)"
            logger.warning(
                "测试 %s/%s: 模型返回空内容，跳过后续能力检测",
                provider_name, model_id,
            )
            self._cache.set(provider_name, model_id, result)
            return {**result, "cached": False}

        # ── 阶段 1b: 400 时测量延迟（用简单请求代替 tool calling） ──
        if tc_400 and result["latency_ms"] == 0:
            try:
                from src.models.schemas import ChatCompletionRequest, ChatMessage
                t1 = time.time()
                lat_req = ChatCompletionRequest(
                    model=model_id,
                    messages=[ChatMessage(role="user", content="hi")],
                    temperature=0.1, max_tokens=10,
                )
                await provider.chat_completion(model_id, lat_req)
                result["latency_ms"] = round((time.time() - t1) * 1000)
            except Exception:
                pass

        # ── 阶段 2：多轮 tool calling（仅当单轮通过时） ──
        if result["tool_calling"]:
            try:
                await asyncio.sleep(self._probe_interval)
                mt_result = await self._test_multi_turn_tc(provider, model_id, tools)
                result.update(mt_result)
            except Exception as e:
                result["multi_turn_tc"] = False
                result["mt_issue"] = f"error: {str(e)[:60]}"

        # ── 阶段 3-7：独立能力并行探测 ──
        async def _safe_probe(name: str, coro):
            """安全执行单个探测，失败返回空 dict"""
            try:
                await asyncio.sleep(self._probe_interval)
                return await coro
            except Exception:
                return {name: False}

        parallel_tasks = [
            _safe_probe("chinese", self._test_chinese(provider, model_id)),
            _safe_probe("vision", self._test_vision(provider, model_id)),
            _safe_probe("json_mode", self._test_json_mode(provider, model_id)),
            _safe_probe("streaming", self._test_streaming(provider, model_id)),
            _safe_probe("reasoning", self._test_reasoning(provider, model_id)),
        ]
        parallel_results = await asyncio.gather(*parallel_tasks)
        for pr in parallel_results:
            result.update(pr)

        self._cache.set(provider_name, model_id, result)
        logger.info(
            "测试 %s/%s: avail=%s tc=%s tc_method=%s mt=%s cn=%s vis=%s json=%s stream=%s reason=%s lat=%dms",
            provider_name, model_id,
            result["available"], result["tool_calling"], result.get("tc_method"),
            result["multi_turn_tc"], result["chinese"],
            result["vision"], result["json_mode"],
            result["streaming"], result["reasoning"],
            result["latency_ms"],
        )
        return {**result, "cached": False}

    async def test_provider_models(
        self,
        provider: "BaseProvider",
        provider_name: str,
        model_ids: list[str],
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """批量测试一个厂商的所有模型"""
        results = []
        for mid in model_ids:
            result = await self.test_model_via_provider(
                provider, provider_name, mid, force=force,
            )
            results.append(result)
            if not result.get("cached"):
                await asyncio.sleep(self._inter_model_interval)

        self._cache.save()
        return results

    async def test_all_providers(
        self,
        providers: dict[str, "BaseProvider"],
        model_map: dict[str, list[str]],
        force: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """测试所有厂商的所有模型

        Args:
            providers: {provider_name: provider_instance}
            model_map: {provider_name: [model_id, ...]}
            force: 强制重新测试已缓存的模型
        """
        all_results = {}
        for prov_name, model_ids in model_map.items():
            # 跳过指定厂商的能力测试（节省 API 用量）
            if prov_name in SKIP_TEST_PROVIDERS:
                logger.info("厂商 %s 在跳过列表中，不执行能力测试，直接标记为可用", prov_name)
                results = []
                for mid in model_ids:
                    default_result = {
                        "provider": prov_name,
                        "model": mid,
                        "available": True,
                        "tool_calling": False,
                        "multi_turn_tc": False,
                        "chinese": True,
                        "vision": False,
                        "json_mode": True,
                        "streaming": True,
                        "reasoning": False,
                        "latency_ms": 0,
                        "error": None,
                        "skipped": True,
                    }
                    self._cache.set(prov_name, mid, default_result)
                    results.append(default_result)
                self._cache.save()
                all_results[prov_name] = results
                continue

            provider = providers.get(prov_name)
            if not provider:
                logger.warning("厂商 %s 未注册，跳过测试", prov_name)
                continue

            if getattr(provider, "skip_bulk_capability_test", False):
                logger.info("厂商 %s 设置了 skip_bulk_capability_test，跳过能力测试", prov_name)
                continue

            results = await self.test_provider_models(
                provider, prov_name, model_ids, force=force,
            )
            all_results[prov_name] = results

        return all_results
