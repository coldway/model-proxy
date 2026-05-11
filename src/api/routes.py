# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelConfig,
    ModelInfo,
    ModelListResponse,
    ProviderConfig,
    ProviderDiscovery,
    RateLimit,
    UsageResponse,
    UsageStats,
)
from src.config.catalog import CatalogManager
from src.scheduler.history import RequestHistory

logger = logging.getLogger(__name__)

router = APIRouter()

# 这些全局变量由 app 启动时注入
_config_manager = None
_dispatcher = None
_rate_limiter = None
_history: RequestHistory | None = None
_catalog: CatalogManager | None = None
_provider_factories: dict = {}


def init_routes(config_manager, dispatcher, rate_limiter, history=None, catalog=None, provider_factories=None):
    global _config_manager, _dispatcher, _rate_limiter, _history, _catalog, _provider_factories
    _config_manager = config_manager
    _dispatcher = dispatcher
    _rate_limiter = rate_limiter
    _history = history
    _catalog = catalog
    _provider_factories = provider_factories or {}


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """聊天补全接口（支持流式和非流式）"""
    from src.scheduler.dispatcher import AllModelsUnavailable, ModelNotFound, RateLimitExceeded

    enabled_models = _config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型，请检查配置")

    start_time = time.time()
    try:
        result = await _dispatcher.dispatch(request, enabled_models)
        latency = (time.time() - start_time) * 1000
        if _history:
            _history.record(
                provider=result.model.split("/")[0] if "/" in result.model else "unknown",
                model=result.model,
                success=True,
                latency_ms=latency,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
            )
        return result
    except RateLimitExceeded as e:
        _record_failure(start_time, str(e))
        raise HTTPException(status_code=429, detail=str(e))
    except ModelNotFound as e:
        _record_failure(start_time, str(e))
        raise HTTPException(status_code=404, detail=str(e))
    except AllModelsUnavailable as e:
        _record_failure(start_time, str(e))
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        _record_failure(start_time, str(e))
        logger.error(f"推理请求异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部错误: {e}")


def _record_failure(start_time: float, error: str) -> None:
    if _history:
        latency = (time.time() - start_time) * 1000
        _history.record(provider="unknown", model="unknown", success=False, latency_ms=latency, error=error)


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    """列出所有已配置模型"""
    models = []
    for prov_name, prov in _config_manager.config.providers.items():
        for m in prov.models:
            models.append(ModelInfo(
                id=m.name,
                provider=prov_name,
                enabled=m.enabled,
                priority=m.priority,
                rate_limit=m.rate_limit,
            ))
    return ModelListResponse(models=models)


@router.get("/v1/usage", response_model=UsageResponse)
async def get_usage():
    """获取使用统计"""
    stats = []
    for prov_name, prov in _config_manager.config.providers.items():
        if not prov.enabled:
            continue
        for m in prov.models:
            if not m.enabled:
                continue
            rpd = m.rate_limit.rpd if m.rate_limit else 0
            rpm = m.rate_limit.rpm if m.rate_limit else 0
            daily, minute = _rate_limiter.get_usage(prov_name, m.name)
            stats.append(UsageStats(
                provider=prov_name,
                model=m.name,
                today_requests=daily,
                minute_requests=minute,
                rpd_limit=rpd,
                rpm_limit=rpm,
                available=_rate_limiter.can_request(prov_name, m.name, rpd, rpm),
            ))
    return UsageResponse(stats=stats)


# --- 配置管理 API ---

@router.get("/api/config")
async def get_config():
    """获取当前配置（隐藏 API Key）"""
    result = {}
    for name, prov in _config_manager.get_providers_sorted():
        result[name] = {
            "enabled": prov.enabled,
            "has_api_key": _config_manager.has_api_key(name),
            "priority": prov.priority,
            "models": [m.model_dump() for m in prov.models],
        }
    return {"providers": result, "settings": _config_manager.settings.model_dump()}


