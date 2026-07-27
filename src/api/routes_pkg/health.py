# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""健康探针 + Prometheus 指标路由"""

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from src.api.routes_pkg.deps import _deps

router = APIRouter(tags=["system"])

_PROCESS_START_TIME = time.time()


@router.get("/health", summary="存活探针")
async def health_probe():
    """进程存活探针（负载均衡 / k8s liveness）"""
    return {"status": "ok"}


@router.get("/ready", summary="就绪探针")
async def ready_probe():
    """就绪探针：配置中至少有一个 provider 填写了 API Key"""
    if not _deps.config_manager:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "配置未初始化"})
    configured = any(
        bool(str(getattr(p, "api_key", "")).strip())
        for p in _deps.config_manager.config.providers.values()
    )
    if not configured:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "未配置任何厂商 API Key"},
        )
    return {"ready": True}


@router.get("/metrics", summary="Prometheus 指标")
async def prometheus_metrics():
    """Prometheus text format 指标导出"""
    lines: list[str] = []

    if _deps.history:
        stats = _deps.history.get_stats()
        lines.append("# HELP model_proxy_requests_total Total number of inference requests")
        lines.append("# TYPE model_proxy_requests_total counter")
        lines.append(f"model_proxy_requests_total {stats.get('total', 0)}")

        total = stats.get("total", 0)
        success_rate = stats.get("success_rate", 0)
        success_count = int(total * success_rate / 100) if total else 0
        lines.append("# HELP model_proxy_requests_success_total Successful requests")
        lines.append("# TYPE model_proxy_requests_success_total counter")
        lines.append(f"model_proxy_requests_success_total {success_count}")

        lines.append("# HELP model_proxy_latency_avg_ms Average latency in milliseconds")
        lines.append("# TYPE model_proxy_latency_avg_ms gauge")
        lines.append(f"model_proxy_latency_avg_ms {stats.get('avg_latency_ms', 0)}")

        for prov, pdata in stats.get("by_provider", {}).items():
            lines.append(f'model_proxy_provider_requests_total{{provider="{prov}"}} {pdata.get("total", 0)}')
            lines.append(f'model_proxy_provider_latency_avg_ms{{provider="{prov}"}} {pdata.get("avg_latency_ms", 0)}')

        by_model_tokens = stats.get("by_model_tokens", {})
        if by_model_tokens:
            lines.append("# HELP model_proxy_model_tokens_total Token usage per model")
            lines.append("# TYPE model_proxy_model_tokens_total counter")
            for model_key, token_data in by_model_tokens.items():
                lines.append(f'model_proxy_model_tokens_total{{model="{model_key}",type="prompt"}} {token_data.get("prompt", 0)}')
                lines.append(f'model_proxy_model_tokens_total{{model="{model_key}",type="completion"}} {token_data.get("completion", 0)}')

    if _deps.dispatcher:
        breaker_status = _deps.dispatcher.get_breaker_status()
        lines.append("# HELP model_proxy_breaker_open Number of open circuit breakers")
        lines.append("# TYPE model_proxy_breaker_open gauge")
        lines.append(f"model_proxy_breaker_open {len(breaker_status)}")

    if _deps.rate_limiter:
        bl = _deps.rate_limiter.get_blacklist()
        lines.append("# HELP model_proxy_blacklist_active Number of 429-blacklisted models")
        lines.append("# TYPE model_proxy_blacklist_active gauge")
        lines.append(f"model_proxy_blacklist_active {len(bl)}")

    if _deps.cost_tracker:
        cost_stats = _deps.cost_tracker.get_stats()
        lines.append("# HELP model_proxy_cost_usd_total Monthly cost in USD")
        lines.append("# TYPE model_proxy_cost_usd_total gauge")
        lines.append(f"model_proxy_cost_usd_total {cost_stats.get('total_cost_usd', 0)}")

    lines.append("# HELP model_proxy_process_start_time_seconds Unix timestamp of process start")
    lines.append("# TYPE model_proxy_process_start_time_seconds gauge")
    lines.append(f"model_proxy_process_start_time_seconds {_PROCESS_START_TIME:.0f}")
    lines.append("# HELP model_proxy_uptime_seconds Process uptime in seconds")
    lines.append("# TYPE model_proxy_uptime_seconds gauge")
    lines.append(f"model_proxy_uptime_seconds {time.time() - _PROCESS_START_TIME:.0f}")

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
