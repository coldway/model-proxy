# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""历史记录、路由日志、熔断、黑名单、会话绑定与 Payload 管理路由"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.routes_pkg.deps import _deps

router = APIRouter()


@router.get("/api/history")
async def get_history(limit: int = 50):
    """获取最近请求历史"""
    cap = 2000
    if _deps.config_manager:
        cap = max(1, int(getattr(_deps.config_manager.settings, "request_history_max_records", cap)))
    limit = min(max(limit, 1), cap)
    if not _deps.history:
        return {"records": [], "stats": {}}
    return {"records": _deps.history.get_recent(limit), "stats": _deps.history.get_stats()}


@router.get("/api/history/stats")
async def get_history_stats():
    """获取请求统计汇总"""
    if not _deps.history:
        return {"total": 0, "success_rate": 0, "avg_latency_ms": 0, "by_provider": {}}
    return _deps.history.get_stats()


@router.get("/api/routing/log")
async def get_routing_log():
    """获取最近的路由决策日志"""
    return {"decisions": _deps.dispatcher.get_route_log() if _deps.dispatcher else []}


@router.get("/api/routing/breaker")
async def get_breaker_status():
    """获取厂商熔断状态"""
    return {"breakers": _deps.dispatcher.get_breaker_status() if _deps.dispatcher else {}}


@router.post("/api/breaker/reset")
async def reset_breaker(provider: str = ""):
    """手动重置熔断状态"""
    if not _deps.dispatcher:
        return {"status": "error", "message": "dispatcher 未初始化"}
    count = _deps.dispatcher.clear_breaker(provider)
    return {"status": "ok", "cleared": count}


@router.get("/api/blacklist")
async def get_blacklist():
    """获取 429 黑名单"""
    bl = _deps.rate_limiter.get_blacklist()
    return {
        "blacklist": [
            {"model": k, "expire_time": v["expire_time"], "remaining_seconds": v["remaining_seconds"]}
            for k, v in bl.items()
        ],
        "count": len(bl),
    }


@router.delete("/api/blacklist/clear")
async def clear_blacklist(provider: str = "", model: str = ""):
    """清除 429 黑名单"""
    count = _deps.rate_limiter.clear_blacklist(provider, model)
    return {"status": "ok", "cleared": count}


@router.get("/api/session-bindings")
async def get_session_bindings():
    """获取所有活跃的会话-模型绑定"""
    bindings = _deps.dispatcher.get_all_session_bindings()
    return {"bindings": bindings, "count": len(bindings)}


@router.delete("/api/session-bindings/clear")
async def clear_session_bindings(session_id: str = ""):
    """清除会话绑定"""
    count = _deps.dispatcher.clear_session_binding(session_id)
    return {"status": "ok", "cleared": count}


@router.get("/api/payload-limits")
async def get_payload_limits():
    """获取所有模型的 payload 上限记录"""
    limits = _deps.dispatcher.payload_tracker.get_all_limits()
    return {"limits": limits, "count": len(limits)}


@router.delete("/api/payload-limits/clear")
async def clear_payload_limits(provider: str = "", model: str = ""):
    """清除 payload 上限记录"""
    count = _deps.dispatcher.payload_tracker.clear(provider, model)
    return {"status": "ok", "cleared": count}
