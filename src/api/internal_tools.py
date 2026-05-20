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


_register_builtin_tools()
