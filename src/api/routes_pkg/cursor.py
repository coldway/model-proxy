# Created by model-proxy on 2026/05/27
# Copyright © 2026

"""Cursor Agent CLI 专用 API 路由"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from src.providers.cursor import CursorProvider

router = APIRouter(prefix="/api/cursor", tags=["cursor"])


class CreateSessionResponse(BaseModel):
    """创建会话响应"""
    session_id: str = Field(description="新创建的 Cursor 会话 ID")


class SessionInfo(BaseModel):
    """会话信息"""
    id: str = Field(description="会话 ID")
    title: str = Field(default="", description="会话标题")


class ListSessionsResponse(BaseModel):
    """列出会话响应"""
    sessions: list[SessionInfo] = Field(description="会话列表")


@router.post("/sessions/create", response_model=CreateSessionResponse)
async def create_cursor_session(
    authorization: str | None = Header(None, description="Bearer {api_key}"),
):
    """创建新的 Cursor 会话
    
    **使用场景**：
    - 预创建会话 ID，用于后续 --resume
    - 管理多个独立会话
    
    **返回**：
    - `session_id`: 新创建的会话 ID，可用于后续请求的 `cursor_session_id` 参数
    
    **示例**：
    ```bash
    curl -X POST http://localhost:8009/api/cursor/sessions/create \\
      -H "Authorization: Bearer your-api-key"
    ```
    """
    api_key = None
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    
    provider = CursorProvider(api_key=api_key or "")
    
    try:
        session_id = await provider.create_session()
        return CreateSessionResponse(session_id=session_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=ListSessionsResponse)
async def list_cursor_sessions(
    authorization: str | None = Header(None, description="Bearer {api_key}"),
):
    """列出所有 Cursor 会话
    
    **使用场景**：
    - 查看历史会话列表
    - 选择要恢复的会话
    
    **返回**：
    - `sessions`: 会话列表，每个会话包含 `id` 和 `title`
    
    **示例**：
    ```bash
    curl -X GET http://localhost:8009/api/cursor/sessions \\
      -H "Authorization: Bearer your-api-key"
    ```
    """
    api_key = None
    if authorization and authorization.startswith("Bearer "):
        api_key = authorization[7:]
    
    provider = CursorProvider(api_key=api_key or "")
    
    try:
        sessions_raw = await provider.list_sessions()
        sessions = [SessionInfo(**s) for s in sessions_raw]
        return ListSessionsResponse(sessions=sessions)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
