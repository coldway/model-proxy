# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

import httpx

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelConfig,
)
from src.config.capability_tester import merge_catalog_capabilities
from src.scheduler.circuit_breaker import CircuitBreaker, should_trigger_breaker
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

# 下列默认值与 AppSettings 中一致，可通过构造参数覆盖
_DEFAULT_ROUTE_CACHE_TTL = 600
_DEFAULT_BREAKER_THRESHOLD = 3
_DEFAULT_BREAKER_COOLDOWN = 300

_route_strategy_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_route_strategy_var", default="",
)


class Dispatcher:
    """模型调度器：根据优先级和可用性选择模型并转发请求

    当 model=auto 时，先用轻量 LLM 分析请求特征，
    智能推荐最适合的模型，再用该模型处理实际请求。
    支持路由缓存和规则快速路径以减少额外 LLM 调用开销。
    """

    # region 初始化与 Provider 注册

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
    ):
        self._rate_limiter = rate_limiter
        self._providers: dict[str, "BaseProvider"] = {}
        self._capability_cache: CapabilityCache | None = capability_cache
        self._catalog: CatalogManager | None = catalog
        self._history = history
        self._payload_tracker = payload_tracker or PayloadTracker()
        self._breaker = CircuitBreaker(threshold=breaker_threshold, cooldown=breaker_cooldown)
        self._route_cache: dict[str, tuple[str, float]] = {}
        self._route_log: deque[dict[str, Any]] = deque(maxlen=ROUTE_LOG_MAX)
        self._session_bindings: dict[str, tuple[str, str, float]] = {}
        self._session_lock = threading.Lock()
        self._route_cache_ttl = route_cache_ttl
        self._session_bind_ttl = session_bind_ttl

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

    def get_route_log(self) -> list[dict[str, Any]]:
        """获取最近的路由决策记录"""
        return list(reversed(self._route_log))

    def _log_route_decision(self, **kwargs: Any) -> None:
        """记录一条路由决策"""
        kwargs["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._route_log.append(kwargs)

    async def close_providers(self) -> None:
        """关闭所有 Provider 的底层连接"""
        for name, prov in self._providers.items():
            try:
                await prov.close()
            except Exception as e:
                logger.warning("关闭 %s Provider 失败: %s", name, e)

    def register_provider(self, name: str, provider: "BaseProvider") -> None:
        self._providers[name] = provider

    async def unregister_provider(self, name: str) -> bool:
        """注销厂商并关闭底层连接，返回是否成功"""
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

    # endregion

    # region 调度入口与非流式

    async def dispatch(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        """
        调度请求到可用模型，返回 (provider_name, model_name, response) 三元组。
        - session_id 有绑定时优先使用绑定的模型
        - model="auto" 时按优先级自动选择
        - 指定模型名时尝试直接路由
        - 失败时自动切换到下一优先级模型
        """
        if not trace_id:
            trace_id = self.generate_trace_id()

        if request.session_id:
            binding = self.get_session_binding(request.session_id)
            if binding:
                prov, model = binding
                logger.info("会话绑定优先: session=%s → %s:%s", request.session_id, prov, model)
                try:
                    rlim = self._rate_limits_for(enabled_models, prov, model)
                    result = await self._call_provider(
                        prov, model, request, trace_id=trace_id, rate_limits=rlim,
                    )
                    _route_strategy_var.set("会话绑定")
                    return prov, model, result
                except Exception as e:
                    logger.warning(
                        "会话绑定模型 %s:%s 失败: %s，清除绑定并降级到正常路由",
                        prov, model, e,
                    )
                    self.clear_session_binding(request.session_id)

        if request.model != "auto":
            _route_strategy_var.set("指定模型")
            result = await self._dispatch_specific(request, enabled_models, trace_id)
            if request.session_id:
                self.bind_session(request.session_id, result[0], result[1])
            return result

        result = await self._dispatch_auto(request, enabled_models, trace_id)
        if request.session_id:
            self.bind_session(request.session_id, result[0], result[1])
        return result

    @property
    def last_route_strategy(self) -> str:
        return _route_strategy_var.get("")

    @property
    def payload_tracker(self) -> PayloadTracker:
        return self._payload_tracker

    # endregion

    # region 会话模型绑定

    _SESSION_BIND_MAX = 10000  # 最大绑定条目数，防止内存无限增长

    def bind_session(self, session_id: str, provider: str, model: str) -> None:
        """绑定 session_id 到指定的 provider/model"""
        with self._session_lock:
            if len(self._session_bindings) >= self._SESSION_BIND_MAX and session_id not in self._session_bindings:
                self._evict_expired_sessions()
                if len(self._session_bindings) >= self._SESSION_BIND_MAX:
                    oldest_sid = min(self._session_bindings, key=lambda k: self._session_bindings[k][2])
                    del self._session_bindings[oldest_sid]
            self._session_bindings[session_id] = (provider, model, time.time())
        logger.info("会话绑定: %s → %s:%s", session_id, provider, model)

    def _evict_expired_sessions(self) -> int:
        """清理过期的会话绑定，返回清理数量"""
        now = time.time()
        expired = [sid for sid, (_, _, ts) in self._session_bindings.items() if now - ts > self._session_bind_ttl]
        for sid in expired:
            del self._session_bindings[sid]
        return len(expired)

    def get_session_binding(self, session_id: str) -> tuple[str, str] | None:
        """获取 session_id 绑定的 (provider, model)，过期返回 None"""
        with self._session_lock:
            if session_id not in self._session_bindings:
                return None
            provider, model, ts = self._session_bindings[session_id]
            if time.time() - ts > self._session_bind_ttl:
                del self._session_bindings[session_id]
                logger.info("会话绑定过期: %s", session_id)
                return None
            return provider, model

    def get_all_session_bindings(self) -> dict[str, dict[str, Any]]:
        """获取所有有效的会话绑定"""
        with self._session_lock:
            return self._get_all_session_bindings_unlocked()

    def _get_all_session_bindings_unlocked(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        result = {}
        expired = []
        for sid, (prov, model, ts) in self._session_bindings.items():
            remaining = self._session_bind_ttl - (now - ts)
            if remaining <= 0:
                expired.append(sid)
                continue
            result[sid] = {
                "provider": prov,
                "model": model,
                "remaining_seconds": round(remaining),
            }
        for sid in expired:
            del self._session_bindings[sid]
        return result

    def clear_session_binding(self, session_id: str = "") -> int:
        """清除会话绑定。返回清除数量。"""
        with self._session_lock:
            if session_id:
                return 1 if self._session_bindings.pop(session_id, None) else 0
            count = len(self._session_bindings)
            self._session_bindings.clear()
            return count

    # endregion

    # region 按模型路由与遍历

    async def _dispatch_specific(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        """路由到指定模型"""
        for provider_name, model_cfg in enabled_models:
            if model_cfg.name == request.model:
                rlim = self._unpack_rate_limit(model_cfg)
                rpd, rpm, tpm, tpd = rlim

                if not self._rate_limiter.can_request(provider_name, model_cfg.name, rpd, rpm, tpm, tpd):
                    raise RateLimitExceeded(
                        f"模型 {model_cfg.name} 已达速率限制，请稍后重试或切换模型"
                    )

                result = await self._call_provider(
                    provider_name, model_cfg.name, request, trace_id=trace_id, rate_limits=rlim,
                )
                return provider_name, model_cfg.name, result

        raise ModelNotFound(f"模型 {request.model} 未找到或未启用")

    async def _dispatch_auto(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ) -> tuple[str, str, ChatCompletionResponse]:
        """智能自动选择模型

        三级路由策略（逐级降级）：
        1. 规则快速路径：当规则排序有明显最优解时直接使用（零开销）
        2. LLM 智能路由：用最快模型分析请求特征，推荐最佳模型（缓存 10 分钟）
        3. 规则遍历回退：LLM 路由失败时按能力评分遍历所有可用模型
        """
        payload_bytes = estimate_payload_bytes(request)
        available = self._filter_available(enabled_models, payload_bytes)
        if not available:
            raise AllModelsUnavailable("所有模型均不可用（配额耗尽或 payload 超出所有模型上限）")

        ordered = self._sort_by_capability(available, request)
        user_hint = self._get_user_hint(request)
        candidate_names = [m.name for _, m in ordered]

        # 策略 1：规则快速路径 — 当最优模型明显领先时跳过 LLM 路由
        if self._can_skip_routing(ordered, request):
            prov_name, model_cfg = ordered[0]
            try:
                rlim = self._unpack_rate_limit(model_cfg)
                result = await self._call_provider(
                    prov_name, model_cfg.name, request, trace_id=trace_id, rate_limits=rlim,
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

        # 策略 2：LLM 智能路由（带缓存）
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
                                prov_name, model_cfg.name, request, trace_id=trace_id, rate_limits=rlim,
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

        # 策略 3：规则遍历回退
        errors: list[str] = []
        for provider_name, model_cfg in ordered:
            try:
                rlim = self._unpack_rate_limit(model_cfg)
                result = await self._call_provider(
                    provider_name, model_cfg.name, request, trace_id=trace_id, rate_limits=rlim,
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

    # endregion

    # region 能力排序与可用性过滤

    @staticmethod
    def _get_user_hint(request: ChatCompletionRequest) -> str:
        """提取最后一条用户消息的前 80 个字符"""
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                return msg.content[:80]
        return ""

    def _can_skip_routing(
        self,
        sorted_models: list[tuple[str, ModelConfig]],
        request: ChatCompletionRequest,
    ) -> bool:
        """判断是否可以跳过 LLM 路由，直接用规则排序的最优模型

        以下场景直接跳过（节省一次 LLM 调用）：
        - 只有 1-3 个可用模型（候选太少，LLM 路由收益不抵开销）
        - 最优模型在所有关键维度上都领先
        - 没有 capability_cache（无法构建有效的路由提示词）
        """
        if len(sorted_models) <= 3:
            return True
        if not self._capability_cache:
            return True

        top_prov, top_model = sorted_models[0]
        top_caps = self._get_caps(top_prov, top_model.name)
        second_prov, second_model = sorted_models[1]
        second_caps = self._get_caps(second_prov, second_model.name)

        needs_tc = bool(request.tools)
        has_chinese = self._detect_chinese(request)

        top_tc = top_caps.get("tool_calling") or top_model.tool_calling
        top_mt = top_caps.get("multi_turn_tc", False)
        top_cn = top_caps.get("chinese", False)

        sec_tc = second_caps.get("tool_calling") or second_model.tool_calling
        sec_mt = second_caps.get("multi_turn_tc", False)

        if needs_tc and top_tc and top_mt and (not has_chinese or top_cn):
            if not sec_tc or not sec_mt:
                return True

        if not needs_tc and not has_chinese:
            top_lat = top_caps.get("latency_ms", 99999)
            sec_lat = second_caps.get("latency_ms", 99999)
            if top_lat < sec_lat * 0.5:
                return True

        return False

    @staticmethod
    def _unpack_rate_limit(model_cfg: ModelConfig) -> tuple[int, int, int, int]:
        rl = model_cfg.rate_limit
        if not rl:
            return 0, 0, 0, 0
        return rl.rpd, rl.rpm, getattr(rl, "tpm", 0), getattr(rl, "tpd", 0)

    @staticmethod
    def _rate_limits_for(
        enabled_models: list[tuple[str, ModelConfig]],
        provider: str,
        model: str,
    ) -> tuple[int, int, int, int]:
        for p, m in enabled_models:
            if p == provider and m.name == model:
                return Dispatcher._unpack_rate_limit(m)
        return 0, 0, 0, 0

    def _filter_available(
        self, models: list[tuple[str, ModelConfig]],
        payload_bytes: int = 0,
    ) -> list[tuple[str, ModelConfig]]:
        """过滤出配额未耗尽且 payload 大小在限制内的模型"""
        result = []
        for prov_name, model_cfg in models:
            rpd, rpm, tpm, tpd = self._unpack_rate_limit(model_cfg)
            if not self._rate_limiter.can_request(prov_name, model_cfg.name, rpd, rpm, tpm, tpd):
                continue
            if payload_bytes and not self._payload_tracker.can_accept(prov_name, model_cfg.name, payload_bytes):
                limit = self._payload_tracker.get_limit(prov_name, model_cfg.name)
                logger.info(
                    "跳过 %s:%s — payload %d bytes ≥ 已知上限 %s bytes",
                    prov_name, model_cfg.name, payload_bytes, limit,
                )
                continue
            result.append((prov_name, model_cfg))
        return result

    def _sort_by_capability(
        self,
        models: list[tuple[str, ModelConfig]],
        request: ChatCompletionRequest,
    ) -> list[tuple[str, ModelConfig]]:
        """根据请求特征、模型能力和运行时健康状况综合排序

        排序优先级（从高到低）：
        1. 健康度：成功率 ≥80% > ≥50% > <50%
        2. 流式匹配：当 request.stream=True 时，支持流式的模型优先
        3. 能力组合：TC+MT+R(6) > TC+MT(5) > TC+R(4) > MT+R(3) > TC(2) > MT|R(1) > 无(0)
        4. 中文匹配：请求含中文时，支持中文的模型优先
        5. 延迟：低延迟优先
        """
        needs_stream = bool(request.stream)
        has_chinese = self._detect_chinese(request)

        health_map = {}
        if self._history and hasattr(self._history, "get_model_health"):
            health_map = self._history.get_model_health(window_minutes=60)

        def score(item: tuple[str, ModelConfig]) -> tuple:
            prov_name, m = item
            caps = self._get_caps(prov_name, m.name)

            stream_ok = 1 if not needs_stream or caps.get("streaming") else 0

            has_tc = bool(caps.get("tool_calling") or m.tool_calling)
            has_mt = bool(caps.get("multi_turn_tc"))
            has_reason = bool(caps.get("reasoning"))

            if has_tc and has_mt and has_reason:
                cap_combo = 6
            elif has_tc and has_mt:
                cap_combo = 5
            elif has_tc and has_reason:
                cap_combo = 4
            elif has_mt and has_reason:
                cap_combo = 3
            elif has_tc:
                cap_combo = 2
            elif has_mt or has_reason:
                cap_combo = 1
            else:
                cap_combo = 0

            cn = 1 if caps.get("chinese") and has_chinese else 0
            latency = caps.get("latency_ms", 99999)

            health_key = f"{prov_name}:{m.name}"
            health = health_map.get(health_key, {})
            success_rate = health.get("success_rate", 1.0)
            health_penalty = 0 if success_rate >= 0.8 else (-1 if success_rate >= 0.5 else -2)

            return (health_penalty, stream_ok, cap_combo, cn, max(0, 30000 - latency))

        _CAP_LABELS = {6: "TC+MT+R", 5: "TC+MT", 4: "TC+R", 3: "MT+R", 2: "TC", 1: "MT|R", 0: "-"}
        scored = [(item, score(item)) for item in models]
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [item for item, _ in scored]
        if len(result) > 1:
            ranking = ", ".join(
                f"{m.name}({_CAP_LABELS.get(s[2], '?')})" for (_, m), s in scored[:5]
            )
            logger.info(
                "[排序] stream=%s cn=%s | 前5: %s",
                needs_stream, has_chinese, ranking,
            )
        return result

    def _get_caps(self, provider: str, model: str) -> dict[str, Any]:
        """从能力缓存读取并与目录（人工标注）合并，目录优先。"""
        cached: dict[str, Any] = {}
        if self._capability_cache:
            entry = self._capability_cache.get(provider, model)
            if entry:
                cached = dict(entry)
        catalog_model = self._catalog.get_model(provider, model) if self._catalog else None
        return merge_catalog_capabilities(cached, catalog_model)

    _RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")

    @staticmethod
    def _detect_chinese(request: ChatCompletionRequest) -> bool:
        """检测请求内容是否包含中文（正则单次扫描，比逐字符循环快 5-10x）"""
        for msg in request.messages:
            if isinstance(msg.content, str) and Dispatcher._RE_CHINESE.search(msg.content):
                return True
        return False

    def _compute_feature_hash(
        self,
        request: ChatCompletionRequest,
        available: list[tuple[str, ModelConfig]],
    ) -> str:
        """将请求特征和可用模型列表哈希化，用于路由缓存 key。

        特征维度：工具集（数量+摘要哈希+schema 体积）、语言、多模态（图像段数与 URL 总长）、
        流式、消息长度桶、可用模型列表。
        消息长度使用对数桶（<1K/1K-4K/4K-16K/16K+）减少 cache miss。
        """
        has_tools = bool(request.tools)
        tool_names = sorted(t.function.name for t in request.tools) if request.tools else []
        if request.tools:
            tools_dump = json.dumps(
                [t.model_dump() for t in request.tools],
                ensure_ascii=False, sort_keys=True,
            )
            tools_sig = (
                f"n={len(request.tools)}|h={hashlib.md5(tools_dump.encode()).hexdigest()[:10]}"
                f"|b={len(tools_dump.encode())}"
            )
        else:
            tools_sig = "n=0"
        has_chinese = self._detect_chinese(request)
        img_parts = 0
        img_url_len = 0
        has_image = False
        for m in request.messages:
            if isinstance(m.content, list):
                for p in m.content:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        has_image = True
                        img_parts += 1
                        iu = p.get("image_url")
                        if isinstance(iu, dict):
                            url = str(iu.get("url") or "")
                        else:
                            url = str(iu or "")
                        img_url_len += len(url)
        total_len = sum(len(m.content) for m in request.messages if isinstance(m.content, str))
        if total_len < 1000:
            len_bucket = "S"
        elif total_len < 4000:
            len_bucket = "M"
        elif total_len < 16000:
            len_bucket = "L"
        else:
            len_bucket = "XL"
        model_set = sorted(m.name for _, m in available)

        feature_str = (
            f"tc={has_tools}|tools={','.join(tool_names[:5])}|tss={tools_sig}"
            f"|cn={has_chinese}|img={has_image}|imgn={img_parts}|imgul={img_url_len}"
            f"|stream={request.stream}|len={len_bucket}"
            f"|models={','.join(model_set)}"
        )
        return hashlib.md5(feature_str.encode()).hexdigest()[:12]

    # region 路由缓存与 LLM 路由

    def _get_cached_route(self, feature_hash: str) -> str | None:
        """查找路由缓存"""
        if feature_hash in self._route_cache:
            model_name, ts = self._route_cache[feature_hash]
            if time.time() - ts < self._route_cache_ttl:
                logger.info("路由缓存命中: %s → %s", feature_hash, model_name)
                return model_name
            del self._route_cache[feature_hash]
        return None

    def _set_cached_route(self, feature_hash: str, model_name: str) -> None:
        """写入路由缓存"""
        if len(self._route_cache) >= ROUTE_CACHE_MAX:
            oldest_key = min(self._route_cache, key=lambda k: self._route_cache[k][1])
            del self._route_cache[oldest_key]
        self._route_cache[feature_hash] = (model_name, time.time())

    def purge_expired_cache(self) -> int:
        """清理所有 TTL 过期的路由缓存条目，返回清理数量"""
        now = time.time()
        expired = [k for k, (_, ts) in self._route_cache.items() if now - ts >= self._route_cache_ttl]
        for k in expired:
            del self._route_cache[k]
        if expired:
            logger.debug("路由缓存过期清理: 删除 %d 条，剩余 %d 条", len(expired), len(self._route_cache))
        return len(expired)

    async def _route_with_llm(
        self,
        request: ChatCompletionRequest,
        available: list[tuple[str, ModelConfig]],
    ) -> str | None:
        """用轻量 LLM 分析请求并推荐最佳模型

        带特征缓存：相同特征（工具集+语言+图像+可用模型列表）的请求
        在 10 分钟内复用路由结果，无需重复调用 LLM。
        """
        if len(available) <= 1:
            return available[0][1].name if available else None

        feature_hash = self._compute_feature_hash(request, available)
        cached = self._get_cached_route(feature_hash)
        if cached:
            valid_names = {m.name for _, m in available}
            if cached in valid_names:
                return cached

        router_prov, router_model = self._pick_router_model(available)
        if not router_prov:
            return None

        model_descriptions = self._build_model_descriptions(available)
        user_summary = self._summarize_request(request)

        routing_prompt = f"""你是一个模型路由器。根据用户的请求特征，从可用模型中选择最合适的一个。
只输出模型名称，不要输出任何其他内容。

## 可用模型及能力
{model_descriptions}

## 用户请求特征
{user_summary}

## 输出
直接输出推荐的模型名称（仅模型名，不含厂商前缀）："""

        try:
            route_request = ChatCompletionRequest(
                model=router_model,
                messages=[ChatMessage(role="user", content=routing_prompt)],
                temperature=0,
                max_tokens=50,
            )
            result = await self._call_provider(
                router_prov, router_model, route_request, timeout=15, is_routing=True,
            )
            recommended = result.choices[0].message.content.strip()
            recommended = recommended.strip("`\"'").split("\n")[0].strip()

            valid_names = {m.name for _, m in available}
            if recommended in valid_names:
                logger.info(
                    "LLM 路由推荐: %s（路由器: %s:%s）",
                    recommended, router_prov, router_model,
                )
                self._set_cached_route(feature_hash, recommended)
                return recommended

            for name in valid_names:
                if recommended in name or name in recommended:
                    logger.info(
                        "LLM 路由推荐（模糊匹配）: %s → %s", recommended, name,
                    )
                    self._set_cached_route(feature_hash, name)
                    return name

            logger.warning("LLM 路由返回无效模型名: %s", recommended)
            return None

        except Exception as e:
            logger.warning("LLM 路由调用失败: %s，回退到规则排序", e)
            return None

    def _pick_router_model(
        self, available: list[tuple[str, ModelConfig]]
    ) -> tuple[str, str]:
        """选择最适合做路由器的模型（低延迟 + 无历史错误）"""
        candidates = []
        for prov_name, model_cfg in available:
            caps = self._get_caps(prov_name, model_cfg.name)
            latency = caps.get("latency_ms") or 99999
            has_error = bool(caps.get("error"))
            candidates.append((has_error, latency, prov_name, model_cfg.name))

        candidates.sort(key=lambda x: (x[0], x[1]))
        if candidates:
            _, lat, prov, model = candidates[0]
            logger.debug("选择路由器模型: %s:%s（延迟 %dms）", prov, model, lat)
            return prov, model
        return "", ""

    def _build_model_descriptions(
        self, available: list[tuple[str, ModelConfig]]
    ) -> str:
        """构建模型能力描述文本供路由 LLM 参考"""
        lines = []
        for prov_name, model_cfg in available:
            caps = self._get_caps(prov_name, model_cfg.name)
            features = []
            if caps.get("tool_calling") or model_cfg.tool_calling:
                features.append("工具调用")
            if caps.get("multi_turn_tc"):
                features.append("多轮对话稳定")
            if caps.get("chinese"):
                features.append("中文友好")
            if caps.get("vision"):
                features.append("图像理解")
            if caps.get("json_mode"):
                features.append("结构化JSON")
            if caps.get("streaming"):
                features.append("流式输出")
            if caps.get("reasoning"):
                features.append("逻辑推理强")
            latency = caps.get("latency_ms")
            lat_str = f"，延迟{latency}ms" if latency else ""
            lines.append(f"- {model_cfg.name}（{prov_name}）：{', '.join(features) or '基础能力'}{lat_str}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_request(request: ChatCompletionRequest) -> str:
        """摘要请求特征"""
        features = []
        if request.tools:
            tool_names = [t.function.name for t in request.tools]
            features.append(f"需要工具调用（{', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}）")
        if request.stream:
            features.append("需要流式输出")

        has_chinese = False
        has_image = False
        msg_count = len(request.messages)
        total_len = 0
        for msg in request.messages:
            if isinstance(msg.content, str):
                total_len += len(msg.content)
                if Dispatcher._RE_CHINESE.search(msg.content):
                    has_chinese = True
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        has_image = True

        if has_chinese:
            features.append("包含中文内容")
        if has_image:
            features.append("包含图像")
        features.append(f"消息数: {msg_count}")
        features.append(f"总文本长度: ~{total_len}字符")

        last_user = ""
        for msg in reversed(request.messages):
            if msg.role == "user" and isinstance(msg.content, str):
                last_user = msg.content[:200]
                break
        if last_user:
            features.append(f"最新用户消息: \"{last_user}\"")

        return "\n".join(f"- {f}" for f in features)

    # endregion

    # region 流式调度

    async def dispatch_stream(
        self,
        request: ChatCompletionRequest,
        enabled_models: list[tuple[str, ModelConfig]],
        trace_id: str = "",
    ):
        """流式调度：返回 (provider_name, model_name, async_iterator) 元组。

        session_id 有绑定时优先使用绑定的模型。
        model=auto 时复用智能路由逻辑（规则快速路径 → LLM路由 → 规则遍历），
        但返回流式迭代器而非完整响应。
        失败时自动尝试下一候选模型，与非流式路径行为一致。
        """
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
                        _route_strategy_var.set("会话绑定")
                        return result
                    except ProviderCallError as e:
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

        payload_bytes = estimate_payload_bytes(request)
        available = self._filter_available(enabled_models, payload_bytes)
        if not available:
            raise AllModelsUnavailable("所有模型均不可用（配额耗尽或 payload 超出所有模型上限）")

        ordered = self._sort_by_capability(available, request)

        def _bind_on_success(result):
            if request.session_id:
                self.bind_session(request.session_id, result[0], result[1])
            return result

        if self._can_skip_routing(ordered, request):
            prov_name, model_cfg = ordered[0]
            try:
                return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id))
            except ProviderCallError as e:
                logger.warning("流式快速路径 %s 失败: %s，继续尝试", model_cfg.name, e)
                ordered = ordered[1:]

        if len(ordered) > 1:
            recommended = await self._route_with_llm(request, ordered)
            if recommended:
                for i, (prov_name, model_cfg) in enumerate(ordered):
                    if model_cfg.name == recommended:
                        try:
                            return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id))
                        except ProviderCallError as e:
                            logger.warning("流式推荐模型 %s 失败: %s，回退遍历", recommended, e)
                            ordered = [x for j, x in enumerate(ordered) if j != i]
                            break

        errors: list[str] = []
        for prov_name, model_cfg in ordered:
            try:
                return _bind_on_success(await self._try_stream(prov_name, model_cfg, request, trace_id=trace_id))
            except ProviderCallError as e:
                errors.append(f"{prov_name}:{model_cfg.name} {e}")
                logger.warning("流式 %s:%s 失败: %s，切换下一模型", prov_name, model_cfg.name, e)

        raise AllModelsUnavailable(f"所有模型均不可用: {'; '.join(errors)}")

    async def _try_stream(self, prov_name: str, model_cfg: ModelConfig, request: ChatCompletionRequest, trace_id: str = ""):
        """创建流式迭代器；仅当流成功结束后才登记 RPD/RPM（与 ``_call_provider`` 一致）

        提前获取第一个 chunk 验证连接是否成功，若连接建立阶段失败
        （如 Google 500）则抛出 ProviderCallError，由 dispatch_stream 重试其他模型。
        首包等待时间取自 ``model_cfg.timeout``（默认 60s）。
        返回的迭代器会捕获流中途的 429 并标记黑名单，
        流式正常结束后原子登记请求与 token 用量。
        """
        if not trace_id:
            trace_id = self.generate_trace_id()

        if self.is_provider_broken(prov_name, model_cfg.name):
            raise ProviderCallError(f"厂商 {prov_name} 模型 {model_cfg.name} 处于熔断状态")
        provider = self._providers.get(prov_name)
        if not provider:
            raise ProviderCallError(f"厂商 {prov_name} 未注册")
        stream_timeout = max(1, int(getattr(model_cfg, "timeout", 60) or 60))
        sid_tag = f" session={request.session_id}" if request.session_id else ""
        msg_summary = self._summarize_messages(request.messages)
        payload_bytes = estimate_payload_bytes(request)
        tools_tag = ""
        if request.tools:
            tnames = [t.function.name for t in request.tools[:5]]
            tools_tag = f" tools=[{','.join(tnames)}]({len(request.tools)}个)"
        params_tag = f" temp={request.temperature}"
        if request.max_tokens:
            params_tag += f" max_tokens={request.max_tokens}"
        logger.info(
            "[流式] trace=%s%s 请求 %s:%s | 消息数=%d payload=%.1fKB%s%s %s",
            trace_id, sid_tag, prov_name, model_cfg.name,
            len(request.messages), payload_bytes / 1024,
            tools_tag, params_tag, msg_summary,
        )
        logger.debug(
            "[流式] trace=%s 请求体摘要: %s",
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
                "[流式] trace=%s %s:%s 首包到达 TTFB=%.0fms",
                trace_id, prov_name, model_cfg.name, ttfb_ms,
            )
        except asyncio.TimeoutError:
            first_chunk_failed = True
            elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
            self._record_provider_failure(prov_name, model_cfg.name)
            logger.error(
                "[流式] trace=%s %s:%s 等待首包超时（%ds, %.0fms）",
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
                        "[流式] trace=%s %s:%s 413 Payload Too Large (%.0fms) | "
                        "请求 payload=%d bytes (%.1f KB) | 模型已知上限=%s",
                        trace_id, prov_name, model_cfg.name, elapsed_ms,
                        _pb, _pb / 1024, _kl_str,
                    )
                    self._payload_tracker.record_413(prov_name, model_cfg.name, _pb)
                else:
                    logger.warning(
                        "[流式] trace=%s %s:%s HTTP %d 客户端错误（不计入熔断, %.0fms）: %s",
                        trace_id, prov_name, model_cfg.name, e.response.status_code, elapsed_ms, e,
                    )
            else:
                self._record_provider_failure(prov_name, model_cfg.name)
                logger.error("[流式] trace=%s %s:%s 连接建立失败（%.0fms）: %s", trace_id, prov_name, model_cfg.name, elapsed_ms, e)
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

            def _accumulate(c: dict | str) -> None:
                if isinstance(c, dict):
                    text_parts.append(c.get("content", ""))
                    r = c.get("reasoning", "") or c.get("reasoning_content", "")
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
                    yield first_chunk
                async for chunk in raw_iter:
                    chunk_count += 1
                    _accumulate(chunk)
                    yield chunk
                record_success(prov_name)
                rate_limiter.clear_429_backoff(prov_name, model_cfg.name)
                if not rate_limiter.try_record_request(
                    prov_name, model_cfg.name, rpd, rpm, tpm, tpd, tokens=0,
                ):
                    logger.warning(
                        "[流式] trace=%s 配额原子登记失败（流已成功完成）%s:%s",
                        trace_id, prov_name, model_cfg.name,
                    )
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                full_text = "".join(text_parts)
                estimated_tokens = len(full_text) // 2
                rate_limiter.record_tokens(prov_name, model_cfg.name, estimated_tokens)

                reasoning_text = "".join(reasoning_parts)

                tc_summary = ""
                if tc_names:
                    parts = []
                    for idx in sorted(tc_names):
                        args_preview = "".join(tc_args.get(idx, []))[:100]
                        parts.append(f"{tc_names[idx]}({args_preview})")
                    tc_summary = f" tool_calls=[{', '.join(parts)}]"

                reasoning_summary = ""
                if reasoning_text:
                    preview = reasoning_text[:200] + ("…" if len(reasoning_text) > 200 else "")
                    reasoning_summary = f" reasoning({len(reasoning_text)}字符)={preview!r}"

                content_preview = ""
                if full_text:
                    preview = full_text[:200].replace("\n", "\\n")
                    content_preview = f" 内容预览={preview!r}"

                logger.info(
                    "[流式] trace=%s 响应完成 %s:%s | 耗时=%.0fms chunks=%d 响应长度=%d%s%s%s",
                    trace_id, prov_name, model_cfg.name,
                    elapsed_ms, chunk_count, len(full_text),
                    tc_summary, reasoning_summary, content_preview,
                )

                debug_response = {"content_length": len(full_text)}
                if tc_names:
                    debug_response["tool_calls_count"] = len(tc_names)
                if reasoning_text:
                    debug_response["reasoning"] = reasoning_text[:500]
                if full_text:
                    debug_response["content_preview"] = full_text[:500]
                logger.debug(
                    "[流式] trace=%s 完整响应详情:\n%s",
                    trace_id,
                    json.dumps(debug_response, ensure_ascii=False, default=str),
                )
            except httpx.HTTPStatusError as e:
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                status = e.response.status_code
                if should_trigger_breaker(status):
                    record_failure(prov_name)
                if status == 429:
                    rate_limiter.mark_429(prov_name, model_cfg.name)
                    logger.warning("[流式] trace=%s %s:%s 收到 429（%.0fms），已加入黑名单", trace_id, prov_name, model_cfg.name, elapsed_ms)
                elif status == 413:
                    _pb = estimate_payload_bytes(request)
                    _kl = payload_tracker.get_limit(prov_name, model_cfg.name)
                    _kl_str = f"{_kl} bytes ({_kl/1024:.1f} KB)" if _kl else "未知"
                    logger.warning(
                        "[流式] trace=%s %s:%s 413 Payload Too Large (%.0fms) | "
                        "请求 payload=%d bytes (%.1f KB) | 模型已知上限=%s",
                        trace_id, prov_name, model_cfg.name, elapsed_ms,
                        _pb, _pb / 1024, _kl_str,
                    )
                    payload_tracker.record_413(prov_name, model_cfg.name, _pb)
                elif not should_trigger_breaker(status):
                    logger.warning("[流式] trace=%s %s:%s HTTP %d 客户端错误（不计入熔断, %.0fms）", trace_id, prov_name, model_cfg.name, status, elapsed_ms)
                else:
                    logger.error("[流式] trace=%s %s:%s HTTP %d（%.0fms）", trace_id, prov_name, model_cfg.name, status, elapsed_ms)
                raise
            except asyncio.CancelledError:
                logger.info("[流式] trace=%s %s:%s 流被取消（不计入熔断）", trace_id, prov_name, model_cfg.name)
                raise
            except Exception as ex:
                elapsed_ms = (time.monotonic_ns() - stream_start) / 1_000_000
                record_failure(prov_name)
                logger.error("[流式] trace=%s %s:%s 异常（%.0fms）: %s", trace_id, prov_name, model_cfg.name, elapsed_ms, ex)
                raise

        return prov_name, model_cfg.name, _guarded_stream()

    # endregion

    @staticmethod
    def _summarize_messages(messages: list) -> str:
        """生成消息摘要：角色分布 + 最后一条用户消息的前 200 字符"""
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
        """脱敏/截断用于 DEBUG 日志的 content（多模态仅记录尺度信息）"""
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
        """与 _messages_to_dicts 相同结构，每条 content 最多记录 max_chars（隐私合规）"""
        raw = Dispatcher._messages_to_dicts(messages)
        out: list[dict] = []
        for d in raw:
            d2 = dict(d)
            d2["content"] = Dispatcher._redact_content_for_log(d2.get("content"), max_chars)
            out.append(d2)
        return out

    @staticmethod
    def _messages_to_dicts(messages: list) -> list[dict]:
        """将 messages 转为可序列化的 dict 列表"""
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

    # region Provider 调用（非流式）

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
    ) -> ChatCompletionResponse:
        """调用具体厂商（含熔断检查 + 超时控制，成功后才原子登记使用量）

        is_routing=True 时为内部路由调用，不消耗正式配额。
        """
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

        start_ns = time.monotonic_ns()
        try:
            result = await asyncio.wait_for(
                provider.chat_completion(model_name, request),
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
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
            if should_trigger_breaker(status):
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
            self._record_provider_failure(provider_name, model_name)
            logger.error("[%s] trace=%s %s:%s 异常(%.0fms): %s", tag, trace_id, provider_name, model_name, elapsed_ms, e)
            raise ProviderCallError(str(e)) from e


    # endregion


# 向后兼容：异常类已移至 src/scheduler/exceptions.py，此处通过 import 重导出
__all__ = [
    "Dispatcher",
    "DispatchError",
    "RateLimitExceeded",
    "ModelNotFound",
    "AllModelsUnavailable",
    "ProviderCallError",
    "PayloadTooLarge",
]