@router.post("/api/config/apikey")
async def update_api_key(provider: str, api_key: str):
    """更新厂商 API Key，同时动态注册/注销厂商 Provider"""
    _config_manager.update_api_key(provider, api_key)

    if api_key.strip() and provider in _provider_factories:
        try:
            new_provider = _provider_factories[provider](api_key.strip())
            _dispatcher.register_provider(provider, new_provider)
            logger.info(f"动态注册厂商 {provider}")
            return {"status": "ok", "message": f"{provider} API Key 已更新，厂商已自动加载"}
        except Exception as e:
            logger.error(f"动态注册厂商 {provider} 失败: {e}")
            return {"status": "ok", "message": f"{provider} API Key 已保存，但厂商加载失败: {e}"}
    elif not api_key.strip():
        _dispatcher.unregister_provider(provider)
        logger.info(f"已注销厂商 {provider}（API Key 已清空）")
        return {"status": "ok", "message": f"{provider} API Key 已清空，厂商已卸载"}

    return {"status": "ok", "message": f"{provider} API Key 已更新"}


@router.post("/api/config/model/toggle")
async def toggle_model(provider: str, model_name: str, enabled: bool):
    """启用/禁用模型"""
    _config_manager.toggle_model(provider, model_name, enabled)
    return {"status": "ok", "message": f"{model_name} 已{'启用' if enabled else '禁用'}"}


@router.post("/api/config/model/priority")
async def update_priority(provider: str, model_name: str, priority: int):
    """更新模型优先级"""
    _config_manager.update_model_priority(provider, model_name, priority)
    return {"status": "ok"}


@router.post("/api/config/model/add")
async def add_model(
    provider: str,
    name: str,
    priority: int = 99,
    rpd: int = 0,
    rpm: int = 0,
):
    """添加新模型（先加入目录，再激活）"""
    if _catalog:
        existing = _catalog.get_models(provider)
        if not any(m["id"] == name for m in existing):
            _catalog.add_model(provider, {
                "id": name,
                "name": name,
                "default_rpd": rpd,
                "default_rpm": rpm,
                "category": "通用",
            })
        _catalog.activate_model(provider, name, priority)
    return {"status": "ok", "message": f"模型 {name} 已添加到 {provider}"}


@router.post("/api/provider/toggle")
async def toggle_provider(provider: str, enabled: bool):
    """启用/禁用厂商，同时动态注册/注销 Provider"""
    if _catalog:
        _catalog.set_provider_enabled(provider, enabled)

    if enabled and not _dispatcher.has_provider(provider):
        if provider == "cursor":
            from src.providers.cursor import CursorProvider
            _dispatcher.register_provider("cursor", CursorProvider())
            logger.info(f"动态注册 Cursor 厂商")
            return {"status": "ok", "message": f"{provider} 已启用并加载"}
        elif provider in _provider_factories:
            api_key = _config_manager.get_api_key(provider) if _config_manager else ""
            if api_key.strip():
                try:
                    _dispatcher.register_provider(provider, _provider_factories[provider](api_key))
                    logger.info(f"动态注册厂商 {provider}")
                    return {"status": "ok", "message": f"{provider} 已启用并加载"}
                except Exception as e:
                    logger.error(f"动态注册 {provider} 失败: {e}")
                    return {"status": "ok", "message": f"{provider} 已启用，但加载失败: {e}"}
            else:
                return {"status": "ok", "message": f"{provider} 已启用，但 API Key 未配置，请先填写 API Key"}
    elif not enabled and _dispatcher.has_provider(provider):
        _dispatcher.unregister_provider(provider)
        logger.info(f"已注销厂商 {provider}")
        return {"status": "ok", "message": f"{provider} 已禁用并卸载"}

    return {"status": "ok", "message": f"{provider} 已{'启用' if enabled else '禁用'}"}


@router.post("/api/provider/priority")
async def update_provider_priority(provider: str, priority: int):
    """更新厂商优先级"""
    _config_manager.update_provider_priority(provider, priority)
    return {"status": "ok", "message": f"{provider} 优先级已更新为 {priority}"}


