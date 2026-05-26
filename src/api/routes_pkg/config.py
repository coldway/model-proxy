# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""配置管理、厂商操作、能力测试与发现路由"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from src.models.schemas import ProviderDiscovery
from src.api.routes_pkg.deps import _deps

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 配置管理 API ---

@router.get("/api/config")
async def get_config():
    """获取当前配置（隐藏 API Key）"""
    result = {}
    for name, prov in _deps.config_manager.get_providers_sorted():
        result[name] = {
            "enabled": prov.enabled,
            "has_api_key": _deps.config_manager.has_api_key(name),
            "priority": prov.priority,
            "models": [m.model_dump() for m in prov.models],
        }
    _SECRET_KEYS = frozenset({"admin_token", "api_token"})
    safe_settings = _deps.config_manager.settings.model_dump(exclude=_SECRET_KEYS)
    for key in _SECRET_KEYS:
        val = getattr(_deps.config_manager.settings, key, "")
        safe_settings[key] = "***" if val else ""
    return {"providers": result, "settings": safe_settings}


class ApiKeyUpdateRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/api/config/apikey")
async def update_api_key(body: ApiKeyUpdateRequest):
    """更新厂商 API Key，同时动态注册/注销厂商 Provider"""
    provider = body.provider
    api_key = body.api_key
    _deps.config_manager.update_api_key(provider, api_key)

    if api_key.strip() and provider in _deps.provider_factories:
        try:
            new_provider = _deps.provider_factories[provider](api_key.strip())
            _deps.dispatcher.register_provider(provider, new_provider)
            logger.info("动态注册厂商 %s", provider)
            return {"status": "ok", "message": f"{provider} API Key 已更新，厂商已自动加载"}
        except Exception as e:
            logger.error("动态注册厂商 %s 失败: %s", provider, e, exc_info=True)
            return {"status": "ok", "message": f"{provider} API Key 已保存，但厂商加载失败，请检查 Key 是否正确"}
    elif not api_key.strip():
        await _deps.dispatcher.unregister_provider(provider)
        logger.info("已注销厂商 %s（API Key 已清空）", provider)
        return {"status": "ok", "message": f"{provider} API Key 已清空，厂商已卸载"}

    return {"status": "ok", "message": f"{provider} API Key 已更新"}


@router.post("/api/config/model/toggle")
async def toggle_model(provider: str, model_name: str, enabled: bool):
    """启用/禁用模型"""
    _deps.config_manager.toggle_model(provider, model_name, enabled)
    return {"status": "ok", "message": f"{model_name} 已{'启用' if enabled else '禁用'}"}


@router.post("/api/config/model/priority")
async def update_priority(provider: str, model_name: str, priority: int):
    """更新模型优先级"""
    _deps.config_manager.update_model_priority(provider, model_name, priority)
    return {"status": "ok"}


