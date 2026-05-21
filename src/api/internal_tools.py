# Created by model-proxy on 2026/05/20
# Copyright © 2026

"""内部工具注册与执行

为 model-proxy 的多轮会话提供可被 LLM 调用的内部工具。
模型通过 tool_calling 调用这些工具，系统自动执行并返回结果。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Callable, Awaitable

import httpx

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
                    pull_success = False
                    async for progress in prov_inst.pull_model(model_id):
                        st = progress.get("status", "")
                        if st == "success":
                            pull_success = True
                            break
                        elif "error" in st.lower() or progress.get("error"):
                            err = progress.get("error", st)
                            return json.dumps({"error": f"下载失败: {err}"}, ensure_ascii=False)
                    if not pull_success:
                        return json.dumps({"error": "下载流异常结束，模型可能未完整安装"}, ensure_ascii=False)
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

        all_catalog = _deps.catalog.get_models(provider_id) if _deps.catalog else []
        catalog_models = {m["id"] for m in all_catalog}
        enabled_models = {m["id"] for m in all_catalog if m.get("enabled")}

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

    # ---- search_ollama_library ----

    def _estimate_suitability(size_b: float, vram_gb: float) -> str:
        """根据参数量和 VRAM 估算 Q4 量化下的适合度"""
        q4_gb = size_b * 0.6
        if q4_gb <= vram_gb * 0.85:
            return "推荐 (完全载入 VRAM)"
        if q4_gb <= vram_gb:
            return "可用 (VRAM 刚好)"
        if q4_gb <= vram_gb + 16:
            return "可用 (需 CPU 辅助, 速度较慢)"
        return "不适合 (显存和内存不足)"

    async def _handle_search_ollama_library(args: dict) -> str:
        """搜索 Ollama 模型库并按本机硬件筛选"""
        from src.api.routes import _deps

        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "必须指定 query 搜索词"}, ensure_ascii=False)

        vram_gb = args.get("vram_gb", 12.0)
        max_results = min(args.get("max_results", 20), 40)

        encoded_q = urllib.parse.quote(query, safe="")
        url = f"https://ollama.com/search?q={encoded_q}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
        except httpx.HTTPError as e:
            return json.dumps({
                "error": f"ollama.com 搜索请求失败: {str(e)[:200]}",
                "hint": "ollama.com 可能不可达，请尝试直接使用 add_model 工具添加已知模型名（如 qwen3:8b）",
            }, ensure_ascii=False)

        card_re = re.compile(
            r'<a\s+href="(/([^"/]+/[^"]+))"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        installed_names: set[str] = set()
        try:
            if _deps.dispatcher and _deps.dispatcher.has_provider("ollama"):
                from src.providers.ollama import OllamaProvider
                prov = _deps.dispatcher.get_provider("ollama")
                if isinstance(prov, OllamaProvider):
                    installed = await prov.list_models()
                    for m in installed:
                        m_lower = m.lower()
                        installed_names.add(m_lower)
                        installed_names.add(m_lower.split(":")[0])
                        base_name = m_lower.split("/")[-1]
                        installed_names.add(base_name)
                        installed_names.add(base_name.split(":")[0])
        except Exception:
            pass

        models: list[dict] = []
        seen: set[str] = set()

        if "<html" not in html[:500].lower():
            return json.dumps({
                "error": "ollama.com 返回格式异常（非 HTML），可能页面结构已变更",
                "hint": "请直接使用 add_model 工具添加已知模型名",
            }, ensure_ascii=False)

        for match in card_re.finditer(html):
            if len(models) >= max_results:
                break
            href = match.group(2)
            inner_html = match.group(3)
            text = re.sub(r"<[^>]+>", " ", inner_html)
            text = text.replace("\xa0", " ").replace("&nbsp;", " ")
            text = re.sub(r"\s+", " ", text).strip()

            name = href
            if name in seen or "/" not in name:
                continue
            seen.add(name)
            if name.startswith("library/"):
                name = name[len("library/"):]

            caps = [c for c in ("tools", "thinking", "vision", "audio")
                    if c in text.lower()]

            sizes_raw = re.findall(r"\b(\d+(?:\.\d+)?)[bB]\b", text)
            sizes_b = sorted(set(float(s) for s in sizes_raw if float(s) >= 0.5))

            pulls_m = re.search(r"([\d,.]+)\s*([KkMm])?\s*Pulls?", text)
            pulls = ""
            if pulls_m:
                pulls = pulls_m.group(1).replace(",", "")
                suffix = (pulls_m.group(2) or "").upper()
                if suffix == "K":
                    pulls = str(int(float(pulls) * 1000))
                elif suffix == "M":
                    pulls = str(int(float(pulls) * 1_000_000))

            is_installed = (name.lower() in installed_names
                           or name.split("/")[-1].lower() in installed_names)

            suitable_sizes: list[dict] = []
            for sb in sizes_b:
                suitable_sizes.append({
                    "params": f"{sb}B",
                    "est_vram_q4_gb": round(sb * 0.6, 1),
                    "suitability": _estimate_suitability(sb, vram_gb),
                })

            if not sizes_b:
                name_lower = name.lower()
                inferred = None
                for pattern, sz in [("8b", 8), ("9b", 9), ("4b", 4), ("2b", 2),
                                    ("0.6b", 0.6), ("27b", 27), ("35b", 35),
                                    ("12b", 12), ("14b", 14), ("31b", 31),
                                    ("e2b", 4), ("e4b", 8)]:
                    if pattern in name_lower:
                        inferred = sz
                        break
                if inferred:
                    suitable_sizes.append({
                        "params": f"~{inferred}B (从名称推断)",
                        "est_vram_q4_gb": round(inferred * 0.6, 1),
                        "suitability": _estimate_suitability(inferred, vram_gb),
                    })

            has_suitable = any(
                "不适合" not in s["suitability"] for s in suitable_sizes
            ) if suitable_sizes else True

            models.append({
                "name": name,
                "capabilities": caps,
                "sizes": suitable_sizes,
                "pulls": int(pulls) if pulls.isdigit() else 0,
                "installed": is_installed,
                "has_suitable_size": has_suitable,
                "url": f"https://ollama.com/{name}",
            })

        if not models:
            return json.dumps({
                "query": query,
                "total_found": 0,
                "warning": "未匹配到任何模型，可能 ollama.com 页面结构已变更",
                "hint": "请尝试更宽泛的关键词，或直接使用 add_model 工具添加已知模型名",
            }, ensure_ascii=False, indent=2)

        suitable = [m for m in models if m["has_suitable_size"]]
        unsuitable = [m for m in models if not m["has_suitable_size"]]

        return json.dumps({
            "query": query,
            "vram_gb": vram_gb,
            "total_found": len(models),
            "suitable_count": len(suitable),
            "suitable_models": suitable,
            "unsuitable_models": unsuitable[:5],
        }, ensure_ascii=False, indent=2)

    register_tool(
        name="search_ollama_library",
        description=(
            "搜索 Ollama 模型库（ollama.com），返回匹配的模型列表并根据本机显存"
            "（默认 12GB）评估每个模型的硬件适合度。标记已安装的模型。"
            "适用于查询如 abliterated、vision、coding 等场景。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（如 abliterated, vision, coding）",
                },
                "vram_gb": {
                    "type": "number",
                    "description": "本机 GPU 显存大小（GB），默认 12",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回数量，默认 20",
                },
            },
            "required": ["query"],
        },
        handler=_handle_search_ollama_library,
    )

    # ---- remove_model (卸载/删除模型) ----

    async def _handle_remove_model(args: dict) -> str:
        """删除/卸载模型。Ollama 会执行 ollama rm；其他厂商仅从配置中停用。"""
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

        if provider_id == "ollama":
            from src.providers.ollama import OllamaProvider
            prov = _deps.dispatcher.get_provider(provider_id)
            if not isinstance(prov, OllamaProvider):
                return json.dumps({"error": "Ollama 厂商类型不匹配"}, ensure_ascii=False)

            try:
                success = await prov.delete_model(model_id)
            except Exception as e:
                return json.dumps({"error": f"卸载失败: {str(e)[:200]}"}, ensure_ascii=False)

            if success:
                _deps.catalog.remove_model(provider_id, model_id)
                return json.dumps({
                    "status": "ok",
                    "action": "uninstalled",
                    "provider": provider_id,
                    "model": model_id,
                    "message": f"模型 {model_id} 已从 Ollama 卸载并从配置中移除",
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "error": f"卸载模型 {model_id} 失败（可能模型不存在或 Ollama 服务异常）",
                }, ensure_ascii=False)

        existing = _deps.catalog.get_model(provider_id, model_id)
        if not existing:
            return json.dumps({
                "error": f"模型 {model_id} 在厂商 {provider_id} 的配置中不存在",
            }, ensure_ascii=False)

        _deps.catalog.set_model_enabled(provider_id, model_id, False)
        return json.dumps({
            "status": "ok",
            "action": "disabled",
            "provider": provider_id,
            "model": model_id,
            "message": f"模型 {model_id} 已停用（可通过 toggle_model 重新启用）",
        }, ensure_ascii=False)

    register_tool(
        name="remove_model",
        description=(
            "删除或停用模型。对 Ollama 模型执行 ollama rm 卸载命令并从配置移除；"
            "对其他厂商（google/groq/github 等）仅关闭启用开关（不删除配置，可重新启用）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "厂商ID（如 ollama, groq, google, github）",
                },
                "model": {
                    "type": "string",
                    "description": "模型ID（如 qwen3:8b, llama-3.3-70b-versatile）",
                },
            },
            "required": ["provider", "model"],
        },
        handler=_handle_remove_model,
    )

    # ---- toggle_model (启用/停用模型开关) ----

    async def _handle_toggle_model(args: dict) -> str:
        """切换模型的启用/停用状态。"""
        from src.api.routes import _deps

        provider_id = args.get("provider", "").strip().lower()
        model_id = args.get("model", "").strip()
        enabled = args.get("enabled", True)

        if not provider_id or not model_id:
            return json.dumps({"error": "必须指定 provider 和 model"}, ensure_ascii=False)

        if not _deps.catalog:
            return json.dumps({"error": "系统未初始化"}, ensure_ascii=False)

        existing = _deps.catalog.get_model(provider_id, model_id)
        if not existing:
            return json.dumps({
                "error": f"模型 {model_id} 在厂商 {provider_id} 的配置中不存在",
                "hint": "可使用 add_model 工具先添加模型",
            }, ensure_ascii=False)

        _deps.catalog.set_model_enabled(provider_id, model_id, enabled)
        action = "已启用" if enabled else "已停用"
        return json.dumps({
            "status": "ok",
            "provider": provider_id,
            "model": model_id,
            "enabled": enabled,
            "message": f"模型 {model_id} {action}",
        }, ensure_ascii=False)

    register_tool(
        name="toggle_model",
        description="切换模型的启用/停用状态。启用后模型参与 auto 路由，停用后不再被选择。",
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "厂商ID（如 ollama, groq, google, github）",
                },
                "model": {
                    "type": "string",
                    "description": "模型ID",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "true=启用, false=停用",
                },
            },
            "required": ["provider", "model", "enabled"],
        },
        handler=_handle_toggle_model,
    )


def ensure_tools_registered():
    """幂等注册：多次调用安全"""
    if not _TOOLS:
        _register_builtin_tools()


ensure_tools_registered()
