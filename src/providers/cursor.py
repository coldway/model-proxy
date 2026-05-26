# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

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

_TC_SYSTEM_PROMPT = """你是 API 助手。用户会通过对话请求你使用工具或直接回答。

当需要调用工具时，你必须只输出一个 JSON 对象（不要 markdown，不要解释）：
{"tool_calls":[{"id":"call_xxx","type":"function","function":{"name":"工具名","arguments":"JSON字符串"}}]}

当可以直接回答、不需要工具时，只输出：
{"content":"你的回答"}

规则：
1. arguments 必须是 JSON 字符串（内部引号需转义）
2. 每次最多调用必要的工具，优先使用已有工具结果
3. 不要输出除 JSON 以外的任何文字
"""


class CursorProvider(BaseProvider):
    """Cursor Agent CLI 适配器

    Tool Calling 通过 prompt 注入 + JSON 解析实现（Cursor CLI 无原生 OpenAI tools 协议）。
    """

    skip_bulk_capability_test = True
    supports_tool_calling = True

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        import os
        self._api_key = api_key or os.environ.get("CURSOR_API_KEY", "")

    def _resolve_cli_model(self, model: str) -> str:
        """将 model-proxy 模型 ID 映射为 Cursor CLI --model 参数"""
        if not model or model == "cursor-agent":
            return "auto"
        return model

    @staticmethod
    def _extract_text(msg: ChatMessage) -> str:
        if isinstance(msg.content, str):
            return msg.content
        if isinstance(msg.content, list):
            return " ".join(
                p.get("text", "")
                for p in msg.content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        return ""

    @staticmethod
    def _serialize_tools(tools) -> str:
        payload = []
        for tool in tools:
            fn = tool.function
            payload.append({
                "name": fn.name,
                "description": fn.description or "",
                "parameters": fn.parameters or {"type": "object", "properties": {}},
            })
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _format_messages(self, messages: list[ChatMessage]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.role
            if role == "tool":
                name = msg.name or "tool"
                lines.append(f"[tool:{name}]: {self._extract_text(msg)}")
                continue
            if role == "assistant" and msg.tool_calls:
                parts = []
                for tc in msg.tool_calls:
                    parts.append(f"{tc.function.name}({tc.function.arguments})")
                lines.append(f"[assistant:tool_calls]: {'; '.join(parts)}")
                if msg.content:
                    lines.append(f"[assistant]: {self._extract_text(msg)}")
                continue
            text = self._extract_text(msg)
            if text:
                lines.append(f"[{role}]: {text}")
        return "\n".join(lines)

    def _build_prompt(self, request: ChatCompletionRequest) -> str:
        parts: list[str] = []
        if request.tools:
            parts.append(_TC_SYSTEM_PROMPT)
            parts.append("可用工具定义（JSON）：")
            parts.append(self._serialize_tools(request.tools))
            parts.append("")
        parts.append("对话历史：")
        parts.append(self._format_messages(request.messages))
        if request.tools:
            parts.append("")
            parts.append("请根据对话历史，输出 JSON（tool_calls 或 content）：")
        return "\n".join(parts)

    def _build_cmd_args(
        self,
        model: str,
        prompt: str,
        *,
        stream: bool = False,
        json_output: bool = False,
    ) -> list[str]:
        cmd_args = [
            "cursor", "agent",
            "--print", "--trust",
            "--model", self._resolve_cli_model(model),
        ]
        if stream:
            cmd_args.extend(["--output-format", "stream-json", "--stream-partial-output"])
        elif json_output:
            cmd_args.extend(["--output-format", "json"])
        if self._api_key:
            cmd_args.extend(["--api-key", self._api_key])
        cmd_args.append(prompt)
        return cmd_args

    @staticmethod
    def _unwrap_cli_result(stdout: str) -> str:
        text = stdout.strip()
        if not text:
            return ""
        try:
            wrapper = json.loads(text)
            if isinstance(wrapper, dict) and wrapper.get("type") == "result":
                result = wrapper.get("result", "")
                return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        return text

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        text = text.strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        for start_char in ("{", "["):
            idx = text.find(start_char)
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

    def _parse_tool_calls(self, raw_calls) -> list[ToolCall]:
        if not isinstance(raw_calls, list):
            return []
        tool_calls: list[ToolCall] = []
        for i, item in enumerate(raw_calls):
            if not isinstance(item, dict):
                continue
            fn = item.get("function") or {}
            name = fn.get("name") or item.get("name")
            if not name:
                continue
            args = fn.get("arguments", item.get("arguments", "{}"))
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            elif args is None:
                args = "{}"
            else:
                args = str(args)
            tool_calls.append(ToolCall(
                id=str(item.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                type=str(item.get("type") or "function"),
                function=FunctionCall(name=str(name), arguments=args),
            ))
        return tool_calls

    def _parse_assistant_output(self, output: str, *, expect_tools: bool) -> tuple[str | None, list[ToolCall] | None, str]:
        parsed = self._extract_json_object(output)
        if parsed:
            tool_calls = self._parse_tool_calls(parsed.get("tool_calls"))
            if tool_calls:
                return None, tool_calls, "tool_calls"
            content = parsed.get("content")
            if content is not None:
                return str(content), None, "stop"

        if expect_tools:
            logger.warning("Cursor TC 响应未能解析为 JSON tool_calls，回退为纯文本: %s", output[:200])
        return output, None, "stop"

    async def _run_cli(
        self,
        model: str,
        prompt: str,
        *,
        stream: bool = False,
        json_output: bool = False,
        timeout: float = 120,
    ) -> str:
        cmd_args = self._build_cmd_args(model, prompt, stream=stream, json_output=json_output)
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Cursor CLI 退出码 {proc.returncode}: {error_msg}")
        raw = stdout.decode("utf-8", errors="replace").strip()
        if json_output:
            return self._unwrap_cli_result(raw)
        return raw

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        expect_tools = bool(request.tools)
        prompt = self._build_prompt(request)
        timeout = 300 if expect_tools else 240

        try:
            output = await self._run_cli(
                model,
                prompt,
                json_output=expect_tools,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError(f"Cursor CLI 调用超时 ({int(timeout)}s)")

        content, tool_calls, finish_reason = self._parse_assistant_output(output, expect_tools=expect_tools)
        message = ChatMessage(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=model or "cursor-agent",
            choices=[
                Choice(
                    index=0,
                    message=message,
                    finish_reason=finish_reason,
                )
            ],
            usage=UsageInfo(),
        )

    async def stream_chat_completion(
        self, model: str, request: ChatCompletionRequest
    ):
        """流式调用：TC 场景回退为非流式后一次性输出（Cursor CLI 无原生 TC 流式协议）"""
        if request.tools:
            result = await self.chat_completion(model, request)
            msg = result.choices[0].message if result.choices else None
            delta: dict = {}
            if msg:
                if msg.content:
                    delta["content"] = msg.content
                if msg.tool_calls:
                    delta["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            yield delta or {"content": ""}
            return

        prompt = self._build_prompt(request)
        cmd_args = self._build_cmd_args(model, prompt, stream=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            buffer = ""
            while True:
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    break

                buffer += chunk.decode("utf-8", errors="replace")
                lines = buffer.split("\n")
                buffer = lines[-1]

                for line in lines[:-1]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "assistant":
                            delta = data.get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield {"content": content}
                        elif "content" in data:
                            yield {"content": data["content"]}
                        elif "delta" in data and isinstance(data["delta"], dict):
                            content = data["delta"].get("content", "")
                            if content:
                                yield {"content": content}
                    except json.JSONDecodeError:
                        if not line.startswith(("S:", "E:", "{")) and line:
                            yield {"content": line}

            if buffer.strip():
                try:
                    data = json.loads(buffer)
                    if data.get("type") == "assistant":
                        content = data.get("delta", {}).get("content") or data.get("content", "")
                        if content:
                            yield {"content": content}
                    elif "content" in data:
                        yield {"content": data["content"]}
                except json.JSONDecodeError:
                    if not buffer.startswith(("S:", "E:", "{")) and buffer:
                        yield {"content": buffer}

            await proc.wait()

        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError("Cursor CLI 调用超时 (120s)")

    @staticmethod
    def _parse_models_output(text: str) -> list[str]:
        models: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line == "Available models" or line.startswith("Tip:"):
                continue
            match = re.match(r"^([^\s]+)\s+-\s+", line)
            if match:
                models.append(match.group(1))
        return models

    async def list_models(self) -> list[str]:
        """通过 Cursor CLI 拉取当前账号可用模型"""
        cmd = ["cursor", "agent", "--list-models"]
        if self._api_key:
            cmd.extend(["--api-key", self._api_key])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Cursor CLI 退出码 {proc.returncode}: {error_msg}")

            models = self._parse_models_output(stdout.decode("utf-8", errors="replace"))
            if not models:
                raise RuntimeError("Cursor CLI 未返回可用模型")
            return models
        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError("Cursor CLI 拉取模型列表超时 (30s)")

    async def health_check(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "cursor", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False