@router.post("/api/provider/reorder")
async def reorder_providers(ordered_ids: list[str]):
    """批量重新排序厂商优先级（用于拖拽排序）"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    _catalog.reorder_providers(ordered_ids)
    return {"status": "ok", "message": "厂商优先级已更新"}


@router.post("/api/settings")
async def update_settings(
    log_level: str | None = None,
    default_provider: str | None = None,
    auto_switch: bool | None = None,
):
    """动态更新运行时设置（host/port 需重启服务）"""
    messages = []
    settings = _config_manager.settings

    if log_level is not None:
        settings.log_level = log_level
        level = getattr(logging, log_level.upper(), logging.INFO)
        logging.getLogger().setLevel(level)
        messages.append(f"日志等级已调整为 {log_level}")

    if default_provider is not None:
        settings.default_provider = default_provider
        messages.append(f"默认厂商已设置为 {default_provider}")

    if auto_switch is not None:
        settings.auto_switch = auto_switch
        messages.append(f"自动切换已{'开启' if auto_switch else '关闭'}")

    if messages:
        _config_manager.save()

    return {"status": "ok", "message": "；".join(messages) if messages else "无变更"}


# --- 模型发现 ---

@router.get("/api/discovery", response_model=list[ProviderDiscovery])
async def discover_providers():
    """查找可免费使用的大模型厂商"""
    discoveries = [
        ProviderDiscovery(
            name="Google AI Studio",
            url="https://aistudio.google.com/",
            description="Google 提供的免费 Gemini 系列模型，有每日请求额度限制",
            free_models=["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemma-4-27b"],
            integration_guide="获取 API Key: https://aistudio.google.com/apikey",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="Groq",
            url="https://console.groq.com/",
            description="Groq 提供的高速推理服务，支持多种开源模型免费调用",
            free_models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"],
            integration_guide="注册后在 https://console.groq.com/keys 获取 API Key",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="GitHub Models",
            url="https://github.com/marketplace/models",
            description="GitHub 提供的模型市场，使用 GitHub Token 即可免费调用",
            free_models=["gpt-4o-mini", "meta-llama-3.1-405b-instruct", "mistral-large"],
            integration_guide="使用 GitHub Personal Access Token，在 Settings > Developer settings 中生成",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="Cerebras",
            url="https://cloud.cerebras.ai/",
            description="Cerebras 提供极速推理（~2000 tokens/s），免费层每日 1000 次请求",
            free_models=["llama-3.3-70b", "llama-3.1-8b", "llama-3.1-70b"],
            integration_guide="注册后在 Dashboard 获取 API Key，兼容 OpenAI SDK，base_url=https://api.cerebras.ai/v1",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="SambaNova",
            url="https://cloud.sambanova.ai/",
            description="SambaNova Cloud 免费推理，速度极快，支持 405B 大模型和 DeepSeek",
            free_models=["Meta-Llama-3.3-70B-Instruct", "Meta-Llama-3.1-405B-Instruct", "DeepSeek-R1", "DeepSeek-V3-0324"],
            integration_guide="注册后在 API 页面获取 Key，接口兼容 OpenAI 格式，base_url=https://api.sambanova.ai/v1",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="OpenRouter",
            url="https://openrouter.ai/",
            description="聚合平台，标注 :free 后缀的模型完全免费，每日约 200 次请求",
            free_models=["meta-llama/llama-3.1-8b-instruct:free", "mistralai/mistral-7b-instruct:free", "qwen/qwen-2.5-72b-instruct:free", "google/gemma-2-9b-it:free"],
            integration_guide="https://openrouter.ai/keys 获取 Key，兼容 OpenAI SDK，base_url=https://openrouter.ai/api/v1",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="Cloudflare Workers AI",
            url="https://ai.cloudflare.com/",
            description="Cloudflare 免费 AI 推理，每日 10,000 neurons（约数千次小请求），无需信用卡",
            free_models=["@cf/meta/llama-3.1-8b-instruct", "@cf/mistral/mistral-7b-instruct-v0.2-lora", "@cf/qwen/qwen1.5-14b-chat-awq"],
            integration_guide="Dashboard > AI > Workers AI，获取 Account ID 和 API Token，接口 REST 格式",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="HuggingFace Inference API",
            url="https://huggingface.co/inference-api",
            description="HuggingFace 免费推理 API，支持数千个开源模型，有速率限制但完全免费",
            free_models=["meta-llama/Llama-3.1-8B-Instruct", "mistralai/Mistral-7B-Instruct-v0.3", "Qwen/Qwen2.5-72B-Instruct", "google/gemma-2-27b-it"],
            integration_guide="https://huggingface.co/settings/tokens 创建 Token，使用 InferenceClient 或 OpenAI 兼容接口",
            new_user_only=False,
        ),
        ProviderDiscovery(
            name="Mistral AI (La Plateforme)",
            url="https://console.mistral.ai/",
            description="Mistral 官方平台免费层，支持 Mistral Small/Nemo/Codestral 等模型",
            free_models=["mistral-small-latest", "open-mistral-nemo", "codestral-latest"],
            integration_guide="https://console.mistral.ai/api-keys/ 获取 Key，兼容 OpenAI SDK，base_url=https://api.mistral.ai/v1",
            new_user_only=False,
        ),
    ]
    return discoveries


@router.get("/api/provider/{provider_name}/models")
async def fetch_provider_models(provider_name: str):
    """拉取厂商最新模型列表"""
    if provider_name not in _dispatcher._providers:
        has_key = _config_manager.has_api_key(provider_name) if _config_manager else False
        if not has_key:
            raise HTTPException(
                status_code=404,
                detail=f"厂商 {provider_name} 未注册：请先在「API Key 配置」中填写 API Key（保存后自动加载）",
            )
        raise HTTPException(
            status_code=404,
            detail=f"厂商 {provider_name} 未注册：API Key 已配置但加载失败，请检查 Key 格式或重启服务",
        )

    provider = _dispatcher._providers[provider_name]
    try:
        models = await provider.list_models()
    except Exception as e:
        logger.error(f"拉取 {provider_name} 模型列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"拉取 {provider_name} 模型列表失败: {e}")
    return {"provider": provider_name, "available_models": models}


# --- 请求历史 ---

@router.get("/api/history")
async def get_history(limit: int = 50):
    """获取最近请求历史"""
    if not _history:
        return {"records": [], "stats": {}}
    return {"records": _history.get_recent(limit), "stats": _history.get_stats()}


@router.get("/api/history/stats")
async def get_history_stats():
    """获取请求统计汇总"""
    if not _history:
        return {"total": 0, "success_rate": 0, "avg_latency_ms": 0, "by_provider": {}}
    return _history.get_stats()


# --- 模型目录（可提交到 GitHub 的部分） ---

@router.get("/api/catalog/providers")
async def catalog_providers():
    """获取目录中所有厂商及其模型"""
    if not _catalog:
        return {"providers": {}}
    return {"providers": _catalog.get_all_providers()}


@router.get("/api/catalog/provider/{provider_id}/models")
async def catalog_provider_models(provider_id: str):
    """获取指定厂商的模型列表"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    models = _catalog.get_models(provider_id)
    return {"provider": provider_id, "models": models}


