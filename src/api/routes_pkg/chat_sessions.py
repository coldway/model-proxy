# Created by model-proxy on 2026/05/21
# Copyright © 2026

"""内置聊天会话管理路由（CRUD + send + stream + memory + catalog）"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from src.api.routes_pkg.deps import _deps, record_failure
from src.api.routes_pkg.chat_pipeline import ChatPipeline
from src.api.thinking import strip_thinking as _strip_thinking
from src.models.schemas import ChatCompletionRequest, ChatMessage
from src.scheduler.exceptions import (
    AllModelsUnavailable,
    ModelNotFound,
    ProviderCallError,
    RateLimitExceeded,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# --- 模型目录 ---

@router.get("/api/catalog/providers", summary="厂商目录")
async def catalog_providers():
    """获取目录中所有厂商及其模型"""
    if not _deps.catalog:
        return {"providers": {}}
    return {"providers": _deps.catalog.get_all_providers()}


@router.get("/api/catalog/provider/{provider_id}/models", summary="厂商模型目录")
async def catalog_provider_models(provider_id: str):
    """获取指定厂商的模型列表"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    models = _deps.catalog.get_models(provider_id)
    return {"provider": provider_id, "models": models}


@router.get("/api/catalog/search", summary="搜索模型")
async def catalog_search(q: str = ""):
    """搜索模型目录"""
    if not _deps.catalog:
        return {"results": []}
    if not q.strip():
        all_models = []
        for prov_id, prov in _deps.catalog.get_all_providers().items():
            for model in prov.get("models", []):
                all_models.append({**model, "provider_id": prov_id, "provider_name": prov.get("name", prov_id)})
        return {"results": all_models}
    return {"results": _deps.catalog.search_models(q)}


@router.post("/api/catalog/model/add", summary="添加到目录")
async def catalog_add_model(provider_id: str, model_id: str, name: str = "", description: str = "", default_rpd: int = 0, default_rpm: int = 0, category: str = "通用"):
    """向目录添加模型"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    model_data = {"id": model_id, "name": name or model_id, "description": description, "default_rpd": default_rpd, "default_rpm": default_rpm, "category": category}
    success = _deps.catalog.add_model(provider_id, model_data)
    if not success:
        raise HTTPException(status_code=400, detail="模型已存在或厂商不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已添加到 {provider_id} 目录"}


@router.delete("/api/catalog/model/delete", summary="从目录移除")
async def catalog_delete_model(provider_id: str, model_id: str):
    """从目录删除模型"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    success = _deps.catalog.remove_model(provider_id, model_id)
    if not success:
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"status": "ok", "message": f"模型 {model_id} 已从 {provider_id} 目录删除"}