@router.post("/api/config/reload")
async def reload_config():
    """热重载配置（重新读取 catalog 和 config.yaml，无需重启服务）"""
    try:
        if _deps.catalog:
            _deps.catalog._data = _deps.catalog._load()
            logger.info("Catalog 热重载完成")
        _deps.config_manager._load()
        _deps.config_manager.invalidate_enabled_models_cache()
        new_models = _deps.config_manager.get_enabled_models()
        return {
            "status": "ok",
            "enabled_models": len(new_models),
            "message": "配置已热重载",
        }
    except Exception as e:
        logger.error("热重载配置失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"热重载失败: {str(e)[:200]}")


@router.post("/api/config/model/add")
async def add_model(provider: str, name: str, priority: int = 99, rpd: int = 0, rpm: int = 0):
    """添加新模型（先加入目录，再激活）"""
    if _deps.catalog:
        existing = _deps.catalog.get_models(provider)
        if not any(m["id"] == name for m in existing):
            _deps.catalog.add_model(provider, {
                "id": name, "name": name, "default_rpd": rpd, "default_rpm": rpm, "category": "通用",
            })
        _deps.catalog.activate_model(provider, name, priority)
    return {"status": "ok", "message": f"模型 {name} 已添加到 {provider}"}


@router.post("/api/provider/toggle")
async def toggle_provider(provider: str, enabled: bool):
    """启用/禁用厂商，同时动态注册/注销 Provider"""
    if _deps.catalog:
        _deps.catalog.set_provider_enabled(provider, enabled)

    _NO_KEY_PROVIDERS = {"cursor", "ollama"}

    if enabled and not _deps.dispatcher.has_provider(provider):
        if provider == "cursor":
            from src.providers.cursor import CursorProvider
            _deps.dispatcher.register_provider("cursor", CursorProvider())
            logger.info("动态注册 Cursor 厂商")
            return {"status": "ok", "message": f"{provider} 已启用并加载"}
        elif provider == "ollama":
            api_key = _deps.config_manager.get_api_key(provider) if _deps.config_manager else ""
            try:
                prov_instance = _deps.provider_factories["ollama"](api_key)
                _deps.dispatcher.register_provider("ollama", prov_instance)
                logger.info("动态注册 Ollama 厂商")
                added = await prov_instance.discover_and_register(_deps.catalog)
                msg = f"ollama 已启用并加载，发现 {len(added)} 个本地模型" if added else "ollama 已启用并加载"
                return {"status": "ok", "message": msg}
            except Exception as e:
                logger.error("动态注册 Ollama 失败: %s", e)
                return {"status": "ok", "message": f"ollama 已启用，但连接失败: {e}"}
        elif provider in _deps.provider_factories:
            api_key = _deps.config_manager.get_api_key(provider) if _deps.config_manager else ""
            if api_key.strip() or provider in _NO_KEY_PROVIDERS:
                try:
                    _deps.dispatcher.register_provider(provider, _deps.provider_factories[provider](api_key))
                    logger.info("动态注册厂商 %s", provider)
                    return {"status": "ok", "message": f"{provider} 已启用并加载"}
                except Exception as e:
                    logger.error("动态注册 %s 失败: %s", provider, e)
                    return {"status": "ok", "message": f"{provider} 已启用，但加载失败，请检查 API Key 是否正确"}
            else:
                return {"status": "ok", "message": f"{provider} 已启用，但 API Key 未配置，请先填写 API Key"}
    elif not enabled and _deps.dispatcher.has_provider(provider):
        await _deps.dispatcher.unregister_provider(provider)
        logger.info("已注销厂商 %s", provider)
        return {"status": "ok", "message": f"{provider} 已禁用并卸载"}

    return {"status": "ok", "message": f"{provider} 已{'启用' if enabled else '禁用'}"}


@router.post("/api/provider/ollama/refresh")
async def refresh_ollama_models():
    """刷新 Ollama 本地模型列表"""
    from src.providers.ollama import OllamaProvider
    prov = _deps.dispatcher.get_provider("ollama")
    if not isinstance(prov, OllamaProvider):
        raise HTTPException(status_code=400, detail="Ollama 厂商未启用或未注册")
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    try:
        added = await prov.discover_and_register(_deps.catalog)
        all_models = await prov.list_models()
        return {"status": "ok", "total": len(all_models), "new": len(added), "new_models": added, "all_models": all_models}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"无法连接 Ollama: {e}") from e


@router.post("/api/provider/ollama/pull")
async def pull_ollama_model(model: str):
    """拉取 Ollama 模型（SSE 流式进度）"""
    from src.providers.ollama import OllamaProvider
    prov = _deps.dispatcher.get_provider("ollama")
    if not isinstance(prov, OllamaProvider):
        raise HTTPException(status_code=400, detail="Ollama 厂商未启用或未注册")

    async def _stream():
        try:
            last_progress_time = time.time()
            async for progress in prov.pull_model(model):
                last_progress_time = time.time()
                yield f"data: {json.dumps(progress, ensure_ascii=False)}\n\n"
                if progress.get("status") == "success":
                    if _deps.catalog:
                        added = await prov.discover_and_register(_deps.catalog)
                        yield f"data: {json.dumps({'status': 'registered', 'new_models': added}, ensure_ascii=False)}\n\n"
                    break
                if time.time() - last_progress_time > 600:
                    yield f"data: {json.dumps({'status': 'error', 'error': '下载超时: 10分钟无进度更新'}, ensure_ascii=False)}\n\n"
                    break
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)[:200]}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/api/provider/ollama/models/{model_name:path}")
async def delete_ollama_model(model_name: str):
    """删除 Ollama 本地模型"""
    from src.providers.ollama import OllamaProvider
    prov = _deps.dispatcher.get_provider("ollama")
    if not isinstance(prov, OllamaProvider):
        raise HTTPException(status_code=400, detail="Ollama 厂商未启用或未注册")
    success = await prov.delete_model(model_name)
    if not success:
        raise HTTPException(status_code=500, detail=f"删除模型 {model_name} 失败")
    if _deps.catalog:
        models = _deps.catalog.get_models("ollama")
        for m in models:
            if m.get("id") == model_name:
                _deps.catalog.remove_model("ollama", model_name)
                break
    return {"status": "ok", "message": f"模型 {model_name} 已删除"}


