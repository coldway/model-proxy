# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""路由依赖容器 & 公共工具函数"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException

from src.config.catalog import CatalogManager
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    ModelNotFound,
    PayloadTooLarge,
    ProviderCallError,
    RateLimitExceeded,
)
from src.scheduler.history import RequestHistory
from src.scheduler.session import SessionManager

logger = logging.getLogger(__name__)


class _RouteDeps:
    """路由依赖容器（单进程模式，由 init_routes 一次性注入）"""
    config_manager = None
    dispatcher = None
    rate_limiter = None
    history: RequestHistory | None = None
    catalog: CatalogManager | None = None
    provider_factories: dict = {}
    capability_tester = None
    session_mgr: SessionManager | None = None
    cost_tracker = None


_deps = _RouteDeps()


def init_routes(config_manager, dispatcher, rate_limiter, history=None, catalog=None, provider_factories=None, capability_tester=None):
    from src.scheduler.cost_tracker import CostTracker

    _deps.config_manager = config_manager
    _deps.dispatcher = dispatcher
    _deps.rate_limiter = rate_limiter
    _deps.history = history
    _deps.catalog = catalog
    _deps.provider_factories = provider_factories or {}
    _deps.capability_tester = capability_tester
    settings = config_manager.settings
    _deps.session_mgr = SessionManager(
        max_context_tokens=settings.max_context_tokens,
        max_sessions=settings.max_sessions,
    )
    _deps.cost_tracker = CostTracker(
        budget_limit_usd=getattr(settings, "monthly_budget_usd", 0.0),
    )


def record_failure(start_time: float, error: str, provider: str = "unknown", model: str = "unknown") -> None:
    if _deps.history:
        latency = (time.time() - start_time) * 1000
        safe_error = error[:200] if error else ""
        _deps.history.record(provider=provider, model=model, success=False, latency_ms=latency, error=safe_error)


def map_dispatch_error(e: Exception, trace_id: str = "") -> HTTPException:
    """将 Dispatcher 异常统一映射为 HTTPException"""
    if isinstance(e, PayloadTooLarge):
        logger.warning("[API] trace=%s payload 过大: %s", trace_id, e)
        return HTTPException(status_code=413, detail=str(e))
    if isinstance(e, RateLimitExceeded):
        logger.warning("[API] trace=%s 限流: %s", trace_id, e)
        return HTTPException(status_code=429, detail=str(e))
    if isinstance(e, ModelNotFound):
        logger.warning("[API] trace=%s 模型未找到: %s", trace_id, e)
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, AllModelsUnavailable):
        logger.error("[API] trace=%s 所有模型不可用: %s", trace_id, e)
        return HTTPException(status_code=503, detail="所有模型均不可用，请稍后重试")
    if isinstance(e, ProviderCallError):
        logger.warning("[API] trace=%s 厂商调用失败（可恢复）: %s", trace_id, e)
        return HTTPException(status_code=503, detail="模型服务暂时不可用，请稍后重试")
    logger.error("[API] trace=%s 推理请求异常: %s", trace_id, e, exc_info=True)
    return HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")