@router.post("/api/catalog/model/activate", summary="激活模型")
async def catalog_activate_model(provider_id: str, model_id: str, priority: int = 99):
    """从目录中激活模型"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    if _deps.catalog.activate_model(provider_id, model_id, priority):
        return {"status": "ok", "message": f"模型 {model_id} 已激活"}
    raise HTTPException(status_code=404, detail="模型在目录中不存在")


@router.post("/api/config/model/delete", summary="删除已激活模型")
async def config_delete_model(provider: str, model_name: str):
    """停用模型"""
    if not _deps.catalog:
        raise HTTPException(status_code=500, detail="目录未初始化")
    if _deps.catalog.deactivate_model(provider, model_name):
        return {"status": "ok", "message": f"模型 {model_name} 已停用"}
    raise HTTPException(status_code=404, detail=f"模型 {model_name} 不存在")


# --- 聊天会话 CRUD ---

@router.get("/api/chat/sessions", summary="会话列表")
async def list_chat_sessions():
    return {"sessions": _deps.session_mgr.list_sessions()}


@router.post("/api/chat/sessions", summary="创建会话")
async def create_chat_session(model: str = "auto", title: str = "新对话"):
    session = _deps.session_mgr.create(model=model, title=title)
    return {"session": {"id": session.id, "title": session.title, "model": session.model}}


@router.get("/api/chat/sessions/{session_id}", summary="会话详情")
async def get_chat_session(session_id: str):
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": {"id": session.id, "title": session.title, "model": session.model, "messages": session.messages, "tokens_est": session.total_tokens_est, "created_at": session.created_at, "updated_at": session.updated_at}}


@router.delete("/api/chat/sessions/{session_id}", summary="删除会话")
async def delete_chat_session(session_id: str):
    from src.scheduler.memory import get_memory_manager
    memory_mgr = get_memory_manager()
    consolidated = memory_mgr.on_session_end(session_id)
    if consolidated:
        logger.info("[Memory] session=%s 巩固 %d 条到长期记忆", session_id, consolidated)
    if _deps.session_mgr.delete(session_id):
        return {"status": "ok", "memory_consolidated": consolidated}
    raise HTTPException(status_code=404, detail="会话不存在")


@router.get("/api/chat/trash", summary="回收站")
async def list_trash_sessions():
    return {"sessions": _deps.session_mgr.list_trash()}


@router.post("/api/chat/trash/{session_id}/restore", summary="恢复会话")
async def restore_trash_session(session_id: str):
    if _deps.session_mgr.restore(session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="回收站中无此会话")


@router.delete("/api/chat/trash/{session_id}", summary="永久删除")
async def permanent_delete_session(session_id: str):
    if _deps.session_mgr.permanent_delete(session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="回收站中无此会话")


@router.put("/api/chat/sessions/{session_id}/title", summary="修改标题")
async def rename_chat_session(session_id: str, title: str):
    if _deps.session_mgr.rename(session_id, title):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="会话不存在")


@router.put("/api/chat/sessions/{session_id}/model", summary="切换模型")
async def switch_session_model(session_id: str, body: dict):
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    new_model = body.get("model", "").strip()
    if not new_model:
        raise HTTPException(status_code=400, detail="model 字段不能为空")
    MODEL_CONTEXT_LIMITS = {
        "gpt-4o": 128_000, "gpt-4o-mini": 128_000,
        "claude-sonnet-4-20250514": 200_000, "claude-3-5-sonnet": 200_000,
        "gemini-2.5-pro": 1_000_000, "gemini-2.5-flash": 1_000_000,
        "deepseek-chat": 64_000, "deepseek-reasoner": 64_000,
    }
    max_ctx = MODEL_CONTEXT_LIMITS.get(new_model)
    old_model = session.switch_model(new_model, max_context_tokens=max_ctx)
    _deps.session_mgr.save()
    return {"status": "ok", "old_model": old_model, "new_model": new_model, "max_context_tokens": max_ctx or session.get_max_context_tokens()}


@router.post("/api/chat/sessions/{session_id}/regenerate", summary="重新生成")
async def regenerate_chat_message(session_id: str, request: ChatCompletionRequest | None = None):
    """重新生成最后一条 assistant 回复"""
    from src.scheduler.memory import get_memory_manager

    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    async with session._async_lock:
        removed = session.regenerate()
        if not removed:
            raise HTTPException(status_code=400, detail="没有可重新生成的 assistant 消息")
        last_user_msg = ""
        for m in reversed(session.messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break
        if not request:
            request = ChatCompletionRequest(model=session.model, messages=[ChatMessage(role="user", content=last_user_msg)])
        trace_id = uuid.uuid4().hex[:12]
        memory_mgr = get_memory_manager()
        return await _do_send_chat(session, request, trace_id, memory_mgr)


@router.post("/api/chat/sessions/{session_id}/edit/{msg_index}", summary="编辑消息重发")
async def edit_chat_message(session_id: str, msg_index: int, body: dict):
    """编辑指定位置的消息"""
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    new_content = body.get("content", "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    async with session._async_lock:
        if not session.edit_message(msg_index, new_content):
            raise HTTPException(status_code=400, detail=f"消息索引 {msg_index} 无效")
    await asyncio.to_thread(_deps.session_mgr.save)
    return {"status": "ok", "total_messages": len(session.messages), "hint": "消息已编辑，后续对话已截断。"}


# --- 发送消息（非流式） ---

@router.post("/api/chat/sessions/{session_id}/send", summary="发送消息")
async def send_chat_message(session_id: str, request: ChatCompletionRequest):
    """向指定会话发送消息并获取回复"""
    from src.scheduler.memory import get_memory_manager

    trace_id = uuid.uuid4().hex[:12]
    memory_mgr = get_memory_manager()
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    async with session._async_lock:
        return await _do_send_chat(session, request, trace_id, memory_mgr)


async def _do_send_chat(session, request, trace_id: str, memory_mgr):
    """send_chat_message 的核心逻辑（使用 ChatPipeline）"""
    from src.api.internal_tools import get_tool_definitions
    from src.models.schemas import ToolDefinition, ToolFunction
    from src.scheduler.context_manager import get_archive

    pipeline = ChatPipeline(session, request, trace_id, memory_mgr)
    pipeline.prepare()

    tool_defs_raw = get_tool_definitions()
    tools = [
        ToolDefinition(type="function", function=ToolFunction(name=td["function"]["name"], description=td["function"]["description"], parameters=td["function"]["parameters"]))
        for td in tool_defs_raw
    ]

    try:
        result = await pipeline.run_tool_loop(tools)
        pipeline.finalize(result)

        try:
            archive = get_archive(session.id)
            archive.archive_turn(user_msg=pipeline._get_user_msg(), assistant_msg=result.reply, tool_names=[tc["tool"] for tc in result.tool_calls_log])
        except Exception:
            pass

        user_turn_count = session.count_role("user")
        if user_turn_count in (6, 12, 20):
            task = asyncio.create_task(memory_mgr.llm_extract_memories(session.id, session.messages))
            task.add_done_callback(lambda t: t.exception() and logger.warning("[Memory] llm_extract 异常: %s", t.exception()))

        await asyncio.to_thread(_deps.session_mgr.save)

        logger.info("[会话] trace=%s 完成 | session=%s provider=%s model=%s 耗时=%.0fms 工具调用=%d",
                    trace_id, session.id, result.provider_name, result.model_name, result.latency_ms, len(result.tool_calls_log))

        resp = {
            "reply": result.reply, "model": result.model_name, "provider": result.provider_name,
            "usage": result.usage.model_dump() if result.usage else {},
            "tokens_est": session.total_tokens_est, "context_messages": len(session.get_context_messages()),
            "total_messages": len(session.messages), "title": session.title,
        }
        if result.tool_calls_log:
            resp["tool_calls"] = result.tool_calls_log
        return resp
    except (RateLimitExceeded, ModelNotFound, AllModelsUnavailable, ProviderCallError) as e:
        status, detail = await pipeline.handle_error(e)
        raise HTTPException(status_code=status, detail=detail)
    except Exception as e:
        status, detail = await pipeline.handle_error(e)
        logger.error("[会话] trace=%s session=%s 异常: %s", trace_id, session.id, e, exc_info=True)
        raise HTTPException(status_code=status, detail=detail)


# --- 发送消息（流式） ---

@router.post("/api/chat/sessions/{session_id}/stream", summary="流式发送")
async def stream_chat_message(session_id: str, request: ChatCompletionRequest):
    """向指定会话发送消息（流式 SSE 响应）"""
    from src.scheduler.memory import get_memory_manager
    from src.api.internal_tools import get_tool_definitions
    from src.models.schemas import ToolDefinition, ToolFunction

    trace_id = uuid.uuid4().hex[:12]
    memory_mgr = get_memory_manager()
    session = _deps.session_mgr.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    user_msg = request.messages[-1].content or "" if request.messages else ""
    logger.info("[流式会话] trace=%s session=%s 用户消息: %s", trace_id, session_id, user_msg[:200])

    enabled_models = _deps.config_manager.get_enabled_models()
    if not enabled_models:
        raise HTTPException(status_code=503, detail="没有可用模型")

    tool_defs_raw = get_tool_definitions()
    tools = [
        ToolDefinition(type="function", function=ToolFunction(name=td["function"]["name"], description=td["function"]["description"], parameters=td["function"]["parameters"]))
        for td in tool_defs_raw
    ]

    async with session._async_lock:
        return await _do_stream_chat(session, request, trace_id, memory_mgr, user_msg, enabled_models, tools)


async def _do_stream_chat(session, request, trace_id: str, memory_mgr, user_msg: str, enabled_models, tools):
    """stream_chat_message 核心逻辑"""
    from src.scheduler.context_manager import AutoCompactManager, get_archive
    from src.api.internal_tools import execute_tool

    session_id = session.id
    session.set_checkpoint()
    session.add_message("user", user_msg)
    is_first_message = len(session.messages) == 1
    long_term_ctx = ""
    if is_first_message:
        session.auto_title()
        long_term_ctx = memory_mgr.on_session_start(session_id, user_msg)

    start_time = time.time()
    stream_compact_mgr = AutoCompactManager(context_window=session._max_context_tokens)

    def _build_context_request(stream: bool, include_tools: bool):
        context_messages = session.get_context_messages()
        if stream_compact_mgr.monitor.estimate_utilization(context_messages) >= 0.55:
            context_messages, _ = stream_compact_mgr.tool_compactor.compact(context_messages)
        session_memory_ctx = memory_mgr.get_context_injection(session_id, user_msg=user_msg)
        full_memory_ctx = "\n\n".join(filter(None, [long_term_ctx, session_memory_ctx]))
        if full_memory_ctx and context_messages and context_messages[0].get("role") == "system":
            context_messages[0] = {**context_messages[0], "content": (context_messages[0]["content"] or "") + "\n\n" + full_memory_ctx}
        return ChatCompletionRequest(
            model=request.model or session.model,
            messages=[ChatMessage(role=m["role"], content=m["content"], tool_calls=m.get("tool_calls"), tool_call_id=m.get("tool_call_id"), name=m.get("name")) for m in context_messages],
            temperature=request.temperature, max_tokens=request.max_tokens, stream=stream,
            tools=tools if include_tools else None,
            mode=request.mode,
            force=request.force,
            sandbox=request.sandbox,
            workspace_path=request.workspace_path,
            cursor_session_id=request.cursor_session_id,
            cursor_continue=request.cursor_continue,
            worktree_name=request.worktree_name,
            worktree_base=request.worktree_base,
            skip_worktree_setup=request.skip_worktree_setup,
            approve_mcps=request.approve_mcps,
        )

    try:
        ctx_request = _build_context_request(stream=False, include_tools=True)
        provider_name, model_name, result = await _deps.dispatcher.dispatch(ctx_request, enabled_models, trace_id=trace_id)
    except (RateLimitExceeded, ModelNotFound, AllModelsUnavailable) as e:
        session.rollback()
        record_failure(start_time, str(e))
        await asyncio.to_thread(_deps.session_mgr.save)
        status = 429 if isinstance(e, RateLimitExceeded) else (404 if isinstance(e, ModelNotFound) else 503)
        raise HTTPException(status_code=status, detail="服务异常")
    except ProviderCallError as e:
        session.rollback()
        record_failure(start_time, str(e))
        await asyncio.to_thread(_deps.session_mgr.save)
        raise HTTPException(status_code=503, detail="模型服务暂时不可用")
    except Exception as e:
        session.rollback()
        record_failure(start_time, str(e))
        await asyncio.to_thread(_deps.session_mgr.save)
        logger.error("[流式会话] trace=%s session=%s 异常: %s", trace_id, session_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="推理服务内部错误")

    max_tool_rounds = 3
    tool_calls_log = []
    msg = result.choices[0].message if result.choices else None

    if msg and msg.tool_calls:
        for round_idx in range(max_tool_rounds):
            if not msg or not msg.tool_calls:
                break
            tc_data = [{"id": tc.id, "type": tc.type, "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
            session.add_message("assistant", msg.content or "", tool_calls=tc_data)
            for tc in msg.tool_calls:
                logger.info("[流式会话] trace=%s 调用工具: %s", trace_id, tc.function.name)
                tool_result = await execute_tool(tc.function.name, tc.function.arguments)
                tool_calls_log.append({"tool": tc.function.name, "result_len": len(tool_result)})
                session.add_message("tool", tool_result, tool_call_id=tc.id, name=tc.function.name)
            include_tools = (round_idx < max_tool_rounds - 1)
            try:
                ctx_request = _build_context_request(stream=False, include_tools=include_tools)
                provider_name, model_name, result = await _deps.dispatcher.dispatch(ctx_request, enabled_models, trace_id=trace_id)
                msg = result.choices[0].message if result.choices else None
            except Exception as e:
                logger.warning("[流式会话] trace=%s tool 后续调用失败: %s", trace_id, e)
                msg = None
                break

    latency = (time.time() - start_time) * 1000
    if _deps.history:
        _deps.history.record(provider=provider_name, model=model_name, success=True, latency_ms=latency)

    final_reply = (msg.content if msg else "") or ""
    final_reply, thinking = _strip_thinking(final_reply)
    if thinking:
        logger.info("[流式会话] trace=%s 模型思考过程:\n%s", trace_id, thinking[:500])

    use_true_stream = not tool_calls_log and not final_reply

    async def _stream_real():
        nonlocal final_reply, provider_name, model_name
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        meta = {"model": model_name, "provider": provider_name, "title": session.title, "session_id": session.id}
        if tool_calls_log:
            meta["tool_calls"] = tool_calls_log
        yield f"data: {json.dumps({'meta': meta}, ensure_ascii=False)}\n\n"

        if use_true_stream:
            try:
                stream_request = _build_context_request(stream=True, include_tools=False)
                s_prov, s_model, content_iter = await _deps.dispatcher.dispatch_stream(stream_request, enabled_models, trace_id=trace_id)
                provider_name, model_name = s_prov, s_model
                collected = []
                async for chunk in content_iter:
                    delta_content = chunk.get("content", "")
                    if delta_content:
                        collected.append(delta_content)
                        data = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model_name, "choices": [{"index": 0, "delta": {"content": delta_content}, "finish_reason": None}]}
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                final_reply = "".join(collected)
            except Exception as e:
                logger.warning("[流式会话] trace=%s 真实流式失败，降级分块输出: %s", trace_id, e)
                final_reply = final_reply or "⚠️ 流式输出异常"
                for i in range(0, len(final_reply), 20):
                    segment = final_reply[i:i + 20]
                    if segment:
                        data = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model_name, "choices": [{"index": 0, "delta": {"content": segment}, "finish_reason": None}]}
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        else:
            chunk_size = 20
            for i in range(0, max(len(final_reply), 1), chunk_size):
                segment = final_reply[i:i + chunk_size]
                if segment:
                    data = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model_name, "choices": [{"index": 0, "delta": {"content": segment}, "finish_reason": None}]}
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        session.add_message("assistant", final_reply, model=model_name)
        session.clear_checkpoint()
        memory_mgr.on_turn_complete(session_id, user_msg, final_reply)
        try:
            archive = get_archive(session_id)
            archive.archive_turn(user_msg=user_msg, assistant_msg=final_reply, tool_names=[tc["tool"] for tc in tool_calls_log])
        except Exception:
            pass
        try:
            await asyncio.to_thread(_deps.session_mgr.save)
        except Exception as save_err:
            logger.error("[流式会话] trace=%s 保存会话失败: %s", trace_id, save_err)
        logger.info("[流式会话] trace=%s 完成 | provider=%s model=%s tools=%d", trace_id, provider_name, model_name, len(tool_calls_log))

        end_data = {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "session_info": {"tokens_est": session.total_tokens_est, "total_messages": len(session.messages)}}
        yield f"data: {json.dumps(end_data, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream_real(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


# --- Cost & Memory ---

@router.get("/api/cost/stats", summary="费用统计")
async def get_cost_stats():
    if not _deps.cost_tracker:
        return {"error": "成本追踪未启用"}
    return _deps.cost_tracker.get_stats()


@router.get("/api/memory/stats", summary="记忆统计")
async def get_memory_stats():
    from src.scheduler.memory import get_memory_manager
    return get_memory_manager().get_long_term_stats()


@router.get("/api/memory/entries", summary="记忆条目")
async def list_memory_entries(limit: int = 50):
    from src.scheduler.memory import get_memory_manager
    store = get_memory_manager()._store
    entries = store.get_all()
    entries.sort(key=lambda e: e.last_accessed, reverse=True)
    return {"entries": [e.to_dict() for e in entries[:limit]], "total": store.size()}


@router.post("/api/memory/consolidate/{session_id}", summary="整合记忆")
async def consolidate_session_memory(session_id: str):
    from src.scheduler.memory import get_memory_manager
    mgr = get_memory_manager()
    added = mgr.on_session_end(session_id)
    return {"consolidated": added}


@router.post("/api/memory/add", summary="添加记忆")
async def add_memory_entry(body: dict):
    from src.scheduler.memory import get_memory_manager, MemoryEntry
    import time as _time
    content = body.get("content", "").strip()
    mem_type = body.get("type", "semantic")
    if not content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="content 长度不能超过 1000 字符")
    _VALID_TYPES = {"semantic", "episodic", "preference"}
    if mem_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"type 必须为 {', '.join(_VALID_TYPES)} 之一")
    mgr = get_memory_manager()
    now = _time.time()
    entry = MemoryEntry(id=f"manual_{uuid.uuid4().hex[:8]}", type=mem_type, content=content, importance=0.8, created_at=now, last_accessed=now, tags=[], source_session="manual")
    mgr._store.add(entry)
    return {"status": "ok", "id": entry.id}


@router.delete("/api/memory/entries/{entry_id}", summary="删除记忆")
async def delete_memory_entry(entry_id: str):
    from src.scheduler.memory import get_memory_manager
    mgr = get_memory_manager()
    if mgr._store.remove_by_id(entry_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="记忆条目不存在")


@router.post("/api/memory/import", summary="导入记忆")
async def import_memory(body: dict):
    from src.scheduler.memory import get_memory_manager, MemoryEntry
    mgr = get_memory_manager()
    entries = body.get("entries", [])
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="entries 必须为数组")
    if len(entries) > 500:
        raise HTTPException(status_code=400, detail="单次最多导入 500 条")
    imported = 0
    for e in entries:
        content = (e.get("content") or "").strip()
        if not content or len(content) > 1000:
            continue
        entry = MemoryEntry.from_dict(e)
        mgr._store.add(entry)
        imported += 1
    return {"status": "ok", "imported": imported}