@router.get("/api/provider/ollama/running")
async def list_ollama_running():
    """查询当前加载在 VRAM 中的 Ollama 模型"""
    from src.providers.ollama import OllamaProvider
    prov = _deps.dispatcher.get_provider("ollama")
    if not isinstance(prov, OllamaProvider):
        raise HTTPException(status_code=400, detail="Ollama 厂商未启用或未注册")
    running = await prov.list_running()
    return {"status": "ok", "models": running}


@router.post("/api/provider/ollama/test-tool-calling/{model_name:path}")
async def test_ollama_tool_calling(model_name: str):
    """动态测试 Ollama 模型是否支持 tool calling"""
    from src.providers.ollama import OllamaProvider
    prov = _deps.dispatcher.get_provider("ollama")
    if not isinstance(prov, OllamaProvider):
        raise HTTPException(status_code=400, detail="Ollama 厂商未启用或未注册")
    supports = await prov.test_tool_calling(model_name)
    if _deps.catalog:
        model_entry = _deps.catalog.get_model("ollama", model_name)
        if model_entry:
            model_entry["tool_calling"] = supports
    return {"status": "ok", "model": model_name, "tool_calling": supports}


@router.post("/api/provider/priority")
async def update_provider_priority(provider: str, priority: int):
    """更新厂商优先级"""
    _deps.config_manager.update_provider_priority(provider, priority)
    return {"status": "ok", "message": f"{provider} 优先级已更新为 {priority}"}


