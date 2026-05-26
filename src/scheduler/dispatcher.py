# Created by model-proxy on 2026/05/11
# Copyright © 2026

"""模型调度器 — 核心入口类

方法实现已按职责拆分到 dispatcher_mixins/ 子模块：
- session_binding.py: 会话模型绑定持久化
- filtering.py: 能力排序与可用性过滤
- routing.py: 路由缓存与 LLM 路由
- streaming.py: 流式调度
- provider_call.py: Provider 调用与日志辅助
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Any

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelConfig,
)
from src.scheduler.circuit_breaker import CircuitBreaker
from src.scheduler.dispatcher_mixins import (
    FilteringMixin,
    ProviderCallMixin,
    RoutingMixin,
    SessionBindingMixin,
    StreamingMixin,
)
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    DispatchError,
    ModelNotFound,
    PayloadTooLarge,
    ProviderCallError,
    RateLimitExceeded,
)
from src.scheduler.payload_tracker import PayloadTracker, estimate_payload_bytes
from src.scheduler.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from src.config.capability_tester import CapabilityCache
    from src.config.catalog import CatalogManager
    from src.providers.base import BaseProvider

logger = logging.getLogger(__name__)

ROUTE_CACHE_MAX = 128
ROUTE_LOG_MAX = 50

_DEFAULT_ROUTE_CACHE_TTL = 600
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN = 300

_route_strategy_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_route_strategy_var", default="",
)


class Dispatcher(
    SessionBindingMixin,
    FilteringMixin,
    RoutingMixin,
    StreamingMixin,
    ProviderCallMixin,
):
    """模型调度器：根据优先级和可用性选择模型并转发请求

    当 model=auto 时，先用轻量 LLM 分析请求特征，
    智能推荐最适合的模型，再用该模型处理实际请求。
    支持路由缓存和规则快速路径以减少额外 LLM 调用开销。
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        capability_cache: "CapabilityCache | None" = None,
        history: Any = None,
        catalog: "CatalogManager | None" = None,
        *,
        route_cache_ttl: int = _DEFAULT_ROUTE_CACHE_TTL,
        breaker_threshold: int = _DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown: int = _DEFAULT_BREAKER_COOLDOWN,
        payload_tracker: PayloadTracker | None = None,
        session_bind_ttl: int = 3600,
        route_llm_timeout: int = 10,
        max_concurrent_requests: int = 50,
    ):
        self._rate_limiter = rate_limiter
        self._providers: dict[str, "BaseProvider"] = {}
        self._capability_cache: CapabilityCache | None = capability_cache
        self._catalog: CatalogManager | None = catalog
        self._history = history
        self._payload_tracker = payload_tracker or PayloadTracker()
        self._breaker = CircuitBreaker(threshold=breaker_threshold, cooldown=breaker_cooldown)
        self._route_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._route_cache_lock = threading.Lock()
        self._route_log: deque[dict[str, Any]] = deque(maxlen=ROUTE_LOG_MAX)
        self._route_hit_counter: dict[str, int] = {}
        self._route_hit_threshold = 5
        self._session_bindings: dict[str, tuple[str, str, float]] = {}
        self._session_lock = threading.Lock()
        self._bind_save_timer: threading.Timer | None = None
        self._route_cache_ttl = route_cache_ttl
        self._session_bind_ttl = session_bind_ttl
        self._route_llm_timeout = route_llm_timeout
        self._request_semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._last_healthy_provider: str = ""
        self._load_session_bindings()

    @property
    def last_route_strategy(self) -> str:
        return _route_strategy_var.get("")

    @property
    def payload_tracker(self) -> PayloadTracker:
        return self._payload_tracker

    # --- 熔断相关 ---

    def _record_provider_failure(self, provider_name: str, model_name: str | None = None) -> None:
        self._breaker.record_failure(provider_name, model_name)

    def _record_provider_success(self, provider_name: str, model_name: str | None = None) -> None:
        self._breaker.record_success(provider_name, model_name)

    def is_provider_broken(self, provider_name: str, model_name: str | None = None) -> bool:
        return self._breaker.is_open(provider_name, model_name)

    def get_breaker_status(self) -> dict[str, Any]:
        return self._breaker.get_status()

    def clear_breaker(self, provider_name: str = "") -> int:
        return self._breaker.clear(provider_name)

    # --- 路由日志 ---

    def get_route_log(self) -> list[dict[str, Any]]:
        return list(reversed(self._route_log))

    def _log_route_decision(self, **kwargs: Any) -> None:
        kwargs["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._route_log.append(kwargs)

    # --- Provider 注册 ---

    async def close_providers(self) -> None:
        for name, prov in self._providers.items():
            try:
                await prov.close()
            except Exception as e:
                logger.warning("关闭 %s Provider 失败: %s", name, e)

    def register_provider(self, name: str, provider: "BaseProvider") -> None:
        self._providers[name] = provider

    async def unregister_provider(self, name: str) -> bool:
        provider = self._providers.pop(name, None)
        if provider is None:
            return False
        try:
            await provider.close()
        except Exception as e:
            logger.warning("关闭 %s Provider 失败: %s", name, e)
        return True

    def has_provider(self, name: str) -> bool:
        return name in self._providers

    def get_provider(self, name: str) -> "BaseProvider | None":
        return self._providers.get(name)

    def get_all_providers(self) -> dict[str, "BaseProvider"]:
        return dict(self._providers)

    @property
    def provider_names(self) -> list[str]:
        return list(self._providers.keys())

    # --- 非流式调度入口 ---

    async def dispatch(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        """调度请求到可用模型，返回 (provider_name, model_name, response)"""
        if not trace_id:
            trace_id = self.generate_trace_id()

        if request.session_id:
            binding = self.get_session_binding(request.session_id)
            if binding:
                prov, model = binding
                model_still_enabled = any(
                    p == prov and m.name == model for p, m in enabled_models
                )
                if not model_still_enabled or self._breaker.is_open(prov, model):
                    logger.info(
                        "会话绑定过期(模型不可用): session=%s %s:%s enabled=%s broken=%s",
                        request.session_id, prov, model, model_still_enabled,
                        self._breaker.is_open(prov, model),
                    )
                else:
                    try:
                        rlim = self._rate_limits_for(enabled_models, prov, model)
                        model_timeout = next(
                            (m.timeout for p, m in enabled_models if p == prov and m.name == model),
                            60,
                        )
                        result = await self._call_provider(
                            prov, model, request,
                            timeout=model_timeout, trace_id=trace_id, rate_limits=rlim,
                        )
                        return prov, model, result
                    except Exception as e:
                        logger.warning("会话绑定调用失败: %s:%s %s", prov, model, e)

        if request.model and request.model != "auto":
            return await self._dispatch_specific(request, enabled_models, trace_id)
        return await self._dispatch_auto(request, enabled_models, trace_id)

    async def _dispatch_specific(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        for provider_name, model_cfg in enabled_models:
            if model_cfg.name == request.model:
                rlim = self._unpack_rate_limit(model_cfg)
                rpd, rpm, tpm, tpd = rlim

                if not self._rate_limiter.can_request(provider_name, model_cfg.name, rpd, rpm, tpm, tpd):
                    raise RateLimitExceeded(
                        f"模型 {model_cfg.name} 已达速率限制，请稍后重试或切换模型"
                    )

                result = await self._call_provider(
                    provider_name, model_cfg.name, request,
                    timeout=model_cfg.timeout, trace_id=trace_id, rate_limits=rlim,
                )
                return provider_name, model_cfg.name, result

        raise ModelNotFound(f"模型 {request.model} 未找到或未启用")

    async def _dispatch_auto(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        """智能自动选择模型（三级路由策略）"""
        _payload_bytes = estimate_payload_bytes(request)
        available = self._filter_available(enabled_models, _payload_bytes)
        if not available:
            raise AllModelsUnavailable("所有模型均不可用（配额耗尽或 payload 超出所有模型上限）")

        has_image = any(
            isinstance(m.content, list) and any(
                isinstance(p, dict) and p.get("type") == "image_url" for p in m.content
            )
            for m in request.messages
        )
        if has_image:
            vision_available = [
                (prov, m) for prov, m in available
                if self._model_supports_vision(prov, m)
            ]
            if vision_available:
                available = vision_available
                logger.debug("auto 路由: 请求含图片，限定为 %d 个 vision 模型", len(available))
            else:
                logger.warning("auto 路由: 请求含图片但无模型支持 vision，使用全部候选兜底")

        if request.tools:
            tc_available = [
                (prov, m) for prov, m in available
                if self._model_supports_tool_calling(prov, m)
            ]
            if tc_available:
                available = tc_available
                logger.debug("auto 路由: 请求含 tools，限定为 %d 个 TC 模型", len(available))
            else:
                raise AllModelsUnavailable(
                    "请求含 tools 但无支持 tool_calling 的模型可用（配额耗尽或全部熔断）"
                )

        ordered = self._sort_by_capability(available, request)
        user_hint = self._get_user_hint(request)
        candidate_names = [m.name for _, m in ordered]

        if self._can_skip_routing(ordered, request):
            prov_name, model_cfg = ordered[0]
            try:
                rlim = self._unpack_rate_limit(model_cfg)
                result = await self._call_provider(
                    prov_name, model_cfg.name, request,
                    timeout=model_cfg.timeout, trace_id=trace_id,
                    rate_limits=rlim, payload_bytes=_payload_bytes,
                )
                _route_strategy_var.set("规则快速路径")
                logger.info("规则快速路径: %s:%s", prov_name, model_cfg.name)
                self._log_route_decision(
                    strategy="规则快速路径",
                    selected=f"{prov_name}:{model_cfg.name}",
                    candidates=candidate_names,
                    user_hint=user_hint,
                    cached=False,
                )
                return prov_name, model_cfg.name, result
            except (RateLimitExceeded, ProviderCallError, PayloadTooLarge) as e:
                logger.warning("快速路径 %s 失败: %s，继续尝试", model_cfg.name, e)
                ordered = ordered[1:]

        if len(ordered) > 1:
            recommended = await self._route_with_llm(request, ordered)
            if recommended:
                feature_hash = self._compute_feature_hash(request, ordered)
                was_cached = self._get_cached_route(feature_hash) is not None
                for i, (prov_name, model_cfg) in enumerate(ordered):
                    if model_cfg.name == recommended:
                        try:
                            rlim = self._unpack_rate_limit(model_cfg)
                            result = await self._call_provider(
                                prov_name, model_cfg.name, request,
                                timeout=model_cfg.timeout, trace_id=trace_id,
                                rate_limits=rlim, payload_bytes=_payload_bytes,
                            )
                            strategy_name = "LLM 智能路由" + ("（缓存）" if was_cached else "")
                            _route_strategy_var.set(strategy_name)
                            logger.info("LLM 路由选择 %s:%s 成功", prov_name, model_cfg.name)
                            self._log_route_decision(
                                strategy=strategy_name,
                                selected=f"{prov_name}:{model_cfg.name}",
                                candidates=candidate_names,
                                user_hint=user_hint,
                                cached=was_cached,
                            )
                            return prov_name, model_cfg.name, result
                        except (RateLimitExceeded, ProviderCallError, PayloadTooLarge) as e:
                            logger.warning("推荐模型 %s 失败: %s，回退遍历", recommended, e)
                            ordered = [x for j, x in enumerate(ordered) if j != i]
                            break

        errors: list[str] = []
        for provider_name, model_cfg in ordered:
            try:
                rlim = self._unpack_rate_limit(model_cfg)
                result = await self._call_provider(
                    provider_name, model_cfg.name, request,
                    timeout=model_cfg.timeout, trace_id=trace_id,
                    rate_limits=rlim, payload_bytes=_payload_bytes,
                )
                _route_strategy_var.set("规则遍历回退")
                self._log_route_decision(
                    strategy="规则遍历回退",
                    selected=f"{provider_name}:{model_cfg.name}",
                    candidates=candidate_names,
                    user_hint=user_hint,
                    cached=False,
                )
                return provider_name, model_cfg.name, result
            except RateLimitExceeded:
                errors.append(f"{provider_name}:{model_cfg.name} 限流")
                logger.warning("%s:%s 限流，切换下一模型", provider_name, model_cfg.name)
            except PayloadTooLarge as e:
                errors.append(f"{provider_name}:{model_cfg.name} payload 过大")
                logger.warning("%s:%s payload 过大: %s，切换下一模型", provider_name, model_cfg.name, e)
            except ProviderCallError as e:
                errors.append(f"{provider_name}:{model_cfg.name} 调用失败: {e}")

        raise AllModelsUnavailable(f"所有模型均不可用: {'; '.join(errors)}")


__all__ = [
    "Dispatcher",
    "DispatchError",
    "RateLimitExceeded",
    "ModelNotFound",
    "AllModelsUnavailable",
    "ProviderCallError",
    "PayloadTooLarge",
]
