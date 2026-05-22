# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""ChatPipeline — 统一的会话处理管道

抽取 _do_send_chat 和 _do_stream_chat 的重复逻辑：
- 会话初始化（checkpoint、auto_title、长期记忆注入）
- 上下文构建（context_messages、compact、memory 注入）
- Tool calling 循环
- 成本记录 & 历史记录
- 错误处理与回滚
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src import LogTag
from src.api.routes_pkg.deps import _deps, record_failure
from src.api.thinking import strip_thinking as _strip_thinking
from src.models.schemas import ChatCompletionRequest, ChatMessage
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    ModelNotFound,
    ProviderCallError,
    RateLimitExceeded,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Pipeline 执行结果"""
    reply: str = ""
    provider_name: str = "unknown"
    model_name: str = "unknown"
    tool_calls_log: list[dict] = field(default_factory=list)
    usage: Any = None
    latency_ms: float = 0.0
    thinking: str = ""


class ChatPipeline:
    """统一的会话处理管道，消除 send/stream 两路代码的重复"""

    DEFAULT_MAX_TOOL_ROUNDS = 3

    def __init__(self, session, request: ChatCompletionRequest, trace_id: str, memory_mgr, *, max_tool_rounds: int = 0):
        self.session = session
        self.request = request
        self.trace_id = trace_id
        self.memory_mgr = memory_mgr
        self.max_tool_rounds = max_tool_rounds or self.DEFAULT_MAX_TOOL_ROUNDS
        self._long_term_ctx = ""
        self._start_time = 0.0

    def prepare(self) -> None:
        """设置 checkpoint、添加 user 消息、auto_title、注入长期记忆"""
        session = self.session
        session.set_checkpoint()

        user_msg = self._get_user_msg()
        session.add_message("user", user_msg)

        is_first_message = len(session.messages) == 1
        if is_first_message:
            session.auto_title()
            self._long_term_ctx = self.memory_mgr.on_session_start(session.id, user_msg) or ""
            if self._long_term_ctx:
                logger.info("[Memory] trace=%s 注入长期记忆上下文 (%d chars)", self.trace_id, len(self._long_term_ctx))

    def build_context_request(self, *, stream: bool, include_tools: bool, tools=None, compact_mgr=None) -> ChatCompletionRequest:
        """构建带上下文的请求"""
        session = self.session
        user_msg = self._get_user_msg()

        context_messages = session.get_context_messages()

        if compact_mgr:
            if compact_mgr.monitor.estimate_utilization(context_messages) >= 0.55:
                if hasattr(compact_mgr, 'maybe_compact') and asyncio.iscoroutinefunction(compact_mgr.maybe_compact):
                    pass  # async compaction handled by caller
                else:
                    context_messages, _ = compact_mgr.tool_compactor.compact(context_messages)

        session_memory_ctx = self.memory_mgr.get_context_injection(session.id, user_msg=user_msg)
        full_memory_ctx = "\n\n".join(filter(None, [self._long_term_ctx, session_memory_ctx]))
        if full_memory_ctx and context_messages and context_messages[0].get("role") == "system":
            context_messages[0] = {
                **context_messages[0],
                "content": (context_messages[0]["content"] or "") + "\n\n" + full_memory_ctx,
            }

        return ChatCompletionRequest(
            model=self.request.model or session.model,
            messages=[
                ChatMessage(
                    role=m["role"], content=m["content"],
                    tool_calls=m.get("tool_calls"), tool_call_id=m.get("tool_call_id"), name=m.get("name"),
                )
                for m in context_messages
            ],
            temperature=self.request.temperature,
            max_tokens=self.request.max_tokens,
            stream=stream,
            tools=tools if include_tools else None,
        )

    async def run_tool_loop(self, tools) -> PipelineResult:
        """执行完整的 tool calling 循环，返回最终回复"""
        from src.api.internal_tools import execute_tool
        from src.scheduler.context_manager import AutoCompactManager

        self._start_time = time.time()
        enabled_models = _deps.config_manager.get_enabled_models()
        tool_calls_log = []
        provider_name = "unknown"
        model_name = "unknown"

        compact_mgr = AutoCompactManager(context_window=self.session._max_context_tokens)

        for round_idx in range(self.max_tool_rounds + 1):
            context_messages = self.session.get_context_messages()
            utilization = compact_mgr.monitor.estimate_utilization(context_messages)
            if utilization >= 0.55:
                context_messages = await compact_mgr.maybe_compact(context_messages)

            ctx_request = self.build_context_request(
                stream=False, include_tools=(round_idx < self.max_tool_rounds), tools=tools, compact_mgr=None,
            )

            provider_name, model_name, result = await _deps.dispatcher.dispatch(ctx_request, enabled_models, trace_id=self.trace_id)
            msg = result.choices[0].message if result.choices else None

            if not msg:
                return PipelineResult(reply="⚠️ 模型返回空响应，请重试", provider_name=provider_name, model_name=model_name)

            if msg.tool_calls and round_idx < self.max_tool_rounds:
                tc_data = [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
                self.session.add_message("assistant", msg.content or "", tool_calls=tc_data)

                for tc in msg.tool_calls:
                    logger.info(f"{LogTag.SESSION} trace=%s 调用工具: %s(%s)", self.trace_id, tc.function.name, tc.function.arguments[:100])
                    tool_result = await execute_tool(tc.function.name, tc.function.arguments)
                    tool_calls_log.append({"tool": tc.function.name, "result_len": len(tool_result)})
                    self.session.add_message("tool", tool_result, tool_call_id=tc.id, name=tc.function.name)
                continue

            raw_reply = msg.content or ""
            reply, thinking = _strip_thinking(raw_reply)
            latency = (time.time() - self._start_time) * 1000
            return PipelineResult(
                reply=reply, provider_name=provider_name, model_name=model_name,
                tool_calls_log=tool_calls_log, usage=result.usage, latency_ms=latency, thinking=thinking,
            )

        latency = (time.time() - self._start_time) * 1000
        return PipelineResult(
            reply="⚠️ 工具调用轮次超限，请简化问题重试",
            provider_name=provider_name, model_name=model_name,
            tool_calls_log=tool_calls_log, latency_ms=latency,
        )

    def finalize(self, result: PipelineResult) -> None:
        """记录历史、成本、Memory"""
        user_msg = self._get_user_msg()

        if _deps.history:
            _deps.history.record(
                provider=result.provider_name, model=result.model_name, success=True,
                latency_ms=result.latency_ms,
                prompt_tokens=result.usage.prompt_tokens if result.usage else 0,
                completion_tokens=result.usage.completion_tokens if result.usage else 0,
                route_strategy=_deps.dispatcher.last_route_strategy,
            )
        if _deps.cost_tracker and result.usage:
            _deps.cost_tracker.record(result.model_name, result.usage.prompt_tokens, result.usage.completion_tokens)

        self.session.add_message("assistant", result.reply, model=result.model_name)
        self.session.clear_checkpoint()
        self.memory_mgr.on_turn_complete(self.session.id, user_msg, result.reply)

    async def handle_error(self, e: Exception) -> tuple[int, str]:
        """统一错误处理：回滚 + 记录失败"""
        rolled = self.session.rollback()
        record_failure(self._start_time or time.time(), str(e))
        await asyncio.to_thread(_deps.session_mgr.save)
        logger.warning(f"{LogTag.SESSION} trace=%s session=%s 失败（回滚 %d 条）: %s", self.trace_id, self.session.id, rolled, e)

        if isinstance(e, RateLimitExceeded):
            return 429, "请求过于频繁，请稍后重试"
        if isinstance(e, ModelNotFound):
            return 404, "模型未找到"
        if isinstance(e, AllModelsUnavailable):
            return 503, "所有模型均不可用，请稍后重试"
        if isinstance(e, ProviderCallError):
            return 503, "模型服务暂时不可用，请稍后重试"
        return 500, "推理服务内部错误，请稍后重试"

    def _get_user_msg(self) -> str:
        if self.request.messages:
            return self.request.messages[-1].content or ""
        return ""
