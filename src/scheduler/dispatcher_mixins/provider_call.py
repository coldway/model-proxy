# Created by model-proxy on 2026/05/21
# Copyright © 2026

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from src.models.schemas import ChatCompletionRequest, ChatCompletionResponse
from src.scheduler.circuit_breaker import should_trigger_breaker
from src.scheduler.exceptions import (
    PayloadTooLarge,
    ProviderCallError,
    RateLimitExceeded,
)
from src.scheduler.payload_tracker import estimate_payload_bytes

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ProviderCallMixin:
    """Provider 调用（非流式）与辅助方法"""

    async def _call_provider(
        self,
        provider_name: str,
        model_name: str,
        request: ChatCompletionRequest,
        timeout: int = 60,
        is_routing: bool = False,
        trace_id: str = "",
        *,
        rate_limits: tuple[int, int, int, int] | None = None,
        payload_bytes: int = 0,
    ) -> ChatCompletionResponse:
        if not trace_id:
            trace_id = self.generate_trace_id()

        if self.is_provider_broken(provider_name, model_name):
            raise ProviderCallError(f"厂商 {provider_name} 模型 {model_name} 处于熔断状态，{self._breaker.cooldown}秒后自动恢复")

        provider = self._providers.get(provider_name)
        if not provider:
            raise ProviderCallError(f"厂商 {provider_name} 未注册")

        tag = "路由" if is_routing else "推理"
        sid_tag = f" session={request.session_id}" if request.session_id else ""
        msg_summary = self._summarize_messages(request.messages)
        if not payload_bytes:
            payload_bytes = estimate_payload_bytes(request)
        tools_tag = ""
        if request.tools:
            tnames = [t.function.name for t in request.tools[:5]]
            tools_tag = f" tools=[{','.join(tnames)}]({len(request.tools)}个)"
        params_tag = f" temp={request.temperature}"
        if request.max_tokens:
            params_tag += f" max_tokens={request.max_tokens}"
        logger.info(
            "[%s] trace=%s%s %s请求 %s:%s | 消息数=%d payload=%.1fKB%s%s %s",
            tag, trace_id, sid_tag, tag, provider_name, model_name,
            len(request.messages), payload_bytes / 1024,
            tools_tag, params_tag, msg_summary,
        )
        logger.debug(
            "[%s] trace=%s 请求体摘要: %s",
            tag, trace_id,
            json.dumps(
                self._messages_to_dicts_redacted(request.messages),
                ensure_ascii=False, default=str,
            ),
        )

        _SLOW_REQUEST_THRESHOLD_MS = 10_000

        start_ns = time.monotonic_ns()
        try:
            async with self._request_semaphore:
                result = await asyncio.wait_for(
                    provider.chat_completion(model_name, request),
                    timeout=timeout,
                )
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
            if not is_routing and elapsed_ms > _SLOW_REQUEST_THRESHOLD_MS:
                logger.warning(
                    "[慢请求] trace=%s %s:%s 耗时 %.0fms 超过阈值 %dms",
                    trace_id, provider_name, model_name, elapsed_ms, _SLOW_REQUEST_THRESHOLD_MS,
                )
            total_tokens = result.usage.total_tokens if result.usage else 0
            if not is_routing:
                rpd, rpm, tpm, tpd = rate_limits if rate_limits is not None else (0, 0, 0, 0)
                if not self._rate_limiter.try_record_request(
                    provider_name, model_name, rpd, rpm, tpm, tpd, tokens=total_tokens,
                ):
                    logger.warning(
                        "[%s] trace=%s 配额原子登记失败（仍返回推理结果）%s:%s",
                        tag, trace_id, provider_name, model_name,
                    )
                self._rate_limiter.clear_429_backoff(provider_name, model_name)
                self._payload_tracker.record_success(
                    provider_name, model_name, estimate_payload_bytes(request),
                )
                self._record_provider_success(provider_name, model_name)

            msg_obj = result.choices[0].message if result.choices else None
            reply_content = msg_obj.content if msg_obj else ""
            reply_len = len(reply_content) if reply_content else 0
            finish_reason = result.choices[0].finish_reason if result.choices else None
            usage = result.usage

            tc_summary = ""
            if msg_obj and getattr(msg_obj, "tool_calls", None):
                tc_list = msg_obj.tool_calls
                tc_names = [getattr(tc.function, "name", "?") for tc in tc_list]
                tc_summary = f" tool_calls=[{', '.join(tc_names)}]({len(tc_list)}个)"

            content_preview = ""
            if reply_content:
                preview = reply_content[:200].replace("\n", "\\n")
                content_preview = f" 内容预览={preview!r}"

            logger.info(
                "[%s] trace=%s%s %s响应 %s:%s | 耗时=%.0fms tokens(prompt=%d,completion=%d,total=%d) "
                "响应长度=%d finish_reason=%s%s%s",
                tag, trace_id, sid_tag, tag, provider_name, model_name,
                elapsed_ms,
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
                usage.total_tokens if usage else 0,
                reply_len, finish_reason, tc_summary, content_preview,
            )

            raw_msg_dict = msg_obj.model_dump() if msg_obj and hasattr(msg_obj, "model_dump") else {}
            if raw_msg_dict.get("content") and len(raw_msg_dict["content"]) > 500:
                raw_msg_dict["content"] = raw_msg_dict["content"][:500] + f"…(截断,共{len(reply_content)}字符)"
            logger.debug(
                "[%s] trace=%s 完整响应 message 对象:\n%s",
                tag, trace_id,
                json.dumps(raw_msg_dict, ensure_ascii=False, default=str),
            )
            return result
        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
            if not is_routing:
                self._record_provider_failure(provider_name, model_name)
            logger.error("[%s] trace=%s %s:%s 超时（%ds, 实际%.0fms）", tag, trace_id, provider_name, model_name, timeout, elapsed_ms)
            raise ProviderCallError(f"{provider_name}:{model_name} 请求超时（{timeout}s）")
        except httpx.HTTPStatusError as e:
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
            status = e.response.status_code
            resp_body = ""
            try:
                resp_body = e.response.text[:500]
            except Exception:
                pass
            if should_trigger_breaker(status) and not is_routing:
                self._record_provider_failure(provider_name, model_name)
                logger.error(
                    "[%s] trace=%s %s:%s HTTP %d (%.0fms) 响应: %s",
                    tag, trace_id, provider_name, model_name,
                    status, elapsed_ms, resp_body,
                )
            else:
                logger.warning(
                    "[%s] trace=%s %s:%s HTTP %d (%.0fms) 客户端错误（不计入熔断）: %s",
                    tag, trace_id, provider_name, model_name,
                    status, elapsed_ms, resp_body,
                )
            if status == 429:
                if not is_routing:
                    self._rate_limiter.mark_429(provider_name, model_name)
                raise RateLimitExceeded(
                    f"{provider_name}:{model_name} 厂商返回 429 限流，已加入黑名单（指数退避后自动恢复）"
                ) from e
            if status == 413:
                payload_bytes = estimate_payload_bytes(request)
                known_limit = self._payload_tracker.get_limit(provider_name, model_name)
                limit_str = f"{known_limit} bytes ({known_limit/1024:.1f} KB)" if known_limit else "未知"
                logger.warning(
                    "[%s] trace=%s %s:%s 413 Payload Too Large | "
                    "请求 payload=%d bytes (%.1f KB) | 模型已知上限=%s",
                    tag, trace_id, provider_name, model_name,
                    payload_bytes, payload_bytes / 1024, limit_str,
                )
                self._payload_tracker.record_413(provider_name, model_name, payload_bytes)
                raise PayloadTooLarge(
                    f"{provider_name}:{model_name} 返回 413，payload {payload_bytes} bytes "
                    f"超出模型限制，已记录上限"
                ) from e
            raise ProviderCallError(str(e)) from e
        except asyncio.CancelledError:
            logger.info("[%s] trace=%s %s:%s 请求被取消（不计入熔断）", tag, trace_id, provider_name, model_name)
            raise
        except Exception as e:
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
            if not is_routing:
                self._record_provider_failure(provider_name, model_name)
            logger.error("[%s] trace=%s %s:%s 异常(%.0fms): %s", tag, trace_id, provider_name, model_name, elapsed_ms, e)
            raise ProviderCallError(str(e)) from e

    @staticmethod
    def _summarize_messages(messages: list) -> str:
        roles = {}
        for m in messages:
            role = m.role if hasattr(m, "role") else str(m.get("role", "?"))
            roles[role] = roles.get(role, 0) + 1
        role_str = ", ".join(f"{r}×{c}" for r, c in roles.items())

        last_user = ""
        for m in reversed(messages):
            role = m.role if hasattr(m, "role") else m.get("role", "")
            content = m.content if hasattr(m, "content") else m.get("content", "")
            if role == "user" and isinstance(content, str):
                last_user = content[:200]
                break
        return f"[{role_str}] 最新用户消息: {last_user!r}"

    @staticmethod
    def _redact_content_for_log(content: Any, max_chars: int = 200) -> Any:
        if isinstance(content, str):
            if len(content) <= max_chars:
                return content
            return content[:max_chars] + f"…(截断,共{len(content)}字符)"
        if isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    parts.append(part)
                    continue
                p = dict(part)
                if p.get("type") == "image_url":
                    iu = p.get("image_url")
                    if isinstance(iu, dict) and isinstance(iu.get("url"), str):
                        u = iu["url"]
                        p["image_url"] = {**iu, "url": f"<image_url len={len(u)}>"}
                elif p.get("type") == "text" and isinstance(p.get("text"), str):
                    t = p["text"]
                    if len(t) > max_chars:
                        p["text"] = t[:max_chars] + f"…(截断,共{len(t)}字符)"
                parts.append(p)
            return parts
        return content

    @staticmethod
    def _messages_to_dicts_redacted(messages: list, max_chars: int = 200) -> list[dict]:
        raw = ProviderCallMixin._messages_to_dicts(messages)
        out: list[dict] = []
        for d in raw:
            d2 = dict(d)
            d2["content"] = ProviderCallMixin._redact_content_for_log(d2.get("content"), max_chars)
            out.append(d2)
        return out

    @staticmethod
    def _messages_to_dicts(messages: list) -> list[dict]:
        result = []
        for m in messages:
            if hasattr(m, "model_dump"):
                result.append(m.model_dump())
            elif isinstance(m, dict):
                result.append(m)
            else:
                result.append({"role": str(getattr(m, "role", "?")), "content": str(getattr(m, "content", ""))})
        return result

    def generate_trace_id(self) -> str:
        return uuid.uuid4().hex[:12]
