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
        mode: str | None = None,
        force: bool = False,
        sandbox: str | None = None,
        workspace: str | None = None,
        resume_chat_id: str | None = None,
        continue_session: bool = False,
        worktree_name: str | None = None,
        worktree_base: str | None = None,
        skip_worktree_setup: bool = False,
        approve_mcps: bool = False,
    ) -> list[str]:
        """构建 Cursor CLI 命令参数
        
        Args:
            model: 模型名称
            prompt: 用户输入
            stream: 是否流式输出
            json_output: 是否要求 JSON 输出
            mode: 执行模式 (plan/ask/agent)
            force: 是否强制执行命令(--force/--yolo)
            sandbox: 沙箱模式 (enabled/disabled)
            workspace: 工作区目录路径
            resume_chat_id: 恢复的会话 ID
            continue_session: 继续上次会话
            worktree_name: Git worktree 名称
            worktree_base: Worktree 基准分支
            skip_worktree_setup: 跳过 worktree 设置脚本
            approve_mcps: 自动批准 MCP 服务器
        """
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
        
        # 添加 mode 参数支持（plan / ask）
        if mode == "plan":
            cmd_args.append("--plan")
        elif mode == "ask":
            cmd_args.extend(["--mode", "ask"])
        elif mode and mode not in ("agent", ""):
            logger.warning("未知的 mode '%s'，忽略（支持: plan, ask, agent）", mode)
        
        # 添加 force 参数支持
        if force:
            cmd_args.append("--force")
        
        # 添加 sandbox 参数支持
        if sandbox in ("enabled", "disabled"):
            cmd_args.extend(["--sandbox", sandbox])
        elif sandbox:
            logger.warning("未知的 sandbox 值 '%s'，忽略（支持: enabled, disabled）", sandbox)
        
        # 工作区路径
        if workspace:
            cmd_args.extend(["--workspace", workspace])
        
        # 会话管理
        if resume_chat_id:
            cmd_args.extend(["--resume", resume_chat_id])
        elif continue_session:
            cmd_args.append("--continue")
        
        # Git Worktree
        if worktree_name:
            cmd_args.extend(["-w", worktree_name])
            if worktree_base:
                cmd_args.extend(["--worktree-base", worktree_base])
            if skip_worktree_setup:
                cmd_args.append("--skip-worktree-setup")
        
        # MCP 自动批准
        if approve_mcps:
            cmd_args.append("--approve-mcps")
        
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
        mode: str | None = None,
        force: bool = False,
        sandbox: str | None = None,
        workspace: str | None = None,
        resume_chat_id: str | None = None,
        continue_session: bool = False,
        worktree_name: str | None = None,
        worktree_base: str | None = None,
        skip_worktree_setup: bool = False,
        approve_mcps: bool = False,
        session_id: str = "",  # 新增，用于日志
    ) -> str:
        sid_tag = f" session={session_id}" if session_id else ""
        cmd_args = self._build_cmd_args(
            model, prompt,
            stream=stream,
            json_output=json_output,
            mode=mode,
            force=force,
            sandbox=sandbox,
            workspace=workspace,
            resume_chat_id=resume_chat_id,
            continue_session=continue_session,
            worktree_name=worktree_name,
            worktree_base=worktree_base,
            skip_worktree_setup=skip_worktree_setup,
            approve_mcps=approve_mcps,
        )
        
        # 打印命令行（隐藏 prompt 内容）
        cmd_display = " ".join(
            arg if not arg.startswith("--") and len(arg) > 50 
            else arg[:100] + "..." if len(arg) > 100 else arg
            for arg in cmd_args
        )
        logger.debug("[Cursor] 执行命令: %s", cmd_display[:300])
        
        import time as time_module
        start_time = time_module.time()
        
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        
        elapsed = time_module.time() - start_time
        
        # 解码 stderr
        stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
        
        if proc.returncode != 0:
            logger.error(
                "[Cursor] CLI 失败: returncode=%d elapsed=%.1fs stderr=%s",
                proc.returncode, elapsed, stderr_text[:1000]
            )
            raise RuntimeError(f"Cursor CLI 退出码 {proc.returncode}: {stderr_text}")
        
        raw = stdout.decode("utf-8", errors="replace").strip()
        
        # 成功时也详细记录 stderr（可能包含警告、进度信息等）
        if stderr_text:
            # stderr 有内容时，按行分割并记录（可能是警告、进度条等非错误信息）
            stderr_lines = stderr_text.split('\n')
            # 去除空行和 ANSI 转义序列
            import re
            clean_lines = [
                re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', line).strip()
                for line in stderr_lines
                if line.strip() and not re.match(r'^[\x00-\x1f]+$', line)
            ]
            if clean_lines:
                stderr_summary = f"{len(clean_lines)}行: " + " | ".join(clean_lines[:5])
                if len(clean_lines) > 5:
                    stderr_summary += f" ... (共{len(clean_lines)}行)"
                logger.info(
                    "[Cursor] CLI 成功:%s elapsed=%.1fs output_size=%d stderr=%s",
                    sid_tag, elapsed, len(raw), stderr_summary[:500]
                )
            else:
                logger.info(
                    "[Cursor] CLI 成功:%s elapsed=%.1fs output_size=%d stderr=无实质内容（仅控制字符）",
                    sid_tag, elapsed, len(raw)
                )
        else:
            logger.info(
                "[Cursor] CLI 成功:%s elapsed=%.1fs output_size=%d stderr=无",
                sid_tag, elapsed, len(raw)
            )
        
        if json_output:
            return self._unwrap_cli_result(raw)
        return raw

    async def chat_completion(
        self, model: str, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        expect_tools = bool(request.tools)
        prompt = self._build_prompt(request)
        timeout = 600 if expect_tools else 600  # 增加到 10 分钟以支持复杂任务
        mode = getattr(request, "mode", None)
        force = getattr(request, "force", False)
        sandbox = getattr(request, "sandbox", None)
        
        # 提取新增参数
        workspace = getattr(request, "workspace_path", None)
        resume_chat_id = getattr(request, "cursor_session_id", None)
        continue_session = getattr(request, "cursor_continue", False)
        worktree_name = getattr(request, "worktree_name", None)
        worktree_base = getattr(request, "worktree_base", None)
        skip_worktree_setup = getattr(request, "skip_worktree_setup", False)
        approve_mcps = getattr(request, "approve_mcps", False)
        
        # 打印 Cursor 特有参数
        cursor_params = []
        if mode:
            cursor_params.append(f"mode={mode}")
        if workspace:
            cursor_params.append(f"workspace={workspace[:50]}...")
        if resume_chat_id:
            cursor_params.append(f"session_id={resume_chat_id[:16]}")
        if continue_session:
            cursor_params.append("continue=True")
        if worktree_name:
            cursor_params.append(f"worktree={worktree_name}")
        if approve_mcps:
            cursor_params.append("approve_mcps=True")
        
        # 添加 session_id（来自request，可能和cursor_session_id不同）
        req_session_id = getattr(request, "session_id", None)
        sid_tag = f" session={req_session_id}" if req_session_id else ""
        
        params_str = " ".join(cursor_params) if cursor_params else "默认参数"
        logger.info(
            "[Cursor] 调用参数: model=%s timeout=%ds%s %s tools=%d",
            model, timeout, sid_tag, params_str, len(request.tools) if request.tools else 0
        )
        
        # plan/ask 模式下忽略 force 参数
        if mode in ("plan", "ask"):
            force = False

        try:
            output = await self._run_cli(
                model,
                prompt,
                json_output=expect_tools,
                timeout=timeout,
                mode=mode,
                force=force,
                sandbox=sandbox,
                workspace=workspace,
                resume_chat_id=resume_chat_id,
                continue_session=continue_session,
                worktree_name=worktree_name,
                worktree_base=worktree_base,
                skip_worktree_setup=skip_worktree_setup,
                approve_mcps=approve_mcps,
                session_id=req_session_id or "",
            )
        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError(f"Cursor CLI 调用超时 ({int(timeout)}s)")

        content, tool_calls, finish_reason = self._parse_assistant_output(output, expect_tools=expect_tools)
        
        # 打印响应摘要
        response_len = len(content) if content else 0
        tool_summary = f" tool_calls={len(tool_calls)}个" if tool_calls else ""
        logger.info(
            "[Cursor] 响应:%s 长度=%d finish=%s%s 内容预览=%s",
            sid_tag, response_len, finish_reason, tool_summary, 
            (content[:100] if content else "")[:100].replace("\n", "\\n")
        )
        
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
        mode = getattr(request, "mode", None)
        force = getattr(request, "force", False)
        sandbox = getattr(request, "sandbox", None)
        
        # 提取新增参数
        workspace = getattr(request, "workspace_path", None)
        resume_chat_id = getattr(request, "cursor_session_id", None)
        continue_session = getattr(request, "cursor_continue", False)
        worktree_name = getattr(request, "worktree_name", None)
        worktree_base = getattr(request, "worktree_base", None)
        skip_worktree_setup = getattr(request, "skip_worktree_setup", False)
        approve_mcps = getattr(request, "approve_mcps", False)
        
        # 打印 Cursor 特有参数（流式）
        cursor_params = []
        if mode:
            cursor_params.append(f"mode={mode}")
        if workspace:
            cursor_params.append(f"workspace={workspace[:50]}...")
        if resume_chat_id:
            cursor_params.append(f"session_id={resume_chat_id[:16]}")
        if continue_session:
            cursor_params.append("continue=True")
        if worktree_name:
            cursor_params.append(f"worktree={worktree_name}")
        if approve_mcps:
            cursor_params.append("approve_mcps=True")
        
        # 添加 session_id（来自request）
        req_session_id = getattr(request, "session_id", None)
        sid_tag = f" session={req_session_id}" if req_session_id else ""
        
        params_str = " ".join(cursor_params) if cursor_params else "默认参数"
        logger.info(
            "[Cursor] 调用参数(流式): model=%s%s %s",
            model, sid_tag, params_str
        )
        
        # plan/ask 模式下忽略 force 参数
        if mode in ("plan", "ask"):
            force = False
        
        cmd_args = self._build_cmd_args(
            model, prompt,
            stream=True,
            mode=mode,
            force=force,
            sandbox=sandbox,
            workspace=workspace,
            resume_chat_id=resume_chat_id,
            continue_session=continue_session,
            worktree_name=worktree_name,
            worktree_base=worktree_base,
            skip_worktree_setup=skip_worktree_setup,
            approve_mcps=approve_mcps,
        )

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

    async def create_session(self) -> str:
        """创建新的 Cursor 会话并返回 session_id"""
        cmd = ["cursor", "agent", "create-chat"]
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
                raise RuntimeError(f"创建 Cursor 会话失败: {error_msg}")

            # 解析输出获取 session_id
            output = stdout.decode("utf-8", errors="replace").strip()
            # 预期格式：Created chat: <session_id> 或直接输出 session_id
            match = re.search(r"Created chat:\s*(\S+)", output)
            if match:
                return match.group(1)

            # 如果没有匹配，假设整个输出就是 session_id
            if output:
                return output
            
            raise RuntimeError("Cursor CLI 未返回会话 ID")
        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            raise RuntimeError("创建 Cursor 会话超时 (30s)")

    async def list_sessions(self) -> list[dict]:
        """列出所有 Cursor 会话"""
        cmd = ["cursor", "agent", "ls"]
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
                logger.warning("列出 Cursor 会话失败: %s", error_msg)
                return []

            # 解析输出
            # 预期格式：每行一个会话，可能包含 ID、标题、时间等
            output = stdout.decode("utf-8", errors="replace").strip()
            
            # 过滤控制字符和错误信息（非交互环境常见问题）
            # 移除 ANSI 转义序列
            output = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', output)
            output = re.sub(r'\x1b\[[0-9;]*m', '', output)
            # 移除其他控制字符（保留换行/tab/空格）
            output = ''.join(c for c in output if c.isprintable() or c in '\n\r\t ')
            
            sessions = []
            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                
                # 过滤明显的错误信息
                error_keywords = [
                    'ERROR', 'Error', 'error', 'warning', 'Warning',
                    'Raw mode', 'stdin', 'github.com', 'handleSetRaw',
                    'index.js', 'not supported', 'ink/#israwmodesupported'
                ]
                if any(kw in line for kw in error_keywords):
                    continue
                
                # 尝试解析 JSON 格式（如果 Cursor CLI 返回 JSON）
                try:
                    session = json.loads(line)
                    if isinstance(session, dict):
                        sessions.append(session)
                    continue
                except json.JSONDecodeError:
                    pass
                
                # 如果不是 JSON，尝试简单解析（ID - Title）
                match = re.match(r"^(\S+)\s+-\s+(.+)$", line)
                if match:
                    sessions.append({"id": match.group(1), "title": match.group(2)})
                else:
                    # 验证是否是有效的 session ID 格式（chat-xxx）
                    if re.match(r"^chat-[a-f0-9-]+$", line):
                        sessions.append({"id": line, "title": line[:16] + "..."})
            
            return sessions
        except FileNotFoundError:
            raise RuntimeError("未找到 cursor 命令，请确认 Cursor CLI 已安装并在 PATH 中")
        except asyncio.TimeoutError:
            logger.warning("列出 Cursor 会话超时")
            return []

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
