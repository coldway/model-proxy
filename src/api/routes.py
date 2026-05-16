# Created by model-proxy on 2026/05/11
# Copyright © 2026

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.models.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelConfig,
    ModelDetail,
    ModelInfo,
    ModelListResponse,
    ProviderConfig,
    ProviderDiscovery,
    ProviderListResponse,
    ProviderModelsResponse,
    ProviderSummary,
    ProxyInfo,
    RateLimit,
    UsageResponse,
    UsageStats,
)
from src.config.catalog import CatalogManager
from src.scheduler.history import RequestHistory
from src.scheduler.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()

_THINK_TAG_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL)

_THINKING_LINE_PATTERNS = [
    re.compile(r"^\s*[*•]\s"),
    re.compile(r"^\s{4,}\S"),
    re.compile(r"^\s*\("),
    re.compile(r"^Final\s+(Polish|Answer|Response|Draft)\s*:", re.I),
    re.compile(
        r"^\s*(Wait|Hmm|Let me|I should|I need to|I will|Since the|However,|Actually,|"
        r"The user|User input|Task:|Constraints?:|Draft|Revised|Refinement)",
        re.I,
    ),
]


def _is_thinking_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(p.match(line) for p in _THINKING_LINE_PATTERNS)


def _strip_thinking(text: str) -> tuple[str, str]:
    """从模型回复中分离思考过程和最终回复。

    返回 (clean_reply, thinking_content)。
    支持：
    1. <think>/<thinking> 标签
    2. Gemma 式 bullet-point 推理 + 末尾回复
    3. 元认知文本（Final Polish/Wait/Since the user 等）+ 末尾回复
    """
    thinking_parts = _THINK_TAG_RE.findall(text)
    if thinking_parts:
        clean = _THINK_TAG_RE.sub("", text).strip()
        thinking = "\n---\n".join(p.strip() for p in thinking_parts if p.strip())
        return clean, thinking

    lines = text.strip().split("\n")
    if len(lines) < 3:
        return text, ""

    non_empty = [(i, lines[i]) for i in range(len(lines)) if lines[i].strip()]
    if len(non_empty) < 3:
        return text, ""

    thinking_count = sum(1 for _, l in non_empty if _is_thinking_line(l))

    if thinking_count < 2 or thinking_count < len(non_empty) * 0.5:
        return text, ""

    last_clean_start = -1
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _is_thinking_line(lines[i]):
            break
        last_clean_start = i

    if last_clean_start > 0:
        clean = "\n".join(lines[last_clean_start:]).strip()
        thinking = "\n".join(lines[:last_clean_start]).strip()
        if clean:
            return _dedup_answer(clean), thinking

    paragraphs = re.split(r"\n\s*\n", text.strip())
    if len(paragraphs) >= 2:
        last_para = paragraphs[-1].strip()
        if last_para and not _is_thinking_line(last_para.split("\n")[0]):
            thinking_text = "\n\n".join(paragraphs[:-1]).strip()
            return _dedup_answer(last_para), thinking_text

    cn_answer = _extract_chinese_answer(text)
    if cn_answer:
        thinking_text = text[:text.rfind(cn_answer)].strip()
        return _dedup_answer(cn_answer), thinking_text

    trailing = _extract_trailing_answer(text)
    if trailing:
        thinking_text = text[:text.rfind(trailing)].strip()
        return _dedup_answer(trailing), thinking_text

    return text, ""


def _dedup_answer(text: str) -> str:
    """去除 Gemma 模型常见的答案重复（先引号内草稿，后直接输出）。

    例如: '"我是AI助手。"我是AI助手。' → '我是AI助手。'
    """
    stripped = text.strip()
    for q_open, q_close in [('"', '"'), ('\u201c', '\u201d'), ("'", "'"), ('\u2018', '\u2019')]:
        if stripped.startswith(q_open) and q_close in stripped[1:]:
            end_idx = stripped.index(q_close, 1) + len(q_close)
            quoted = stripped[len(q_open):end_idx - len(q_close)].strip()
            rest = stripped[end_idx:].strip()
            if rest and _similar(quoted, rest):
                return rest
    half = len(stripped) // 2
    if half > 2:
        first_half = stripped[:half].strip()
        second_half = stripped[half:].strip()
        if _similar(first_half, second_half):
            return second_half
    return stripped