@router.get("/api/catalog/search")
async def catalog_search(q: str = ""):
    """搜索模型目录（模糊匹配名称、描述、分类）"""
    if not _catalog:
        return {"results": []}
    if not q.strip():
        # 返回全部
        all_models = []
        for prov_id, prov in _catalog.get_all_providers().items():
            for model in prov.get("models", []):
                all_models.append({**model, "provider_id": prov_id, "provider_name": prov.get("name", prov_id)})
        return {"results": all_models}
    return {"results": _catalog.search_models(q)}


@router.post("/api/catalog/model/add")
async def catalog_add_model(
    provider_id: str,
    model_id: str,
    name: str = "",
    description: str = "",
    default_rpd: int = 0,
    default_rpm: int = 0,
    category: str = "通用",
):
    """向目录添加模型（更新 providers_catalog.yaml，可提交 GitHub）"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    model_data = {
        "id": model_id,
        "name": name or model_id,
        "description": description,
        "default_rpd": default_rpd,
        "default_rpm": default_rpm,
        "category": category,
    }
    success = _catalog.add_model(provider_id, model_data)
    if not success:
        raise HTTPException(status_code=400, detail="模型已存在或厂商不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已添加到 {provider_id} 目录"}


@router.delete("/api/catalog/model/delete")
async def catalog_delete_model(provider_id: str, model_id: str):
    """从目录删除模型"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    success = _catalog.remove_model(provider_id, model_id)
    if not success:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已从 {provider_id} 目录删除"}


@router.post("/api/catalog/model/activate")
async def catalog_activate_model(provider_id: str, model_id: str, priority: int = 99):
    """从目录中激活模型（设置 enabled=true）"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")

    if _catalog.activate_model(provider_id, model_id, priority):
        return {"status": "ok", "message": f"模型 {model_id} 已激活"}
    raise HTTPException(status_code=404, detail="模型在目录中不存在")


@router.post("/api/config/model/delete")
async def config_delete_model(provider: str, model_name: str):
    """停用模型（从目录中移除 enabled 标记）"""
    if not _catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    if _catalog.deactivate_model(provider, model_name):
        return {"status": "ok", "message": f"模型 {model_name} 已停用"}
    raise HTTPException(status_code=404, detail=f"模型 {model_name} 不存在")
