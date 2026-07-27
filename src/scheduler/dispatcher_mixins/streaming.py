# Created by model-proxy on 2026/05/21
# Copyright © 2026

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from src import LogTag
from src.models.schemas import ChatCompletionRequest, ModelConfig
from src.scheduler.circuit_breaker import should_trigger_breaker
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    ModelNotFound,
    ProviderCallError,
    RateLimitExceeded,
)
from src.scheduler.payload_tracker import estimate_payload_bytes

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _get_route_strategy_var():
    from src.scheduler.dispatcher import _route_strategy_var
    return _route_strategy_var


class StreamingMixin:
    """流式调度相关方法"""

    async def dispatch_stream(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ):
        if not trace_id:
            trace_id = self.generate_trace_id()

        if request.session_id:
            binding = self.get_session_binding(request.session_id)
            if binding:
                prov, model = binding
                logger.info("流式会话绑定优先: session=%s → %s:%s", request.session_id, prov, model)
                candidates = [(p, m) for p, m in enabled_models if p == prov and m.name == model]
                if candidates:
                    try:
                        result = await self._try_stream(candidates[0][0], candidates[0][1], request, trace_id=trace_id)
                        _get_route_strategy_var().set("会话绑定")
                        return result
                    except Exception as e:
                        logger.warning("流式会话绑定 %s:%s 失败: %s，清除绑定并降级到正常路由", prov, model, e)
                        self.clear_session_binding(request.session_id)

        if request.model != "auto":
            candidates = [(p, m) for p, m in enabled_models if m.name == request.model]
            if not candidates:
                raise ModelNotFound(f"模型 {request.model} 未找到或未启用")
            prov_name, model_cfg = candidates[0]
            rpd, rpm, tpm, tpd = self._unpack_rate_limit(model_cfg)
            if not self._rate_limiter.can_request(prov_name, model_cfg.name, rpd, rpm, tpm, tpd):
                raise RateLimitExceeded(f"模型 {model_cfg.name} 已达速率限制")
            result = await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id)
            if request.session_id:
                self.bind_session(request.session_id, result[0], result[1])
            return result

        _payload_bytes = estimate_payload_bytes(request)
        available = self._filter_available(enabled_models, _payload_bytes)
        if not available:
            raise AllModelsUnavailable("所有模型均不可用（配额耗尽或 payload 超出所有模型上限）")

        if request.tools:
            tc_available = [
                (prov, m) for prov, m in available
                if self._model_supports_tool_calling(prov, m)
            ]
            if tc_available:
                available = tc_available
                logger.debug("流式 auto 路由: 请求含 tools，限定为 %d 个 TC 模型", len(available))
            else:
                raise AllModelsUnavailable(
                    "请求含 tools 但无支持 tool_calling 的模型可用（配额耗尽或全部熔断）"
                )

        ordered = self._sort_by_capability(available, request)

        def _bind_on_success(result):
            if request.session_id:
                self.bind_session(request.session_id, result[0], result[1])
            return result

        if self._can_skip_routing(ordered, request):
            prov_name, model_cfg = ordered[0]
            try:
                _get_route_strategy_var().set("规则快速路径")
                return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id, payload_bytes=_payload_bytes))
            except ProviderCallError as e:
                logger.warning("流式快速路径 %s 失败: %s，继续尝试", model_cfg.name, e)
                ordered = ordered[1:]

        if len(ordered) > 1:
            recommended = await self._route_with_llm(request, ordered)
            if recommended:
                for i, (prov_name, model_cfg) in enumerate(ordered):
                    if model_cfg.name == recommended:
                        try:
                            _get_route_strategy_var().set("LLM 智能路由")
                            return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id, payload_bytes=_payload_bytes))
                        except ProviderCallError as e:
                            logger.warning("流式推荐模型 %s 失败: %s，回退遍历", recommended, e)
                            ordered = [x for j, x in enumerate(ordered) if j != i]
                            break

        errors: list[str] = []
        for prov_name, model_cfg in ordered:
            try:
                _get_route_strategy_var().set("规则遍历回退")
                return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id, payload_bytes=_payload_bytes))
            except ProviderCallError as e:
                errors.append(f"{prov_name}:{model_cfg.name} {e}")
                logger.warning("流式 %s:%s 失败: %s，切换下一模型", prov_name, model_cfg.name, e)

        raise AllModelsUnavailable(f"所有模型均不可用: {'; '.join(errors)}")

    async def _try_stream(self, prov_name: str, model_cfg: ModelConfig, request: ChatCompletionRequest, trace_id: str = "", payload_bytes: int = 0):
        if not trace_id:
            trace_id = self.generate_trace_id()

        if self.is_provider_broken(prov_name, model_cfg.name):
            raise ProviderCallError(f"厂商 {prov_name} 模型 {model_cfg.name} 处于熔断状态")
        provider = self._providers.get(prov_name)
        if not provider:
            raise ProviderCallError(f"厂商 {prov_name} 未注册")
        stream_timeout = max(1, int(getattr(model_cfg, "timeout", 0) or 300))
        sid_tag = f" session={request.session_id}" if request.session_id else ""
        msg_summary = self._summarize_messages(request.messages)
        if not payload_bytes:
            payload_bytes = estimate_payload_bytes(request)
        tools_tag = ""
        if request.tools:
            tnames = [t.function.name for t in request.tools]
            tools_tag = f" tools=[{','.join(tnames)}]({len(request.tools)}个)"
        params_tag = f" temp={request.temperature}"
        if request.max_tokens:
            params_tag += f" max_tokens={request.max_tokens}"
        logger.info(
            f"{LogTag.STREAM} trace=%s%s 请求 %s:%s | 消息数=%d payload=%.1fKB%s%s %s",
            trace_id, sid_tag, prov_name, model_cfg.name,
            len(request.messages), payload_bytes / 1024,
            tools_tag, params_tag, msg_summary,
        )
        logger.debug(
            f"{LogTag.STREAM} trace=%s 请求体摘要: %s",
            trace_id,
            json.dumps(
                self._messages_to_dicts_redacted(request.messages),
                ensure_ascii=False, default=str,
            ),
        )

        raw_iter = provider.stream_chat_completion(model_cfg.name, request)
        stream_start = time.monotonic_ns()

        first_chunk = None
        first_chunk_failed = False
        try:
            first_chunk = await asyncio.wait_for(raw_iter.__anext__(), timeout=stream_timeout)
            ttfb_ms = (time.monotonic_ns() - stream_start) / 1_000_000
            logger.info(
                f"{LogTag.STREAM} trace=%s %s:%s 首包到达 TTFB=%.0fms",
                trace_id, prov_name, model_cfg.name, ttfb_ms,
            )
        except asyncio.TimeoutError:
            first_chunk_failed = True
            elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
            self._record_provider_failure(prov_name, model_cfg.name)
            logger.error(
                f"{LogTag.STREAM} trace=%s %s:%s 等待首包超时（%ds, %.0fms）",
                trace_id, prov_name, model_cfg.name, stream_timeout, elapsed_ms,
            )
            raise ProviderCallError(f"流式等待首包超时（{stream_timeout}s）") from None
        except StopAsyncIteration:
            pass
        except Exception as e:
            first_chunk_failed = True
            elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
            if isinstance(e, httpx.HTTPStatusError) and not should_trigger_breaker(e.response.status_code):
                if e.response.status_code == 413:
                    _pb = estimate_payload_bytes(request)
                    _kl = self._payload_tracker.get_limit(prov_name, model_cfg.name)
                    _kl_str = f"{_kl} bytes ({_kl/1024:.1f} KB)" if _kl else "未知"
                    logger.warning(
                        f"{LogTag.STREAM} trace=%s %s:%s 413 Payload Too Large (%.0fms) | "
                        "请求 payload=%d bytes (%.1f KB) | 模型已知上限=%s",
                        trace_id, prov_name, model_cfg.name, elapsed_ms,
                        _pb, _pb / 1024, _kl_str,
                    )
                    self._payload_tracker.record_413(prov_name, model_cfg.name, _pb)
                else:
                    logger.warning(
                        f"{LogTag.STREAM} trace=%s %s:%s HTTP %d 客户端错误（不计入熔断, %.0fms）: %s",
                        trace_id, prov_name, model_cfg.name, e.response.status_code, elapsed_ms, e,
                    )
            else:
                self._record_provider_failure(prov_name, model_cfg.name)
                logger.error(f"{LogTag.STREAM} trace=%s %s:%s 连接建立失败（%.0fms）: %s", trace_id, prov_name, model_cfg.name, elapsed_ms, e)
            raise ProviderCallError(f"流式连接失败: {e}") from e
        finally:
            if first_chunk_failed:
                try:
                    await raw_iter.aclose()
                except Exception:
                    pass

        rate_limiter = self._rate_limiter
        payload_tracker = self._payload_tracker
        def record_failure(pn: str):
            self._record_provider_failure(pn, model_cfg.name)

        def record_success(pn: str):
            self._record_provider_success(pn, model_cfg.name)
        rpd, rpm, tpm, tpd = self._unpack_rate_limit(model_cfg)

        async def _guarded_stream():
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tc_names: dict[int, str] = {}
            tc_args: dict[int, list[str]] = {}
            chunk_count = 0
            stream_usage: dict[str, Any] | None = None

            def _accumulate(c: dict | str) -> None:
                nonlocal stream_usage
                if isinstance(c, dict):
                    if "__usage__" in c:
                        stream_usage = c["__usage__"]
                        return
                    text_parts.append(c.get("content") or "")
                    r = c.get("reasoning") or c.get("reasoning_content") or ""
                    if r:
                        reasoning_parts.append(r)
                    for tc in c.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tc_names[idx] = fn["name"]
                        if fn.get("arguments"):
                            tc_args.setdefault(idx, []).append(fn["arguments"])
                else:
                    text_parts.append(c)

            try:
                if first_chunk is not None:
                    chunk_count += 1
                    _accumulate(first_chunk)
                    if "__usage__" not in (first_chunk if isinstance(first_chunk, dict) else {}):
                        yield first_chunk
                async for chunk in raw_iter:
                    chunk_count += 1
                    _accumulate(chunk)
                    if isinstance(chunk, dict) and "__usage__" in chunk:
                        continue
                    yield chunk
                record_success(prov_name)
                rate_limiter.clear_429_backoff(prov_name, model_cfg.name)

                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0
                is_estimated = True
                if stream_usage:
                    prompt_tokens = stream_usage.get("prompt_tokens", 0)
                    completion_tokens = stream_usage.get("completion_tokens", 0)
                    total_tokens = stream_usage.get("total_tokens", 0) or (prompt_tokens + completion_tokens)
                    is_estimated = False
                else:
                    full_text_for_est = "".join(text_parts)
                    total_tokens = len(full_text_for_est) // 2
                    prompt_tokens = total_tokens // 3
                    completion_tokens = total_tokens - prompt_tokens

                if not rate_limiter.try_record_request(
                    prov_name, model_cfg.name, rpd, rpm, tpm, tpd, tokens=total_tokens,
                ):
                    logger.warning(
                        f"{LogTag.STREAM} trace=%s 配额原子登记失败（流已成功完成）%s:%s",
                        trace_id, prov_name, model_cfg.name,
                    )
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                full_text = "".join(text_parts)
                rate_limiter.record_tokens(prov_name, model_cfg.name, total_tokens)
                try:
                    from src.api.routes_pkg.deps import _deps as _route_deps
                    if _route_deps.cost_tracker:
                        _route_deps.cost_tracker.record(model_cfg.name, prompt_tokens, completion_tokens)
                except Exception:
                    pass

                usage_dict = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
                yield {"__usage__": usage_dict, "__estimated__": is_estimated}

                reasoning_text = "".join(reasoning_parts)

                tc_summary = ""
                if tc_names:
                    parts = []
                    for idx in sorted(tc_names):
                        args_full = "".join(tc_args.get(idx, []))
                        parts.append(f"{tc_names[idx]}({args_full})")
                    tc_summary = f" tool_calls=[{', '.join(parts)}]"

                reasoning_summary = ""
                if reasoning_text:
                    reasoning_summary = f" reasoning({len(reasoning_text)}字符)={reasoning_text!r}"

                content_preview = ""
                if full_text:
                    content_preview = f" 内容预览={full_text!r}"

                tokens_tag = "estimated" if is_estimated else "actual"
                logger.info(
                    f"{LogTag.STREAM} trace=%s 响应完成 %s:%s | 耗时=%.0fms chunks=%d 响应长度=%d"
                    f" tokens(%s)=input:%d+output:%d=%d%s%s%s",
                    trace_id, prov_name, model_cfg.name,
                    elapsed_ms, chunk_count, len(full_text),
                    tokens_tag, prompt_tokens, completion_tokens, total_tokens,
                    tc_summary, reasoning_summary, content_preview,
                )

                debug_response = {"content_length": len(full_text)}
                if tc_names:
                    debug_response["tool_calls_count"] = len(tc_names)
                if reasoning_text:
                    debug_response["reasoning"] = reasoning_text
                if full_text:
                    debug_response["content"] = full_text
                logger.debug(
                    f"{LogTag.STREAM} trace=%s 完整响应详情:\n%s",
                    trace_id,
                    json.dumps(debug_response, ensure_ascii=False, default=str),
                )

                self._feedback_streaming_capabilities(
                    prov_name, model_cfg.name, request,
                    full_text, bool(tc_names),
                )
            except httpx.HTTPStatusError as e:
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                status = e.response.status_code
                if should_trigger_breaker(status):
                    record_failure(prov_name)
                if status == 429:
                    rate_limiter.mark_429(prov_name, model_cfg.name)
                    logger.warning(f"{LogTag.STREAM} trace=%s %s:%s 收到 429（%.0fms），已加入黑名单", trace_id, prov_name, model_cfg.name, elapsed_ms)
                elif status == 413:
                    _pb = estimate_payload_bytes(request)
                    _kl = payload_tracker.get_limit(prov_name, model_cfg.name)
                    _kl_str = f"{_kl} bytes ({_kl/1024:.1f} KB)" if _kl else "未知"
                    logger.warning(
                        f"{LogTag.STREAM} trace=%s %s:%s 413 Payload Too Large (%.0fms) | "
                        "请求 payload=%d bytes (%.1f KB) | 模型已知上限=%s",
                        trace_id, prov_name, model_cfg.name, elapsed_ms,
                        _pb, _pb / 1024, _kl_str,
                    )
                    payload_tracker.record_413(prov_name, model_cfg.name, _pb)
                elif not should_trigger_breaker(status):
                    logger.warning(f"{LogTag.STREAM} trace=%s %s:%s HTTP %d 客户端错误（不计入熔断, %.0fms）", trace_id, prov_name, model_cfg.name, status, elapsed_ms)
                else:
                    logger.error(f"{LogTag.STREAM} trace=%s %s:%s HTTP %d（%.0fms）", trace_id, prov_name, model_cfg.name, status, elapsed_ms)
                raise
            except asyncio.CancelledError:
                logger.info(f"{LogTag.STREAM} trace=%s %s:%s 流被取消（不计入熔断）", trace_id, prov_name, model_cfg.name)
                raise
            except Exception as ex:
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                record_failure(prov_name)
                logger.error(f"{LogTag.STREAM} trace=%s %s:%s 异常（%.0fms）: %s", trace_id, prov_name, model_cfg.name, elapsed_ms, ex)
                raise

        return prov_name, model_cfg.name, _guarded_stream()

    def _feedback_streaming_capabilities(
        self,
        provider_name: str,
        model_name: str,
        request: ChatCompletionRequest,
        full_text: str,
        has_tool_calls: bool,
    ) -> None:
        """根据流式调用结果动态更新能力缓存"""
        cache = getattr(self, "_capability_cache", None)
        if not cache:
            return

        existing = cache.get(provider_name, model_name) or {}
        if existing.get("manual_override"):
            return

        updates: dict = {}

        if not existing.get("streaming"):
            updates["streaming"] = True
            logger.info(
                "[能力反馈] %s/%s 流式调用成功，确认 streaming 能力",
                provider_name, model_name,
            )

        if not existing.get("available"):
            updates["available"] = True

        if has_tool_calls and request.tools and not existing.get("tool_calling"):
            updates["tool_calling"] = True
            logger.info(
                "[能力反馈] %s/%s 流式调用中发现 tool_calling 能力",
                provider_name, model_name,
            )

        if full_text and not existing.get("chinese"):
            has_chinese = sum(1 for c in full_text if "\u4e00" <= c <= "\u9fff") >= 5
            if has_chinese:
                updates["chinese"] = True

        if updates:
            merged = {**existing, **updates}
            cache.set(provider_name, model_name, merged)
            self._cap_dirty = True
            self._maybe_save_capabilities()