def _similar(a: str, b: str) -> bool:
    a_clean = re.sub(r"[\s，。！？、""''\"'\.\,]", "", a)
    b_clean = re.sub(r"[\s，。！？、""''\"'\.\,]", "", b)
    if not a_clean or not b_clean:
        return False
    shorter = min(len(a_clean), len(b_clean))
    longer = max(len(a_clean), len(b_clean))
    if shorter < 3:
        return a_clean == b_clean
    common = sum(1 for ca, cb in zip(a_clean, b_clean) if ca == cb)
    return common / longer > 0.8


_CHINESE_SENTENCE_RE = re.compile(
    r"([\u4e00-\u9fff][\u4e00-\u9fff\s，。！？、（）""''：；…·\w A-Za-z0-9\-_.]{2,}[。！？\u201d]?)$"
)


def _extract_chinese_answer(text: str) -> str:
    """提取文本末尾的中文句子作为最终答案。"""
    m = _CHINESE_SENTENCE_RE.search(text.strip())
    if m:
        candidate = m.group(1).strip()
        if candidate and len(candidate) < len(text) * 0.5:
            return candidate
    return ""


def _extract_trailing_answer(text: str) -> str:
    """从全是思考内容的文本末尾提取被拼接的最终答案。

    Gemma 模型常见模式：思考过程以 bullet-point 输出，最终答案
    无换行直接拼接在最后一行末尾或最后一个 bullet 内容之后。
    """
    lines = text.strip().split("\n")
    last_line = ""
    for line in reversed(lines):
        if line.strip():
            last_line = line
            break
    if not last_line:
        return ""

    content = re.sub(r"^\s*[*•]\s+", "", last_line).strip()
    if not content:
        return ""

    for pattern in [
        re.compile(r'[.。!！?？)\）"\u201d]\s*(.+)$'),
        re.compile(r'(?:best|better|correct|answer|回复|答案|直接)[.。"\u201d)）]*\s*(.+)$', re.I),
    ]:
        m = pattern.search(content)
        if m:
            candidate = m.group(1).strip()
            if candidate and 0 < len(candidate) < len(content):
                return candidate

    all_thinking = all(
        _is_thinking_line(l) for l in lines if l.strip()
    )
    if all_thinking and len(content) < 200:
        return content

    return ""


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


_deps = _RouteDeps()