@router.post("/api/provider/reorder")
async def reorder_providers(ordered_ids: list[str]):
    """批量重新排序厂商优先级"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    _deps.catalog.reorder_providers(ordered_ids)
    return {"status": "ok", "message": "厂商优先级已更新"}


@router.post("/api/settings")
async def update_settings(log_level: str | None = None, default_provider: str | None = None, auto_switch: bool | None = None):
    """动态更新运行时设置"""
    messages = []
    settings = _deps.config_manager.settings
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
        _deps.config_manager.save()
    return {"status": "ok", "message": "；".join(messages) if messages else "无变更"}


# --- 模型发现 ---

def _build_discovery_guide(prov_id: str, prov_data: dict) -> str:
    """从 catalog 的 api_key_guide 字段获取接入指南，无需手动维护 fallback"""
    guide = prov_data.get("api_key_guide", "")
    if guide:
        return guide
    url = prov_data.get("url", "")
    return f"请访问 {url} 获取 API Key" if url else "请查看厂商官网获取 API Key"


@router.get("/api/discovery", response_model=list[ProviderDiscovery])
async def discover_providers():
    """查找可免费使用的大模型厂商（自动从 catalog 读取，无需手动维护列表）"""
    if not _deps.catalog:
        return []
    discoveries = []
    for prov_id, prov_data in _deps.catalog.get_all_providers().items():
        model_ids = [m["id"] for m in prov_data.get("models", [])[:5]]
        discoveries.append(ProviderDiscovery(
            name=prov_data.get("name", prov_id),
            url=prov_data.get("url", ""),
            description=prov_data.get("description", f"{prov_data.get('name', prov_id)} 免费模型推理"),
            free_models=model_ids,
            integration_guide=_build_discovery_guide(prov_id, prov_data),
            new_user_only=False,
        ))
    return discoveries


@router.get("/api/provider/{provider_name}/models")
async def fetch_provider_models(provider_name: str, force: bool = False):
    """拉取厂商最新模型列表并自动测试能力"""
    if not _deps.dispatcher.has_provider(provider_name):
        has_key = _deps.config_manager.has_api_key(provider_name) if _deps.config_manager else False
        if not has_key:
            raise HTTPException(status_code=404, detail=f"厂商 {provider_name} 未注册：请先填写 API Key")
        raise HTTPException(status_code=404, detail=f"厂商 {provider_name} 未注册：API Key 已配置但加载失败")

    provider = _deps.dispatcher.get_provider(provider_name)
    try:
        models = await provider.list_models()
    except Exception as e:
        logger.error("拉取 %s 模型列表失败: %s", provider_name, e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"拉取 {provider_name} 模型列表失败")

    result = {"provider": provider_name, "available_models": models}

    skip_bulk_test = getattr(provider, "skip_bulk_capability_test", False)
    if _deps.capability_tester and not skip_bulk_test:
        to_test = []
        skipped = []
        for mid in models:
            if not force and _deps.capability_tester.cache.has(provider_name, mid):
                cached = _deps.capability_tester.cache.get(provider_name, mid)
                err = (cached or {}).get("error", "")
                if err and ("429" in err or "rate_limit" in err):
                    to_test.append(mid)
                else:
                    skipped.append(mid)
            else:
                to_test.append(mid)

        test_results = []
        if to_test:
            test_results = await _deps.capability_tester.test_provider_models(provider, provider_name, to_test, force=force)
            _apply_capabilities_to_catalog(provider_name, test_results)

        cached_results = [
            {**_deps.capability_tester.cache.get(provider_name, mid), "cached": True}
            for mid in skipped
        ]
        result["capabilities"] = {
            "tested": test_results, "cached": cached_results,
            "summary": {"total": len(models), "tested_now": len(to_test), "from_cache": len(skipped), "all_cached": len(to_test) == 0},
        }

    return result


def _apply_capabilities_to_catalog(provider_name: str, test_results: list[dict]) -> None:
    """将测试结果回写到 catalog"""
    if not _deps.catalog:
        return
    for r in test_results:
        mid = r.get("model", "")
        for model in _deps.catalog.get_models(provider_name):
            if model["id"] == mid:
                model["tool_calling"] = r.get("tool_calling", False)
                break
    _deps.catalog.save()


# --- 模型能力测试 ---

@router.post("/api/capabilities/test")
async def test_capabilities(provider: str = "", model: str = "", force: bool = False):
    """测试模型能力"""
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")

    if provider:
        if not _deps.dispatcher.has_provider(provider):
            raise HTTPException(status_code=404, detail=f"厂商 {provider} 未注册")
        prov_inst = _deps.dispatcher.get_provider(provider)
        if model:
            if provider == "ollama" and hasattr(prov_inst, "is_model_installed"):
                installed = await prov_inst.is_model_installed(model)
                if not installed:
                    return {"provider": provider, "results": [{"provider": provider, "model": model, "available": False, "not_installed": True, "error": f"模型 {model} 未安装"}]}
            models = [model]
        else:
            try:
                models = await prov_inst.list_models()
            except Exception:
                raise HTTPException(status_code=502, detail="拉取模型列表失败")
        results = await _deps.capability_tester.test_provider_models(prov_inst, provider, models, force=force)
        _apply_capabilities_to_catalog(provider, results)
        return {"provider": provider, "results": results}

    all_providers = _deps.dispatcher.get_all_providers()
    model_map = {}
    for prov_name, prov_inst in all_providers.items():
        try:
            models = await prov_inst.list_models()
            model_map[prov_name] = models
        except Exception as e:
            logger.error("拉取 %s 模型列表失败: %s", prov_name, e)

    all_results = await _deps.capability_tester.test_all_providers(all_providers, model_map, force=force)
    for prov_name, results in all_results.items():
        _apply_capabilities_to_catalog(prov_name, results)

    summary = {}
    for prov_name, results in all_results.items():
        summary[prov_name] = {
            "total": len(results),
            "tested_now": sum(1 for r in results if not r.get("cached")),
            "from_cache": sum(1 for r in results if r.get("cached")),
            "tool_calling": sum(1 for r in results if r.get("tool_calling")),
            "available": sum(1 for r in results if r.get("available")),
        }
    return {"results": all_results, "summary": summary}


@router.get("/api/capabilities")
async def get_capabilities():
    """查看已缓存的模型能力"""
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")
    cached = _deps.capability_tester.cache.get_all()
    summary = {"total": len(cached), "tool_calling": sum(1 for v in cached.values() if v.get("tool_calling")), "available": sum(1 for v in cached.values() if v.get("available"))}
    return {"capabilities": cached, "summary": summary}


@router.delete("/api/capabilities/clear")
async def clear_capabilities():
    """清除能力缓存"""
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")
    _deps.capability_tester.cache.clear()
    _deps.capability_tester.cache.save()
    return {"status": "ok", "message": "能力缓存已清除"}
