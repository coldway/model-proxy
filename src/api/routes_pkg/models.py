# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""模型管理、厂商列表与用量统计路由"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.config.capability_tester import merge_catalog_capabilities
from src.models.schemas import (
    ModelCapabilities,
    ModelDetail,
    ModelInfo,
    ModelListResponse,
    ProviderListResponse,
    ProviderModelsResponse,
    ProviderSummary,
    UsageResponse,
    UsageStats,
)
from src.api.routes_pkg.deps import _deps

router = APIRouter(tags=["models"])


@router.get("/v1/models", response_model=ModelListResponse, summary="列出可用模型", description="返回所有已启用厂商的已启用模型列表，含优先级、限速和能力信息。")
async def list_models():
    """列出所有已启用厂商的已启用模型（含已探测的能力信息）"""
    models = []
    for prov_name, prov in _deps.config_manager.config.providers.items():
        if not prov.enabled:
            continue
        for m in prov.models:
            if not m.enabled:
                continue
            caps = ModelCapabilities()
            cached_raw: dict | None = None
            if _deps.capability_tester:
                cached_raw = _deps.capability_tester.cache.get(prov_name, m.name)
            catalog_model = _deps.catalog.get_model(prov_name, m.name) if _deps.catalog else None
            merged = merge_catalog_capabilities(cached_raw, catalog_model)
            if merged and not merged.get("error"):
                caps = ModelCapabilities(
                    streaming=bool(merged.get("streaming")),
                    reasoning=bool(merged.get("reasoning")),
                    multi_turn_tc=bool(merged.get("multi_turn_tc")),
                    chinese=bool(merged.get("chinese")),
                    vision=bool(merged.get("vision")),
                    json_mode=bool(merged.get("json_mode")),
                    latency_ms=merged.get("latency_ms", 99999),
                )
            models.append(ModelInfo(
                id=m.name,
                provider=prov_name,
                enabled=m.enabled,
                priority=m.priority,
                rate_limit=m.rate_limit,
                tool_calling=bool(merged.get("tool_calling", m.tool_calling)),
                model_type=m.model_type,
                capabilities=caps,
            ))
    return ModelListResponse(models=models)


@router.get("/v1/providers", response_model=ProviderListResponse, summary="列出所有厂商", description="返回系统中配置的所有厂商及其启用状态、已注册模型数量。")
async def list_providers():
    """查询支持的厂商列表"""
    providers = []
    for prov_name, prov in _deps.config_manager.config.providers.items():
        enabled_count = sum(1 for m in prov.models if m.enabled)
        catalog_total = 0
        if _deps.catalog:
            catalog_models = _deps.catalog.get_models(prov_name)
            catalog_total = len(catalog_models)
        providers.append(ProviderSummary(
            id=prov_name,
            enabled=prov.enabled,
            priority=prov.priority,
            model_count=enabled_count,
            total_models=max(catalog_total, len(prov.models)),
            has_api_key=bool(prov.api_key),
        ))
    providers.sort(key=lambda p: (not p.enabled, p.priority))
    return ProviderListResponse(providers=providers)


@router.get("/v1/providers/{provider_id}/models", response_model=ProviderModelsResponse, summary="列出厂商模型", description="返回指定厂商下的所有模型及其配置信息。")
async def list_provider_models(provider_id: str):
    """查询指定厂商支持的模型列表（含能力信息）"""
    prov = _deps.config_manager.config.providers.get(provider_id)
    if not prov:
        raise HTTPException(status_code=404, detail=f"厂商 {provider_id} 不存在")

    cap_cache = {}
    if _deps.capability_tester:
        all_caps = _deps.capability_tester.cache.get_all()
        for key, val in all_caps.items():
            parts = key.split("/", 1)
            if parts[0] == provider_id and len(parts) > 1:
                cap_cache[parts[1]] = val

    models = []
    for m in prov.models:
        cached_raw = cap_cache.get(m.name) or None
        catalog_model = _deps.catalog.get_model(provider_id, m.name) if _deps.catalog else None
        cap = merge_catalog_capabilities(cached_raw, catalog_model)
        models.append(ModelDetail(
            id=m.name,
            provider=provider_id,
            enabled=m.enabled,
            priority=m.priority,
            tool_calling=bool(cap.get("tool_calling", m.tool_calling)),
            model_type=m.model_type,
            rate_limit=m.rate_limit,
            capabilities=cap,
        ))
    models.sort(key=lambda x: (not x.enabled, x.priority))
    return ProviderModelsResponse(provider=provider_id, models=models)


@router.get("/v1/usage", response_model=UsageResponse, summary="用量统计", description="返回各厂商/模型的调用次数与 Token 消耗统计。")
async def get_usage():
    """获取使用统计"""
    stats = []
    for prov_name, prov in _deps.config_manager.config.providers.items():
        if not prov.enabled:
            continue
        for m in prov.models:
            if not m.enabled:
                continue
            rpd = m.rate_limit.rpd if m.rate_limit else 0
            rpm = m.rate_limit.rpm if m.rate_limit else 0
            tpm = getattr(m.rate_limit, "tpm", 0) if m.rate_limit else 0
            tpd = getattr(m.rate_limit, "tpd", 0) if m.rate_limit else 0
            daily, minute, daily_tok, minute_tok = _deps.rate_limiter.get_usage(prov_name, m.name)
            stats.append(UsageStats(
                provider=prov_name,
                model=m.name,
                today_requests=daily,
                minute_requests=minute,
                today_tokens=daily_tok,
                minute_tokens=minute_tok,
                rpd_limit=rpd,
                rpm_limit=rpm,
                tpm_limit=tpm,
                tpd_limit=tpd,
                available=_deps.rate_limiter.can_request(prov_name, m.name, rpd, rpm, tpm, tpd),
            ))
    return UsageResponse(stats=stats)
