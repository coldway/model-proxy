# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""内部工具注册与执行

为 model-proxy 的多轮会话提供可被 LLM 调用的内部工具。
模型通过 tool_calling 调用这些工具，系统自动执行并返回结果。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]

_TOOLS: dict[str, dict[str, Any]] = {}
_HANDLERS: dict[str, ToolHandler] = {}


def register_tool(name: str, description: str, parameters: dict, handler: ToolHandler):
    """注册一个内部工具"""
    _TOOLS[name] = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
    _HANDLERS[name] = handler


def get_tool_definitions() -> list[dict]:
    """获取所有工具定义（OpenAI tools 格式）"""
    return list(_TOOLS.values())


async def execute_tool(name: str, arguments: str) -> str:
    """执行工具并返回结果字符串"""
    handler = _HANDLERS.get(name)
    if not handler:
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    try:
        result = await handler(args)
        return result
    except Exception as e:
        logger.error("工具 %s 执行失败: %s", name, e, exc_info=True)
        return json.dumps({"error": str(e)[:200]}, ensure_ascii=False)


def _register_builtin_tools():
    """注册所有内置工具"""

    async def _handle_test_structured(args: dict) -> str:
        from src.api.routes import test_structured_output, StructuredTestRequest
        req = StructuredTestRequest(
            model=args.get("model"),
            schema_level=args.get("schema_level", "basic"),
        )
        result = await test_structured_output(req)
        return json.dumps(result, ensure_ascii=False, indent=2)

    register_tool(
        name="test_structured_output",
        description="测试模型的结构化 JSON 输出能力。可指定单个模型或测试全部模型。返回每个模型的 JSON 合法性、Schema 合规性和延迟。",
        parameters={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "要测试的模型ID（如 gpt-4o-mini），不传则测试所有模型",
                },
                "schema_level": {
                    "type": "string",
                    "enum": ["basic", "nested", "enum_constraints"],
                    "description": "测试复杂度：basic（简单数组）、nested（嵌套对象）、enum_constraints（枚举+数值约束）",
                },
            },
        },
        handler=_handle_test_structured,
    )

    async def _handle_list_models(args: dict) -> str:
        from src.api.routes import _deps
        enabled = _deps.config_manager.get_enabled_models()
        models = []
        for prov, mc in enabled:
            models.append({"id": mc.name, "provider": prov, "priority": mc.priority})
        return json.dumps({"models": models, "total": len(models)}, ensure_ascii=False)

    register_tool(
        name="list_available_models",
        description="列出当前所有已启用的模型及其提供商。",
        parameters={"type": "object", "properties": {}},
        handler=_handle_list_models,
    )

    async def _handle_model_capabilities(args: dict) -> str:
        from src.api.routes import _deps
        if not _deps.capability_tester:
            return json.dumps({"error": "能力测试器未初始化"}, ensure_ascii=False)
        cache = _deps.capability_tester.cache
        model = args.get("model", "")
        if model:
            all_caps = cache.get_all()
            matches = {k: v for k, v in all_caps.items() if model in k}
            return json.dumps(matches or {"error": f"未找到 {model} 的能力数据"}, ensure_ascii=False, indent=2)
        return json.dumps(cache.get_all(), ensure_ascii=False, indent=2)

    register_tool(
        name="get_model_capabilities",
        description="查询模型的已探测能力（tool_calling、streaming、中文、视觉、JSON模式、推理等）。",
        parameters={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "模型ID（模糊匹配），不传则返回全部",
                },
            },
        },
        handler=_handle_model_capabilities,
    )

    async def _handle_add_model(args: dict) -> str:
        """为指定厂商添加模型。Ollama 自动安装，其他厂商检查支持后启用并测试。"""
        from src.api.routes import _deps

        provider_id = args.get("provider", "").strip().lower()
        model_id = args.get("model", "").strip()
        if not provider_id or not model_id:
            return json.dumps({"error": "必须指定 provider 和 model"}, ensure_ascii=False)

        if not _deps.dispatcher or not _deps.catalog:
            return json.dumps({"error": "系统未初始化"}, ensure_ascii=False)

        if not _deps.dispatcher.has_provider(provider_id):
            available = list(_deps.dispatcher.get_all_providers().keys())
            return json.dumps({
                "error": f"厂商 {provider_id} 未注册",
                "available_providers": available,
            }, ensure_ascii=False)

        prov_inst = _deps.dispatcher.get_provider(provider_id)

        if provider_id == "ollama":
            from src.providers.ollama import OllamaProvider
            if not isinstance(prov_inst, OllamaProvider):
                return json.dumps({"error": "Ollama 厂商类型不匹配"}, ensure_ascii=False)

            installed = await prov_inst.is_model_installed(model_id)

            if not installed:
                try:
                    async for progress in prov_inst.pull_model(model_id):
                        st = progress.get("status", "")
                        if st == "success":
                            break
                        elif "error" in st.lower() or progress.get("error"):
                            err = progress.get("error", st)
                            return json.dumps({"error": f"下载失败: {err}"}, ensure_ascii=False)
                except Exception as e:
                    return json.dumps({"error": f"拉取模型失败: {str(e)[:200]}"}, ensure_ascii=False)

            added = await prov_inst.discover_and_register(_deps.catalog)
            return json.dumps({
                "status": "ok",
                "provider": provider_id,
                "model": model_id,
                "action": "installed" if not installed else "already_installed",
                "registered": model_id in added or bool(_deps.catalog.get_model(provider_id, model_id)),
            }, ensure_ascii=False)

        remote_models = []
        try:
            remote_models = await prov_inst.list_models()
        except Exception as e:
            logger.warning("拉取 %s 模型列表失败: %s", provider_id, e)

        if remote_models and model_id not in remote_models:
            close_matches = [m for m in remote_models if model_id.lower() in m.lower()]
            return json.dumps({
                "error": f"厂商 {provider_id} 不支持模型 {model_id}",
                "suggestions": close_matches[:10],
                "total_available": len(remote_models),
            }, ensure_ascii=False)

        existing = _deps.catalog.get_model(provider_id, model_id)
        if not existing:
            _deps.catalog.add_model(provider_id, {
                "id": model_id, "name": model_id,
                "description": "通过聊天添加",
                "enabled": True, "priority": 50,
            })

        test_result = {}
        if _deps.capability_tester:
            try:
                results = await _deps.capability_tester.test_provider_models(
                    prov_inst, provider_id, [model_id], force=True,
                )
                if results:
                    test_result = results[0]
            except Exception as e:
                test_result = {"test_error": str(e)[:100]}

        return json.dumps({
            "status": "ok",
            "provider": provider_id,
            "model": model_id,
            "action": "added_and_tested",
            "capabilities": {
                k: test_result.get(k)
                for k in ("available", "tool_calling", "chinese", "streaming", "latency_ms")
                if k in test_result
            },
        }, ensure_ascii=False, indent=2)

    register_tool(
        name="add_model",
        description="为指定厂商添加模型。Ollama 类型的厂商会自动下载安装模型；其他远程厂商会先检查是否支持该模型，然后添加到配置并测试能力。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "厂商ID（如 ollama, groq, google, github）",
                },
                "model": {
                    "type": "string",
                    "description": "模型ID（如 qwen3:8b, llama-3.3-70b-versatile, gemini-2.5-flash）",
                },
            },
            "required": ["provider", "model"],
        },
        handler=_handle_add_model,
    )

    async def _handle_list_provider_models(args: dict) -> str:
        """查询指定厂商支持的全部模型，过滤已在配置中启用的模型。"""
        from src.api.routes import _deps

        provider_id = args.get("provider", "").strip().lower()
        if not provider_id:
            return json.dumps({"error": "必须指定 provider"}, ensure_ascii=False)

        if not _deps.dispatcher:
            return json.dumps({"error": "系统未初始化"}, ensure_ascii=False)

        if not _deps.dispatcher.has_provider(provider_id):
            available = list(_deps.dispatcher.get_all_providers().keys())
            return json.dumps({
                "error": f"厂商 {provider_id} 未注册",
                "available_providers": available,
            }, ensure_ascii=False)

        prov_inst = _deps.dispatcher.get_provider(provider_id)

        try:
            remote_models = await prov_inst.list_models()
        except Exception as e:
            return json.dumps({"error": f"拉取模型列表失败: {str(e)[:200]}"}, ensure_ascii=False)

        catalog_models = {m["id"] for m in (_deps.catalog.get_models(provider_id) if _deps.catalog else [])}
        enabled_models = {m["id"] for m in (_deps.catalog.get_models(provider_id) if _deps.catalog else []) if m.get("enabled")}

        not_added = [m for m in remote_models if m not in catalog_models]
        added_enabled = [m for m in remote_models if m in enabled_models]
        added_disabled = [m for m in remote_models if m in catalog_models and m not in enabled_models]

        return json.dumps({
            "provider": provider_id,
            "total_available": len(remote_models),
            "not_added": not_added,
            "not_added_count": len(not_added),
            "added_enabled": added_enabled,
            "added_disabled": added_disabled,
        }, ensure_ascii=False, indent=2)

    register_tool(
        name="list_provider_models",
        description="查询指定厂商远程支持的全部模型列表。返回已启用模型、未添加模型、已禁用模型三个分组。用于发现新可用模型。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "厂商ID（如 ollama, groq, google, github, huggingface）",
                },
            },
            "required": ["provider"],
        },
        handler=_handle_list_provider_models,
    )


_register_builtin_tools()