def init_routes(config_manager, dispatcher, rate_limiter, history=None, catalog=None, provider_factories=None, capability_tester=None):
    _deps.config_manager = config_manager
    _deps.dispatcher = dispatcher
    _deps.rate_limiter = rate_limiter
    _deps.history = history
    _deps.catalog = catalog
    _deps.provider_factories = provider_factories or {}
    _deps.capability_tester = capability_tester
    _deps.session_mgr = SessionManager()


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """聊天补全接口（支持流式和非流式）"""
    from src.scheduler.dispatcher import AllModelsUnavailable, ModelNotFound, PayloadTooLarge, RateLimitExceeded

    trace_id = uuid.uuid4().hex[:12]
    sid_tag = f" session={request.session_id}" if request.session_id else ""
    logger.info(
        "[API] trace=%s%s /v1/chat/completions | model=%s stream=%s 消息数=%d",
        trace_id, sid_tag, request.model, request.stream, len(request.messages),
    )

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型，请检查配置")

    if request.stream:
        return await _handle_stream(request, enabled_models, trace_id)

    start_time = time.time()
    try:
        provider_name, model_name, result = await _deps.dispatcher.dispatch(request, enabled_models, trace_id=trace_id)
        latency = (time.time() - start_time) * 1000
        route_strategy = _deps.dispatcher.last_route_strategy or ""
        if _deps.history:
            _deps.history.record(
                provider=provider_name,
                model=model_name,
                success=True,
                latency_ms=latency,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                route_strategy=route_strategy,
            )
        bound = _deps.dispatcher.get_session_binding(request.session_id) if request.session_id else None
        info = ProxyInfo(
            provider=provider_name,
            trace_id=trace_id,
            latency_ms=round(latency, 1),
            route_strategy=route_strategy,
            session_id=request.session_id,
            bound_model=f"{bound[0]}:{bound[1]}" if bound else None,
        )
        logger.info("[API] trace=%s%s 完成 | provider=%s model=%s 耗时=%.0fms", trace_id, sid_tag, provider_name, model_name, latency)
        out = result.model_dump()
        out["proxy_info"] = info.model_dump(exclude_none=True)
        return JSONResponse(content=out)
    except PayloadTooLarge as e:
        _record_failure(start_time, str(e))
        logger.warning("[API] trace=%s payload 过大: %s", trace_id, e)
        raise HTTPException(status_code=413, detail=str(e))
    except RateLimitExceeded as e:
        _record_failure(start_time, str(e))
        logger.warning("[API] trace=%s 限流: %s", trace_id, e)
        raise HTTPException(status_code=429, detail=str(e))
    except ModelNotFound as e:
        _record_failure(start_time, str(e))
        logger.warning("[API] trace=%s 模型未找到: %s", trace_id, e)
        raise HTTPException(status_code=404, detail=str(e))
    except AllModelsUnavailable as e:
        _record_failure(start_time, str(e))
        logger.error("[API] trace=%s 所有模型不可用: %s", trace_id, e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        _record_failure(start_time, str(e))
        logger.error("[API] trace=%s 推理请求异常: %s", trace_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")


async def _handle_stream(request: ChatCompletionRequest, enabled_models, trace_id: str = ""):
    """处理流式请求，返回 SSE StreamingResponse"""
    from src.api.streaming import create_stream_response
    from src.scheduler.dispatcher import AllModelsUnavailable, ModelNotFound, RateLimitExceeded

    sid_tag = f" session={request.session_id}" if request.session_id else ""
    start_time = time.time()
    try:
        provider_name, model_name, content_iter = await _deps.dispatcher.dispatch_stream(request, enabled_models, trace_id=trace_id)
    except RateLimitExceeded as e:
        _record_failure(start_time, str(e))
        logger.warning("[API] trace=%s%s 流式限流: %s", trace_id, sid_tag, e)
        raise HTTPException(status_code=429, detail=str(e))
    except ModelNotFound as e:
        _record_failure(start_time, str(e))
        logger.warning("[API] trace=%s%s 流式模型未找到: %s", trace_id, sid_tag, e)
        raise HTTPException(status_code=404, detail=str(e))
    except AllModelsUnavailable as e:
        _record_failure(start_time, str(e))
        logger.error("[API] trace=%s 流式所有模型不可用: %s", trace_id, e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        _record_failure(start_time, str(e))
        logger.error("[API] trace=%s 流式请求异常: %s", trace_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")

    latency = (time.time() - start_time) * 1000
    route_strategy = _deps.dispatcher.last_route_strategy or ""
    if _deps.history:
        _deps.history.record(
            provider=provider_name, model=model_name,
            success=True, latency_ms=latency,
        )
    logger.info("[API] trace=%s%s 流式连接建立 | provider=%s model=%s 耗时=%.0fms", trace_id, sid_tag, provider_name, model_name, latency)
    bound = _deps.dispatcher.get_session_binding(request.session_id) if request.session_id else None
    info = ProxyInfo(
        provider=provider_name,
        trace_id=trace_id,
        latency_ms=round(latency, 1),
        route_strategy=route_strategy,
        session_id=request.session_id,
        bound_model=f"{bound[0]}:{bound[1]}" if bound else None,
    )
    return create_stream_response(model_name, content_iter, proxy_info=info)


def _record_failure(start_time: float, error: str, provider: str = "unknown", model: str = "unknown") -> None:
    if _deps.history:
        latency = (time.time() - start_time) * 1000
        _deps.history.record(provider=provider, model=model, success=False, latency_ms=latency, error=error)


@router.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    """列出所有已配置模型"""
    models = []
    for prov_name, prov in _deps.config_manager.config.providers.items():
        for m in prov.models:
            models.append(ModelInfo(
                id=m.name,
                provider=prov_name,
                enabled=m.enabled,
                priority=m.priority,
                rate_limit=m.rate_limit,
                tool_calling=m.tool_calling,
            ))
    return ModelListResponse(models=models)


@router.get("/v1/providers", response_model=ProviderListResponse)
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


@router.get("/v1/providers/{provider_id}/models", response_model=ProviderModelsResponse)
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
        cap = cap_cache.get(m.name, {})
        models.append(ModelDetail(
            id=m.name,
            provider=provider_id,
            enabled=m.enabled,
            priority=m.priority,
            tool_calling=m.tool_calling,
            rate_limit=m.rate_limit,
            capabilities=cap,
        ))
    models.sort(key=lambda x: (not x.enabled, x.priority))
    return ProviderModelsResponse(provider=provider_id, models=models)


@router.get("/v1/usage", response_model=UsageResponse)
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
    return {"providers": result, "settings": _deps.config_manager.settings.model_dump()}


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
            logger.info(f"动态注册厂商 {provider}")
            return {"status": "ok", "message": f"{provider} API Key 已更新，厂商已自动加载"}
        except Exception as e:
            logger.error(f"动态注册厂商 {provider} 失败: {e}")
            return {"status": "ok", "message": f"{provider} API Key 已保存，但厂商加载失败: {e}"}
    elif not api_key.strip():
        _deps.dispatcher.unregister_provider(provider)
        logger.info(f"已注销厂商 {provider}（API Key 已清空）")
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


@router.post("/api/config/model/add")
async def add_model(
    provider: str,
    name: str,
    priority: int = 99,
    rpd: int = 0,
    rpm: int = 0,
):
    """添加新模型（先加入目录，再激活）"""
    if _deps.catalog:
        existing = _deps.catalog.get_models(provider)
        if not any(m["id"] == name for m in existing):
            _deps.catalog.add_model(provider, {
                "id": name,
                "name": name,
                "default_rpd": rpd,
                "default_rpm": rpm,
                "category": "通用",
            })
        _deps.catalog.activate_model(provider, name, priority)
    return {"status": "ok", "message": f"模型 {name} 已添加到 {provider}"}


@router.post("/api/provider/toggle")
async def toggle_provider(provider: str, enabled: bool):
    """启用/禁用厂商，同时动态注册/注销 Provider"""
    if _deps.catalog:
        _deps.catalog.set_provider_enabled(provider, enabled)

    if enabled and not _deps.dispatcher.has_provider(provider):
        if provider == "cursor":
            from src.providers.cursor import CursorProvider
            _deps.dispatcher.register_provider("cursor", CursorProvider())
            logger.info(f"动态注册 Cursor 厂商")
            return {"status": "ok", "message": f"{provider} 已启用并加载"}
        elif provider in _deps.provider_factories:
            api_key = _deps.config_manager.get_api_key(provider) if _deps.config_manager else ""
            if api_key.strip():
                try:
                    _deps.dispatcher.register_provider(provider, _deps.provider_factories[provider](api_key))
                    logger.info(f"动态注册厂商 {provider}")
                    return {"status": "ok", "message": f"{provider} 已启用并加载"}
                except Exception as e:
                    logger.error(f"动态注册 {provider} 失败: {e}")
                    return {"status": "ok", "message": f"{provider} 已启用，但加载失败: {e}"}
            else:
                return {"status": "ok", "message": f"{provider} 已启用，但 API Key 未配置，请先填写 API Key"}
    elif not enabled and _deps.dispatcher.has_provider(provider):
        _deps.dispatcher.unregister_provider(provider)
        logger.info(f"已注销厂商 {provider}")
        return {"status": "ok", "message": f"{provider} 已禁用并卸载"}

    return {"status": "ok", "message": f"{provider} 已{'启用' if enabled else '禁用'}"}


@router.post("/api/provider/priority")
async def update_provider_priority(provider: str, priority: int):
    """更新厂商优先级"""
    _deps.config_manager.update_provider_priority(provider, priority)
    return {"status": "ok", "message": f"{provider} 优先级已更新为 {priority}"}


@router.post("/api/provider/reorder")
async def reorder_providers(ordered_ids: list[str]):
    """批量重新排序厂商优先级（用于拖拽排序）"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    _deps.catalog.reorder_providers(ordered_ids)
    return {"status": "ok", "message": "厂商优先级已更新"}


@router.post("/api/settings")
async def update_settings(
    log_level: str | None = None,
    default_provider: str | None = None,
    auto_switch: bool | None = None,
):
    """动态更新运行时设置（host/port 需重启服务）"""
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
async def fetch_provider_models(provider_name: str, force: bool = False):
    """拉取厂商最新模型列表并自动测试能力

    默认行为：自动测试未缓存的模型能力（tool_calling 等），
    已缓存的模型直接跳过。全部已缓存时不发起任何测试请求。

    Args:
        force: 强制重新测试所有模型（忽略缓存）
    """
    if not _deps.dispatcher.has_provider(provider_name):
        has_key = _deps.config_manager.has_api_key(provider_name) if _deps.config_manager else False
        if not has_key:
            raise HTTPException(
                status_code=404,
                detail=f"厂商 {provider_name} 未注册：请先在「API Key 配置」中填写 API Key（保存后自动加载）",
            )
        raise HTTPException(
            status_code=404,
            detail=f"厂商 {provider_name} 未注册：API Key 已配置但加载失败，请检查 Key 格式或重启服务",
        )

    provider = _deps.dispatcher.get_provider(provider_name)
    try:
        models = await provider.list_models()
    except Exception as e:
        logger.error(f"拉取 {provider_name} 模型列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"拉取 {provider_name} 模型列表失败: {e}")

    result = {"provider": provider_name, "available_models": models}

    if _deps.capability_tester:
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
            test_results = await _deps.capability_tester.test_provider_models(
                provider, provider_name, to_test, force=force,
            )
            _apply_capabilities_to_catalog(provider_name, test_results)

        cached_results = [
            {**_deps.capability_tester.cache.get(provider_name, mid), "cached": True}
            for mid in skipped
        ]

        result["capabilities"] = {
            "tested": test_results,
            "cached": cached_results,
            "summary": {
                "total": len(models),
                "tested_now": len(to_test),
                "from_cache": len(skipped),
                "all_cached": len(to_test) == 0,
            },
        }

    return result


def _apply_capabilities_to_catalog(provider_name: str, test_results: list[dict]) -> None:
    """将测试结果回写到 catalog（更新 tool_calling 标记）"""
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
    """测试模型能力

    - 指定 provider + model: 只测试该厂商的指定模型（最快）
    - 指定 provider: 只测试该厂商的所有模型
    - 不指定 provider: 测试所有已注册厂商
    - force=true: 强制重新测试已缓存的模型
    """
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")

    if provider:
        if not _deps.dispatcher.has_provider(provider):
            raise HTTPException(status_code=404, detail=f"厂商 {provider} 未注册")
        prov_inst = _deps.dispatcher.get_provider(provider)

        if model:
            models = [model]
        else:
            try:
                models = await prov_inst.list_models()
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"拉取模型列表失败: {e}")

        results = await _deps.capability_tester.test_provider_models(
            prov_inst, provider, models, force=force,
        )
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

    all_results = await _deps.capability_tester.test_all_providers(
        all_providers, model_map, force=force,
    )

    for prov_name, results in all_results.items():
        _apply_capabilities_to_catalog(prov_name, results)

    summary = {}
    for prov_name, results in all_results.items():
        tested_now = sum(1 for r in results if not r.get("cached"))
        from_cache = sum(1 for r in results if r.get("cached"))
        tc_count = sum(1 for r in results if r.get("tool_calling"))
        available_count = sum(1 for r in results if r.get("available"))
        summary[prov_name] = {
            "total": len(results),
            "tested_now": tested_now,
            "from_cache": from_cache,
            "tool_calling": tc_count,
            "available": available_count,
        }

    return {"results": all_results, "summary": summary}


@router.get("/api/capabilities")
async def get_capabilities():
    """查看已缓存的模型能力"""
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")
    cached = _deps.capability_tester.cache.get_all()
    summary = {
        "total": len(cached),
        "tool_calling": sum(1 for v in cached.values() if v.get("tool_calling")),
        "available": sum(1 for v in cached.values() if v.get("available")),
    }
    return {"capabilities": cached, "summary": summary}


@router.delete("/api/capabilities/clear")
async def clear_capabilities():
    """清除能力缓存（下次拉取时重新测试所有模型）"""
    if not _deps.capability_tester:
        raise HTTPException(status_code=500, detail="能力测试器未初始化")
    _deps.capability_tester.cache.clear()
    _deps.capability_tester.cache.save()
    return {"status": "ok", "message": "能力缓存已清除"}


# --- 请求历史 ---

@router.get("/api/history")
async def get_history(limit: int = 50):
    """获取最近请求历史"""
    limit = min(max(limit, 1), 500)
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
    """手动重置熔断状态。不传 provider 则清除全部；传 provider=xxx 则只清除指定厂商。"""
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
    """清除 429 黑名单（可指定模型或清除全部）"""
    count = _deps.rate_limiter.clear_blacklist(provider, model)
    return {"status": "ok", "cleared": count}


# --- 会话模型绑定 ---

@router.get("/api/session-bindings")
async def get_session_bindings():
    """获取所有活跃的会话-模型绑定"""
    bindings = _deps.dispatcher.get_all_session_bindings()
    return {"bindings": bindings, "count": len(bindings)}


@router.delete("/api/session-bindings/clear")
async def clear_session_bindings(session_id: str = ""):
    """清除会话绑定（可指定 session_id 或全部清除）"""
    count = _deps.dispatcher.clear_session_binding(session_id)
    return {"status": "ok", "cleared": count}


# --- Payload 上限管理 ---

@router.get("/api/payload-limits")
async def get_payload_limits():
    """获取所有模型的 payload 上限记录"""
    limits = _deps.dispatcher.payload_tracker.get_all_limits()
    return {
        "limits": limits,
        "count": len(limits),
    }


@router.delete("/api/payload-limits/clear")
async def clear_payload_limits(provider: str = "", model: str = ""):
    """清除 payload 上限记录（可指定厂商/模型或全部清除）"""
    count = _deps.dispatcher.payload_tracker.clear(provider, model)
    return {"status": "ok", "cleared": count}


# --- 模型目录（可提交到 GitHub 的部分） ---

@router.get("/api/catalog/providers")
async def catalog_providers():
    """获取目录中所有厂商及其模型"""
    if not _deps.catalog:
        return {"providers": {}}
    return {"providers": _deps.catalog.get_all_providers()}


@router.get("/api/catalog/provider/{provider_id}/models")
async def catalog_provider_models(provider_id: str):
    """获取指定厂商的模型列表"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    models = _deps.catalog.get_models(provider_id)
    return {"provider": provider_id, "models": models}


@router.get("/api/catalog/search")
async def catalog_search(q: str = ""):
    """搜索模型目录（模糊匹配名称、描述、分类）"""
    if not _deps.catalog:
        return {"results": []}
    if not q.strip():
        # 返回全部
        all_models = []
        for prov_id, prov in _deps.catalog.get_all_providers().items():
            for model in prov.get("models", []):
                all_models.append({**model, "provider_id": prov_id, "provider_name": prov.get("name", prov_id)})
        return {"results": all_models}
    return {"results": _deps.catalog.search_models(q)}


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
    """向目录添加模型（更新 providers_deps.catalog.yaml，可提交 GitHub）"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    model_data = {
        "id": model_id,
        "name": name or model_id,
        "description": description,
        "default_rpd": default_rpd,
        "default_rpm": default_rpm,
        "category": category,
    }
    success = _deps.catalog.add_model(provider_id, model_data)
    if not success:
        raise HTTPException(status_code=400, detail="模型已存在或厂商不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已添加到 {provider_id} 目录"}


@router.delete("/api/catalog/model/delete")
async def catalog_delete_model(provider_id: str, model_id: str):
    """从目录删除模型"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    success = _deps.catalog.remove_model(provider_id, model_id)
    if not success:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已从 {provider_id} 目录删除"}


@router.post("/api/catalog/model/activate")
async def catalog_activate_model(provider_id: str, model_id: str, priority: int = 99):
    """从目录中激活模型（设置 enabled=true）"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")

    if _deps.catalog.activate_model(provider_id, model_id, priority):
        return {"status": "ok", "message": f"模型 {model_id} 已激活"}
    raise HTTPException(status_code=404, detail="模型在目录中不存在")


@router.post("/api/config/model/delete")
async def config_delete_model(provider: str, model_name: str):
    """停用模型（从目录中移除 enabled 标记）"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    if _deps.catalog.deactivate_model(provider, model_name):
        return {"status": "ok", "message": f"模型 {model_name} 已停用"}
    raise HTTPException(status_code=404, detail=f"模型 {model_name} 不存在")


# --- 聊天会话管理 ---

@router.get("/api/chat/sessions")
async def list_chat_sessions():
    """获取所有聊天会话列表"""
    return {"sessions": _deps.session_mgr.list_sessions()}


@router.post("/api/chat/sessions")
async def create_chat_session(model: str = "auto", title: str = "新对话"):
    """创建新聊天会话"""
    session = _deps.session_mgr.create(model=model, title=title)
    return {"session": {"id": session.id, "title": session.title, "model": session.model}}


@router.get("/api/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    """获取指定会话的完整消息历史"""
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session": {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "messages": session.messages,
            "tokens_est": session.total_tokens_est,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        },
    }


@router.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    """删除聊天会话"""
    if _deps.session_mgr.delete(session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="会话不存在")


@router.put("/api/chat/sessions/{session_id}/title")
async def rename_chat_session(session_id: str, title: str):
    """重命名聊天会话"""
    if _deps.session_mgr.rename(session_id, title):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="会话不存在")


@router.post("/api/chat/sessions/{session_id}/send")
async def send_chat_message(session_id: str, request: ChatCompletionRequest):
    """向指定会话发送消息并获取回复（自动管理上下文）"""
    from src.scheduler.dispatcher import AllModelsUnavailable, ModelNotFound, RateLimitExceeded

    trace_id = uuid.uuid4().hex[:12]

    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    user_msg = ""
    if request.messages:
        user_msg = request.messages[-1].content or ""

    logger.info(
        "[会话] trace=%s session=%s 用户消息: %s",
        trace_id, session_id, user_msg[:200],
    )

    session.add_message("user", user_msg)

    if len(session.messages) == 1:
        session.auto_title()

    context_messages = session.get_context_messages()
    ctx_request = ChatCompletionRequest(
        model=request.model or session.model,
        messages=[ChatMessage(role=m["role"], content=m["content"]) for m in context_messages],
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=False,
    )

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        session.add_message("assistant", "❌ 没有可用模型，请检查配置")
        await asyncio.to_thread(_deps.session_mgr.save)
        raise HTTPException(status_code=503, detail="没有可用模型")

    start_time = time.time()
    try:
        provider_name, model_name, result = await _deps.dispatcher.dispatch(ctx_request, enabled_models, trace_id=trace_id)
        latency = (time.time() - start_time) * 1000
        if _deps.history:
            _deps.history.record(
                provider=provider_name,
                model=model_name,
                success=True,
                latency_ms=latency,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                route_strategy=_deps.dispatcher.last_route_strategy,
            )

        raw_reply = result.choices[0].message.content or ""
        reply, thinking = _strip_thinking(raw_reply)
        if thinking:
            logger.info(
                "[会话] trace=%s 模型 %s 思考过程:\n%s",
                trace_id, result.model,
                thinking[:500] + ("…" if len(thinking) > 500 else ""),
            )
        session.add_message("assistant", reply, model=result.model)
        await asyncio.to_thread(_deps.session_mgr.save)

        logger.info(
            "[会话] trace=%s 完成 | session=%s provider=%s model=%s 耗时=%.0fms 回复长度=%d",
            trace_id, session_id, provider_name, model_name, latency, len(reply),
        )

        return {
            "reply": reply,
            "model": result.model,
            "provider": provider_name,
            "usage": result.usage.model_dump(),
            "tokens_est": session.total_tokens_est,
            "context_messages": len(context_messages),
            "total_messages": len(session.messages),
            "title": session.title,
        }
    except (RateLimitExceeded, ModelNotFound, AllModelsUnavailable) as e:
        _record_failure(start_time, str(e))
        await asyncio.to_thread(_deps.session_mgr.save)
        logger.warning("[会话] trace=%s session=%s 失败: %s", trace_id, session_id, e)
        status = 429 if isinstance(e, RateLimitExceeded) else (404 if isinstance(e, ModelNotFound) else 503)
        raise HTTPException(status_code=status, detail=str(e))
    except Exception as e:
        _record_failure(start_time, str(e))
        logger.error("[会话] trace=%s session=%s 异常: %s", trace_id, session_id, e, exc_info=True)
        await asyncio.to_thread(_deps.session_mgr.save)
        raise HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")


@router.post("/api/chat/sessions/{session_id}/stream")
async def stream_chat_message(session_id: str, request: ChatCompletionRequest):
    """向指定会话发送消息（流式 SSE 响应 + 自动上下文管理）"""
    from src.api.streaming import create_stream_response
    from src.scheduler.dispatcher import AllModelsUnavailable, ModelNotFound, RateLimitExceeded

    trace_id = uuid.uuid4().hex[:12]

    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    user_msg = ""
    if request.messages:
        user_msg = request.messages[-1].content or ""

    logger.info(
        "[流式会话] trace=%s session=%s 用户消息: %s",
        trace_id, session_id, user_msg[:200],
    )

    session.add_message("user", user_msg)
    if len(session.messages) == 1:
        session.auto_title()

    context_messages = session.get_context_messages()
    ctx_request = ChatCompletionRequest(
        model=request.model or session.model,
        messages=[ChatMessage(role=m["role"], content=m["content"]) for m in context_messages],
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        stream=True,
    )

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型")

    start_time = time.time()
    try:
        provider_name, model_name, content_iter = await _deps.dispatcher.dispatch_stream(ctx_request, enabled_models, trace_id=trace_id)
    except (RateLimitExceeded, ModelNotFound, AllModelsUnavailable) as e:
        _record_failure(start_time, str(e))
        logger.warning("[流式会话] trace=%s session=%s 失败: %s", trace_id, session_id, e)
        status = 429 if isinstance(e, RateLimitExceeded) else (404 if isinstance(e, ModelNotFound) else 503)
        raise HTTPException(status_code=status, detail=str(e))
    except Exception as e:
        _record_failure(start_time, str(e))
        logger.error("[流式会话] trace=%s session=%s 异常: %s", trace_id, session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="推理服务内部错误，请稍后重试")

    latency = (time.time() - start_time) * 1000
    if _deps.history:
        _deps.history.record(provider=provider_name, model=model_name, success=True, latency_ms=latency)

    logger.info("[流式会话] trace=%s 流式连接建立 | provider=%s model=%s", trace_id, provider_name, model_name)

    async def _collect_and_stream():
        """流式输出的同时收集完整回复写入会话"""
        import json as _json

        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        full_reply = []

        meta = {"model": model_name, "provider": provider_name, "title": session.title, "session_id": session.id}
        yield f"data: {_json.dumps({'meta': meta}, ensure_ascii=False)}\n\n"

        try:
            async for chunk in content_iter:
                full_reply.append(chunk)
                data = {
                    "id": chat_id, "object": "chat.completion.chunk", "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                }
                yield f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as ex:
            logger.error("[流式会话] trace=%s 流式传输异常: %s", trace_id, ex)

        raw_text = "".join(full_reply)
        reply_text, thinking = _strip_thinking(raw_text)
        if thinking:
            logger.info("[流式会话] trace=%s 模型 %s 思考过程:\n%s", trace_id, model_name, thinking[:500])
        session.add_message("assistant", reply_text, model=model_name)
        await asyncio.to_thread(_deps.session_mgr.save)

        logger.info(
            "[流式会话] trace=%s 流式完成 | session=%s model=%s 回复长度=%d",
            trace_id, session_id, model_name, len(reply_text),
        )

        end_data = {
            "id": chat_id, "object": "chat.completion.chunk", "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "session_info": {
                "tokens_est": session.total_tokens_est,
                "total_messages": len(session.messages),
            },
        }
        yield f"data: {_json.dumps(end_data, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        _collect_and_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- 实时日志 ---

@router.get("/api/logs")
async def get_logs(after: int = 0, limit: int = 200):
    """获取最近的日志条目（支持增量拉取）。

    - after: 上次返回的 latest_seq，仅获取此后的新日志
    - limit: 最多返回条数
    """
    from src.api.log_buffer import get_instance
    handler = get_instance()
    if not handler:
        return {"entries": [], "latest_seq": 0}
    limit = min(max(limit, 1), 500)
    entries, latest_seq = handler.get_logs(after_seq=after, limit=limit)
    return {"entries": entries, "latest_seq": latest_seq}


@router.delete("/api/logs/clear")
async def clear_logs():
    """清空日志缓冲"""
    from src.api.log_buffer import get_instance
    handler = get_instance()
    if handler:
        handler.clear()
    return {"status": "ok"}


@router.get("/api/logs/level")
async def get_log_level():
    """获取当前日志级别"""
    level = logging.getLogger().level
    return {"level": logging.getLevelName(level)}


@router.post("/api/logs/level")
async def set_log_level(level: str):
    """动态切换日志级别（INFO/DEBUG/WARNING/ERROR）"""
    level_upper = level.upper()
    numeric = getattr(logging, level_upper, None)
    if numeric is None:
        raise HTTPException(status_code=400, detail=f"无效的日志级别: {level}")
    logging.getLogger().setLevel(numeric)
    logger.info("日志级别已切换为 %s", level_upper)
    return {"status": "ok", "level": level_upper}
